"""Bundles: any date range of the published datasets as one ``.tar.gz``.

A bundle holds one file per table, with the rows of the requested range gathered from
whichever day and month files cover it, plus ``README.txt`` (what it is, the range,
every license notice), ``index.json`` (the manifest entries of the datasets in it) and
``SHA256SUMS``. It is streamed: each member is staged in a spooled temporary file under
``<data_dir>/tmp`` (never the container's small tmpfs) and the gzip stream is yielded as
it is written, so memory stays flat whatever the range.

The same code writes the ready-made monthly archives (``archives/murmur-YYYYMM.tar.gz``)
when a finished month is compacted, and serves ``/data/{dataset}/latest.{table}.parquet``.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import tarfile
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from murmur.config import Config

log = logging.getLogger(__name__)

FORMATS = ("parquet", "csv")
DATE_COLUMNS = ("date", "trading_day", "as_of")
"""The row date of a daily table: the first of these it has."""
CSV_MAX_DAYS = {"news": 93}
"""CSV is large for the article tables; longer ranges must use Parquet."""
SPOOL_BYTES = 8 * 1024 * 1024
BATCH_ROWS = 20_000
ARCHIVES = "archives"


class BundleError(ValueError):
    pass


@dataclass
class Member:
    dataset: str
    table: str
    cadence: str
    files: list[dict]
    key: tuple[str, ...]
    license: dict


@dataclass
class Plan:
    start: date
    end: date
    fmt: str
    members: list[Member] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"murmur_{self.start.isoformat()}_{self.end.isoformat()}"

    @property
    def estimated_bytes(self) -> int:
        """Bytes of the source files touched (an upper bound for a Parquet bundle)."""
        return sum(f["bytes"] for m in self.members for f in m.files)

    def describe(self) -> dict:
        return {
            "name": f"{self.name}.tar.gz",
            "from": self.start.isoformat(),
            "to": self.end.isoformat(),
            "format": self.fmt,
            "estimated_bytes": self.estimated_bytes,
            "tables": [
                {"dataset": m.dataset, "table": m.table, "files": len(m.files)}
                for m in self.members
            ],
        }


def load_manifest(config: Config) -> dict:
    path = config.published_dir / "index.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        from murmur.export import manifest

        return manifest(config.published_dir, config)


def _overlaps(file_date: str, start: date, end: date) -> bool:
    if len(file_date) == 7:  # a month file
        return start.isoformat()[:7] <= file_date <= end.isoformat()[:7]
    return start.isoformat() <= file_date <= end.isoformat()


def plan(
    config: Config,
    start: date,
    end: date,
    *,
    datasets: list[str] | None = None,
    tables: list[str] | None = None,
    fmt: str = "parquet",
    include_private: bool = False,
    manifest: dict | None = None,
) -> Plan:
    """Which files feed which member. ``datasets``/``tables`` empty = all of them; a
    table may be named ``table`` or ``dataset/table``."""
    from murmur.export import datasets as registry
    from murmur.export import table_cadence

    if fmt not in FORMATS:
        raise BundleError(f"format must be one of {', '.join(FORMATS)}")
    if end < start:
        raise BundleError("to must not be before from")
    manifest = manifest if manifest is not None else load_manifest(config)
    entries = manifest.get("datasets", {})
    known = {ds.name: ds for ds in registry(config)}
    wanted = datasets or list(known)
    unknown = [d for d in wanted if d not in known]
    if unknown:
        raise BundleError(f"unknown dataset(s): {', '.join(unknown)}")
    private = [d for d in wanted if d in config.datasets.private]
    if private and not include_private:
        if datasets:
            raise BundleError(f"private dataset(s): {', '.join(private)}")
        wanted = [d for d in wanted if d not in private]
    if fmt == "csv":
        days = (end - start).days + 1
        for ds, limit in CSV_MAX_DAYS.items():
            if ds in wanted and days > limit:
                raise BundleError(f"CSV of {ds} is limited to {limit} days; use parquet")
    out = Plan(start, end, fmt)
    for name in wanted:
        ds = known[name]
        files = [
            f
            for f in entries.get(name, {}).get("files", [])
            if f["format"] == "parquet" and _overlaps(f["date"], start, end)
        ]
        for t in ds.tables:
            if tables and t.name not in tables and f"{name}/{t.name}" not in tables:
                continue
            mine = sorted((f for f in files if f["table"] == t.name), key=lambda f: f["date"])
            cadence = table_cadence(ds, t)
            if t.name == "companies" and mine:
                mine = mine[-1:]  # a cumulative snapshot: the latest one in range says it all
            if mine:
                out.members.append(Member(name, t.name, cadence, mine, t.key, ds.license))
    if not out.members:
        raise BundleError("nothing published in that range")
    return out


def _date_column(names: list[str], cadence: str) -> str | None:
    cols = DATE_COLUMNS if cadence == "daily" else ("month", "as_of")
    return next((c for c in cols if c in names), None)


def _filtered(member: Member, base: Path, start: date, end: date) -> Iterator:
    """Each source file's rows inside the range, as pyarrow tables of at most
    ``BATCH_ROWS`` rows, so a month file is never held in memory whole."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    lo, hi = start.isoformat(), end.isoformat()
    for f in member.files:
        source = pq.ParquetFile(base / f["path"])
        col = _date_column(source.schema_arrow.names, member.cadence)
        for batch in source.iter_batches(batch_size=BATCH_ROWS):
            table = pa.Table.from_batches([batch])
            if col is not None and member.table != "companies":
                values = pc.cast(table[col], "string")
                if col == "month":
                    mask = pc.and_(pc.greater_equal(values, lo[:7]), pc.less_equal(values, hi[:7]))
                else:
                    prefix = pc.utf8_slice_codeunits(values, 0, 10)
                    mask = pc.and_(pc.greater_equal(prefix, lo), pc.less_equal(prefix, hi))
                table = table.filter(mask)
            if table.num_rows:
                yield table


def _unified_schema(member: Member, base: Path):
    import pyarrow as pa
    import pyarrow.parquet as pq

    schemas = [pq.read_schema(base / f["path"]) for f in member.files]
    return pa.unify_schemas([s.remove_metadata() for s in schemas], promote_options="permissive")


def write_member(member: Member, base: Path, start: date, end: date, fmt: str, out) -> int:
    """Write one table's rows for the range to the binary file ``out``; returns rows."""
    import pyarrow.csv as pcsv
    import pyarrow.parquet as pq

    from murmur.publish import file_metadata

    schema = _unified_schema(member, base)
    meta = file_metadata(
        member.dataset,
        member.table,
        member.license,
        {"murmur_period": f"{start.isoformat()}..{end.isoformat()}"},
    )
    schema = schema.with_metadata({**(schema.metadata or {}), **meta})
    rows = 0
    writer = (
        pq.ParquetWriter(out, schema, compression="zstd", write_batch_size=BATCH_ROWS)
        if fmt == "parquet"
        else pcsv.CSVWriter(out, schema.remove_metadata())
    )
    try:
        for table in _filtered(member, base, start, end):
            table = _pad(table, schema)
            table = table.cast(schema if fmt == "parquet" else schema.remove_metadata())
            writer.write_table(table)
            rows += table.num_rows
    finally:
        writer.close()
    return rows


def _pad(table, schema):
    import pyarrow as pa

    cols = [
        table[name]
        if name in table.column_names
        else pa.nulls(table.num_rows, schema.field(name).type)
        for name in schema.names
    ]
    return pa.table(cols, names=schema.names)


def _readme(p: Plan, counts: dict[str, int]) -> str:
    from murmur import __version__
    from murmur.publish import DATA_VERSION

    lines = [
        "Murmur data bundle",
        "==================",
        "",
        "News-headline sentiment (FinBERT) for the largest US companies, their daily",
        "returns, and how the two correlate. https://github.com/GabeBecker2048/murmur",
        "",
        f"Range:        {p.start.isoformat()} to {p.end.isoformat()} (America/New_York)",
        f"Format:       {p.fmt}",
        f"Data version: {DATA_VERSION} (murmur {__version__})",
        f"Built:        {time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}",
        "",
        "Files (rows):",
    ]
    lines += [f"  {path}  ({n:,} rows)" for path, n in counts.items()]
    lines += [
        "",
        "Monthly tables cover every month that overlaps the range; companies is the",
        "cumulative list as of the last month in range. Columns are described in",
        "docs/data-dictionary.md of the repository; index.json lists keys and licenses.",
        "",
        "Licenses",
        "--------",
    ]
    seen = set()
    for m in p.members:
        lic = m.license
        if (m.dataset, lic["name"]) in seen:
            continue
        seen.add((m.dataset, lic["name"]))
        lines += [
            f"{m.dataset}: {lic['name']} ({lic.get('url', '')})",
            f"  Attribution: {lic.get('attribution', '')}",
            f"  {lic.get('note', '')}",
            f"  Contact: {lic.get('contact', '')}",
            "",
        ]
    return "\n".join(lines) + "\n"


class _Sink(io.RawIOBase):
    """A write-only file whose bytes are collected and drained by the generator."""

    def __init__(self):
        self.chunks: list[bytes] = []

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        self.chunks.append(bytes(b))
        return len(b)

    def drain(self) -> Iterator[bytes]:
        chunks, self.chunks = self.chunks, []
        yield from chunks


def _add(tar: tarfile.TarFile, name: str, fileobj, size: int) -> None:
    info = tarfile.TarInfo(name)
    info.size = size
    info.mtime = int(time.time())
    info.mode = 0o644
    fileobj.seek(0)
    tar.addfile(info, fileobj)


def stream(config: Config, p: Plan, manifest: dict | None = None) -> Iterator[bytes]:
    """The bundle as gzip chunks."""
    base = config.published_dir
    tmp = config.data_dir / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    manifest = manifest if manifest is not None else load_manifest(config)
    sink = _Sink()
    tar = tarfile.open(fileobj=sink, mode="w|gz")  # noqa: SIM115 (closed below)
    sums: list[str] = []
    counts: dict[str, int] = {}
    try:
        for m in p.members:
            arcname = f"{m.dataset}/{m.table}.{p.fmt}"
            with tempfile.SpooledTemporaryFile(max_size=SPOOL_BYTES, dir=tmp) as staged:
                rows = write_member(m, base, p.start, p.end, p.fmt, staged)
                size = staged.tell()
                staged.seek(0)
                digest = hashlib.sha256()
                for chunk in iter(lambda: staged.read(1 << 20), b""):  # noqa: B023
                    digest.update(chunk)
                sums.append(f"{digest.hexdigest()}  {arcname}")
                counts[arcname] = rows
                _add(tar, f"{p.name}/{arcname}", staged, size)
            yield from sink.drain()
        subset = {
            "data_version": manifest.get("data_version"),
            "range": {"from": p.start.isoformat(), "to": p.end.isoformat()},
            "datasets": {
                name: {k: v for k, v in spec.items() if k != "files"}
                for name, spec in manifest.get("datasets", {}).items()
                if name in {m.dataset for m in p.members}
            },
        }
        for name, body in (
            ("README.txt", _readme(p, counts)),
            ("index.json", json.dumps(subset, indent=1) + "\n"),
            ("SHA256SUMS", "\n".join(sums) + "\n"),
        ):
            data = body.encode("utf-8")
            _add(tar, f"{p.name}/{name}", io.BytesIO(data), len(data))
        tar.close()
        yield from sink.drain()
    finally:
        if not tar.closed:
            tar.close()


def write_archive(config: Config, p: Plan, path: Path, manifest: dict | None = None) -> Path:
    """Write a bundle to ``path`` atomically (the ready-made monthly archives)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(path.name + ".part")
    with part.open("wb") as out:
        for chunk in stream(config, p, manifest):
            out.write(chunk)
    part.replace(path)
    return path


def month_archive(config: Config, month: str, manifest: dict | None = None) -> Path | None:
    """``archives/murmur-YYYYMM.tar.gz``: every public dataset of a finished month."""
    import calendar

    year, mon = int(month[:4]), int(month[5:7])
    first, last = date(year, mon, 1), date(year, mon, calendar.monthrange(year, mon)[1])
    try:
        p = plan(config, first, last, manifest=manifest)
    except BundleError:
        return None
    path = config.published_dir / ARCHIVES / f"murmur-{month.replace('-', '')}.tar.gz"
    write_archive(config, p, path, manifest)
    log.info("archive %s written", path.name)
    return path


def archives_manifest(base: Path) -> list[dict]:
    root = base / ARCHIVES
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.glob("murmur-*.tar.gz")):
        stamp = path.name[len("murmur-") : -len(".tar.gz")]
        out.append(
            {
                "month": f"{stamp[:4]}-{stamp[4:]}",
                "path": path.relative_to(base).as_posix(),
                "bytes": path.stat().st_size,
            }
        )
    return out


def latest(config: Config, dataset: str, table: str, manifest: dict | None = None):
    """The newest rows of a table: ``(pyarrow table, dated path)`` or None. A daily
    table is cut to its newest date, so ``latest`` is always one session (or month)."""
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    from murmur.export import datasets as registry
    from murmur.export import table_cadence

    ds = next((d for d in registry(config) if d.name == dataset), None)
    t = next((x for x in ds.tables if x.name == table), None) if ds else None
    if t is None:
        return None
    manifest = manifest if manifest is not None else load_manifest(config)
    files = [
        f
        for f in manifest.get("datasets", {}).get(dataset, {}).get("files", [])
        if f["table"] == table and f["format"] == "parquet"
    ]
    if not files:
        return None
    newest = max(files, key=lambda f: (f["date"], len(f["date"])))
    data = pq.read_table(config.published_dir / newest["path"])
    col = _date_column(data.column_names, table_cadence(ds, t))
    if table_cadence(ds, t) == "daily" and col and data.num_rows:
        values = pc.utf8_slice_codeunits(pc.cast(data[col], "string"), 0, 10)
        data = data.filter(pc.equal(values, pc.max(values)))
    return data, newest["path"]
