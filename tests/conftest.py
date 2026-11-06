from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from murmur.models import SentimentScores
from murmur.store import Store

FIXTURES = Path(__file__).parent / "fixtures"

POSITIVE_WORDS = ("beats", "surges", "record", "soars", "upgrade")
NEGATIVE_WORDS = ("misses", "plunges", "lawsuit", "falls", "downgrade")


class FakeScorer:
    """Deterministic keyword scorer with the FinBERT output shape."""

    model_name = "fake/keywords"

    def __init__(self):
        self.calls: list[list[str]] = []

    def score(self, titles: list[str]) -> list[SentimentScores]:
        self.calls.append(list(titles))
        out = []
        for title in titles:
            low = title.lower()
            if any(w in low for w in POSITIVE_WORDS):
                out.append(SentimentScores(0.8, 0.1, 0.1, "positive"))
            elif any(w in low for w in NEGATIVE_WORDS):
                out.append(SentimentScores(0.1, 0.8, 0.1, "negative"))
            else:
                out.append(SentimentScores(0.2, 0.2, 0.6, "neutral"))
        return out


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never touch the network; any attempt fails loudly instead of slowly."""

    def refuse(self, *args, **kwargs):
        raise RuntimeError("network access attempted in a test")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)


@pytest.fixture
def store():
    with Store(":memory:") as s:
        yield s


@pytest.fixture
def scorer():
    return FakeScorer()


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text("utf-8"))
