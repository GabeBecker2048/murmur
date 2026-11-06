"""``murmur`` command line."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Sequence

from murmur import __version__
from murmur.clock import parse_date, today_in
from murmur.config import ConfigError, load_config
from murmur.store import Store

log = logging.getLogger("murmur")

HEALTH_MAX_AGE = 5 * 60


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="murmur",
        description="Daily news-sentiment vs stock-return pipeline.",
    )
    parser.add_argument("--version", action="version", version=f"murmur {__version__}")
    parser.add_argument("-c", "--config", metavar="PATH", help="TOML config file")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name: str, help_text: str, dated: bool = True) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text)
        if dated:
            p.add_argument("--date", metavar="YYYY-MM-DD", help="day to process (default: today)")
        return p

    add("run", "universe -> news -> prices -> sentiment -> analyze -> export for one day")
    u = add("universe", "show (and if needed build) the universe in force on a day")
    u.add_argument("--rebuild", action="store_true", help="recompute the month even if cached")
    add("news", "fetch and store headlines for the universe")
    add("prices", "fetch and store daily bars for the universe")
    add("analyze", "score pending headlines, then compute the day's sentiment and correlations")
    e = add("export", "write the day's published files")
    e.add_argument("--all", action="store_true", help="rebuild the published tree for every day")
    add("schedule", "run the daily job forever (the container's main process)", dated=False)
    r = add("runs", "list recent runs", dated=False)
    r.add_argument("--limit", type=int, default=20)
    add("health", "exit 0 if the scheduler's heartbeat is fresh", dated=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in (
        "urllib3",
        "httpx",
        "httpx2",
        "huggingface_hub",
        "peewee",
        "yfinance",
        "transformers",
        "matplotlib",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        config = load_config(args.config)
    except ConfigError as e:
        print(f"murmur: {e}", file=sys.stderr)
        return 2
    log.debug("config from %s, data_dir %s", config.source_path or "defaults", config.data_dir)

    if args.command == "health":
        from murmur.scheduler import heartbeat_age_seconds

        age = heartbeat_age_seconds(config)
        fresh = age is not None and age < HEALTH_MAX_AGE
        print(f"heartbeat {'fresh' if fresh else 'stale or missing'} ({age and round(age)}s)")
        return 0 if fresh else 1
    if args.command == "schedule":
        from murmur.scheduler import serve

        return serve(config)

    try:
        day = parse_date(args.date) if getattr(args, "date", None) else today_in(config.timezone)
    except ValueError:
        print(f"murmur: invalid --date {args.date!r}", file=sys.stderr)
        return 2

    from murmur import pipeline

    with Store(config.store_path) as store:
        if args.command == "run":
            results = pipeline.run_day(store, config, day, "manual")
            print(pipeline.format_results(results))
            return 0 if all(r.ok for r in results) else 1
        if args.command == "universe":
            from murmur.universe import applicable_month, membership_for

            members, rebuilt = membership_for(store, config, day, rebuild=args.rebuild)
            state = "rebuilt" if rebuilt else "cached"
            print(f"{applicable_month(day)}: {len(members)} tickers ({state})")
            for m in members:
                cap = f"{m.market_cap / 1e9:10.1f}B" if m.market_cap else " " * 11
                print(f"{m.rank:4d} {m.ticker:<8}{cap}  {m.name}")
        elif args.command == "news":
            print(pipeline.run_news(store, config, day))
        elif args.command == "prices":
            print(pipeline.run_prices(store, config, day))
        elif args.command == "analyze":
            print(pipeline.run_sentiment(store, config))
            result = pipeline.run_analyze(store, config, day)
            print(pipeline.describe_analyze(result))
            for c in result.today.correlations:
                ci = f" [{c.ci_low:+.3f}, {c.ci_high:+.3f}]" if c.ci_low is not None else ""
                print(
                    f"  {c.window:<9} {c.return_type:<9} {c.method:<8} "
                    f"{c.estimate:+.3f}{ci}  p={c.p_value:.3f}  n={c.n}"
                )
        elif args.command == "export":
            paths = (
                pipeline.export_all(store, config)
                if args.all
                else pipeline.run_export(store, config, day)
            )
            for path in paths:
                print(path)
        elif args.command == "runs":
            for r in store.recent_runs(args.limit):
                stages = json.loads(r["stages"]) if r["stages"] else []
                failed = [s["name"] for s in stages if not s["ok"]]
                status = "ok" if r["ok"] else ("FAIL " + ",".join(failed) if r["ok"] == 0 else "…")
                print(
                    f"{r['id']:4d} {r['date']} {r['trigger']:<8} #{r['attempt']} "
                    f"{r['started_at']} -> {r['finished_at'] or '…'}  {status}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
