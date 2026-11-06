"""Headline sentiment. One finance-tuned model scores every headline regardless
of source so sources stay comparable.

This module imports only the standard library at import time; torch and
transformers are imported inside ``FinBertScorer`` so ``murmur news`` and
``murmur prices`` never load them.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol

from murmur.clock import utc_now_iso
from murmur.models import SentimentScores
from murmur.store import Store

log = logging.getLogger(__name__)

LABELS = {"positive", "negative", "neutral"}
COMMIT_EVERY = 256


class SentimentScorer(Protocol):
    model_name: str

    def score(self, titles: list[str]) -> list[SentimentScores]: ...


def resolve_device(requested: str, cuda_available: bool) -> str:
    """``cpu``/``cuda``/``auto`` -> the torch device string to use."""
    if requested == "auto":
        return "cuda" if cuda_available else "cpu"
    if requested == "cuda" and not cuda_available:
        raise RuntimeError(
            "sentiment.device is 'cuda' but torch reports no CUDA device "
            "(on Linux the lock file pins the CPU-only torch build)"
        )
    if requested not in ("cpu", "cuda"):
        raise ValueError(f"unknown device {requested!r}")
    return requested


class FinBertScorer:
    """``ProsusAI/finbert`` (or any 3-class positive/negative/neutral model)."""

    def __init__(
        self, model_name: str = "ProsusAI/finbert", batch_size: int = 32, device: str = "cpu"
    ):
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        from transformers.utils import logging as hf_logging

        hf_logging.disable_progress_bar()
        self.model_name = model_name
        self.batch_size = batch_size
        self._torch = torch
        self.device = resolve_device(device, torch.cuda.is_available())
        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_name)
        self.model.to(self.device)
        self.model.eval()
        log.info("sentiment: %s on %s", model_name, self.device)
        id2label = {int(i): str(name).lower() for i, name in self.model.config.id2label.items()}
        if set(id2label.values()) != LABELS:
            raise ValueError(f"{model_name} labels {id2label} are not {sorted(LABELS)}")
        self.labels = [id2label[i] for i in range(len(id2label))]

    def score(self, titles: list[str]) -> list[SentimentScores]:
        torch = self._torch
        out: list[SentimentScores] = []
        for start in range(0, len(titles), self.batch_size):
            batch = titles[start : start + self.batch_size]
            encoded = self.tokenizer(
                batch, padding=True, truncation=True, max_length=128, return_tensors="pt"
            ).to(self.device)
            with torch.inference_mode():
                logits = self.model(**encoded).logits
            probs = torch.softmax(logits, dim=-1).cpu().tolist()
            for row in probs:
                by_label = dict(zip(self.labels, row, strict=True))
                out.append(
                    SentimentScores(
                        positive=by_label["positive"],
                        negative=by_label["negative"],
                        neutral=by_label["neutral"],
                        label=max(by_label, key=by_label.get),
                    )
                )
        return out


def _chunks(items: list, size: int) -> Iterable[list]:
    for i in range(0, len(items), size):
        yield items[i : i + size]


def score_pending(store: Store, scorer: SentimentScorer) -> int:
    """Score every article that has no row for ``scorer.model_name``. Resumable."""
    pending = store.unscored_articles(scorer.model_name)
    if not pending:
        log.info("sentiment: nothing to score for %s", scorer.model_name)
        return 0
    log.info("sentiment: scoring %d headlines with %s", len(pending), scorer.model_name)
    from murmur import jobs as jobctl

    done = 0
    for chunk in _chunks(pending, COMMIT_EVERY):
        jobctl.report("sentiment", done, len(pending), scorer.model_name)
        scores = scorer.score([title for _, title in chunk])
        if len(scores) != len(chunk):
            raise RuntimeError("scorer returned a different number of results than inputs")
        store.insert_scores(
            scorer.model_name,
            zip((aid for aid, _ in chunk), scores, strict=True),
            utc_now_iso(),
        )
        done += len(chunk)
        log.debug("sentiment: %d/%d", done, len(pending))
    return done
