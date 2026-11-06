"""Request-scoped dependencies: the config provider, a per-request store, the worker."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Request

from murmur.config import Config, ConfigProvider
from murmur.jobs import Worker
from murmur.store import Store


def get_provider(request: Request) -> ConfigProvider:
    return request.app.state.provider


def get_config(request: Request) -> Config:
    return request.app.state.provider.get()


def get_store(request: Request) -> Iterator[Store]:
    """A connection per request. FastAPI may run the dependency and the handler in
    different threadpool threads, hence ``check_same_thread=False``."""
    config = request.app.state.provider.get()
    store = Store(config.store_path, check_same_thread=False, migrate=False)
    try:
        yield store
    finally:
        store.close()


def get_worker(request: Request) -> Worker | None:
    return request.app.state.worker
