"""The murmur-backup service: copy each new store snapshot offsite (S3 or compatible).

The worker's ``backup`` job writes integrity-checked snapshots into
``<data_dir>/backups`` (``*.part`` while in progress). This process never opens the
store: it gzips the newest finished snapshot into its own staging directory, uploads
it with a SHA-256 in the object metadata, checks the size the bucket reports, and
records the result in ``<data_dir>/offsite/status.json``. So the container can mount
the data volume read-only except ``offsite/``, and it is the only process that holds
the S3 credentials (env only; deleting old copies is the bucket's lifecycle rule).

Env: ``MURMUR_S3_BUCKET`` (unset = not configured: the service idles, healthy),
``MURMUR_S3_REGION``, ``MURMUR_S3_ENDPOINT`` (for an S3-compatible provider),
``AWS_ACCESS_KEY_ID`` / ``AWS_SECRET_ACCESS_KEY`` (read by boto3 itself).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from murmur.clock import utc_now_iso
from murmur.config import Config, ConfigProvider

log = logging.getLogger(__name__)

ENV_BUCKET = "MURMUR_S3_BUCKET"
ENV_REGION = "MURMUR_S3_REGION"
ENV_ENDPOINT = "MURMUR_S3_ENDPOINT"
POLL_SECONDS = 60.0
HISTORY = 20
"""Uploaded snapshot names remembered in the status file."""


@dataclass(frozen=True)
class S3Settings:
    bucket: str
    region: str | None
    endpoint: str | None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> S3Settings | None:
        env = os.environ if env is None else env
        bucket = env.get(ENV_BUCKET, "").strip()
        if not bucket:
            return None
        return cls(bucket, env.get(ENV_REGION) or None, env.get(ENV_ENDPOINT) or None)


class Target(Protocol):
    def upload(self, path: Path, key: str, metadata: dict[str, str]) -> None: ...

    def size(self, key: str) -> int | None: ...

    def download(self, key: str, dest: Path) -> dict[str, str]:
        """Fetch ``key`` to ``dest``; return the object's metadata."""
        ...


class S3Target:
    """boto3, imported here only (the other services never load it)."""

    def __init__(self, settings: S3Settings, client=None):
        if client is None:
            import boto3

            client = boto3.client("s3", region_name=settings.region, endpoint_url=settings.endpoint)
        self.client = client
        self.bucket = settings.bucket

    def upload(self, path: Path, key: str, metadata: dict[str, str]) -> None:
        self.client.upload_file(
            str(path),
            self.bucket,
            key,
            ExtraArgs={"Metadata": metadata, "ContentType": "application/gzip"},
        )

    def download(self, key: str, dest: Path) -> dict[str, str]:
        self.client.download_file(self.bucket, key, str(dest))
        head = self.client.head_object(Bucket=self.bucket, Key=key)
        return {k.lower(): v for k, v in head.get("Metadata", {}).items()}

    def size(self, key: str) -> int | None:
        from botocore.exceptions import ClientError

        try:
            return int(self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"])
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise


# -- status file


def status_path(config: Config) -> Path:
    return config.offsite_dir / "status.json"


def heartbeat_path(config: Config) -> Path:
    return config.offsite_dir / "heartbeat"


def read_status(config: Config) -> dict:
    try:
        return json.loads(status_path(config).read_text())
    except (OSError, ValueError):
        return {}


def _write_status(config: Config, status: dict) -> None:
    path = status_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    part.write_text(json.dumps(status, indent=2, sort_keys=True))
    part.replace(path)


def last_success_age_hours(status: dict, now: datetime | None = None) -> float | None:
    when = status.get("last_success")
    if not when:
        return None
    then = datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return ((now or datetime.now(UTC)) - then).total_seconds() / 3600


# -- the work


def finished_snapshots(config: Config) -> list[Path]:
    """Snapshots the backup job has finished, oldest first (``*.part`` excluded)."""
    if not config.backups_dir.is_dir():
        return []
    return sorted(config.backups_dir.glob("murmur_*.sqlite"))


def _gzip_with_sha256(src: Path, dest: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    with src.open("rb") as fin, gzip.open(dest, "wb", compresslevel=6) as fout:
        while chunk := fin.read(1 << 20):
            digest.update(chunk)
            fout.write(chunk)
    return digest.hexdigest(), src.stat().st_size


def upload_pending(config: Config, target: Target, settings: S3Settings | None = None) -> dict:
    """Upload the newest finished snapshot if it has not been uploaded yet. Older
    snapshots that were never uploaded are skipped: the newest supersedes them.
    Returns the status written."""
    status = read_status(config)
    status.update(
        configured=True,
        bucket=settings.bucket if settings else status.get("bucket"),
        prefix=config.backup.prefix,
        last_check=utc_now_iso(),
    )
    snapshots = finished_snapshots(config)
    uploaded = list(status.get("uploaded", []))
    if not snapshots or snapshots[-1].name in uploaded:
        _write_status(config, status)
        return status
    snap = snapshots[-1]
    key = f"{config.backup.prefix}/store/{snap.name}.gz"
    staging = config.offsite_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    gz = staging / f"{snap.name}.gz"
    status["last_attempt"] = utc_now_iso()
    try:
        sha, raw_bytes = _gzip_with_sha256(snap, gz)
        target.upload(gz, key, {"sha256": sha, "raw-bytes": str(raw_bytes), "source": snap.name})
        remote = target.size(key)
        if remote != gz.stat().st_size:
            raise RuntimeError(f"uploaded size {remote} != local {gz.stat().st_size}")
        status.update(
            last_success=utc_now_iso(),
            last_error=None,
            last_key=key,
            last_snapshot=snap.name,
            last_bytes=gz.stat().st_size,
            last_raw_bytes=raw_bytes,
            last_sha256=sha,
            uploaded=(uploaded + [snap.name])[-HISTORY:],
        )
        log.info("offsite: uploaded %s (%.1f MB gzipped)", key, gz.stat().st_size / 1e6)
        settings_file = config.settings_path
        if settings_file.is_file():
            target.upload(settings_file, f"{config.backup.prefix}/settings.toml", {})
    except Exception as e:  # noqa: BLE001 - recorded, retried on the next poll
        status["last_error"] = f"{type(e).__name__}: {e}"
        log.exception("offsite: upload of %s failed", snap.name)
    finally:
        gz.unlink(missing_ok=True)
    _write_status(config, status)
    return status


VERIFY_EVERY_DAYS = 7
VERIFY_STALE_DAYS = 8
"""The health report warns when a configured offsite copy has not been restored and
checked for this long."""


def verify_latest(config: Config, target: Target) -> dict:
    """The restore drill: download the newest uploaded snapshot, check its SHA-256
    against the one recorded at upload, gunzip it, run SQLite's integrity check and open
    it read-only. The result is recorded in the status file; the copy is deleted."""
    import sqlite3

    status = read_status(config)
    key = status.get("last_key")
    status["last_verify"] = utc_now_iso()
    staging = config.offsite_dir / "staging"
    staging.mkdir(parents=True, exist_ok=True)
    # Per-process names: a manual `murmur offsite --verify` may run while the service
    # does its own (2026-11-24: two drills sharing one file broke each other).
    tag = f"verify-{os.getpid()}"
    gz, raw = staging / f"{tag}.sqlite.gz", staging / f"{tag}.sqlite"
    try:
        if not key:
            raise RuntimeError("nothing has been uploaded yet")
        meta = target.download(key, gz)
        digest = hashlib.sha256()
        with gzip.open(gz, "rb") as fin, raw.open("wb") as fout:
            while chunk := fin.read(1 << 20):
                digest.update(chunk)
                fout.write(chunk)
        expected = meta.get("sha256")
        if expected and digest.hexdigest() != expected:
            raise RuntimeError(f"sha256 mismatch: {digest.hexdigest()} != {expected}")
        con = sqlite3.connect(f"file:{raw}?mode=ro", uri=True)
        try:
            check = con.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"integrity_check: {check}")
            schema = con.execute("PRAGMA user_version").fetchone()[0]
            articles = con.execute("SELECT count(*) FROM articles").fetchone()[0]
        finally:
            con.close()
        status.update(
            last_verify_ok=True,
            last_verify_error=None,
            last_verify_key=key,
            last_verify_schema=schema,
            last_verify_articles=articles,
            last_verify_success=status["last_verify"],
        )
        log.info("offsite: verified %s (schema %s, %d articles)", key, schema, articles)
    except Exception as e:  # noqa: BLE001 - recorded and reported by the health report
        status.update(last_verify_ok=False, last_verify_error=f"{type(e).__name__}: {e}")
        log.exception("offsite: verify of %s failed", key)
    finally:
        for leftover in (
            gz,
            raw,
            raw.with_name(raw.name + "-wal"),
            raw.with_name(raw.name + "-shm"),
        ):
            leftover.unlink(missing_ok=True)
    _write_status(config, status)
    return status


def verify_due(status: dict, now: datetime | None = None) -> bool:
    if not status.get("last_key"):
        return False
    when = status.get("last_verify")
    if not when:
        return True
    then = datetime.strptime(when, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - then >= timedelta(days=VERIFY_EVERY_DAYS)


def offsite_main(
    provider: ConfigProvider,
    once: bool = False,
    poll_seconds: float = POLL_SECONDS,
    target: Target | None = None,
    env: Mapping[str, str] | None = None,
    verify_only: bool = False,
) -> int:
    """The ``murmur offsite`` process (the murmur-backup service). It also runs the
    restore drill (``verify_latest``) once a week; ``verify_only`` runs it once now."""
    import signal

    stop = threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
    settings = S3Settings.from_env(env)
    config = provider.get()
    config.offsite_dir.mkdir(parents=True, exist_ok=True)
    if settings is None and target is None:
        log.warning("offsite: %s is not set; idling (nothing is uploaded)", ENV_BUCKET)
        status = read_status(config)
        status.update(configured=False, last_check=utc_now_iso())
        _write_status(config, status)
    elif target is None:
        target = S3Target(settings)
        log.info("offsite: s3://%s/%s/", settings.bucket, config.backup.prefix)
    while not stop.is_set():
        config = provider.get()
        from murmur.jobs import write_heartbeat

        write_heartbeat(heartbeat_path(config))
        if target is not None:
            if verify_only:
                return 0 if verify_latest(config, target).get("last_verify_ok") else 1
            status = upload_pending(config, target, settings)
            if verify_due(status):
                verify_latest(config, target)
        elif verify_only:
            log.error("offsite: --verify needs %s", ENV_BUCKET)
            return 1
        if once:
            break
        stop.wait(poll_seconds)
    return 0
