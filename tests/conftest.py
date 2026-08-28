from __future__ import annotations

from datetime import UTC, datetime

import pytest

from bnsf_fm.ingest import CAMPUS_EDGES, FixtureSource, load
from bnsf_fm.store import Store

# Fixed "now" so every age-dependent assertion is stable regardless of when the
# suite runs. The fixture generator is seeded from the same instant.
NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


@pytest.fixture
def store() -> Store:
    with Store(":memory:") as store:
        load(FixtureSource(now=NOW), store)
        store.set_campus_edges(CAMPUS_EDGES)
        yield store


@pytest.fixture
def small_store() -> Store:
    """A tiny store for tests that need exact, hand-checkable numbers."""
    with Store(":memory:") as store:
        store.set_campus_edges(CAMPUS_EDGES)
        yield store
