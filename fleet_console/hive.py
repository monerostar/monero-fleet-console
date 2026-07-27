"""Read-only Hive OS API client (Cloudflare-friendly headers)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .config import UA, WORKERS, HiveEnv


@dataclass
class WorkerSnapshot:
    worker_id: int
    name: str
    role: str
    owner: str
    note: str
    online: bool
    lan_ip: str | None
    flight_sheet: str | None
    miner: str | None
    algo: str | None
    # Hive RandomX `hash` field is already kH/s for CPU miners
    hashrate_khs: float | None
    cpu_temp_c: float | None
    accepted: int | None
    rejected: int | None
    share_ratio: float | None
    needs_upgrade: bool
    raw_description: str | None
    miner_version: str | None = None
    alerts: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)

    @property
    def status_label(self) -> str:
        if self.alerts:
            return "ALERT"
        if self.warns:
            return "WARN"
        if not self.online:
            return "OFF" if self.role == "on_demand" else "DOWN"
        return "OK"


@dataclass
class FleetSnapshot:
    fetched_at: str
    farm_id: int
    farm_name: str | None
    workers: list[WorkerSnapshot]
    api_ok: bool
    error: str | None = None

    @property
    def alert_count(self) -> int:
        return sum(1 for w in self.workers if w.alerts)

    @property
    def warn_count(self) -> int:
        return sum(1 for w in self.workers if w.warns)


def _request(env: HiveEnv, path: str, timeout: float = 30.0) -> Any:
    url = f"{env.base}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {env.token}",
            "Accept": "application/json",
            "User-Agent": UA,
            "Origin": "https://the.hiveos.farm",
            "Referer": "https://the.hiveos.farm/",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            return json.loads(body)
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "replace")[:400]
        raise RuntimeError(f"Hive HTTP {e.code} on {path}: {err_body}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Hive network error on {path}: {e}") from e


def _num(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _pick_hashrate_khs(worker: dict) -> float | None:
    """Hive CPU/RandomX `hash` is kH/s (e.g. 13.8 for a 3950X)."""
    ms = worker.get("miners_summary") or {}
    if isinstance(ms, dict):
        miners = ms.get("hashrates") or ms.get("miners") or []
        if isinstance(miners, list):
            total = 0.0
            found = False
            for m in miners:
                if not isinstance(m, dict):
                    continue
                v = _num(m.get("hash") if m.get("hash") is not None else m.get("hashrate"))
                if v is not None:
                    total += v
                    found = True
            if found:
                return total
        v = _num(ms.get("hash") if ms.get("hash") is not None else ms.get("hashrate"))
        if v is not None and v > 0:
            return v
    return None


def _pick_temp(worker: dict) -> float | None:
    hw = worker.get("hardware_stats") or {}
    if isinstance(hw, dict):
        cputemp = hw.get("cputemp")
        if isinstance(cputemp, list) and cputemp:
            temps = [t for t in (_num(x) for x in cputemp) if t is not None]
            if temps:
                return max(temps)
        v = _num(hw.get("temp"))
        if v is not None:
            return v
    # miner temps array (CPU mining reports here too)
    mstats = worker.get("miners_stats") or {}
    if isinstance(mstats, dict):
        for m in mstats.get("hashrates") or []:
            if not isinstance(m, dict):
                continue
            temps = m.get("temps") or []
            if isinstance(temps, list):
                vals = [t for t in (_num(x) for x in temps) if t is not None and t > 0]
                if vals:
                    return max(vals)
    return None


def _pick_shares(worker: dict) -> tuple[int | None, int | None, float | None]:
    ms = worker.get("miners_summary") or {}
    if isinstance(ms, dict):
        miners = ms.get("hashrates") or []
        if isinstance(miners, list) and miners and isinstance(miners[0], dict):
            sh = miners[0].get("shares") or {}
            if isinstance(sh, dict):
                try:
                    a = int(sh["accepted"]) if sh.get("accepted") is not None else None
                    r = int(sh["rejected"]) if sh.get("rejected") is not None else None
                    ratio = _num(sh.get("ratio"))
                    return a, r, ratio
                except (TypeError, ValueError):
                    pass
    return None, None, None


def _miner_name(worker: dict) -> tuple[str | None, str | None, str | None]:
    ms = worker.get("miners_summary") or {}
    if isinstance(ms, dict):
        miners = ms.get("hashrates") or []
        if isinstance(miners, list) and miners and isinstance(miners[0], dict):
            m0 = miners[0]
            miner = str(m0.get("miner") or m0.get("name") or "") or None
            algo = str(m0.get("algo") or "") or None
            ver = str(m0.get("ver") or "") or None
            if miner and ver:
                miner = f"{miner} {ver}"
            return miner, algo, ver
    return None, None, None


def _flight_sheet_name(worker: dict) -> str | None:
    fs = worker.get("flight_sheet") or worker.get("fs")
    if isinstance(fs, dict):
        return str(fs.get("name") or "") or None
    name = worker.get("flight_sheet_name") or worker.get("fs_name")
    return str(name) if name else None


def _lan_ip(worker: dict) -> str | None:
    ips = worker.get("ip_addresses")
    if isinstance(ips, list) and ips:
        return str(ips[0])
    lan = worker.get("lan_config") or {}
    if isinstance(lan, dict) and lan.get("address"):
        return str(lan["address"]).split("/")[0]
    stats = worker.get("stats") or {}
    if isinstance(stats, dict):
        v = stats.get("ip") or stats.get("local_ip")
        if v:
            return str(v)
    return None


def evaluate_worker(worker: dict, meta: dict) -> WorkerSnapshot:
    wid = int(worker.get("id") or 0)
    stats = worker.get("stats") or {}
    online = bool(stats.get("online")) if isinstance(stats, dict) else bool(worker.get("online"))

    hr = _pick_hashrate_khs(worker)
    temp = _pick_temp(worker)
    acc, rej, ratio = _pick_shares(worker)
    miner, algo, ver = _miner_name(worker)
    fs = _flight_sheet_name(worker)
    needs = bool(worker.get("needs_upgrade"))
    # redline from Hive when present
    red_cpu = _num(worker.get("red_cpu_temp"))
    temp_alert = float(meta.get("temp_alert_c", red_cpu or 90))
    if red_cpu is not None:
        temp_alert = min(temp_alert, red_cpu)

    snap = WorkerSnapshot(
        worker_id=wid,
        name=str(worker.get("name") or meta.get("name") or wid),
        role=str(meta.get("role") or "unknown"),
        owner=str(meta.get("owner") or ""),
        note=str(meta.get("note") or ""),
        online=online,
        lan_ip=_lan_ip(worker),
        flight_sheet=fs,
        miner=miner,
        algo=algo,
        hashrate_khs=hr,
        cpu_temp_c=temp,
        accepted=acc,
        rejected=rej,
        share_ratio=ratio,
        needs_upgrade=needs,
        raw_description=(str(worker.get("description")) if worker.get("description") else None),
        miner_version=ver,
    )

    if snap.role == "always_on" and not online:
        snap.alerts.append("24/7 worker offline")
    if online and temp is not None:
        if temp >= temp_alert:
            snap.alerts.append(f"CPU temp {temp:.0f}°C ≥ alert ({temp_alert:.0f}°C)")
        elif temp >= float(meta.get("temp_warn_c", 80)):
            snap.warns.append(f"CPU temp {temp:.0f}°C elevated")
    min_khs = float(meta.get("min_hashrate_khs") or 0)
    if online and min_khs > 0:
        if hr is None:
            snap.warns.append("hashrate missing while online")
        elif hr < min_khs:
            snap.alerts.append(f"hashrate {hr:.2f} kH/s below floor {min_khs:.1f}")
    if online and needs:
        snap.warns.append("Hive needs_upgrade")
    # dummy GPU noise is expected on CPU-only boxes — ignore missed_unit/missed_temp

    return snap


def fetch_fleet(env: HiveEnv | None = None) -> FleetSnapshot:
    from .config import discover_env

    env = env or discover_env()
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    try:
        farm = _request(env, f"/farms/{env.farm_id}")
        workers_payload = _request(env, f"/farms/{env.farm_id}/workers")
    except Exception as e:  # noqa: BLE001 — surface as snapshot error
        return FleetSnapshot(
            fetched_at=now,
            farm_id=env.farm_id,
            farm_name=None,
            workers=[],
            api_ok=False,
            error=str(e),
        )

    farm_name = None
    if isinstance(farm, dict):
        farm_name = farm.get("name") or farm.get("title")

    items: list[dict] = []
    if isinstance(workers_payload, dict):
        data = workers_payload.get("data")
        if isinstance(data, list):
            items = data
        elif isinstance(workers_payload.get("workers"), list):
            items = workers_payload["workers"]
    elif isinstance(workers_payload, list):
        items = workers_payload

    # Prefer known workers; still include unexpected ones as unknown
    by_id = {int(w.get("id")): w for w in items if w.get("id") is not None}
    snaps: list[WorkerSnapshot] = []

    for wid, meta in WORKERS.items():
        if wid in by_id:
            snaps.append(evaluate_worker(by_id[wid], meta))
        else:
            snaps.append(
                WorkerSnapshot(
                    worker_id=wid,
                    name=str(meta["name"]),
                    role=str(meta["role"]),
                    owner=str(meta.get("owner") or ""),
                    note=str(meta.get("note") or ""),
                    online=False,
                    lan_ip=None,
                    flight_sheet=None,
                    miner=None,
                    algo=None,
                    hashrate_khs=None,
                    cpu_temp_c=None,
                    accepted=None,
                    rejected=None,
                    share_ratio=None,
                    needs_upgrade=False,
                    raw_description=None,
                    alerts=["worker missing from Hive farm list"]
                    if meta["role"] == "always_on"
                    else [],
                    warns=["worker not in farm response"] if meta["role"] != "always_on" else [],
                )
            )

    for wid, w in by_id.items():
        if wid in WORKERS:
            continue
        # unknown extra worker — informational, no always_on alert
        snaps.append(
            evaluate_worker(
                w,
                meta = {
                                    "name": w.get("name") or str(wid),
                                    "role": "unknown",
                                    "owner": "",
                                    "note": "not in local role map",
                                    "min_hashrate_khs": 0,
                                    "temp_warn_c": 80,
                                    "temp_alert_c": 90,
                                },
                            )
                        )

    # stable sort: always_on first, then name
    order = {"always_on": 0, "flex": 1, "on_demand": 2, "unknown": 3}
    snaps.sort(key=lambda s: (order.get(s.role, 9), s.name.lower()))

    return FleetSnapshot(
        fetched_at=now,
        farm_id=env.farm_id,
        farm_name=str(farm_name) if farm_name else None,
        workers=snaps,
        api_ok=True,
        error=None,
    )
