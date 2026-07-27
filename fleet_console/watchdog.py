"""Fail-only alert text + optional de-dupe state for cron."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .config import ALWAYS_ON_POOL_NAMES, POOL_MIN_KHS, load_fleet_policy
from .hashvault import HashVaultSnapshot
from .hive import FleetSnapshot
from .monerod_node import MoneroNodeSnapshot
from .render import render_table

# If monerod is further behind tip than this, soft-warn (blocks)
NODE_BEHIND_WARN = 200


def _always_on_names() -> frozenset[str]:
    load_fleet_policy()
    return ALWAYS_ON_POOL_NAMES


def _pool_floor() -> float:
    load_fleet_policy()
    return float(POOL_MIN_KHS)


def alert_fingerprint(
    snap: FleetSnapshot,
    hv: HashVaultSnapshot | None = None,
    node: MoneroNodeSnapshot | None = None,
) -> str:
    parts = []
    floor = _pool_floor()
    always = _always_on_names()
    for w in snap.workers:
        for a in w.alerts:
            parts.append(f"{w.worker_id}|A|{a}")
        for wn in w.warns:
            parts.append(f"{w.worker_id}|W|{wn}")
    if not snap.api_ok:
        parts.append(f"API|{snap.error}")
    if hv is not None:
        if not hv.api_ok:
            parts.append(f"HV|{hv.error}")
        elif hv.hashrate_khs is not None and hv.hashrate_khs < floor:
            parts.append(f"HV|low|{hv.hashrate_khs:.1f}")
        for w in hv.workers:
            if (
                w.name.upper() in always
                and not w.online
                and not getattr(w, "expected", False)
            ):
                parts.append(f"HV|{w.name}|off")
    if node is not None:
        if not node.api_ok and not node.process_running:
            parts.append("NODE|down")
        elif not node.api_ok and node.process_running:
            parts.append("NODE|rpc")
        elif node.behind is not None and node.behind >= NODE_BEHIND_WARN:
            parts.append(f"NODE|behind|{node.behind}")
    raw = "\n".join(sorted(parts))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def load_state(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def watchdog_message(
    snap: FleetSnapshot,
    *,
    include_warns: bool = True,
    hv: HashVaultSnapshot | None = None,
    node: MoneroNodeSnapshot | None = None,
) -> str | None:
    lines: list[str] = []
    floor = _pool_floor()
    always = _always_on_names()

    if not snap.api_ok:
        lines.append(f"ALERT  Hive API failed: {snap.error}")
    else:
        for w in snap.workers:
            for a in w.alerts:
                lines.append(f"ALERT  {w.name}: {a}")
            if include_warns:
                for wn in w.warns:
                    if wn == "Hive needs_upgrade":
                        continue
                    lines.append(f"WARN   {w.name}: {wn}")

    if hv is not None:
        if not hv.api_ok:
            lines.append(f"WARN   HashVault API failed: {hv.error}")
        else:
            if hv.hashrate_khs is not None and hv.hashrate_khs < floor:
                lines.append(
                    f"WARN   HashVault wallet HR {hv.hashrate_khs:.2f} kH/s "
                    f"below floor {floor:.0f}"
                )
            for w in hv.workers:
                if (
                    w.name.upper() in always
                    and not w.online
                    and not getattr(w, "expected", False)
                ):
                    lines.append(f"ALERT  HashVault worker offline: {w.name}")

    if node is not None:
        if not node.api_ok and not node.process_running:
            if include_warns:
                lines.append("WARN   monerod DOWN (no process / no RPC)")
        elif not node.api_ok and node.process_running:
            lines.append("WARN   monerod process up but RPC not answering")
        elif include_warns and node.behind is not None and node.behind >= NODE_BEHIND_WARN:
            lines.append(
                f"WARN   monerod behind tip by {node.behind} blocks "
                f"({node.height}/{node.target_height})"
            )

    if not lines:
        return None

    body = ["Monero fleet watchdog", f"Fetched: {snap.fetched_at}", ""]
    body.extend(lines)
    body.append("")
    body.append(render_table(snap, hv, node))
    return "\n".join(body)


def run_watchdog(
    snap: FleetSnapshot,
    state_path: Path,
    *,
    include_warns: bool = True,
    force: bool = False,
    hv: HashVaultSnapshot | None = None,
    node: MoneroNodeSnapshot | None = None,
) -> tuple[int, str]:
    msg = watchdog_message(snap, include_warns=include_warns, hv=hv, node=node)
    if msg is None:
        st = load_state(state_path)
        st["last_ok_at"] = snap.fetched_at
        st["last_fingerprint"] = "ok"
        save_state(state_path, st)
        return 0, ""

    fp = alert_fingerprint(snap, hv, node)
    st = load_state(state_path)
    prev = st.get("last_fingerprint")
    if not force and prev == fp:
        return 0, ""

    st["last_fingerprint"] = fp
    st["last_alert_at"] = snap.fetched_at
    st["last_message_preview"] = msg.splitlines()[:8]
    save_state(state_path, st)
    return 0, msg
