"""The SQLite operational store. Every write is an upsert so reruns are idempotent."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from murmur.models import Article, PriceBar, SentimentScores, Split, UniverseMember, normalize_title

SCHEMA_VERSION = 9

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
    2: """
    ALTER TABLE universe_membership ADD COLUMN cik INTEGER;
    CREATE TABLE companies (
        cik                INTEGER PRIMARY KEY,
        ticker             TEXT NOT NULL,
        name               TEXT NOT NULL,
        country            TEXT,
        country_checked_at TEXT
    );
    CREATE TABLE universe_months (
        month       TEXT NOT NULL,
        rank        INTEGER NOT NULL,
        ticker      TEXT NOT NULL,
        cik         INTEGER,
        name        TEXT NOT NULL,
        market_cap  REAL,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (month, ticker)
    );
    CREATE TABLE runs (
        id          INTEGER PRIMARY KEY,
        date        TEXT NOT NULL,
        trigger     TEXT NOT NULL,
        attempt     INTEGER NOT NULL,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        ok          INTEGER,
        stages      TEXT
    );
    CREATE INDEX idx_runs_date ON runs (date);
    """,
    3: """
    ALTER TABLE articles ADD COLUMN trading_day TEXT;
    ALTER TABLE articles ADD COLUMN window TEXT;
    CREATE INDEX idx_articles_trading_day ON articles (trading_day, window);
    CREATE TABLE window_sentiment (
        date       TEXT NOT NULL,
        window     TEXT NOT NULL,
        ticker     TEXT NOT NULL,
        model      TEXT NOT NULL,
        n_articles INTEGER NOT NULL,
        mean_score REAL NOT NULL,
        PRIMARY KEY (date, window, ticker, model)
    );
    CREATE TABLE returns (
        ticker     TEXT NOT NULL,
        date       TEXT NOT NULL,
        prev_date  TEXT NOT NULL,
        prev_close REAL NOT NULL,
        open       REAL NOT NULL,
        close      REAL NOT NULL,
        overnight  REAL NOT NULL,
        intraday   REAL NOT NULL,
        daily      REAL NOT NULL,
        PRIMARY KEY (ticker, date)
    );
    CREATE INDEX idx_returns_date ON returns (date);
    CREATE TABLE correlations_v3 (
        date        TEXT NOT NULL,
        window      TEXT NOT NULL,
        return_type TEXT NOT NULL,
        lag         INTEGER NOT NULL,
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
        PRIMARY KEY (date, window, return_type, lag, scope, source, method, model)
    );
    INSERT INTO correlations_v3
        SELECT date, 'day', 'daily', 0, scope, source, method, model, n, estimate, p_value,
               ci_low, ci_high, tickers, computed_at
        FROM correlations;
    DROP TABLE correlations;
    ALTER TABLE correlations_v3 RENAME TO correlations;
    DROP TABLE daily_sentiment;
    """,
    4: """
    CREATE TABLE rolling_correlations (
        as_of       TEXT NOT NULL,
        months      INTEGER NOT NULL,
        window      TEXT NOT NULL,
        return_type TEXT NOT NULL,
        lag         INTEGER NOT NULL,
        scope       TEXT NOT NULL,
        ticker      TEXT NOT NULL,
        method      TEXT NOT NULL,
        n           INTEGER NOT NULL,
        estimate    REAL NOT NULL,
        p_value     REAL NOT NULL,
        ci_low      REAL,
        ci_high     REAL,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (as_of, months, window, return_type, lag, scope, ticker, method)
    );
    """,
    5: """
    CREATE TABLE article_sources (
        article_id   INTEGER NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
        source       TEXT NOT NULL,
        url          TEXT NOT NULL,
        published_at TEXT,
        seen_at      TEXT NOT NULL,
        PRIMARY KEY (article_id, source)
    );
    CREATE INDEX idx_article_sources_source ON article_sources (source, article_id);
    INSERT INTO article_sources (article_id, source, url, published_at, seen_at)
        SELECT id, source, url, published_at, first_seen_at FROM articles;
    CREATE TABLE window_sentiment_v5 (
        date       TEXT NOT NULL,
        window     TEXT NOT NULL,
        source     TEXT NOT NULL,
        ticker     TEXT NOT NULL,
        model      TEXT NOT NULL,
        n_articles INTEGER NOT NULL,
        mean_score REAL NOT NULL,
        PRIMARY KEY (date, window, source, ticker, model)
    );
    INSERT INTO window_sentiment_v5
        SELECT date, window, 'all', ticker, model, n_articles, mean_score FROM window_sentiment;
    DROP TABLE window_sentiment;
    ALTER TABLE window_sentiment_v5 RENAME TO window_sentiment;
    CREATE TABLE rolling_correlations_v5 (
        as_of       TEXT NOT NULL,
        months      INTEGER NOT NULL,
        window      TEXT NOT NULL,
        return_type TEXT NOT NULL,
        lag         INTEGER NOT NULL,
        source      TEXT NOT NULL,
        scope       TEXT NOT NULL,
        ticker      TEXT NOT NULL,
        method      TEXT NOT NULL,
        n           INTEGER NOT NULL,
        estimate    REAL NOT NULL,
        p_value     REAL NOT NULL,
        ci_low      REAL,
        ci_high     REAL,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (as_of, months, window, return_type, lag, source, scope, ticker, method)
    );
    INSERT INTO rolling_correlations_v5
        SELECT as_of, months, window, return_type, lag, 'all', scope, ticker, method, n,
               estimate, p_value, ci_low, ci_high, computed_at
        FROM rolling_correlations;
    DROP TABLE rolling_correlations;
    ALTER TABLE rolling_correlations_v5 RENAME TO rolling_correlations;
    """,
    6: """
    CREATE TABLE jobs (
        id          INTEGER PRIMARY KEY,
        type        TEXT NOT NULL,
        params      TEXT NOT NULL,
        day         TEXT,
        trigger     TEXT NOT NULL,
        status      TEXT NOT NULL,
        priority    INTEGER NOT NULL DEFAULT 0,
        not_before  TEXT,
        attempt     INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL,
        started_at  TEXT,
        finished_at TEXT,
        progress    TEXT,
        result      TEXT,
        error       TEXT,
        run_id      INTEGER,
        log         TEXT
    );
    CREATE INDEX idx_jobs_status ON jobs (status, not_before, priority, id);
    CREATE INDEX idx_jobs_day ON jobs (type, day);
    """,
    7: """
    -- 0.7.0: the universe doubles as a cap-weighted index (weight = cap / month total).
    ALTER TABLE universe_months ADD COLUMN weight REAL;
    ALTER TABLE universe_months ADD COLUMN exchange TEXT;
    ALTER TABLE companies ADD COLUMN exchange TEXT;
    UPDATE universe_months SET weight = market_cap / (
        SELECT sum(market_cap) FROM universe_months t WHERE t.month = universe_months.month
    ) WHERE market_cap IS NOT NULL;
    """,
    8: """
    -- 0.7.0: runs know what they did (full | collect | analyze); split ratios for returns.
    ALTER TABLE runs ADD COLUMN kind TEXT NOT NULL DEFAULT 'full';
    CREATE TABLE splits (
        ticker         TEXT NOT NULL,
        execution_date TEXT NOT NULL,
        ratio          REAL NOT NULL,
        source         TEXT NOT NULL,
        PRIMARY KEY (ticker, execution_date)
    );
    """,
    9: """
    -- 0.7.1: how a month's caps were obtained (live screener | reconstructed | static).
    ALTER TABLE universe_months ADD COLUMN method TEXT NOT NULL DEFAULT 'live';
    """,
}

JOB_ACTIVE = ("queued", "running", "cancelling")
JOB_FINAL = ("done", "failed", "cancelled", "interrupted")

DEDUP_WINDOW_DAYS = 2
"""Two articles with the same normalized title published within this many days
of each other are the same article (seen through different sources)."""


def _member(r: sqlite3.Row) -> UniverseMember:
    keys = r.keys()
    return UniverseMember(
        r["ticker"],
        r["name"],
        r["market_cap"],
        r["rank"],
        r["cik"],
        weight=r["weight"] if "weight" in keys else None,
        exchange=r["exchange"] if "exchange" in keys else None,
    )


class Store:
    def __init__(
        self,
        path: str | Path,
        *,
        check_same_thread: bool = True,
        timeout: float = 30.0,
        migrate: bool = True,
    ):
        self.path = Path(path) if path != ":memory:" else None
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(
            str(path), isolation_level=None, check_same_thread=check_same_thread, timeout=timeout
        )
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        if self.path is not None:
            self._ensure_wal()
        if migrate:
            self._migrate()

    def _ensure_wal(self) -> None:
        """Switch a new file to WAL once. Changing the journal mode needs exclusive
        access, so a connection racing another on a brand-new file retries briefly."""
        import time

        for attempt in range(50):
            try:
                mode = self.conn.execute("PRAGMA journal_mode").fetchone()[0]
                if str(mode).lower() == "wal":
                    return
                self.conn.execute("PRAGMA journal_mode = WAL")
                return
            except sqlite3.OperationalError as e:
                if "locked" not in str(e) or attempt == 49:
                    raise
                time.sleep(0.05)

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
            # One script per version, applied atomically.
            self.conn.executescript(
                f"BEGIN;\n{MIGRATIONS[version]}\nPRAGMA user_version = {version};\nCOMMIT;"
            )

    # -- universe -------------------------------------------------------------

    def record_universe(self, date: str, members: Iterable[UniverseMember], source: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute("DELETE FROM universe_membership WHERE date = ?", (date,))
            self.conn.executemany(
                """INSERT INTO universe_membership (date, ticker, name, market_cap, rank, source,
                                                    cik)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [(date, m.ticker, m.name, m.market_cap, m.rank, source, m.cik) for m in members],
            )

    def universe_for(self, date: str) -> list[UniverseMember]:
        rows = self.conn.execute(
            """SELECT ticker, name, market_cap, rank, cik FROM universe_membership
               WHERE date = ? ORDER BY rank, ticker""",
            (date,),
        )
        return [_member(r) for r in rows]

    def universe_month(self, month: str) -> list[UniverseMember]:
        rows = self.conn.execute(
            """SELECT ticker, name, market_cap, rank, cik, weight, exchange FROM universe_months
               WHERE month = ? ORDER BY rank, ticker""",
            (month,),
        )
        return [_member(r) for r in rows]

    def save_universe_month(
        self,
        month: str,
        members: Iterable[UniverseMember],
        computed_at: str,
        method: str = "live",
    ) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute("DELETE FROM universe_months WHERE month = ?", (month,))
            self.conn.executemany(
                """INSERT INTO universe_months (month, rank, ticker, cik, name, market_cap,
                                                computed_at, weight, exchange, method)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        month,
                        m.rank,
                        m.ticker,
                        m.cik,
                        m.name,
                        m.market_cap,
                        computed_at,
                        m.weight,
                        m.exchange,
                        method,
                    )
                    for m in members
                ],
            )

    def universe_month_method(self, month: str) -> str | None:
        row = self.conn.execute(
            "SELECT method, computed_at FROM universe_months WHERE month = ? LIMIT 1", (month,)
        ).fetchone()
        return row["method"] if row else None

    def universe_months(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT month FROM universe_months ORDER BY month")
        return [r["month"] for r in rows]

    def universe_changes(self, month: str) -> list[sqlite3.Row]:
        """Constituents added to and removed from ``month`` relative to the previous
        month on record (``month, cik, ticker, name, action, rank``). The first month
        has no changes. Companies are matched by CIK, falling back to ticker."""
        prev = self.conn.execute(
            "SELECT max(month) FROM universe_months WHERE month < ?", (month,)
        ).fetchone()[0]
        if prev is None:
            return []
        return self.conn.execute(
            """SELECT ? AS month, cik, ticker, name, 'added' AS action, rank
               FROM universe_months a WHERE a.month = ? AND NOT EXISTS (
                 SELECT 1 FROM universe_months b WHERE b.month = ?
                   AND coalesce(b.cik, b.ticker) = coalesce(a.cik, a.ticker))
               UNION ALL
               SELECT ? AS month, cik, ticker, name, 'removed' AS action, rank
               FROM universe_months a WHERE a.month = ? AND NOT EXISTS (
                 SELECT 1 FROM universe_months b WHERE b.month = ?
                   AND coalesce(b.cik, b.ticker) = coalesce(a.cik, a.ticker))
               ORDER BY action, rank""",
            (month, month, prev, month, prev, month),
        ).fetchall()

    def ticker_history(self, key: str) -> list[sqlite3.Row]:
        """Rank and weight per month for a ticker or a CIK (digits)."""
        where = "cik = ?" if key.isdigit() else "ticker = ?"
        return self.conn.execute(
            f"""SELECT month, rank, ticker, cik, name, market_cap, weight
                FROM universe_months WHERE {where} ORDER BY month""",
            (int(key) if key.isdigit() else key,),
        ).fetchall()

    def companies_snapshot(self, upto: str | None = None) -> list[sqlite3.Row]:
        """One row per company ranked in any month up to ``upto`` (default: all):
        identity, the latest ticker and name, exchange and country where known, and
        the first and last month ranked."""
        upto = upto or "9999-99"
        return self.conn.execute(
            """SELECT g.cik, l.ticker, l.name, c.exchange, c.country,
                      g.first_month, g.last_month, g.months
               FROM (SELECT coalesce(cik, ticker) AS k, cik, min(month) AS first_month,
                            max(month) AS last_month, count(*) AS months
                     FROM universe_months WHERE month <= ?
                     GROUP BY coalesce(cik, ticker)) g
               JOIN universe_months l
                 ON coalesce(l.cik, l.ticker) = g.k AND l.month = g.last_month
               LEFT JOIN companies c ON c.cik = g.cik
               ORDER BY l.ticker""",
            (upto,),
        ).fetchall()

    def companies_query(self, q: str | None, limit: int, offset: int) -> tuple[list, int]:
        rows = self.companies_snapshot()
        if q:
            needle = q.lower()
            rows = [
                r
                for r in rows
                if needle in r["ticker"].lower()
                or needle in r["name"].lower()
                or (r["cik"] is not None and needle == str(r["cik"]))
            ]
        return rows[offset : offset + limit], len(rows)

    def ticker_ciks(self) -> dict[str, int]:
        """ticker -> CIK from the latest month each ticker was ranked."""
        rows = self.conn.execute(
            """SELECT ticker, cik FROM universe_months WHERE cik IS NOT NULL
               ORDER BY month"""  # later months overwrite earlier ones
        )
        return {r["ticker"]: r["cik"] for r in rows}

    # -- companies (CIK -> country cache) -------------------------------------

    def company_countries(self) -> dict[int, tuple[str | None, str | None]]:
        rows = self.conn.execute("SELECT cik, country, country_checked_at FROM companies")
        return {r["cik"]: (r["country"], r["country_checked_at"]) for r in rows}

    def upsert_company(
        self,
        cik: int,
        ticker: str,
        name: str,
        country: str | None,
        checked_at: str,
        exchange: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(
                """INSERT INTO companies (cik, ticker, name, country, country_checked_at,
                                          exchange)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT (cik) DO UPDATE SET
                     ticker = excluded.ticker, name = excluded.name,
                     country = excluded.country,
                     country_checked_at = excluded.country_checked_at,
                     exchange = coalesce(excluded.exchange, companies.exchange)""",
                (cik, ticker, name, country, checked_at, exchange),
            )

    def set_company_exchange(self, cik: int, exchange: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute("UPDATE companies SET exchange = ? WHERE cik = ?", (exchange, cik))

    # -- runs -----------------------------------------------------------------

    def start_run(
        self, date: str, trigger: str, attempt: int, started_at: str, kind: str = "full"
    ) -> int:
        with self.conn:
            self.conn.execute("BEGIN")
            cur = self.conn.execute(
                """INSERT INTO runs (date, trigger, attempt, started_at, kind)
                   VALUES (?, ?, ?, ?, ?)""",
                (date, trigger, attempt, started_at, kind),
            )
            return cur.lastrowid

    def finish_run(self, run_id: int, finished_at: str, ok: bool, stages_json: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "UPDATE runs SET finished_at = ?, ok = ?, stages = ? WHERE id = ?",
                (finished_at, int(ok), stages_json, run_id),
            )

    def successful_run_exists(self, date: str, kind: str | None = None) -> bool:
        """A successful run for the day; with ``kind``, one of that kind or a full run."""
        if kind is None or kind == "full":
            kinds = ("full",) if kind == "full" else ("full", "collect", "analyze")
        else:
            kinds = ("full", kind)
        marks = ",".join("?" * len(kinds))
        row = self.conn.execute(
            f"SELECT 1 FROM runs WHERE date = ? AND ok = 1 AND kind IN ({marks}) LIMIT 1",
            (date, *kinds),
        ).fetchone()
        return row is not None

    def recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT id, date, trigger, attempt, started_at, finished_at, ok, stages, kind
               FROM runs ORDER BY id DESC LIMIT ?""",
            (limit,),
        ).fetchall()

    # -- splits ---------------------------------------------------------------

    def upsert_splits(self, splits: Iterable[Split], source: str) -> int:
        rows = [(x.ticker, x.execution_date, x.ratio, source) for x in splits]
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.executemany(
                """INSERT INTO splits (ticker, execution_date, ratio, source)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT (ticker, execution_date) DO UPDATE SET
                     ratio = excluded.ratio, source = excluded.source""",
                rows,
            )
        return len(rows)

    def splits_on(self, date: str) -> dict[str, float]:
        """ticker -> ratio for splits that take effect on ``date``."""
        rows = self.conn.execute(
            "SELECT ticker, ratio FROM splits WHERE execution_date = ?", (date,)
        )
        return {r["ticker"]: r["ratio"] for r in rows}

    def splits_between(self, start: str, end: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT ticker, execution_date, ratio, source FROM splits
               WHERE execution_date >= ? AND execution_date <= ? ORDER BY execution_date, ticker""",
            (start, end),
        ).fetchall()

    # -- articles -------------------------------------------------------------

    def upsert_article(self, article: Article, ticker: str, seen_at: str) -> tuple[int, bool]:
        """Insert or merge, and link it to ``ticker``.

        An article is the same article if its URL is known, or if an article
        with the same normalized title was published within ``DEDUP_WINDOW_DAYS``.
        On a merge the earliest ``published_at`` wins and ``first_seen_*`` is
        never overwritten; every source that reported it is recorded in
        ``article_sources``. Returns ``(article_id, is_new)``.
        """
        norm = normalize_title(article.title)
        with self.conn:
            self.conn.execute("BEGIN")
            row = self.conn.execute(
                "SELECT id FROM articles WHERE url = ?", (article.url,)
            ).fetchone()
            if row is None:
                row = self._same_title(norm, article.published_at)
            if row is None:
                cur = self.conn.execute(
                    """INSERT INTO articles (url, source_id, title, title_norm, published_at,
                                             source, provider, content_type, first_seen_at,
                                             first_seen_source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        article.url,
                        article.source_id,
                        article.title,
                        norm,
                        article.published_at,
                        article.source,
                        article.provider,
                        article.content_type,
                        seen_at,
                        article.source,
                    ),
                )
                article_id, is_new = cur.lastrowid, True
            else:
                article_id, is_new = row["id"], False
                self.conn.execute(
                    """UPDATE articles SET
                         published_at = CASE
                           WHEN ? IS NULL THEN published_at
                           WHEN published_at IS NULL THEN ?
                           ELSE min(published_at, ?) END,
                         provider = coalesce(provider, ?),
                         source_id = coalesce(source_id, ?)
                       WHERE id = ?""",
                    (
                        article.published_at,
                        article.published_at,
                        article.published_at,
                        article.provider,
                        article.source_id,
                        article_id,
                    ),
                )
            self.conn.execute(
                """INSERT OR IGNORE INTO article_sources (article_id, source, url, published_at,
                                                          seen_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (article_id, article.source, article.url, article.published_at, seen_at),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO article_tickers (article_id, ticker) VALUES (?, ?)",
                (article_id, ticker),
            )
        return article_id, is_new

    def _same_title(self, title_norm: str, published_at: str | None) -> sqlite3.Row | None:
        if not title_norm:
            return None
        if published_at is None:
            return self.conn.execute(
                "SELECT id FROM articles WHERE title_norm = ? ORDER BY id LIMIT 1", (title_norm,)
            ).fetchone()
        from datetime import timedelta

        from murmur.clock import parse_utc, to_utc_iso

        stamp = parse_utc(published_at)
        lo = to_utc_iso(stamp - timedelta(days=DEDUP_WINDOW_DAYS))
        hi = to_utc_iso(stamp + timedelta(days=DEDUP_WINDOW_DAYS))
        return self.conn.execute(
            """SELECT id FROM articles
               WHERE title_norm = ? AND (published_at IS NULL OR published_at BETWEEN ? AND ?)
               ORDER BY id LIMIT 1""",
            (title_norm, lo, hi),
        ).fetchone()

    def sources_seen(self) -> list[str]:
        return [
            r[0]
            for r in self.conn.execute("SELECT DISTINCT source FROM article_sources ORDER BY 1")
        ]

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
            "universe_months",
            "companies",
            "articles",
            "article_tickers",
            "article_sources",
            "prices",
            "sentiment",
            "window_sentiment",
            "returns",
            "correlations",
            "rolling_correlations",
            "runs",
            "jobs",
        }:
            raise ValueError(table)
        return self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]

    def articles_without_window(self) -> list[tuple[int, str]]:
        rows = self.conn.execute(
            """SELECT id, published_at FROM articles
               WHERE published_at IS NOT NULL AND trading_day IS NULL ORDER BY id"""
        )
        return [(r["id"], r["published_at"]) for r in rows]

    def set_article_windows(self, rows: Iterable[tuple[str, str, int]]) -> None:
        """``(trading_day, window, article_id)`` triples."""
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.executemany(
                "UPDATE articles SET trading_day = ?, window = ? WHERE id = ?", list(rows)
            )

    def articles_on(self, trading_day: str) -> list[sqlite3.Row]:
        """Articles of a trading day (both windows) with their tickers comma-joined."""
        return self.conn.execute(
            """SELECT a.id, a.url, a.title, a.published_at, a.trading_day, a.window, a.source,
                      a.provider, a.content_type, a.first_seen_at,
                      (SELECT group_concat(ticker, ',') FROM article_tickers
                        WHERE article_id = a.id) AS tickers,
                      (SELECT group_concat(source, ',') FROM (
                         SELECT source FROM article_sources
                         WHERE article_id = a.id ORDER BY seen_at, rowid)) AS sources
               FROM articles a
               WHERE a.trading_day = ?
               ORDER BY a.published_at, a.id""",
            (trading_day,),
        ).fetchall()

    def known_days(self) -> list[str]:
        """Every date that has price bars or articles assigned to it."""
        rows = self.conn.execute(
            """SELECT date FROM prices
               UNION SELECT trading_day FROM articles WHERE trading_day IS NOT NULL
               ORDER BY 1"""
        )
        return [r[0] for r in rows]

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

    def bars_on(self, date: str) -> dict[str, sqlite3.Row]:
        return {r["ticker"]: r for r in self.prices_on(date)}

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

    def sentiment_on(self, trading_day: str, model: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT s.article_id, t.ticker, a.trading_day, a.window, a.published_at,
                      s.positive, s.negative, s.neutral, s.label, s.score, s.model
               FROM sentiment s
               JOIN articles a ON a.id = s.article_id
               JOIN article_tickers t ON t.article_id = a.id
               WHERE s.model = ? AND a.trading_day = ?
               ORDER BY t.ticker, a.published_at, s.article_id""",
            (model, trading_day),
        ).fetchall()

    def ticker_scores(
        self, trading_day: str, windows: tuple[str, ...], model: str, source: str = "all"
    ) -> dict[str, tuple[int, float]]:
        """``ticker -> (n_articles, mean_score)`` over the given windows of a trading
        day; ``source`` restricts to articles that source reported."""
        marks = ",".join("?" * len(windows))
        join = (
            ""
            if source == "all"
            else "JOIN article_sources src ON src.article_id = a.id AND src.source = ?"
        )
        params = (
            (model, trading_day, *windows)
            if source == "all"
            else (source, model, trading_day, *windows)
        )
        rows = self.conn.execute(
            f"""SELECT t.ticker, count(*) AS n, avg(s.score) AS mean_score
                FROM sentiment s
                JOIN articles a ON a.id = s.article_id
                JOIN article_tickers t ON t.article_id = a.id
                {join}
                WHERE s.model = ? AND a.trading_day = ? AND a.window IN ({marks})
                GROUP BY t.ticker""",
            params,
        )
        return {r["ticker"]: (r["n"], r["mean_score"]) for r in rows}

    # -- analysis results -----------------------------------------------------

    def upsert_window_sentiment(
        self,
        date: str,
        window: str,
        model: str,
        per_ticker: dict[str, tuple[int, float]],
        source: str = "all",
    ) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "DELETE FROM window_sentiment "
                "WHERE date = ? AND window = ? AND source = ? AND model = ?",
                (date, window, source, model),
            )
            self.conn.executemany(
                """INSERT INTO window_sentiment (date, window, source, ticker, model, n_articles,
                                                 mean_score)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [(date, window, source, t, model, n, mean) for t, (n, mean) in per_ticker.items()],
            )

    def window_sentiment_on(self, date: str, model: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT date, window, source, ticker, model, n_articles, mean_score
               FROM window_sentiment
               WHERE date = ? AND model = ? ORDER BY source, ticker, window""",
            (date, model),
        ).fetchall()

    def upsert_returns(self, rows: Iterable[tuple]) -> int:
        """``(ticker, date, prev_date, prev_close, open, close, overnight, intraday, daily)``."""
        rows = list(rows)
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.executemany(
                """INSERT INTO returns (ticker, date, prev_date, prev_close, open, close,
                                        overnight, intraday, daily)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT (ticker, date) DO UPDATE SET
                     prev_date = excluded.prev_date, prev_close = excluded.prev_close,
                     open = excluded.open, close = excluded.close,
                     overnight = excluded.overnight, intraday = excluded.intraday,
                     daily = excluded.daily""",
                rows,
            )
        return len(rows)

    def returns_between(self, start: str, end: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT ticker, date, overnight, intraday, daily FROM returns
               WHERE date >= ? AND date <= ? ORDER BY date, ticker""",
            (start, end),
        ).fetchall()

    def window_sentiment_between(
        self, start: str, end: str, model: str, source: str = "all"
    ) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT date, ticker, window, n_articles, mean_score FROM window_sentiment
               WHERE model = ? AND source = ? AND date >= ? AND date <= ?
               ORDER BY date, ticker, window""",
            (model, source, start, end),
        ).fetchall()

    def days_with_returns(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT DISTINCT date FROM returns ORDER BY date")]

    def replace_rolling(self, as_of: str, rows: Iterable[tuple]) -> int:
        """Replace every rolling row for ``as_of`` with ``rows`` (``(months, window,
        return_type, lag, source, scope, ticker, method, n, estimate, p_value, ci_low,
        ci_high, computed_at)``)."""
        rows = list(rows)
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute("DELETE FROM rolling_correlations WHERE as_of = ?", (as_of,))
            self.conn.executemany(
                """INSERT INTO rolling_correlations (as_of, months, window, return_type, lag,
                       source, scope, ticker, method, n, estimate, p_value, ci_low, ci_high,
                       computed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [(as_of, *r) for r in rows],
            )
        return len(rows)

    def prune_rolling_tickers(self, keep_sessions: int) -> int:
        """Delete per-ticker rolling rows older than the ``keep_sessions`` most recent
        sessions, except each month's last session (kept forever for the monthly
        snapshot files). Pooled rows are never touched. Returns rows deleted."""
        with self.conn:
            self.conn.execute("BEGIN")
            cutoff = self.conn.execute(
                """SELECT min(as_of) FROM (
                     SELECT DISTINCT as_of FROM rolling_correlations
                     ORDER BY as_of DESC LIMIT ?)""",
                (max(1, keep_sessions),),
            ).fetchone()[0]
            if cutoff is None:
                return 0
            return self.conn.execute(
                """DELETE FROM rolling_correlations
                   WHERE scope = 'ticker' AND as_of < ?
                     AND as_of NOT IN (SELECT max(as_of) FROM rolling_correlations
                                       GROUP BY substr(as_of, 1, 7))""",
                (cutoff,),
            ).rowcount

    def rolling_month_snapshot(self, month: str) -> list[sqlite3.Row]:
        """Per-ticker rolling rows as of the last session of ``month`` that has any
        (the month's end-of-month snapshot; for the current month, the latest so far)."""
        as_of = self.conn.execute(
            "SELECT max(as_of) FROM rolling_correlations WHERE substr(as_of, 1, 7) = ?",
            (month,),
        ).fetchone()[0]
        if as_of is None:
            return []
        return self.conn.execute(
            """SELECT as_of, months, window, return_type, lag, source, scope, ticker, method, n,
                      estimate, p_value, ci_low, ci_high, computed_at
               FROM rolling_correlations WHERE as_of = ? AND scope = 'ticker'
               ORDER BY months, window, return_type, lag, source, ticker, method""",
            (as_of,),
        ).fetchall()

    def vacuum(self) -> None:
        """Rebuild the database file to reclaim space. Needs free disk up to the file's
        size and a temp directory (``SQLITE_TMPDIR``) that can hold it. In WAL mode the
        rebuilt pages sit in the WAL until a checkpoint, so one follows."""
        self.conn.execute("VACUUM")
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def rolling_on(self, as_of: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT as_of, months, window, return_type, lag, source, scope, ticker, method, n,
                      estimate, p_value, ci_low, ci_high, computed_at
               FROM rolling_correlations WHERE as_of = ?
               ORDER BY months, window, return_type, lag, source, scope, ticker, method""",
            (as_of,),
        ).fetchall()

    def returns_on(self, date: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT ticker, date, prev_date, prev_close, open, close, overnight, intraday,
                      daily FROM returns WHERE date = ? ORDER BY ticker""",
            (date,),
        ).fetchall()

    def prices_with_returns_on(self, date: str) -> list[sqlite3.Row]:
        """The day's bars with the three returns alongside (the ``prices`` dataset)."""
        return self.conn.execute(
            """SELECT p.ticker, p.date, p.open, p.high, p.low, p.close, p.adj_close, p.volume,
                      p.source, r.prev_date, r.prev_close, r.overnight, r.intraday, r.daily,
                      s.ratio AS split_ratio
               FROM prices p
               LEFT JOIN returns r ON r.ticker = p.ticker AND r.date = p.date
               LEFT JOIN splits s ON s.ticker = p.ticker AND s.execution_date = p.date
               WHERE p.date = ? ORDER BY p.ticker""",
            (date,),
        ).fetchall()

    def articles_with_scores_on(self, trading_day: str, model: str) -> list[sqlite3.Row]:
        """One row per article x ticker for a trading day, with the model's scores
        where they exist (the ``news`` dataset)."""
        return self.conn.execute(
            """SELECT a.id AS article_id, t.ticker, a.title, a.url, a.provider AS publisher,
                      a.content_type, a.source, a.published_at, a.first_seen_at, a.trading_day,
                      a.window,
                      (SELECT group_concat(source, ',') FROM (
                         SELECT source FROM article_sources
                         WHERE article_id = a.id ORDER BY seen_at, rowid)) AS sources,
                      s.positive, s.negative, s.neutral, s.label, s.score,
                      CASE WHEN s.article_id IS NULL THEN NULL ELSE ? END AS model
               FROM articles a
               JOIN article_tickers t ON t.article_id = a.id
               LEFT JOIN sentiment s ON s.article_id = a.id AND s.model = ?
               WHERE a.trading_day = ?
               ORDER BY t.ticker, a.published_at, a.id""",
            (model, model, trading_day),
        ).fetchall()

    def upsert_correlation(self, row: dict) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(
                """INSERT INTO correlations (date, window, return_type, lag, scope, source,
                                             method, model, n, estimate, p_value, ci_low,
                                             ci_high, tickers, computed_at)
                   VALUES (:date, :window, :return_type, :lag, :scope, :source, :method,
                           :model, :n, :estimate, :p_value, :ci_low, :ci_high, :tickers,
                           :computed_at)
                   ON CONFLICT (date, window, return_type, lag, scope, source, method, model)
                   DO UPDATE SET
                     n = excluded.n, estimate = excluded.estimate, p_value = excluded.p_value,
                     ci_low = excluded.ci_low, ci_high = excluded.ci_high,
                     tickers = excluded.tickers, computed_at = excluded.computed_at""",
                row,
            )

    def correlations_on(self, date: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT date, window, return_type, lag, scope, source, method, model, n,
                      estimate, p_value, ci_low, ci_high, tickers, computed_at
               FROM correlations WHERE date = ?
               ORDER BY window, return_type, lag, scope, source, method""",
            (date,),
        ).fetchall()

    def table_counts(self) -> dict[str, int]:
        return {
            t: self.count(t)
            for t in (
                "articles",
                "article_sources",
                "prices",
                "returns",
                "sentiment",
                "window_sentiment",
                "correlations",
                "rolling_correlations",
                "universe_months",
                "runs",
                "jobs",
            )
        }

    # -- jobs -----------------------------------------------------------------

    def create_job(
        self,
        type_: str,
        params: dict,
        trigger: str,
        created_at: str,
        *,
        day: str | None = None,
        priority: int = 0,
        not_before: str | None = None,
        attempt: int = 1,
    ) -> int:
        import json

        with self.conn:
            self.conn.execute("BEGIN")
            cur = self.conn.execute(
                """INSERT INTO jobs (type, params, day, trigger, status, priority, not_before,
                                     attempt, created_at)
                   VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)""",
                (
                    type_,
                    json.dumps(params, sort_keys=True),
                    day,
                    trigger,
                    priority,
                    not_before,
                    attempt,
                    created_at,
                ),
            )
            return cur.lastrowid

    def queued_duplicate(self, type_: str, params: dict) -> int | None:
        import json

        row = self.conn.execute(
            "SELECT id FROM jobs WHERE type = ? AND params = ? AND status IN ('queued', 'running')",
            (type_, json.dumps(params, sort_keys=True)),
        ).fetchone()
        return row["id"] if row else None

    def next_job(self, now: str) -> sqlite3.Row | None:
        return self.conn.execute(
            """SELECT * FROM jobs
               WHERE status = 'queued' AND (not_before IS NULL OR not_before <= ?)
               ORDER BY priority, id LIMIT 1""",
            (now,),
        ).fetchone()

    def next_queued(self) -> sqlite3.Row | None:
        """The earliest queued job regardless of ``not_before`` (for 'next run' displays)."""
        return self.conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY not_before, priority, id LIMIT 1"
        ).fetchone()

    def start_job(self, job_id: int, started_at: str) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(
                "UPDATE jobs SET status = 'running', started_at = ? WHERE id = ?",
                (started_at, job_id),
            )

    def update_job(
        self, job_id: int, *, progress: str | None = None, log: str | None = None
    ) -> None:
        sets, vals = [], []
        if progress is not None:
            sets.append("progress = ?")
            vals.append(progress)
        if log is not None:
            sets.append("log = ?")
            vals.append(log)
        if not sets:
            return
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", (*vals, job_id))

    def finish_job(
        self,
        job_id: int,
        status: str,
        finished_at: str,
        *,
        result: str | None = None,
        error: str | None = None,
        log: str | None = None,
        run_id: int | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute("BEGIN")
            self.conn.execute(
                """UPDATE jobs SET status = ?, finished_at = ?, result = ?, error = ?,
                                   log = coalesce(?, log), run_id = coalesce(?, run_id)
                   WHERE id = ?""",
                (status, finished_at, result, error, log, run_id, job_id),
            )

    def request_cancel(self, job_id: int, now: str) -> str | None:
        """Cancel a queued job outright or ask a running one to stop. Returns the new
        status, or None if the job does not exist or is already finished."""
        row = self.job(job_id)
        if row is None or row["status"] not in ("queued", "running"):
            return None
        new = "cancelled" if row["status"] == "queued" else "cancelling"
        with self.conn:
            self.conn.execute("BEGIN")
            if new == "cancelled":
                self.conn.execute(
                    "UPDATE jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
                    (now, job_id),
                )
            else:
                self.conn.execute("UPDATE jobs SET status = 'cancelling' WHERE id = ?", (job_id,))
        return new

    def job_status(self, job_id: int) -> str | None:
        row = self.conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return row["status"] if row else None

    def job(self, job_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def jobs(
        self, status: str | None = None, limit: int = 50, offset: int = 0
    ) -> tuple[list[sqlite3.Row], int]:
        where, params = "", ()
        if status:
            where, params = "WHERE status = ?", (status,)
        total = self.conn.execute(f"SELECT count(*) FROM jobs {where}", params).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT id, type, params, day, trigger, status, priority, not_before, attempt,
                       created_at, started_at, finished_at, progress, result, error, run_id
                FROM jobs {where} ORDER BY id DESC LIMIT ? OFFSET ?""",
            (*params, limit, offset),
        ).fetchall()
        return rows, total

    def mark_interrupted(self, now: str, before: str | None = None) -> int:
        """On boot: running/cancelling jobs became ``interrupted``; jobs queued before
        ``before`` (the boot time) are dropped. Jobs queued since boot are kept."""
        before = before or now
        with self.conn:
            self.conn.execute("BEGIN")
            n = self.conn.execute(
                """UPDATE jobs SET status = 'interrupted', finished_at = ?,
                                   error = 'process stopped while the job was running'
                   WHERE status IN ('running', 'cancelling') AND started_at < ?""",
                (now, before),
            ).rowcount
            self.conn.execute(
                """UPDATE jobs SET status = 'cancelled', finished_at = ?,
                                   error = 'dropped on restart'
                   WHERE status = 'queued' AND created_at < ?""",
                (now, before),
            )
        return n

    def run_jobs_for_day(self, day: str, kind: str | None = None) -> list[sqlite3.Row]:
        """Run jobs for a day; with ``kind``, only those of that kind (a job without a
        ``kind`` param is a full run)."""
        if kind is None:
            return self.conn.execute(
                "SELECT id, status, attempt FROM jobs WHERE type = 'run' AND day = ? ORDER BY id",
                (day,),
            ).fetchall()
        return self.conn.execute(
            """SELECT id, status, attempt FROM jobs
               WHERE type = 'run' AND day = ?
                 AND coalesce(json_extract(params, '$.kind'), 'full') = ?
               ORDER BY id""",
            (day, kind),
        ).fetchall()

    # -- API read queries -----------------------------------------------------

    def last_run(self) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()

    def run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()

    def latest_analysis_day(self) -> str | None:
        row = self.conn.execute("SELECT max(date) FROM correlations").fetchone()
        return row[0]

    def latest_rolling_as_of(self) -> str | None:
        return self.conn.execute("SELECT max(as_of) FROM rolling_correlations").fetchone()[0]

    def days_summary(self, start: str, end: str) -> list[sqlite3.Row]:
        """Per date in ``[start, end]`` that has any data: price bars, articles (by trading
        day), returns and correlation rows."""
        return self.conn.execute(
            """SELECT d.date,
                      (SELECT count(*) FROM prices p WHERE p.date = d.date) AS n_prices,
                      (SELECT count(*) FROM articles a WHERE a.trading_day = d.date) AS n_articles,
                      (SELECT count(*) FROM returns r WHERE r.date = d.date) AS n_returns,
                      (SELECT count(*) FROM correlations c WHERE c.date = d.date) AS n_correlations
               FROM (SELECT date FROM prices
                     UNION SELECT trading_day FROM articles WHERE trading_day IS NOT NULL
                     UNION SELECT date FROM correlations) d
               WHERE d.date >= ? AND d.date <= ? ORDER BY d.date""",
            (start, end),
        ).fetchall()

    def correlations_between(
        self, start: str, end: str, filters: dict[str, object] | None = None
    ) -> list[sqlite3.Row]:
        where = ["date >= ?", "date <= ?"]
        params: list[object] = [start, end]
        for key, value in (filters or {}).items():
            if value is not None:
                where.append(f"{key} = ?")
                params.append(value)
        return self.conn.execute(
            f"""SELECT date, window, return_type, lag, scope, source, method, model, n, estimate,
                       p_value, ci_low, ci_high
                FROM correlations WHERE {" AND ".join(where)}
                ORDER BY date, window, return_type, lag, source, method""",
            params,
        ).fetchall()

    def rolling_rows(
        self, as_of: str, filters: dict[str, object] | None = None
    ) -> list[sqlite3.Row]:
        where = ["as_of = ?"]
        params: list[object] = [as_of]
        for key, value in (filters or {}).items():
            if value is not None:
                where.append(f"{key} = ?")
                params.append(value)
        return self.conn.execute(
            f"""SELECT as_of, months, window, return_type, lag, source, scope, ticker, method, n,
                       estimate, p_value, ci_low, ci_high
                FROM rolling_correlations WHERE {" AND ".join(where)}
                ORDER BY months, window, return_type, lag, source, scope, ticker, method""",
            params,
        ).fetchall()

    def rolling_history(self, filters: dict[str, object]) -> list[sqlite3.Row]:
        where, params = [], []
        for key, value in filters.items():
            where.append(f"{key} = ?")
            params.append(value)
        return self.conn.execute(
            f"""SELECT as_of, n, estimate, p_value, ci_low, ci_high
                FROM rolling_correlations WHERE {" AND ".join(where)} ORDER BY as_of""",
            params,
        ).fetchall()

    def ticker_series(self, ticker: str, start: str, end: str) -> list[sqlite3.Row]:
        """Per day: the bar, the returns and the pooled window sentiment."""
        return self.conn.execute(
            """SELECT p.date, p.open, p.high, p.low, p.close, p.volume,
                      r.overnight, r.intraday, r.daily,
                      (SELECT mean_score FROM window_sentiment w WHERE w.date = p.date
                         AND w.ticker = p.ticker AND w.window = 'overnight' AND w.source = 'all')
                        AS overnight_sentiment,
                      (SELECT n_articles FROM window_sentiment w WHERE w.date = p.date
                         AND w.ticker = p.ticker AND w.window = 'overnight' AND w.source = 'all')
                        AS overnight_n,
                      (SELECT mean_score FROM window_sentiment w WHERE w.date = p.date
                         AND w.ticker = p.ticker AND w.window = 'intraday' AND w.source = 'all')
                        AS intraday_sentiment,
                      (SELECT n_articles FROM window_sentiment w WHERE w.date = p.date
                         AND w.ticker = p.ticker AND w.window = 'intraday' AND w.source = 'all')
                        AS intraday_n,
                      (SELECT mean_score FROM window_sentiment w WHERE w.date = p.date
                         AND w.ticker = p.ticker AND w.window = 'day' AND w.source = 'all')
                        AS day_sentiment,
                      (SELECT n_articles FROM window_sentiment w WHERE w.date = p.date
                         AND w.ticker = p.ticker AND w.window = 'day' AND w.source = 'all')
                        AS day_n
               FROM prices p
               LEFT JOIN returns r ON r.ticker = p.ticker AND r.date = p.date
               WHERE p.ticker = ? AND p.date >= ? AND p.date <= ?
               ORDER BY p.date""",
            (ticker, start, end),
        ).fetchall()

    def ticker_info(self, ticker: str) -> dict | None:
        months = self.conn.execute(
            """SELECT month, rank, name, market_cap, cik FROM universe_months
               WHERE ticker = ? ORDER BY month""",
            (ticker,),
        ).fetchall()
        if not months:
            return None
        counts = self.conn.execute(
            """SELECT (SELECT count(*) FROM article_tickers WHERE ticker = ?) AS n_articles,
                      (SELECT count(*) FROM prices WHERE ticker = ?) AS n_prices,
                      (SELECT min(date) FROM prices WHERE ticker = ?) AS first_price,
                      (SELECT max(date) FROM prices WHERE ticker = ?) AS last_price""",
            (ticker, ticker, ticker, ticker),
        ).fetchone()
        latest = months[-1]
        return {
            "ticker": ticker,
            "name": latest["name"],
            "cik": latest["cik"],
            "months": [dict(m) for m in months],
            "n_articles": counts["n_articles"],
            "n_prices": counts["n_prices"],
            "first_price": counts["first_price"],
            "last_price": counts["last_price"],
        }

    def tickers_coverage(self, month: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT u.rank, u.ticker, u.name, u.market_cap, u.cik,
                      (SELECT count(*) FROM article_tickers t JOIN articles a ON a.id = t.article_id
                        WHERE t.ticker = u.ticker AND a.trading_day LIKE ? || '%') AS n_articles,
                      (SELECT count(*) FROM prices p WHERE p.ticker = u.ticker
                        AND p.date LIKE ? || '%') AS n_prices
               FROM universe_months u WHERE u.month = ? ORDER BY u.rank""",
            (month, month, month),
        ).fetchall()

    def articles_query(
        self,
        *,
        ticker: str | None = None,
        trading_day: str | None = None,
        start: str | None = None,
        end: str | None = None,
        window: str | None = None,
        source: str | None = None,
        model: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[sqlite3.Row], int]:
        where, params = [], []
        if ticker:
            where.append("a.id IN (SELECT article_id FROM article_tickers WHERE ticker = ?)")
            params.append(ticker)
        if trading_day:
            where.append("a.trading_day = ?")
            params.append(trading_day)
        if start:
            where.append("a.trading_day >= ?")
            params.append(start)
        if end:
            where.append("a.trading_day <= ?")
            params.append(end)
        if window:
            where.append("a.window = ?")
            params.append(window)
        if source:
            where.append("a.id IN (SELECT article_id FROM article_sources WHERE source = ?)")
            params.append(source)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        total = self.conn.execute(f"SELECT count(*) FROM articles a {clause}", params).fetchone()[0]
        rows = self.conn.execute(
            f"""SELECT a.id, a.url, a.title, a.published_at, a.trading_day, a.window, a.provider,
                       a.first_seen_source,
                       (SELECT group_concat(ticker, ',') FROM article_tickers
                         WHERE article_id = a.id) AS tickers,
                       (SELECT group_concat(source, ',') FROM (
                          SELECT source FROM article_sources WHERE article_id = a.id
                          ORDER BY seen_at, rowid)) AS sources,
                       s.label, s.score, s.positive, s.negative, s.neutral
                FROM articles a
                LEFT JOIN sentiment s ON s.article_id = a.id AND s.model = ?
                {clause}
                ORDER BY a.published_at DESC, a.id DESC LIMIT ? OFFSET ?""",
            (model, *params, limit, offset),
        ).fetchall()
        return rows, total

    def source_stats(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT s.source,
                      count(*) AS n_articles,
                      count(DISTINCT a.trading_day) AS n_days,
                      min(a.trading_day) AS first_day, max(a.trading_day) AS last_day,
                      (SELECT count(DISTINCT t.ticker) FROM article_tickers t
                        WHERE t.article_id IN (SELECT article_id FROM article_sources
                                               WHERE source = s.source)) AS n_tickers,
                      (SELECT count(*) FROM article_sources o
                        WHERE o.source != s.source AND o.article_id IN
                          (SELECT article_id FROM article_sources WHERE source = s.source))
                        AS n_shared
               FROM article_sources s JOIN articles a ON a.id = s.article_id
               GROUP BY s.source ORDER BY s.source""",
        ).fetchall()

    def source_series(self, source: str, start: str, end: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            """SELECT a.trading_day AS date, count(*) AS n_articles,
                      count(DISTINCT t.ticker) AS n_tickers
               FROM article_sources s
               JOIN articles a ON a.id = s.article_id
               JOIN article_tickers t ON t.article_id = a.id
               WHERE s.source = ? AND a.trading_day >= ? AND a.trading_day <= ?
               GROUP BY a.trading_day ORDER BY a.trading_day""",
            (source, start, end),
        ).fetchall()

    # -- deletions (destructive jobs) -----------------------------------------

    def delete_analysis(self, date: str) -> dict[str, int]:
        with self.conn:
            self.conn.execute("BEGIN")
            out = {}
            for table, col in (
                ("returns", "date"),
                ("window_sentiment", "date"),
                ("correlations", "date"),
                ("rolling_correlations", "as_of"),
            ):
                out[table] = self.conn.execute(
                    f"DELETE FROM {table} WHERE {col} = ?", (date,)
                ).rowcount
            return out

    def delete_scores(self, model: str) -> dict[str, int]:
        with self.conn:
            self.conn.execute("BEGIN")
            return {
                "sentiment": self.conn.execute(
                    "DELETE FROM sentiment WHERE model = ?", (model,)
                ).rowcount,
                "window_sentiment": self.conn.execute(
                    "DELETE FROM window_sentiment WHERE model = ?", (model,)
                ).rowcount,
                "correlations": self.conn.execute(
                    "DELETE FROM correlations WHERE model = ?", (model,)
                ).rowcount,
            }

    def delete_universe_month(self, month: str) -> int:
        with self.conn:
            self.conn.execute("BEGIN")
            n = self.conn.execute("DELETE FROM universe_months WHERE month = ?", (month,)).rowcount
            self.conn.execute("DELETE FROM universe_membership WHERE date LIKE ? || '%'", (month,))
            return n

    def months_with_data(self) -> list[str]:
        return [
            r[0]
            for r in self.conn.execute(
                """SELECT DISTINCT substr(date, 1, 7) FROM prices
                   UNION SELECT DISTINCT substr(trading_day, 1, 7) FROM articles
                     WHERE trading_day IS NOT NULL
                   ORDER BY 1"""
            )
        ]
