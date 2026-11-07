"""Process log format. ``MURMUR_LOG_FORMAT=json`` writes one JSON object per line
(for a log collector reading container logs); anything else is the plain text
format. The API's in-memory log and job logs stay plain text either way."""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

ENV_LOG_FORMAT = "MURMUR_LOG_FORMAT"
TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "thread": record.threadName,
        }
        if record.exc_info:
            out["exception"] = self.formatException(record.exc_info)
        return json.dumps(out, ensure_ascii=False)


def configure(verbose: bool = False, env: dict | None = None) -> None:
    env = os.environ if env is None else env
    handler = logging.StreamHandler()
    if env.get(ENV_LOG_FORMAT, "").lower() == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter(TEXT_FORMAT, datefmt="%H:%M:%S"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, handlers=[handler])
