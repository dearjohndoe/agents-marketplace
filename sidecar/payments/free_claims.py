from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import aiosqlite


class FreeClaimStore:
    """Persistent per-IP accounting for FREE SKU usage.

    Bounds abuse of free products: at most `limit` claims per (ip, sku_id)
    within a rolling `window_seconds`. Backed by the same SQLite file as
    ProcessedTxStore so a single per-agent DB file holds all payment state.

    Identity is the client IP (see settings.trusted_proxy_ips / X-Forwarded-For).
    A free product has no on-chain payment, so this is a best-effort gate —
    rotating IPs (e.g. via a VPN) can bypass it. The global `initial_stock`
    cap on the SKU is an independent second gate.
    """

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None
        # Serialise count->insert so two concurrent claims from one IP can't
        # both pass the limit check. One sidecar == one process == one conn.
        self._lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()

    async def init(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA busy_timeout=15000")
        await self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS free_claims (
                ip         TEXT NOT NULL,
                sku_id     TEXT NOT NULL,
                claimed_at INTEGER NOT NULL,
                job_id     TEXT
            )
            """
        )
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS free_claims_idx ON free_claims(ip, sku_id, claimed_at)"
        )
        await self._conn.commit()

    async def try_claim(
        self,
        ip: str,
        sku_id: str,
        *,
        limit: int,
        window_seconds: int,
        job_id: str | None = None,
    ) -> int | None:
        """Reserve one free claim. Returns the claim timestamp on success, or
        None if the limit for (ip, sku_id) within the window is already reached.

        The returned timestamp is the claim token — pass it to rollback_claim()
        to undo the reservation (e.g. if the run never starts)."""
        if not self._conn:
            await self.init()
        now = int(time.time())
        cutoff = now - max(window_seconds, 0)
        async with self._lock:
            # Drop stale rows for this identity so the window stays accurate.
            await self._conn.execute(
                "DELETE FROM free_claims WHERE ip = ? AND sku_id = ? AND claimed_at <= ?",
                (ip, sku_id, cutoff),
            )
            async with self._conn.execute(
                "SELECT COUNT(*) FROM free_claims WHERE ip = ? AND sku_id = ? AND claimed_at > ?",
                (ip, sku_id, cutoff),
            ) as cursor:
                row = await cursor.fetchone()
            count = int(row[0]) if row else 0
            if count >= max(limit, 0):
                await self._conn.commit()
                return None
            await self._conn.execute(
                "INSERT INTO free_claims (ip, sku_id, claimed_at, job_id) VALUES (?, ?, ?, ?)",
                (ip, sku_id, now, job_id),
            )
            await self._conn.commit()
            return now

    async def rollback_claim(self, ip: str, sku_id: str, claimed_at: int) -> None:
        """Undo a claim made by try_claim. Idempotent."""
        if not self._conn:
            await self.init()
        await self._conn.execute(
            "DELETE FROM free_claims WHERE ip = ? AND sku_id = ? AND claimed_at = ?",
            (ip, sku_id, claimed_at),
        )
        await self._conn.commit()

    async def cleanup(self, older_than_seconds: int) -> None:
        if not self._conn:
            await self.init()
        cutoff = int(time.time()) - max(older_than_seconds, 0)
        await self._conn.execute(
            "DELETE FROM free_claims WHERE claimed_at <= ?",
            (cutoff,),
        )
        await self._conn.commit()

    async def close(self) -> None:
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        if self._conn:
            await self._conn.close()
            self._conn = None
