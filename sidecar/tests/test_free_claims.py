"""Tests for FreeClaimStore — per-IP free-SKU usage accounting."""

from __future__ import annotations

import asyncio

import pytest

from payments import FreeClaimStore


@pytest.fixture
async def store(tmp_path):
    s = FreeClaimStore(str(tmp_path / "free.db"))
    await s.init()
    yield s
    await s.close()


async def test_try_claim_allows_up_to_limit(store):
    a = await store.try_claim("1.2.3.4", "trial", limit=2, window_seconds=1000)
    b = await store.try_claim("1.2.3.4", "trial", limit=2, window_seconds=1000)
    c = await store.try_claim("1.2.3.4", "trial", limit=2, window_seconds=1000)
    assert a is not None
    assert b is not None
    assert c is None  # limit reached


async def test_try_claim_is_per_ip_and_per_sku(store):
    assert await store.try_claim("1.1.1.1", "trial", limit=1, window_seconds=1000) is not None
    # Different IP — independent quota.
    assert await store.try_claim("2.2.2.2", "trial", limit=1, window_seconds=1000) is not None
    # Same IP, different SKU — independent quota.
    assert await store.try_claim("1.1.1.1", "other", limit=1, window_seconds=1000) is not None
    # Same IP + SKU again — blocked.
    assert await store.try_claim("1.1.1.1", "trial", limit=1, window_seconds=1000) is None


async def test_window_expiry_frees_slot(store):
    # window_seconds=0 means every prior claim is immediately stale.
    assert await store.try_claim("1.1.1.1", "trial", limit=1, window_seconds=0) is not None
    assert await store.try_claim("1.1.1.1", "trial", limit=1, window_seconds=0) is not None


async def test_rollback_frees_slot(store):
    ts = await store.try_claim("1.1.1.1", "trial", limit=1, window_seconds=1000)
    assert ts is not None
    assert await store.try_claim("1.1.1.1", "trial", limit=1, window_seconds=1000) is None
    await store.rollback_claim("1.1.1.1", "trial", ts)
    assert await store.try_claim("1.1.1.1", "trial", limit=1, window_seconds=1000) is not None


async def test_concurrent_claims_do_not_exceed_limit(store):
    results = await asyncio.gather(*[
        store.try_claim("9.9.9.9", "trial", limit=1, window_seconds=1000)
        for _ in range(10)
    ])
    granted = [r for r in results if r is not None]
    assert len(granted) == 1


async def test_cleanup_removes_stale_rows(store):
    await store.try_claim("1.1.1.1", "trial", limit=5, window_seconds=1000)
    await store.cleanup(older_than_seconds=0)  # everything is older than 0s ago
    # Slot is free again after cleanup.
    assert await store.try_claim("1.1.1.1", "trial", limit=1, window_seconds=1000) is not None
