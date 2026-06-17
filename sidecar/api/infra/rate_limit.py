from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiohttp import web

    from settings import Settings


def client_ip(request: "web.Request", settings: "Settings") -> str:
    """Resolve the client IP, honouring X-Forwarded-For only behind a trusted proxy.

    Shared by the rate-limit middleware and the free-claim gate so both key on
    the same identity. Untrusted X-Forwarded-For is ignored — otherwise a client
    could spoof the header to bypass per-IP limits.
    """
    remote = request.remote or ""
    if remote and settings.trusted_proxy_ips and remote in settings.trusted_proxy_ips:
        return (request.headers.get("X-Forwarded-For") or remote).split(",")[0].strip()
    return remote or "unknown"


def cleanup_rate_limits(rate_limits: dict[str, list[float]], window_seconds: int) -> None:
    """Drop rate-limit entries whose every timestamp is stale.

    Without this sweep, rate_limits grows unboundedly as new IPs connect —
    the middleware only filters a key's history when that same IP makes
    another request, so rotating source IPs is a slow-drip memory leak.
    """
    cutoff = time.time() - window_seconds
    stale = [
        ip
        for ip, history in rate_limits.items()
        if not history or all(ts <= cutoff for ts in history)
    ]
    for ip in stale:
        rate_limits.pop(ip, None)
