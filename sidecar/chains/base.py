"""Target interfaces for the multichain refactor (MULTICHAIN_PLAN.md §2).

THIN BEACON — orientation only. This file defines the *shape* the existing TON
code (and the future Solana code) should converge on; it is intentionally not
wired into anything yet. The concrete TON rail moves under ``chains/ton/`` and
gets wrapped to satisfy these Protocols in a later step, at which point these
signatures get refined against what the real code actually needs. Don't grow
behaviour here.

Key principle (plan §2): discovery and payments are independent layers.
- A *rail* is a ``(chain, asset)`` pair identified by ``rail_id``:
  ``"ton"``, ``"ton:usdt"``, ``"sol"``, ``"sol:usdc"``.
- An agent advertises any set of rails, independent of which registries it
  heartbeats into.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from payments.types import VerifiedPayment


# ── chain namespacing (MULTICHAIN_PLAN.md §3) ──────────────────────────
#
# Persisted tx identifiers (processed_txs dedup keys, refund_queue PKs, and the
# refund memo's "tx" field) are namespaced ``{chain}:{tx_hash}`` so a TON hash
# and a Solana signature can never collide. The chain is derived from the rail:
# TON-native and USDT-on-TON both settle on "ton".

_RAIL_TO_CHAIN = {"TON": "ton", "USDT": "ton"}


def chain_for_rail(rail_id: str) -> str:
    """Chain a rail settles on (e.g. "TON"/"USDT" → "ton")."""
    return _RAIL_TO_CHAIN.get(rail_id, rail_id.lower())


def namespaced_tx_key(chain: str, tx_hash: str) -> str:
    """``{chain}:{tx_hash}``. Idempotent: an already-namespaced value (one that
    contains ``:`` — bare TON hashes / Solana sigs never do) is returned as-is,
    so wrapping twice is a safe no-op. Migration of legacy bare keys treats them
    as ``ton:`` (see the stores' ``init``)."""
    if ":" in tx_hash:
        return tx_hash
    return f"{chain}:{tx_hash}"


@runtime_checkable
class ChainRail(Protocol):
    """One payment rail: verify an incoming payment, refund it, describe itself
    in a 402 response, and report monitor freshness for the Plan-D health gate.

    Maps onto today's code as follows (to be unified during the wrap step):
    - ``verify``         ← ``PaymentVerifier.verify`` / ``JettonPaymentVerifier.verify``
    - ``refund``         ← the per-rail refund send (formerly refund_user's branch)
    - ``payment_option`` ← the per-rail dict built in ``_invoke_helpers.build_402_response``
    - ``monitor_healthy``← ``*Verifier.is_healthy``
    """

    #: Stable rail identifier, e.g. "ton", "ton:usdt", "sol:usdc".
    rail_id: str

    async def verify(self, proof: str, nonce: str, min_amount: int) -> VerifiedPayment:
        """Confirm an on-chain payment for ``nonce`` of at least ``min_amount``.

        ``proof`` is rail-specific: a TON tx hash, a Solana signature, or the
        contents of an x402 ``X-PAYMENT`` header. Returns the verified payment
        (with the *real* on-chain tx hash, not the caller-supplied one) or
        raises ``PaymentVerificationError`` on a definitive on-chain rejection.
        """
        ...

    async def refund(
        self, to: str, amount: int, *, original_tx_hash: str, reason: str,
    ) -> str | None:
        """Send ``amount`` (rail base units, pre-fee) back to ``to``.

        Returns the refund tx id, or ``None`` when the refund is skipped
        (amount ≤ fee) or the send fails — best-effort, never raises.
        ``original_tx_hash``/``reason`` are stamped into the refund body so a
        later worker can dedup via an on-chain scan.
        """
        ...

    def payment_option(self, amount: int, nonce: str) -> dict[str, Any]:
        """Build this rail's slice of a 402 ``payment_options`` entry
        (``rail``/``address``/``amount``/``memo`` and, for jettons, ``token``).
        The caller stitches in cross-rail fields like ``sku``.
        """
        ...

    def monitor_healthy(self, max_age_seconds: float = 60.0) -> bool:
        """True iff this rail's monitor had a fresh successful poll within
        ``max_age_seconds`` — gates whether we advertise the rail (plan D).
        """
        ...


@runtime_checkable
class ChainRegistry(Protocol):
    """Publishes heartbeats so the agent is discoverable on a chain's registry.

    Maps onto today's ``HeartbeatManager`` (TON). Solana's registry writes a
    transfer + Memo ``CTLX:REG:`` per plan §5.2.
    """

    async def publish_heartbeat(self, payload: dict[str, Any]) -> str:
        """Publish ``payload`` to the registry; returns the registry tx id."""
        ...
