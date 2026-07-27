"""Local monerod node status (+ optional start via helper script)."""

from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import UA

DEFAULT_RPC = os.environ.get(
    "MONEROD_RPC_URL", "http://127.0.0.1:18081/json_rpc"
).strip()


def _default_binary() -> Path:
    env = os.environ.get("MONEROD_BINARY", "").strip()
    if env:
        return Path(env)
    # Common Windows GUI install path — override with MONEROD_BINARY if different
    return Path(r"C:\Program Files\Monero GUI Wallet\monerod.exe")


def _default_data() -> Path:
    env = os.environ.get("MONEROD_DATA_DIR", "").strip()
    if env:
        return Path(env)
    # No personal drive letter baked in — empty means "unknown / not set"
    return Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Monero"


def _default_start_script() -> Path:
    env = os.environ.get("MONEROD_START_SCRIPT", "").strip()
    if env:
        return Path(env)
    # Optional Hermes helper if present; otherwise --start-node reports missing
    return (
        Path.home()
        / "AppData"
        / "Local"
        / "hermes"
        / "profiles"
        / "main"
        / "scripts"
        / "monerod-start.py"
    )


DEFAULT_BINARY = _default_binary()
DEFAULT_DATA = _default_data()
DEFAULT_START_SCRIPT = _default_start_script()


@dataclass
class MoneroNodeSnapshot:
    fetched_at: str
    api_ok: bool
    error: str | None
    process_running: bool
    rpc_url: str
    height: int | None = None
    target_height: int | None = None
    behind: int | None = None
    synchronized: bool | None = None
    busy_syncing: bool | None = None
    peers_out: int | None = None
    peers_in: int | None = None
    version: str | None = None
    database_gb: float | None = None
    nettype: str | None = None
    tx_count: int | None = None
    offline: bool | None = None
    update_available: bool | None = None
    data_dir: str | None = None
    binary_present: bool | None = None

    @property
    def status_label(self) -> str:
        if not self.process_running and not self.api_ok:
            return "DOWN"
        if self.process_running and not self.api_ok:
            return "STARTING"
        if self.api_ok and self.synchronized:
            return "SYNCED"
        if self.api_ok and (self.behind or 0) > 0:
            return "SYNCING"
        if self.api_ok:
            return "UP"
        return "UNKNOWN"


def process_running() -> bool:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq monerod.exe", "/NH"],
            text=True,
            errors="replace",
            timeout=15,
        )
        return "monerod.exe" in out.lower()
    except Exception:
        # Non-Windows or tasklist unavailable
        try:
            out = subprocess.check_output(
                ["pgrep", "-x", "monerod"],
                text=True,
                errors="replace",
                timeout=10,
            )
            return bool(out.strip())
        except Exception:
            return False


def _rpc(method: str, rpc_url: str = DEFAULT_RPC, timeout: float = 20.0) -> dict | None:
    body = json.dumps({"jsonrpc": "2.0", "id": "0", "method": method}).encode()
    req = urllib.request.Request(
        rpc_url,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": UA},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
            return data.get("result")
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def fetch_monerod(
    rpc_url: str = DEFAULT_RPC,
    data_dir: Path | None = None,
    binary: Path | None = None,
) -> MoneroNodeSnapshot:
    data_dir = data_dir if data_dir is not None else _default_data()
    binary = binary if binary is not None else _default_binary()
    now = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    alive = process_running()
    info = _rpc("get_info", rpc_url=rpc_url)
    if not info:
        return MoneroNodeSnapshot(
            fetched_at=now,
            api_ok=False,
            error="RPC not answering" if alive else "monerod not running",
            process_running=alive,
            rpc_url=rpc_url,
            data_dir=str(data_dir),
            binary_present=binary.is_file(),
        )

    h = int(info.get("height") or 0)
    t = int(info.get("target_height") or h or 0)
    behind = max(0, t - h)
    db = info.get("database_size")
    db_gb = round(float(db) / (1024**3), 2) if db is not None else None

    return MoneroNodeSnapshot(
        fetched_at=now,
        api_ok=True,
        error=None,
        process_running=alive or True,
        rpc_url=rpc_url,
        height=h,
        target_height=t,
        behind=behind,
        synchronized=bool(info.get("synchronized")),
        busy_syncing=bool(info.get("busy_syncing")),
        peers_out=int(info["outgoing_connections_count"])
        if info.get("outgoing_connections_count") is not None
        else None,
        peers_in=int(info["incoming_connections_count"])
        if info.get("incoming_connections_count") is not None
        else None,
        version=str(info.get("version") or "") or None,
        database_gb=db_gb,
        nettype=str(info.get("nettype") or "") or None,
        tx_count=int(info["tx_count"]) if info.get("tx_count") is not None else None,
        offline=bool(info.get("offline")) if info.get("offline") is not None else None,
        update_available=bool(info.get("update_available"))
        if info.get("update_available") is not None
        else None,
        data_dir=str(data_dir),
        binary_present=binary.is_file(),
    )


def start_monerod(
    start_script: Path | None = None,
    timeout: float = 90.0,
) -> tuple[int, str]:
    """Idempotent start via optional helper script (detached launcher)."""
    start_script = start_script if start_script is not None else _default_start_script()
    if not start_script.is_file():
        return 1, f"start script missing: {start_script}"
    try:
        proc = subprocess.run(
            ["python", str(start_script)],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired:
        return 1, "monerod-start timed out"
    except Exception as e:  # noqa: BLE001
        return 1, f"monerod-start failed: {e}"
    out = ((proc.stdout or "") + (proc.stderr or "")).strip()
    return proc.returncode, out or f"exit {proc.returncode}"


def monerod_to_dict(snap: MoneroNodeSnapshot) -> dict[str, Any]:
    return {
        "fetched_at": snap.fetched_at,
        "api_ok": snap.api_ok,
        "error": snap.error,
        "status": snap.status_label,
        "process_running": snap.process_running,
        "rpc_url": snap.rpc_url,
        "height": snap.height,
        "target_height": snap.target_height,
        "behind": snap.behind,
        "synchronized": snap.synchronized,
        "busy_syncing": snap.busy_syncing,
        "peers_out": snap.peers_out,
        "peers_in": snap.peers_in,
        "version": snap.version,
        "database_gb": snap.database_gb,
        "nettype": snap.nettype,
        "tx_count": snap.tx_count,
        "offline": snap.offline,
        "update_available": snap.update_available,
        "data_dir": snap.data_dir,
        "binary_present": snap.binary_present,
    }
