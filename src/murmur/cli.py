"""``murmur`` command line."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence

from murmur import __version__
from murmur.clock import parse_date, today_in
from murmur.config import ConfigError, load_config
from murmur.store import Store

log = logging.getLogger("murmur")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="murmur",
        description="Daily news-sentiment vs stock-return pipeline.",
    )
    parser.add_argument("--version", action="version", version=f"murmur {__version__}")
    parser.add_argument("-c", "--config", metavar="PATH", help="TOML config file")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--date", metavar="YYYY-MM-DD", help="day to process (default: today)")
        return p

    add("run", "news -> prices -> sentiment -> analyze -> export for one day")
    add("news", "fetch and store headlines for the universe")
    add("prices", "fetch and store daily bars for the universe")
    add("analyze", "score pending headlines, then compute the day's sentiment and correlations")
    add("export", "write the day's published files")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("urllib3", "peewee", "yfinance", "transformers", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"murmur: {e}", file=sys.stderr)
        return 2
    try:
        day = parse_date(args.date) if args.date else today_in(config.timezone)
    except ValueError:
        print(f"murmur: invalid --date {args.date!r}", file=sys.stderr)
        return 2
    log.debug("config from %s, data_dir %s", config.source_path or "defaults", config.data_dir)

    from murmur import pipeline

    with Store(config.store_path) as store:
        if args.command == "run":
            results = pipeline.run_all(store, config, day)
            for r in results:
                status = "ok  " if r.ok else "FAIL"
                print(f"{status} {r.name:<9} {r.seconds:6.1f}s  {r.summary}")
            return 0 if all(r.ok for r in results) else 1
        if args.command == "news":
            print(pipeline.run_news(store, config, day))
        elif args.command == "prices":
            print(pipeline.run_prices(store, config, day))
        elif args.command == "analyze":
            print(pipeline.run_sentiment(store, config))
            print(pipeline.describe_day(pipeline.run_analyze(store, config, day)))
        elif args.command == "export":
            for path in pipeline.run_export(store, config, day):
                print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
