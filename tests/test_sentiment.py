import os
import subprocess
import sys

import pytest

from murmur.models import Article
from murmur.sentiment import score_pending


def add(store, n, ticker="AAPL"):
    ids = []
    for i in range(n):
        aid, _ = store.upsert_article(
            Article(f"https://e/{ticker}/{i}", f"{ticker} beats {i}", None, "yahoo"), ticker, "t"
        )
        ids.append(aid)
    return ids


def test_score_pending_is_resumable(store, scorer):
    add(store, 3)
    assert score_pending(store, scorer) == 3
    assert store.count("sentiment") == 3
    assert score_pending(store, scorer) == 0
    add(store, 2, "MSFT")
    assert score_pending(store, scorer) == 2
    assert store.count("sentiment") == 5
    row = store.conn.execute("SELECT * FROM sentiment LIMIT 1").fetchone()
    assert row["model"] == scorer.model_name
    assert row["label"] == "positive"
    assert row["score"] == pytest.approx(0.7)


def test_scorer_length_mismatch_is_an_error(store):
    class Broken:
        model_name = "broken"

        def score(self, titles):
            return []

    add(store, 1)
    with pytest.raises(RuntimeError):
        score_pending(store, Broken())


def test_cli_and_sources_do_not_import_torch():
    code = (
        "import sys, murmur.cli, murmur.pipeline, murmur.sentiment, "
        "murmur.sources.yahoo_news, murmur.sources.yfinance_prices, murmur.export; "
        "bad = {m for m in ('torch', 'transformers', 'matplotlib') if m in sys.modules}; "
        "sys.exit(f'imported {bad}' if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout


@pytest.mark.skipif(not os.environ.get("MURMUR_TEST_MODEL"), reason="set MURMUR_TEST_MODEL=1")
def test_real_finbert():
    from murmur.sentiment import FinBertScorer

    scorer = FinBertScorer()
    good, bad = scorer.score(["Apple posts record profit, shares surge", "Apple misses estimates"])
    assert good.label == "positive" and good.score > 0.3
    assert bad.label == "negative" and bad.score < -0.3
    assert abs(good.positive + good.negative + good.neutral - 1) < 1e-4
