"""Fleet roles, thresholds, and env loading. No secrets in this file.

Personal farm/worker maps live in gitignored ``fleet.local.json``
(or ``FLEET_CONFIG``). Ship ``fleet.example.json`` as the template.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

Role = Literal["always_on", "on_demand", "flex", "unknown"]

DEFAULT_BASE = "https://api2.hiveos.farm/api/v2"

# Populated by load_fleet_policy() — empty until first discover/load.
WORKERS: dict[int, dict] = {}
KNOWN_BY_NAME: dict[str, dict] = {}
EXPECTED_POOL_WORKERS: tuple[str, ...] = ()
ALWAYS_ON_POOL_NAMES: frozenset[str] = frozenset()
POOL_MIN_KHS: float = 12.0

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _as_int_keys(raw: dict[str, Any] | None) -> dict[int, dict]:
    out: dict[int, dict] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        try:
            out[int(k)] = dict(v)
        except (TypeError, ValueError):
            continue
    return out


def _default_policy() -> dict[str, Any]:
    """Minimal empty policy — works once env has token + farm id."""
    return {
        "workers": {},
        "known_by_name": {},
        "expected_pool_workers": [],
        "always_on_pool_names": [],
        "pool_min_khs": 12.0,
    }


def fleet_config_candidates() -> list[Path]:
    paths: list[Path] = []
    if os.environ.get("FLEET_CONFIG"):
        paths.append(Path(os.environ["FLEET_CONFIG"]))
    root = project_root()
    paths.append(root / "fleet.local.json")
    # Optional committed example only used if nothing else exists
    paths.append(root / "fleet.example.json")
    return paths


def load_fleet_policy(force: bool = False) -> dict[str, Any]:
    """Load worker/role policy into module globals. Idempotent unless force."""
    global WORKERS, KNOWN_BY_NAME, EXPECTED_POOL_WORKERS
    global ALWAYS_ON_POOL_NAMES, POOL_MIN_KHS

    if WORKERS or KNOWN_BY_NAME or EXPECTED_POOL_WORKERS:
        if not force:
            return {
                "workers": WORKERS,
                "known_by_name": KNOWN_BY_NAME,
                "expected_pool_workers": list(EXPECTED_POOL_WORKERS),
                "always_on_pool_names": sorted(ALWAYS_ON_POOL_NAMES),
                "pool_min_khs": POOL_MIN_KHS,
            }

    data = _default_policy()
    chosen: Path | None = None
    for p in fleet_config_candidates():
        if not p.is_file():
            continue
        # Prefer local over example
        if p.name == "fleet.example.json" and any(
            c.is_file() and c.name != "fleet.example.json" for c in fleet_config_candidates()
        ):
            # still allow example if it's the only file
            others = [
                c
                for c in fleet_config_candidates()
                if c.is_file() and c.resolve() != p.resolve()
            ]
            if others:
                continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(raw, dict):
            data = {**_default_policy(), **raw}
            chosen = p
            break

    WORKERS = _as_int_keys(data.get("workers"))
    kb = data.get("known_by_name") or {}
    KNOWN_BY_NAME = {str(k): dict(v) for k, v in kb.items() if isinstance(v, dict)}
    exp = data.get("expected_pool_workers") or []
    EXPECTED_POOL_WORKERS = tuple(str(x) for x in exp) if isinstance(exp, list) else ()
    aon = data.get("always_on_pool_names") or []
    if isinstance(aon, list) and aon:
        ALWAYS_ON_POOL_NAMES = frozenset(str(x).upper() for x in aon)
    else:
        # Derive from known_by_name roles
        ALWAYS_ON_POOL_NAMES = frozenset(
            n.upper()
            for n, meta in KNOWN_BY_NAME.items()
            if str(meta.get("role") or "") == "always_on"
        )
    try:
        POOL_MIN_KHS = float(data.get("pool_min_khs") or 12.0)
    except (TypeError, ValueError):
        POOL_MIN_KHS = 12.0

    return {
        "source": str(chosen) if chosen else None,
        "workers": WORKERS,
        "known_by_name": KNOWN_BY_NAME,
        "expected_pool_workers": list(EXPECTED_POOL_WORKERS),
        "always_on_pool_names": sorted(ALWAYS_ON_POOL_NAMES),
        "pool_min_khs": POOL_MIN_KHS,
    }


# Eager load so importers see policy immediately when local/example exists.
load_fleet_policy()


def normalize_worker_name(name: str) -> str:
    """Collapse whitespace; keep case for display but lookup is case-insensitive."""
    return " ".join((name or "").strip().split())


def lookup_known(name: str) -> dict | None:
    """Match KNOWN_BY_NAME with light aliasing for common 7600X variants."""
    load_fleet_policy()
    n = normalize_worker_name(name)
    if not n:
        return None
    for key, meta in KNOWN_BY_NAME.items():
        if key.lower() == n.lower():
            out = {**meta}
            out["canonical"] = str(meta.get("canonical") or key)
            return out
    compact = re.sub(r"[^a-z0-9]", "", n.lower())
    if "7600x" in compact:
        slot = None
        m = re.search(r"7600x(?:[#_\-\s]*)([12])\b", n.lower())
        if not m:
            m = re.search(r"7600x([12])(?:\D|$)", compact)
        if m:
            slot = int(m.group(1))
        if slot == 1 and "7600X-1" in KNOWN_BY_NAME:
            base = KNOWN_BY_NAME["7600X-1"]
            return {"canonical": "7600X-1", **base}
        if slot == 2 and "7600X-2" in KNOWN_BY_NAME:
            base = KNOWN_BY_NAME["7600X-2"]
            return {"canonical": "7600X-2", **base}
        if "7600X" in KNOWN_BY_NAME:
            base = KNOWN_BY_NAME["7600X"]
            return {"canonical": n, **base}
    return None


@dataclass(frozen=True)
class HiveEnv:
    token: str
    base: str
    farm_id: int


def _read_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            out[k] = v
    return out


def _dotenv_candidates() -> list[Path]:
    candidates: list[Path] = []
    if os.environ.get("HIVEOS_ENV_FILE"):
        candidates.append(Path(os.environ["HIVEOS_ENV_FILE"]))
    # Project-local .env (gitignored)
    candidates.append(project_root() / ".env")
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        # Optional Hermes tech profile .env (operator convenience)
        candidates.append(Path(local) / "hermes" / "profiles" / "tech" / ".env")
    return candidates


def load_merged_dotenv() -> dict[str, str]:
    merged: dict[str, str] = {}
    for p in _dotenv_candidates():
        for k, v in _read_dotenv(p).items():
            merged.setdefault(k, v)
    return merged


def discover_env() -> HiveEnv:
    """Load HIVEOS_* from process env, then project/.env, then optional tech .env."""
    load_fleet_policy()
    file_env = load_merged_dotenv()

    token = (
        os.environ.get("HIVEOS_API_TOKEN")
        or os.environ.get("HIVE_OS_API_TOKEN")
        or file_env.get("HIVEOS_API_TOKEN")
        or file_env.get("HIVE_OS_API_TOKEN")
        or ""
    ).strip()
    base = (
        os.environ.get("HIVEOS_API_BASE")
        or file_env.get("HIVEOS_API_BASE")
        or DEFAULT_BASE
    ).rstrip("/")
    farm_raw = (
        os.environ.get("HIVEOS_FARM_ID")
        or file_env.get("HIVEOS_FARM_ID")
        or ""
    ).strip()
    if not farm_raw:
        raise SystemExit(
            "Missing HIVEOS_FARM_ID. Set it in .env / environment "
            "(see .env.example)."
        )
    try:
        farm_id = int(farm_raw)
    except ValueError as e:
        raise SystemExit(f"Invalid HIVEOS_FARM_ID: {farm_raw!r}") from e

    if not token:
        raise SystemExit(
            "Missing HIVEOS_API_TOKEN. Set it in .env or the environment "
            "(see .env.example)."
        )
    return HiveEnv(token=token, base=base, farm_id=farm_id)


def discover_hashvault_wallet() -> str | None:
    """Payout address for HashVault stats. Prefer env; optional for Hive-only mode."""
    file_env = load_merged_dotenv()
    w = (
        os.environ.get("HASHVAULT_WALLET")
        or os.environ.get("XMR_POOL_WALLET")
        or file_env.get("HASHVAULT_WALLET")
        or file_env.get("XMR_POOL_WALLET")
        or ""
    ).strip()
    return w or None


def default_dashboard_path() -> Path:
    out_dir = project_root() / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / "fleet-dashboard.html"


def default_state_path() -> Path:
    """Watchdog de-dupe state (last alert fingerprint)."""
    # Prefer project out/ so public clone works without Hermes paths
    env_state = os.environ.get("FLEET_WATCHDOG_STATE")
    if env_state:
        p = Path(env_state)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    cache_root = os.environ.get("LOCALAPPDATA")
    if cache_root:
        cache = Path(cache_root) / "hermes" / "profiles" / "tech" / "cache"
        try:
            cache.mkdir(parents=True, exist_ok=True)
            return cache / "hive-fleet-watchdog-state.json"
        except OSError:
            pass
    d = project_root() / "out"
    d.mkdir(parents=True, exist_ok=True)
    return d / "hive-fleet-watchdog-state.json"


_SENSITIVE = re.compile(
    r"(password|passwd|wallet|address|private|secret|token)\s*[:=]\s*\S+",
    re.I,
)


def scrub(text: str) -> str:
    return _SENSITIVE.sub(r"\1=***", text)
