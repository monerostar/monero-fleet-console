#!/usr/bin/env python3
"""Privacy lottery share scaffold — peel/add one Hive box privacy↔HashVault.

Weekday helper for the privacy lottery experiment. Watches (or accepts) your
ticket share and moves ONE always-on Hive worker at a time:

  share too high  → peel one box privacy → HashVault (earn steady while share holds)
  share too low   → add one box HashVault → privacy (buy tickets back)
  in band         → silent (exit 0, no stdout)

Default is DRY-RUN (prints the plan). Pass --apply to actually PATCH Hive FS.

Always-on lottery foot (env worker/FS ids):
  3950X, 3600XT, 5700X (24/7 soon; offline OK while placing)
Flex (only flipped if currently online + already in the pairs):
  3700X Asher (~10d/mo when home)

Thresholds (env overrides):
  PRIVACY_SHARE_LOW=0.20   # below → add privacy
  PRIVACY_SHARE_HIGH=0.35  # above → peel to HV
  PRIVACY_WALLET_PREFIX=454qa  # match Top Miners line (optional)

Share input (first match wins):
  1) --share 0.275 or --share 27.5
  2) PRIVACY_SHARE env
  3) --share-file path (single float or JSON {"share": 0.27})
  4) optional scrape stub (off unless PRIVACY_SHARE_SCRAPE=1)

Exit codes:
  0  in band / dry-run plan printed / applied OK / no action needed
  1  apply failed or missing config
  2  no share input and scrape unavailable
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENV_CANDIDATES = [
    Path(os.environ.get("HERMES_TECH_ENV", "")),
    Path.home() / "AppData/Local/hermes/profiles/tech/.env",
    Path(__file__).resolve().parent.parent / ".env",
]


def load_env() -> None:
    # Later files win so tech profile .env overrides a stale project .env.
    for p in ENV_CANDIDATES:
        if not p or not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")


def env_int(name: str) -> int:
    v = os.environ.get(name)
    if not v:
        raise KeyError(name)
    return int(v)


def parse_share(raw: str) -> float:
    s = float(str(raw).strip().replace("%", ""))
    if s > 1.0:
        s = s / 100.0
    if not 0.0 <= s <= 1.0:
        raise ValueError(f"share out of range: {raw}")
    return s


def resolve_share(args: argparse.Namespace) -> float | None:
    if args.share is not None:
        return parse_share(args.share)
    if os.environ.get("PRIVACY_SHARE"):
        return parse_share(os.environ["PRIVACY_SHARE"])
    if args.share_file:
        p = Path(args.share_file)
        text = p.read_text(encoding="utf-8").strip()
        try:
            data = json.loads(text)
            if isinstance(data, dict) and "share" in data:
                return parse_share(data["share"])
        except json.JSONDecodeError:
            pass
        return parse_share(text.split()[0])
    # scrape stub — site often bot-blocks; leave hook for later
    if os.environ.get("PRIVACY_SHARE_SCRAPE", "").strip() in {"1", "true", "yes"}:
        return None  # not implemented reliably yet
    return None


def hive(path: str, method: str = "GET", body: dict | None = None) -> dict:
    token = os.environ["HIVEOS_API_TOKEN"]
    url = f"https://api2.hiveos.farm/api/v2{path}"
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method)
    for k, v in {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0",
        "Origin": "https://the.hiveos.farm",
        "Referer": "https://the.hiveos.farm/",
    }.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=45) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else {}


def worker_state(farm: str, wid: int) -> dict:
    w = hive(f"/farms/{farm}/workers/{wid}")
    fs = w.get("flight_sheet") or {}
    online = bool((w.get("stats") or {}).get("online")) or bool(w.get("active"))
    return {
        "id": wid,
        "name": w.get("name"),
        "online": online,
        "fs_id": fs.get("id"),
        "fs_name": fs.get("name"),
        "ip": w.get("ip_addresses"),
    }


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="Privacy share scaffold (Hive FS peel/add)")
    ap.add_argument("--share", help="Current ticket share 0.27 or 27 or 27%")
    ap.add_argument("--share-file", help="Path to float or JSON {share: ...}")
    ap.add_argument("--apply", action="store_true", help="Actually PATCH Hive (default dry-run)")
    ap.add_argument("--low", type=float, default=None, help="Add privacy below this (default 0.20)")
    ap.add_argument("--high", type=float, default=None, help="Peel to HV above this (default 0.35)")
    args = ap.parse_args()

    low = args.low if args.low is not None else float(os.environ.get("PRIVACY_SHARE_LOW", "0.20"))
    high = args.high if args.high is not None else float(os.environ.get("PRIVACY_SHARE_HIGH", "0.35"))
    if low >= high:
        print("bad thresholds: low must be < high", file=sys.stderr)
        return 1

    try:
        share = resolve_share(args)
    except Exception as e:
        print(f"share parse error: {e}", file=sys.stderr)
        return 1
    if share is None:
        print(
            "no share input (pass --share / PRIVACY_SHARE / --share-file). "
            "scrape not reliable yet.",
            file=sys.stderr,
        )
        return 2

    try:
        farm = os.environ["HIVEOS_FARM_ID"]
        # Always-on ladder. Peel order = biggest ticket chunk first when share high.
        # Add order = reverse (bring smallest/cheapest tickets back first).
        boxes = [
            {
                "key": "5700X",
                "wid": env_int("HIVE_WORKER_5700X"),
                "priv": env_int("HIVE_FS_5700X_PRIV"),
                "hv": env_int("HIVE_FS_5700X_HV"),
                "role": "always",
            },
            {
                "key": "3950X",
                "wid": env_int("HIVE_WORKER_3950X"),
                "priv": env_int("HIVE_FS_3950X_PRIV"),
                "hv": env_int("HIVE_FS_3950X_HV"),
                "role": "always",
            },
            {
                "key": "3600XT",
                "wid": env_int("HIVE_WORKER_3600XT"),
                "priv": env_int("HIVE_FS_3600XT_PRIV"),
                "hv": env_int("HIVE_FS_3600XT_HV"),
                "role": "always",
            },
            {
                "key": "3700X",
                "wid": env_int("HIVE_WORKER_3700X"),
                "priv": env_int("HIVE_FS_3700X_PRIV"),
                "hv": env_int("HIVE_FS_3700X_HV"),
                "role": "flex",  # Asher — only if online
            },
        ]
    except KeyError as e:
        print(f"missing env {e}", file=sys.stderr)
        return 1

    # Live FS state
    live = []
    for b in boxes:
        try:
            st = worker_state(farm, b["wid"])
        except Exception as e:
            st = {"id": b["wid"], "name": b["key"], "online": False, "fs_id": None, "error": str(e)}
        live.append({**b, **st})

    on_priv = [b for b in live if b.get("online") and b.get("fs_id") == b["priv"]]
    on_hv = [b for b in live if b.get("online") and b.get("fs_id") == b["hv"]]
    offline = [b for b in live if not b.get("online")]

    mode = "apply" if args.apply else "dry-run"
    header = f"share={share:.1%} band=[{low:.0%},{high:.0%}] mode={mode}"

    if low <= share <= high:
        # silent success when in band (cron-friendly); still print if dry-run verbose wanted
        if not args.apply:
            print(f"IN_BAND {header}")
            for b in live:
                print(
                    f"  {b['key']:7} online={b.get('online')} fs={b.get('fs_name')} role={b['role']}"
                )
        return 0

    action = None
    target = None
    new_fs = None
    label = None

    if share > high:
        # Peel: privacy → HV. Prefer always-on big chunks first (5700 then 3950 then 3600).
        # Flex 3700 only if it's the only privacy box left online.
        peel_order = [b for b in live if b["key"] in ("5700X", "3950X", "3600XT", "3700X")]
        for b in peel_order:
            if b.get("online") and b.get("fs_id") == b["priv"]:
                action, target, new_fs, label = "peel_to_hv", b, b["hv"], f"{b['key']} → HV"
                break
        if target is None:
            print(f"NO_PEEL_CANDIDATE {header} (nobody on privacy)")
            return 0
    else:
        # share < low: add privacy. Prefer always-on currently on HV; skip offline flex.
        add_order = [b for b in live if b["key"] in ("3600XT", "3950X", "5700X", "3700X")]
        for b in add_order:
            if b.get("online") and b.get("fs_id") == b["hv"]:
                action, target, new_fs, label = "add_privacy", b, b["priv"], f"{b['key']} → privacy"
                break
        if target is None:
            print(f"NO_ADD_CANDIDATE {header} (nobody on HV to pull back)")
            return 0

    plan = f"{action} {label} share={share:.1%} ({mode})"
    print(plan)
    print(
        f"  worker={target['key']} id={target['wid']} "
        f"from_fs={target.get('fs_id')} to_fs={new_fs}"
    )
    if offline:
        print("  offline (skipped): " + ", ".join(b["key"] for b in offline))

    if not args.apply:
        print("DRY_RUN (pass --apply to execute)")
        return 0

    try:
        hive(f"/farms/{farm}/workers/{target['wid']}", method="PATCH", body={"fs_id": new_fs})
    except urllib.error.HTTPError as e:
        print(f"APPLY_FAIL HTTP {e.code}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"APPLY_FAIL {e}", file=sys.stderr)
        return 1

    print(f"APPLIED {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
