"""The murmur-backup service: snapshots go offsite exactly once, failures are
recorded and retried, and nothing here opens the store."""

import gzip
import json
from unittest import mock

import pytest

from murmur import offsite
from murmur.config import ConfigProvider, load_config


class FakeTarget:
    def __init__(self, fail=False, wrong_size=False):
        self.objects: dict[str, bytes] = {}
        self.metadata: dict[str, dict] = {}
        self.fail = fail
        self.wrong_size = wrong_size

    def upload(self, path, key, metadata):
        if self.fail:
            raise ConnectionError("bucket unreachable")
        self.objects[key] = path.read_bytes()
        self.metadata[key] = metadata

    def size(self, key):
        if key not in self.objects:
            return None
        return len(self.objects[key]) + (1 if self.wrong_size else 0)

    def download(self, key, dest):
        dest.write_bytes(self.objects[key])
        return self.metadata[key]


@pytest.fixture
def cfg(tmp_path):
    user = tmp_path / "murmur.toml"
    user.write_text(
        f'data_dir = "{tmp_path / "data"}"\n'
        '[universe]\nsource = "static"\n[universe.static]\nAAPL = "Apple"\n'
        '[backup]\nprefix = "test/murmur"\n'
    )
    return load_config(user, env={})


def snapshot(cfg, name, body=b"SQLite format 3\x00" + b"x" * 1000):
    cfg.backups_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.backups_dir / name
    path.write_bytes(body)
    return path


def test_uploads_the_newest_finished_snapshot_once(cfg):
    snapshot(cfg, "murmur_2026-11-06_043000.sqlite", b"old")
    snap = snapshot(cfg, "murmur_2026-11-07_043000.sqlite")
    snapshot(cfg, "murmur_2026-11-08_043000.sqlite.part", b"half")  # in progress: ignored
    cfg.settings_path.write_text("[schedule]\npaused = false\n")
    target = FakeTarget()
    status = offsite.upload_pending(cfg, target, offsite.S3Settings("b", None, None))
    key = "test/murmur/store/murmur_2026-11-07_043000.sqlite.gz"
    assert set(target.objects) == {key, "test/murmur/settings.toml"}
    assert gzip.decompress(target.objects[key]) == snap.read_bytes()
    assert target.metadata[key]["source"] == snap.name and len(target.metadata[key]["sha256"]) == 64
    assert status["last_key"] == key and status["last_error"] is None and status["bucket"] == "b"
    assert json.loads(offsite.status_path(cfg).read_text())["uploaded"] == [snap.name]
    assert not list((cfg.offsite_dir / "staging").iterdir())  # staging cleaned up
    # a second pass with nothing new uploads nothing
    target.objects.clear()
    offsite.upload_pending(cfg, target)
    assert target.objects == {}


def test_failures_are_recorded_and_retried(cfg):
    snapshot(cfg, "murmur_2026-11-07_043000.sqlite")
    status = offsite.upload_pending(cfg, FakeTarget(fail=True))
    assert "bucket unreachable" in status["last_error"] and "last_success" not in status
    status = offsite.upload_pending(cfg, FakeTarget(wrong_size=True))
    assert "uploaded size" in status["last_error"]
    good = FakeTarget()
    status = offsite.upload_pending(cfg, good)
    assert status["last_error"] is None and status["last_success"] and good.objects


def test_unconfigured_service_idles_healthy(cfg, tmp_path):
    provider = ConfigProvider(cfg.source_path, env={})
    assert offsite.offsite_main(provider, once=True, env={}) == 0
    assert offsite.read_status(cfg)["configured"] is False
    assert offsite.heartbeat_path(cfg).exists()
    assert offsite.S3Settings.from_env({"MURMUR_S3_BUCKET": " "}) is None
    s = offsite.S3Settings.from_env({"MURMUR_S3_BUCKET": "bk", "MURMUR_S3_REGION": "us-east-2"})
    assert s == offsite.S3Settings("bk", "us-east-2", None)


def test_offsite_main_once_with_a_target(cfg):
    snapshot(cfg, "murmur_2026-11-07_043000.sqlite")
    target = FakeTarget()
    provider = ConfigProvider(cfg.source_path, env={})
    offsite.offsite_main(provider, once=True, target=target, env={})
    assert any(k.endswith(".sqlite.gz") for k in target.objects)


def test_s3_target_calls_boto3():
    from botocore.exceptions import ClientError

    client = mock.Mock()
    client.head_object.return_value = {"ContentLength": 42}
    t = offsite.S3Target(offsite.S3Settings("bk", None, None), client=client)
    t.upload(offsite.Path("/x.gz"), "p/k", {"sha256": "ab"})
    client.upload_file.assert_called_once_with(
        "/x.gz",
        "bk",
        "p/k",
        ExtraArgs={"Metadata": {"sha256": "ab"}, "ContentType": "application/gzip"},
    )
    assert t.size("p/k") == 42
    client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    assert t.size("p/missing") is None


def test_offsite_never_imports_the_store():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(offsite))
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "Store" not in names


def test_backup_prefix_is_validated(tmp_path):
    from murmur.config import ConfigError

    user = tmp_path / "c.toml"
    user.write_text(
        '[universe]\nsource = "static"\n[universe.static]\nA = "A"\n[backup]\nprefix = "a b"\n'
    )
    with pytest.raises(ConfigError, match="backup.prefix"):
        load_config(user, env={})


def real_store_snapshot(cfg, name="murmur_2026-11-07_043000.sqlite"):
    import sqlite3

    cfg.backups_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.backups_dir / name
    con = sqlite3.connect(path)
    con.executescript(
        # WAL like the real snapshots: opening the copy creates -wal/-shm files that
        # the drill must clean up.
        "PRAGMA journal_mode = wal;"
        "CREATE TABLE articles (id INTEGER PRIMARY KEY, title TEXT);"
        "INSERT INTO articles (title) VALUES ('a'), ('b');"
        "PRAGMA user_version = 13;"
    )
    con.commit()
    con.close()
    return path


def test_restore_drill_checks_the_uploaded_copy(cfg):
    real_store_snapshot(cfg)
    target = FakeTarget()
    offsite.upload_pending(cfg, target)
    assert offsite.verify_due(offsite.read_status(cfg))
    status = offsite.verify_latest(cfg, target)
    assert status["last_verify_ok"] is True and status["last_verify_error"] is None
    assert status["last_verify_schema"] == 13 and status["last_verify_articles"] == 2
    assert not offsite.verify_due(status)
    assert not list((cfg.offsite_dir / "staging").iterdir())


def test_restore_drill_catches_a_bad_copy(cfg):
    real_store_snapshot(cfg)
    target = FakeTarget()
    offsite.upload_pending(cfg, target)
    key = offsite.read_status(cfg)["last_key"]
    target.metadata[key] = {**target.metadata[key], "sha256": "0" * 64}
    status = offsite.verify_latest(cfg, target)
    assert status["last_verify_ok"] is False and "sha256 mismatch" in status["last_verify_error"]
    target.objects[key] = gzip.compress(b"not a database at all" * 100)
    target.metadata[key] = {}
    status = offsite.verify_latest(cfg, target)
    assert status["last_verify_ok"] is False


def test_restore_drill_with_nothing_uploaded(cfg):
    status = offsite.verify_latest(cfg, FakeTarget())
    assert (
        status["last_verify_ok"] is False
        and "nothing has been uploaded" in status["last_verify_error"]
    )
    assert not offsite.verify_due(status)


def test_the_service_runs_the_drill_weekly_and_on_demand(cfg, tmp_path):
    real_store_snapshot(cfg)
    provider = ConfigProvider(cfg.source_path, env={})
    target = FakeTarget()
    assert offsite.offsite_main(provider, once=True, target=target) == 0
    assert offsite.read_status(cfg)["last_verify_ok"] is True  # first upload -> verified
    assert offsite.offsite_main(provider, once=True, target=target, verify_only=True) == 0
    broken = FakeTarget()
    broken.objects, broken.metadata = (
        dict(target.objects),
        {k: {"sha256": "0" * 64} for k in target.objects},
    )
    assert offsite.offsite_main(provider, once=True, target=broken, verify_only=True) == 1
