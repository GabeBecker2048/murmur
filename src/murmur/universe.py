"""Which companies are in the index on a given day.

0.1.0 only knows the static list from the config. 0.2.0 adds the market-cap
universe; the store already records membership per day, so nothing downstream
needs to change when it does.
"""

from __future__ import annotations

from datetime import date

from murmur.config import Config
from murmur.models import UniverseMember
from murmur.store import Store


def universe_for(config: Config, day: date) -> list[UniverseMember]:
    if config.universe.source != "static":
        raise NotImplementedError(config.universe.source)
    return [UniverseMember(t, name) for t, name in sorted(config.universe.static.items())]


def record_universe(store: Store, config: Config, day: date) -> list[UniverseMember]:
    members = universe_for(config, day)
    store.record_universe(day.isoformat(), members, config.universe.source)
    return members
