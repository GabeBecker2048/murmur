"""The SQLite operational store. Every write is an upsert so reruns are idempotent."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from murmur.models import Article, PriceBar, SentimentScores, UniverseMember, normalize_title

SCHEMA_VERSION = 1

# One entry per schema version; applied in order on open. Never edit a shipped entry.
MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE universe_membership (
        date        TEXT NOT NULL,
        ticker      TEXT NOT NULL,
        name        TEXT NOT NULL,
        market_cap  REAL,
        rank        INTEGER,
        source      TEXT NOT NULL,
        PRIMARY KEY (date, ticker)
    );
    CREATE TABLE articles (
        id                INTEGER PRIMARY KEY,
        url               TEXT NOT NULL UNIQUE,
        source_id         TEXT,
        title             TEXT NOT NULL,
        title_norm        TEXT NOT NULL,
        published_at      TEXT,
        source            TEXT NOT NULL,
        provider          TEXT,
        content_type      TEXT,
        first_seen_at     TEXT NOT NULL,
        first_seen_source TEXT NOT NULL
    );
    CREATE INDEX idx_articles_published_at ON articles (published_at);
    CREATE INDEX idx_articles_title_norm ON articles (title_norm);
    CREATE TABLE article_tickers (
        article_id INTEGER NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
        ticker     TEXT NOT NULL,
        PRIMARY KEY (article_id, ticker)
    );
    CREATE INDEX idx_article_tickers_ticker ON article_tickers (ticker, article_id);
    CREATE TABLE prices (
        ticker    TEXT NOT NULL,
        date      TEXT NOT NULL,
        open      REAL,
        high      REAL,
        low       REAL,
        close     REAL NOT NULL,
        adj_close REAL,
        volume    INTEGER,
        source    TEXT NOT NULL,
        PRIMARY KEY (ticker, date)
    );
    CREATE INDEX idx_prices_date ON prices (date);
    CREATE TABLE sentiment (
        article_id INTEGER NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
        model      TEXT NOT NULL,
        positive   REAL NOT NULL,
        negative   REAL NOT NULL,
        neutral    REAL NOT NULL,
        label      TEXT NOT NULL,
        score      REAL NOT NULL,
        scored_at  TEXT NOT NULL,
        PRIMARY KEY (article_id, model)
    );
    CREATE TABLE daily_sentiment (
        date       TEXT NOT NULL,
        ticker     TEXT NOT NULL,
        model      TEXT NOT NULL,
        n_articles INTEGER NOT NULL,
        mean_score REAL NOT NULL,
        PRIMARY KEY (date, ticker, model)
    );
    CREATE TABLE correlations (
        date        TEXT NOT NULL,
        window      TEXT NOT NULL,
        scope       TEXT NOT NULL,
        source      TEXT NOT NULL,
        method      TEXT NOT NULL,
        model       TEXT NOT NULL,
        n           INTEGER NOT NULL,
        estimate    REAL NOT NULL,
        p_value     REAL NOT NULL,
        ci_low      REAL,
        ci_high     REAL,
        tickers     TEXT NOT NULL,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (date, window, scope, source, method, model)
    );
    """,
}


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path) if path != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path is not None:
            self.conn.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    @property
    def schema_version(self) -> int:
        return self.conn.execute("PRAGMA user_version").fetchone()[0]

    def _migrate(self) -> None:
        current = self.schema_version
        if current > SCHEMA_VERSION:
            raise RuntimeError(
                f"store schema {current} is newer than this murmur ({SCHEMA_VERSION})"
            )
        for version in range(current + 1, SCHEMA_VERSION + 1):
            with self.conn:
                self.conn.execute("BEGIN")
                self.conn.executescript(MIGRATIONS[version])
                self.conn.execute(f"PRAGMA user_version = {version}")

    # -- universe -------------------------------------------------------------

    def record_universe(self, date: str, members: Iterable[UniverseMember], source: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.executemany(
                """INSERT INTO universe_membership (date, ticker, name, market_cap, rank, source)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (date, ticker) DO UPDATE SET
                     name = excluded.name, market_cap = excluded.market_cap,
                     rank = excluded.rank, source = excluded.source""",
                [(date, m.ticker, m.name, m.market_cap, m.rank, source) for m in members],
            )

    def universe_for(self, date: str) -> list[UniverseMember]:
        rows = self.conn.execute(
            """SELECT ticker, name, market_cap, rank FROM universe_membership
               WHERE date = ? ORDER BY rank, ticker""",
            (date,),
        )
        return [UniverseMember(r["ticker"], r["name"], r["market_cap"], r["rank"]) for r in rows]

    # -- articles -------------------------------------------------------------

    def upsert_article(self, article: Article, ticker: str, seen_at: str) -> tuple[int, bool]:
        """Insert or merge by URL and link it to ``ticker``.

        On conflict the earliest ``published_at`` wins and ``first_seen_*`` is
        never overwritten. Returns ``(article_id, is_new)``.
        """
        with self.conn:
            self.conn.execute("BEGIN")
            existing = self.conn.execute(
                "SELECT id FROM articles WHERE url = ?", (article.url,)
            ).fetchone()
            self.conn.execute(
                """INSERT INTO articles (url, source_id, title, title_norm, published_at, source,
                                         provider, content_type, first_seen_at, first_seen_source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (url) DO UPDATE SET
                     published_at = CASE
                       WHEN excluded.published_at IS NULL THEN articles.published_at
                       WHEN articles.published_at IS NULL THEN excluded.published_at
                       ELSE min(articles.published_at, excluded.published_at)
                     END,
                     provider = coalesce(articles.provider, excluded.provider),
                     source_id = coalesce(articles.source_id, excluded.source_id)""",
                (
                    article.url,
                    article.source_id,
                    article.title,
                    normalize_title(article.title),
                    article.published_at,
                    article.source,
                    article.provider,
                    article.content_type,
                    seen_at,
                    article.source,
                ),
            )
            article_id = (
                existing["id"]
                if existing
                else self.conn.execute(
                    "SELECT id FROM articles WHERE url = ?", (article.url,)
                ).fetchone()["id"]
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO article_tickers (article_id, ticker) VALUES (?, ?)",
                (article_id, ticker),
            )
        return article_id, existing is None

    def unscored_articles(self, model: str) -> list[tuple[int, str]]:
        rows = self.conn.execute(
            """SELECT a.id, a.title FROM articles a
               LEFT JOIN sentiment s ON s.article_id = a.id AND s.model = ?
               WHERE s.article_id IS NULL ORDER BY a.id""",
            (model,),
        )
        return [(r["id"], r["title"]) for r in rows]

    def count(self, table: str) -> int:
        if table not in {
            "universe_membership",
            "articles",
            "article_tickers",
            "prices",
            "sentiment",
            "daily_sentiment",
            "correlations",
        }:
            raise ValueError(table)
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def articles_in_window(self, start_utc: str, end_utc: str) -> list[sqlite3.Row]:
        """Articles published in ``[start, end)`` with their tickers comma-joined."""
        return self.conn.execute(
            """SELECT a.id, a.url, a.title, a.published_at, a.source, a.provider, a.content_type,
                      a.first_seen_at, group_concat(t.ticker, ',') AS tickers
               FROM articles a JOIN article_tickers t ON t.article_id = a.id
               WHERE a.published_at >= ? AND a.published_at < ?
               GROUP BY a.id ORDER BY a.published_at, a.id""",
            (start_utc, end_utc),
        ).fetchall()

    # -- prices ---------------------------------------------------------------

    def upsert_prices(self, bars: Iterable[PriceBar], source: str) -> int:
        rows = [
            (b.ticker, b.date, b.open, b.high, b.low, b.close, b.adj_close, b.volume, source)
            for b in bars
        ]
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.executemany(
                """INSERT INTO prices (ticker, date, open, high, low, close, adj_close, volume,
                                       source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (ticker, date) DO UPDATE SET
                     open = excluded.open, high = excluded.high, low = excluded.low,
                     close = excluded.close, adj_close = excluded.adj_close,
                     volume = excluded.volume, source = excluded.source""",
                rows,
            )
        return len(rows)

    def prices_on(self, date: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT ticker, date, open, high, low, close, adj_close, volume, source
               FROM prices WHERE date = ? ORDER BY ticker""",
            (date,),
        ).fetchall()

    def last_two_closes(self, date: str) -> dict[str, list[tuple[str, float]]]:
        """For each ticker, the two latest ``(date, close)`` on or before ``date``, newest first."""
        rows = self.conn.execute(
            """SELECT ticker, date, close FROM (
                 SELECT ticker, date, close,
                        row_number() OVER (PARTITION BY ticker ORDER BY date DESC) AS rn
                 FROM prices WHERE date <= ?)
               WHERE rn <= 2 ORDER BY ticker, date DESC""",
            (date,),
        )
        out: dict[str, list[tuple[str, float]]] = {}
        for r in rows:
            out.setdefault(r["ticker"], []).append((r["date"], r["close"]))
        return out

    # -- sentiment ------------------------------------------------------------

    def insert_scores(
        self, model: str, scored: Iterable[tuple[int, SentimentScores]], scored_at: str
    ) -> int:
        rows = [
            (aid, model, s.positive, s.negative, s.neutral, s.label, s.score, scored_at)
            for aid, s in scored
        ]
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.executemany(
                """INSERT INTO sentiment (article_id, model, positive, negative, neutral, label,
                                          score, scored_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (article_id, model) DO UPDATE SET
                     positive = excluded.positive, negative = excluded.negative,
                     neutral = excluded.neutral, label = excluded.label,
                     score = excluded.score, scored_at = excluded.scored_at""",
                rows,
            )
        return len(rows)

    def sentiment_in_window(self, start_utc: str, end_utc: str, model: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT s.article_id, t.ticker, a.published_at, s.positive, s.negative, s.neutral,
                      s.label, s.score, s.model
               FROM sentiment s
               JOIN articles a ON a.id = s.article_id
               JOIN article_tickers t ON t.article_id = a.id
               WHERE s.model = ? AND a.published_at >= ? AND a.published_at < ?
               ORDER BY t.ticker, a.published_at, s.article_id""",
            (model, start_utc, end_utc),
        ).fetchall()

    def ticker_scores_in_window(
        self, start_utc: str, end_utc: str, model: str
    ) -> dict[str, tuple[int, float]]:
        """``ticker -> (n_articles, mean_score)`` over articles published in the window."""
        rows = self.conn.execute(
            """SELECT t.ticker, count(*) AS n, avg(s.score) AS mean_score
               FROM sentiment s
               JOIN articles a ON a.id = s.article_id
               JOIN article_tickers t ON t.article_id = a.id
               WHERE s.model = ? AND a.published_at >= ? AND a.published_at < ?
               GROUP BY t.ticker""",
            (model, start_utc, end_utc),
        )
        return {r["ticker"]: (r["n"], r["mean_score"]) for r in rows}

    # -- analysis results -----------------------------------------------------

    def upsert_daily_sentiment(
        self, date: str, model: str, per_ticker: dict[str, tuple[int, float]]
    ) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.executemany(
                """INSERT INTO daily_sentiment (date, ticker, model, n_articles, mean_score)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (date, ticker, model) DO UPDATE SET
                     n_articles = excluded.n_articles, mean_score = excluded.mean_score""",
                [(date, t, model, n, mean) for t, (n, mean) in per_ticker.items()],
            )

    def daily_sentiment_on(self, date: str, model: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT date, ticker, model, n_articles, mean_score FROM daily_sentiment
               WHERE date = ? AND model = ? ORDER BY ticker""",
            (date, model),
        ).fetchall()

    def upsert_correlation(self, row: dict) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(
                """INSERT INTO correlations (date, window, scope, source, method, model, n,
                                             estimate, p_value, ci_low, ci_high, tickers,
                                             computed_at)
                   VALUES (:date, :window, :scope, :source, :method, :model, :n, :estimate,
                           :p_value, :ci_low, :ci_high, :tickers, :computed_at)
                   ON CONFLICT (date, window, scope, source, method, model) DO UPDATE SET
                     n = excluded.n, estimate = excluded.estimate, p_value = excluded.p_value,
                     ci_low = excluded.ci_low, ci_high = excluded.ci_high,
                     tickers = excluded.tickers, computed_at = excluded.computed_at""",
                row,
            )

    def correlations_on(self, date: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT date, window, scope, source, method, model, n, estimate, p_value,
                      ci_low, ci_high, tickers, computed_at
               FROM correlations WHERE date = ? ORDER BY window, scope, source, method""",
            (date,),
        ).fetchall()
