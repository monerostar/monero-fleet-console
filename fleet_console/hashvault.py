"""HashVault.pro public API client (wallet + pool). Never logs full wallet."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import (
    EXPECTED_POOL_WORKERS,
    UA,
    discover_hashvault_wallet,
    lookup_known,
)

HASHVAULT_BASE = "https://api.hashvault.pro/v3/monero"
ATOMIC = 1_000_000_000_000  # 1 XMR


def mask_wallet(addr: str) -> str:
    if not addr or len(addr) < 12:
        return "***"
    return f"{addr[:6]}…{addr[-4:]}"


def _get(url: str, timeout: float = 45.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": UA,
            "Referer": "https://hashvault.pro/monero/dashboard",
            "Origin": "https://hashvault.pro",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HashVault HTTP {e.code}: {body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"HashVault network error: {e}") from e


def _num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _hs_to_khs(hs: float | None) -> float | None:
    """HashVault wallet/worker hashRate is H/s."""
    if hs is None:
        return None
    return hs / 1000.0


def _atomic_to_xmr(v: Any) -> float | None:
    n = _num(v)
    if n is None:
        return None
    return n / ATOMIC


@dataclass
class HashVaultWorker:
    name: str
    online: bool
    hashrate_khs: float | None
    avg1_khs: float | None
    avg24_khs: float | None
    valid_shares: int | None
    invalid_shares: int | None
    stale_shares: int | None
    last_share_ts: int | None
    role: str = "unknown"
    owner: str = ""
    note: str = ""
    os_label: str = ""
    expected: bool = False  # reserved seat, not yet seen on pool
    canonical: str | None = None


@dataclass
class HashVaultSnapshot:
    fetched_at: str
    api_ok: bool
    error: str | None
    wallet_masked: str | None
    # wallet collective
    hashrate_khs: float | None = None
    avg1_khs: float | None = None
    avg24_khs: float | None = None
    confirmed_xmr: float | None = None
    unconfirmed_xmr: float | None = None
    total_paid_xmr: float | None = None
    daily_paid_xmr: float | None = None
    payout_threshold_xmr: float | None = None
    workers: list[HashVaultWorker] = field(default_factory=list)
    # pool / network context (no wallet needed)
    pool_hashrate_mhs: float | None = None
    pool_miners: int | None = None
    pool_effort_pct: float | None = None
    pool_last_block_height: int | None = None
    network_height: int | None = None
    xmr_usd: float | None = None


def fetch_pool() -> dict:
    return _get(f"{HASHVAULT_BASE}/pool")


def fetch_wallet_stats(wallet: str | None = None) -> dict:
    w = wallet or discover_hashvault_wallet()
    if not w:
        raise RuntimeError(
            "Missing HASHVAULT_WALLET (tech .env) — set payout address to enable pool worker stats"
        )
    q = (
        f"{HASHVAULT_BASE}/wallet/{w}/stats"
        f"?chart=false&inactivityThreshold=10&order=name&period=daily"
        f"&poolType=false&workers=true"
    )
    return _get(q)


def fetch_hashvault(wallet: str | None = None) -> HashVaultSnapshot:
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    try:
        w = wallet or discover_hashvault_wallet()
    except Exception as e:  # noqa: BLE001
        return HashVaultSnapshot(
            fetched_at=now, api_ok=False, error=str(e), wallet_masked=None
        )

    if not w:
        # still try pool-only
        try:
            pool = fetch_pool()
            return _from_pool_only(now, pool)
        except Exception as e:  # noqa: BLE001
            return HashVaultSnapshot(
                fetched_at=now, api_ok=False, error=str(e), wallet_masked=None
            )

    try:
        pool = fetch_pool()
        stats = fetch_wallet_stats(w)
    except Exception as e:  # noqa: BLE001
        return HashVaultSnapshot(
            fetched_at=now,
            api_ok=False,
            error=str(e),
            wallet_masked=mask_wallet(w),
        )

    snap = _from_pool_only(now, pool)
    snap.wallet_masked = mask_wallet(w)
    snap.api_ok = True
    snap.error = None

    col = stats.get("collective") or {}
    rev = stats.get("revenue") or {}
    snap.hashrate_khs = _hs_to_khs(_num(col.get("hashRate")))
    snap.avg1_khs = _hs_to_khs(_num(col.get("avg1hashRate")))
    snap.avg24_khs = _hs_to_khs(_num(col.get("avg24hashRate")))
    snap.confirmed_xmr = _atomic_to_xmr(rev.get("confirmedBalance"))
    unc = rev.get("unconfirmedBalance") or {}
    if isinstance(unc, dict):
        c = unc.get("collective") or {}
        snap.unconfirmed_xmr = _atomic_to_xmr(c.get("total") if isinstance(c, dict) else None)
    snap.total_paid_xmr = _atomic_to_xmr(rev.get("totalPaid"))
    snap.daily_paid_xmr = _atomic_to_xmr(rev.get("dailyPaid"))
    snap.payout_threshold_xmr = _atomic_to_xmr(rev.get("payoutThreshold"))

    workers_raw = stats.get("collectiveWorkers") or []
    seen: dict[str, HashVaultWorker] = {}
    if isinstance(workers_raw, list):
        for wr in workers_raw:
            if not isinstance(wr, dict):
                continue
            raw_name = str(wr.get("name") or "unknown")
            known = lookup_known(raw_name) or {}
            canonical = str(known.get("canonical") or raw_name)
            display = canonical if known else raw_name
            worker = HashVaultWorker(
                name=display,
                online=not bool(wr.get("offline")),
                hashrate_khs=_hs_to_khs(_num(wr.get("hashRate"))),
                avg1_khs=_hs_to_khs(_num(wr.get("avg1hashRate"))),
                avg24_khs=_hs_to_khs(_num(wr.get("avg24hashRate"))),
                valid_shares=int(wr["validShares"]) if wr.get("validShares") is not None else None,
                invalid_shares=int(wr["invalidShares"]) if wr.get("invalidShares") is not None else None,
                stale_shares=int(wr["staleShares"]) if wr.get("staleShares") is not None else None,
                last_share_ts=int(wr["lastShare"]) if wr.get("lastShare") else None,
                role=str(known.get("role") or "unknown"),
                owner=str(known.get("owner") or ""),
                note=str(known.get("note") or ""),
                os_label=str(known.get("os") or ""),
                expected=False,
                canonical=canonical if known else None,
            )
            # if two aliases map to same canonical, keep the higher hashrate row
            prev = seen.get(display.lower())
            if prev is None:
                seen[display.lower()] = worker
            else:
                prev_hr = prev.hashrate_khs or 0.0
                cur_hr = worker.hashrate_khs or 0.0
                if cur_hr >= prev_hr:
                    seen[display.lower()] = worker

    # Reserve seats for expected flex/core workers not yet on the pool
    for exp in EXPECTED_POOL_WORKERS:
        key = exp.lower()
        if key in seen:
            continue
        known = lookup_known(exp) or {}
        seen[key] = HashVaultWorker(
            name=exp,
            online=False,
            hashrate_khs=None,
            avg1_khs=None,
            avg24_khs=None,
            valid_shares=None,
            invalid_shares=None,
            stale_shares=None,
            last_share_ts=None,
            role=str(known.get("role") or "flex"),
            owner=str(known.get("owner") or ""),
            note=str(known.get("note") or "expected — not on pool yet"),
            os_label=str(known.get("os") or ""),
            expected=True,
            canonical=exp,
        )

    order = {"always_on": 0, "flex": 1, "on_demand": 2, "unknown": 3}
    out = list(seen.values())
    out.sort(key=lambda x: (order.get(x.role, 9), x.name.lower()))
    snap.workers = out
    return snap


def _from_pool_only(now: str, pool: dict) -> HashVaultSnapshot:
    ps = (pool.get("pool_statistics") or {}).get("collective") or {}
    ns = pool.get("network_statistics") or {}
    market = pool.get("market") or {}
    last = ps.get("lastFoundBlock") or {}
    hr = _num(ps.get("hashRate"))
    return HashVaultSnapshot(
        fetched_at=now,
        api_ok=True,
        error=None,
        wallet_masked=None,
        pool_hashrate_mhs=(hr / 1_000_000.0) if hr is not None else None,
        pool_miners=int(ps["miners"]) if ps.get("miners") is not None else None,
        pool_effort_pct=_num(ps.get("currentEffort")),
        pool_last_block_height=int(last["height"]) if last.get("height") is not None else None,
        network_height=int(ns["height"]) if ns.get("height") is not None else None,
        xmr_usd=_num(market.get("price_usd") or market.get("usd") or market.get("XMR_USD")),
    )
