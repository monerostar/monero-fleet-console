#!/usr/bin/env python3
"""fleet-status-write.py — per-box status writer (Linux hosts).

Reads the local xmrig API (127.0.0.1:4028 or $PORT) and writes a tiny JSON
status file that the fleet-tg dispatcher can read over SSH in milliseconds.
Run from cron every 3 minutes.

Writes to: ~/fleet-status.json
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PORT = os.environ.get("FLEET_STATUS_PORT", "4028")
OUT = Path(os.environ.get("FLEET_STATUS_FILE", str(Path.home() / "fleet-status.json")))
BOX = os.environ.get("FLEET_STATUS_BOX", Path.home().name)


def main() -> int:
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/1/summary",
            headers={"User-Agent": "fleet-status-write/1.0"},
        )
        with urllib.request.urlopen(req, timeout=6) as r:
            d = json.loads(r.read().decode())
        hr = (d.get("hashrate") or {}).get("total") or [0, 0, 0]
        conn = d.get("connection") or {}
        data = {
            "box": BOX,
            "worker": d.get("worker_id"),
            "hr_10s": round(hr[0] or 0, 1),
            "pool": conn.get("pool"),
            "online": bool(conn.get("pool")),
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    except Exception as e:  # noqa: BLE001
        data = {
            "box": BOX,
            "worker": None,
            "hr_10s": 0,
            "pool": None,
            "online": False,
            "error": str(e)[:120],
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    OUT.write_text(json.dumps(data), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
