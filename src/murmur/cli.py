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

    r = add("run", "universe -> news -> prices -> sentiment -> analyze -> export for one day")
    r.add_argument(
        "--kind",
        choices=("full", "collect", "analyze"),
        default="full",
        help="full (default), collect (news only) or analyze (prices + analysis)",
    )
    u = add("universe", "show (and if needed build) the universe in force on a day")
    u.add_argument("--rebuild", action="store_true", help="recompute the month even if cached")
    n = add("news", "fetch and store headlines for the universe")
    n.add_argument("--from", dest="start", metavar="YYYY-MM-DD", help="backfill from this date")
    n.add_argument("--to", dest="end", metavar="YYYY-MM-DD", help="backfill to (default: --date)")
    pr = add("prices", "fetch and store daily bars for the universe")
    pr.add_argument("--from", dest="start", metavar="YYYY-MM-DD", help="backfill from this date")
    pr.add_argument("--to", dest="end", metavar="YYYY-MM-DD", help="backfill to (default: --date)")
    a = add("analyze", "score pending headlines, then compute the day's sentiment and correlations")
    a.add_argument(
        "--all",
        action="store_true",
        help="recompute every day with returns (after a schema change)",
    )
    f = add("findings", "compute a findings snapshot (the conclusion over a window)")
    f.add_argument("--id", help="snapshot id, e.g. 2026-10 (prose: findings_text/<id>.toml)")
    f.add_argument("--from", dest="start", metavar="YYYY-MM-DD", help="first sentiment session")
    f.add_argument("--to", dest="end", metavar="YYYY-MM-DD", help="last sentiment session")
    f.add_argument(
        "--freeze", action="store_true", help="freeze it: published, never replaced again"
    )
    f.add_argument("--live", action="store_true", help="recompute the live 12-month row")
    f.add_argument("--list", action="store_true", help="list the stored snapshots")
    f.add_argument("--source", default="all", help="one news source (e.g. finnhub); default all")
    f.add_argument("--delete", metavar="ID", help="delete a draft (never a frozen snapshot)")
    e = add("export", "write the day's published files")
    e.add_argument("--all", action="store_true", help="rebuild the published tree for every day")
    sv = add(
        "serve", "the HTTP API + UI; by default also the scheduler and the job worker", dated=False
    )
    sv.add_argument(
        "--no-worker", action="store_true", help="API only: jobs run in `murmur worker`"
    )
    sv.add_argument("--no-scheduler", action="store_true", help="no scheduler tick in this process")
    w = add("worker", "the job worker process (exactly one per store)", dated=False)
    w.add_argument("--once", action="store_true", help="drain the queue, then exit")
    sch = add("scheduler", "the scheduler process: queues the daily run(s)", dated=False)
    sch.add_argument("--once", action="store_true", help="one tick, then exit")
    off = add("offsite", "the backup service: upload new store snapshots to S3", dated=False)
    off.add_argument("--once", action="store_true", help="one pass, then exit")
    off.add_argument(
        "--verify",
        action="store_true",
        help="restore drill: download the newest copy, check it, exit 0/1",
    )
    add("schedule", "(legacy) the pre-0.6.0 scheduler loop, no HTTP, no jobs", dated=False)
    r = add("runs", "list recent runs", dated=False)
    r.add_argument("--limit", type=int, default=20)
    h = add("health", "exit 0 if the running service reports healthy", dated=False)
    h.add_argument(
        "--role",
        choices=("api", "scheduler", "worker", "backup"),
        default="api",
        help="api = GET /api/v1/health; the others = that process's heartbeat file age",
    )
    h.add_argument("--file", action="store_true", help="alias for --role scheduler")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    from murmur.logs import configure as configure_logging

    configure_logging(args.verbose)
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
        role = "scheduler" if args.file else args.role
        if role != "api":
            from murmur.offsite import heartbeat_path as offsite_heartbeat
            from murmur.scheduler import heartbeat_age_seconds

            path = {
                "scheduler": config.heartbeat_path,
                "worker": config.worker_heartbeat_path,
                "backup": offsite_heartbeat(config),
            }[role]
            age = heartbeat_age_seconds(config, path)
            fresh = age is not None and age < HEALTH_MAX_AGE
            if age is None:
                print(f"{role} heartbeat missing ({path})")
            else:
                print(f"{role} heartbeat {'fresh' if fresh else 'stale'} ({round(age)}s)")
            return 0 if fresh else 1
        return health_http(config)
    if args.command == "serve":
        return serve_http(args.config, worker=not args.no_worker, scheduler=not args.no_scheduler)
    if args.command == "worker":
        from murmur.config import ConfigProvider
        from murmur.jobs import worker_main

        return worker_main(ConfigProvider(args.config), once=args.once)
    if args.command == "scheduler":
        from murmur.config import ConfigProvider
        from murmur.jobs import scheduler_main

        return scheduler_main(ConfigProvider(args.config), once=args.once)
    if args.command == "offsite":
        from murmur.config import ConfigProvider
        from murmur.offsite import offsite_main

        return offsite_main(
            ConfigProvider(args.config), once=args.once or args.verify, verify_only=args.verify
        )
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
            results = pipeline.run_day(store, config, day, "manual", kind=args.kind)
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
            if args.start:
                start, end = parse_date(args.start), parse_date(args.end) if args.end else day
                print(pipeline.backfill_news(store, config, start, end))
            else:
                print(pipeline.run_news(store, config, day))
        elif args.command == "prices":
            if args.start:
                start, end = parse_date(args.start), parse_date(args.end) if args.end else day
                print(pipeline.backfill_prices(store, config, start, end))
            else:
                print(pipeline.run_prices(store, config, day))
        elif args.command == "analyze":
            print(pipeline.run_sentiment(store, config))
            if args.all:
                days = pipeline.analyze_all(store, config)
                print(
                    f"re-analysed {len(days)} days"
                    + (f" ({days[0]} to {days[-1]})" if days else "")
                )
                return 0
            result = pipeline.run_analyze(store, config, day)
            print(pipeline.describe_analyze(result))
            for c in sorted(
                result.today.correlations, key=lambda c: (c.window, c.return_type, c.lag, c.method)
            ):
                ci = f" [{c.ci_low:+.3f}, {c.ci_high:+.3f}]" if c.ci_low is not None else ""
                print(
                    f"  {c.window:<9} {c.return_type:<9} lag {c.lag:+d} {c.method:<8} "
                    f"{c.estimate:+.3f}{ci}  p={c.p_value:.3f}  n={c.n}"
                )
        elif args.command == "findings":
            return _findings_command(store, config, args, day)
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


def _findings_command(store, config, args, day) -> int:
    from murmur import findings

    if args.delete:
        try:
            findings.delete_draft(store, args.delete)
        except findings.FindingsError as e:
            print(f"murmur: {e}", file=sys.stderr)
            return 1
        print(f"deleted draft {args.delete}")
        return 0
    if args.list:
        for r in store.findings_list():
            span = f"{r['start_day']} .. {r['end_day']}"
            print(f"{r['id']:<12} {r['kind']:<7} {span}  {r['created_at']}")
        return 0
    try:
        if args.live:
            payload = findings.refresh_live(store, config, day)
        elif not (args.id and args.start and args.end):
            print("murmur: findings needs --id, --from, --to (or --live/--list)", file=sys.stderr)
            return 2
        else:
            payload = findings.create(
                store,
                config,
                args.id,
                parse_date(args.start),
                parse_date(args.end),
                freeze=args.freeze,
                source=args.source,
            )
    except findings.FindingsError as e:
        print(f"murmur: {e}", file=sys.stderr)
        return 1
    if payload is None:
        print("no data for live findings")
        return 1
    for lag, row in payload["headline"]["lags"].items():
        if row:
            print(f"day news vs daily return, lag {int(lag):+d}: r={row['r']:+.3f} n={row['n']}")
    print(f"stored {payload['id']} ({payload['kind']})")
    return 0


def health_http(config) -> int:
    """GET /api/v1/health on the configured port; the Docker HEALTHCHECK."""
    import json as _json
    import urllib.error
    import urllib.request

    host = "127.0.0.1" if config.api.host in ("0.0.0.0", "") else config.api.host
    url = f"http://{host}:{config.api.port}/api/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:  # noqa: S310
            body = _json.loads(resp.read())
        print(f"healthy: murmur {body.get('version')}")
        return 0
    except urllib.error.HTTPError as e:
        print(f"unhealthy: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:200]}")
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(f"unreachable: {e}")
        return 1


def serve_http(config_path: str | None, worker: bool = True, scheduler: bool = True) -> int:
    import uvicorn

    from murmur.api.app import create_app
    from murmur.config import ConfigProvider

    provider = ConfigProvider(config_path)
    config = provider.get()
    app = create_app(provider, run_worker=worker, run_scheduler=scheduler and worker)
    uvicorn.run(
        app,
        host=config.api.host,
        port=config.api.port,
        log_level="info",
        access_log=False,
        proxy_headers=True,
        forwarded_allow_ips=list(config.api.trusted_proxies),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
