#!/usr/bin/env python3
"""fleet-tg.py — Telegram dispatcher backend for privacy lottery <-> HashVault.

Main profile runs this when Dustin texts a fleet command. Reads Hive
credentials + FS IDs from tech .env (never shown in chat). Prints short,
Telegram-friendly replies only.

Usage (invoked by main agent via terminal):
  python fleet-tg.py help
  python fleet-tg.py status
  python fleet-tg.py share 47
  python fleet-tg.py set <box> <privacy|hv>
  python fleet-tg.py peel [--share 0.47] [--apply]
  python fleet-tg.py add  [--share 0.18] [--apply]
  python fleet-tg.py weekend
  python fleet-tg.py monday
  python fleet-tg.py legion on|off

Boxes: 5700 3950 3600 3700
Safety: `set` applies directly (explicit named action). `peel`/`add` are
scaffold moves and stay dry-run unless --apply is passed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

TECH_ENV = Path.home() / "AppData/Local/hermes/profiles/tech/.env"
PROJECT_ENV = Path(__file__).resolve().parent.parent / ".env"
SHARE_FILE = Path(__file__).resolve().parent / ".share-state.txt"


def load_env() -> None:
    """Tech .env wins (later files override) so worker/FS ids stay correct."""
    for p in (PROJECT_ENV, TECH_ENV):
        if not p or not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip().strip('"').strip("'")


def env_int(name: str) -> int:
    return int(os.environ[name])


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


def boxes() -> list[dict]:
    return [
        {"key": "5700", "name": "5700X", "wid": env_int("HIVE_WORKER_5700X"),
         "priv": env_int("HIVE_FS_5700X_PRIV"), "hv": env_int("HIVE_FS_5700X_HV")},
        {"key": "3950", "name": "3950X", "wid": env_int("HIVE_WORKER_3950X"),
         "priv": env_int("HIVE_FS_3950X_PRIV"), "hv": env_int("HIVE_FS_3950X_HV")},
        {"key": "3600", "name": "3600XT", "wid": env_int("HIVE_WORKER_3600XT"),
         "priv": env_int("HIVE_FS_3600XT_PRIV"), "hv": env_int("HIVE_FS_3600XT_HV")},
        {"key": "3700", "name": "3700X", "wid": env_int("HIVE_WORKER_3700X"),
         "priv": env_int("HIVE_FS_3700X_PRIV"), "hv": env_int("HIVE_FS_3700X_HV")},
    ]


def worker_state(farm: str, wid: int) -> dict:
    w = hive(f"/farms/{farm}/workers/{wid}")
    fs = w.get("flight_sheet") or {}
    ms = (w.get("miners_summary") or {}).get("hashrates") or [{}]
    hs = w.get("hardware_stats") or {}
    online = bool((w.get("stats") or {}).get("online")) or bool(w.get("active"))
    return {
        "name": w.get("name"),
        "online": online,
        "fs_id": fs.get("id"),
        "fs_name": fs.get("name"),
        "hr": ms[0].get("hash") if ms else None,
        "temp": hs.get("cputemp"),
    }


HELP = """Fleet commands (phone -> main Telegram):
  fleet help        this table
  fleet status      who's on privacy vs HV + HR
  fleet share 47    remember current ticket share %
  fleet peel        move ONE box privacy -> HV (scaffold)
  fleet add         move ONE box HV -> privacy
  fleet 5700 hv     force 5700X to HashVault
  fleet 5700 priv   force 5700X to privacy
  fleet 3950 hv|priv   same
  fleet 3600 hv|priv   same
  fleet 3700 hv|priv   same (Asher flex, offline OK)
  fleet weekend     run Friday weekend flip now
  fleet monday      run Monday privacy flip now
  fleet legion on|off   test seat only
  peel/add need --apply (or append: go) to execute"""


def cmd_help() -> str:
    return HELP


def read_ssh_status(host: str, path: str, box_label: str, timeout: int = 12) -> str:
    """Cat a per-box fleet-status.json over SSH (written by a local cron/task)."""
    try:
        # Windows hosts use cmd.exe as the SSH shell: quote the path with double
        # quotes inside the remote command; Linux passes the path as-is.
        if host in ("family-7600x", "legion-go"):
            cat_cmd = f'type "{path}"'
        else:
            cat_cmd = f"cat {path}"
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", f"ConnectTimeout={timeout}", host, cat_cmd],
            capture_output=True, text=True, timeout=timeout + 8,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return f"  {box_label:7} UNREACHABLE"
        # PowerShell 5.1 Set-Content -Encoding utf8 writes a UTF-8 BOM; strip it
        text = r.stdout.lstrip("\ufeff").strip()
        data = json.loads(text)
        if not data.get("online"):
            return f"  {box_label:7} OFFLINE   -"
        # hr_10s is H/s; display as kH with one decimal (e.g. 5712.7 -> 5.7kH)
        hr_hs = float(data.get("hr_10s", 0))
        hr_kh = hr_hs / 1000.0
        pool = (data.get("pool") or "?").split(":")[0]
        return f"  {box_label:7} PRIV     {hr_kh:.1f}kH  {pool}"
    except Exception:  # noqa: BLE001
        return f"  {box_label:7} UNREACHABLE"


def cmd_status(farm: str) -> str:
    lines = ["Fleet:"]
    for b in boxes():
        try:
            st = worker_state(farm, b["wid"])
            tag = "OFFLINE"
            if st["online"]:
                tag = "PRIV" if st["fs_id"] == b["priv"] else ("HV" if st["fs_id"] == b["hv"] else st["fs_name"])
            hr = f"{st['hr']:.1f}kH" if st.get("hr") else "-"
            tmp = f" {st['temp']}C" if st.get("temp") else ""
            lines.append(f"  {b['name']:7} {tag:7} {hr}{tmp}")
        except Exception:
            lines.append(f"  {b['name']:7} ERROR")
    # Non-Hive boxes: read per-box status files over SSH
    lines.append("  ---")
    lines.append(read_ssh_status("linux-5800x", "/home/hermes/fleet-status.json", "5800X"))
    lines.append(read_ssh_status("family-7600x", "C:\\xmrig\\fleet-status.json", "Family"))
    lines.append(read_ssh_status("legion-go", "C:\\xmrig\\fleet-status.json", "Legion"))
    return "\n".join(lines)


def cmd_share(args) -> str:
    if args.share is None:
        if SHARE_FILE.exists():
            return f"Last share: {SHARE_FILE.read_text().strip()}%"
        return "No share saved yet. Send: fleet share 47"
    v = args.share
    SHARE_FILE.write_text(v.strip().strip("%"), encoding="utf-8")
    return f"Share set to {v.strip().strip('%')}%"


def cmd_set(args) -> str:
    key = args.box.lower()
    b = next((x for x in boxes() if x["key"] == key), None)
    if not b:
        return f"Unknown box {args.box}. Try 5700, 3950, 3600, 3700."
    target = args.side.lower()
    if target in ("privacy", "priv"):
        fs = b["priv"]
        label = "privacy"
    elif target in ("hv", "hashvault", "hash"):
        fs = b["hv"]
        label = "HashVault"
    else:
        return f"Unknown side {args.side}. Try privacy or hv."
    farm = os.environ["HIVEOS_FARM_ID"]
    hive(f"/farms/{farm}/workers/{b['wid']}", method="PATCH", body={"fs_id": fs})
    return f"✅ {b['name']} -> {label} (FS {fs})"


def scaffold_share() -> float | None:
    if SHARE_FILE.exists():
        try:
            v = float(SHARE_FILE.read_text().strip().replace("%", ""))
            return v / 100.0 if v > 1 else v
        except ValueError:
            pass
    return None


def cmd_peel_add(args, direction: str) -> str:
    farm = os.environ["HIVEOS_FARM_ID"]
    share = scaffold_share()
    share_note = f"share {share:.0%}" if share else "no share saved"
    # pick one box to move
    chosen = None
    for b in boxes():
        st = worker_state(farm, b["wid"])
        if not st["online"]:
            continue
        if direction == "peel" and st["fs_id"] == b["priv"]:
            chosen = (b, st, b["hv"], "HV")
            break
        if direction == "add" and st["fs_id"] == b["hv"]:
            chosen = (b, st, b["priv"], "privacy")
            break
    if not chosen:
        return f"No box to {direction} ({share_note}). Run: fleet status"
    b, st, new_fs, label = chosen
    line = f"{direction}: {b['name']} -> {label} ({share_note})"
    if not args.apply:
        return f"{line}\nDRY-RUN. Send again with: go / --apply"
    hive(f"/farms/{farm}/workers/{b['wid']}", method="PATCH", body={"fs_id": new_fs})
    return f"✅ {line}"


def run_ps_script(name: str) -> str:
    script = Path(__file__).resolve().parent / name
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
        capture_output=True, text=True, timeout=240,
    )
    tail = (r.stdout or r.stderr or "").strip().splitlines()[-6:]
    body = "\n".join(tail) if tail else f"rc={r.returncode}"
    return f"{name}: rc={r.returncode}\n{body}"


def cmd_weekend() -> str:
    return run_ps_script("privacy-weekend-flip.ps1")


def cmd_monday() -> str:
    return run_ps_script("privacy-monday-flip.ps1")


def cmd_legion(args) -> str:
    side = (args.side or "on").lower()
    if side == "off":
        cmd = "schtasks /Delete /tn legion-privacy-6t /f 2>nul & taskkill /F /IM xmrig.exe /T 2>nul & echo LEGION_OFF"
    else:
        cmd = ("taskkill /F /IM xmrig.exe /T 2>nul & schtasks /Delete /tn legion-privacy-6t /f 2>nul & "
               "schtasks /Create /tn legion-privacy-6t /tr \"C:\\xmrig\\xmrig-6.26.0\\xmrig.exe -c C:\\xmrig\\xmrig-6.26.0\\config-6t-privacy.json\" "
               "/sc once /st 00:00 /ru SYSTEM /rl HIGHEST /f & schtasks /Run /tn legion-privacy-6t & echo LEGION_ON")
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12", "legion-go", cmd],
                       capture_output=True, text=True, timeout=90)
    out = (r.stdout or "").strip().splitlines()
    ok = any("LEGION_ON" in o or "LEGION_OFF" in o for o in out) or r.returncode == 0
    return f"Legion {side}: {'OK' if ok else 'failed rc=' + str(r.returncode)}"


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description="fleet-tg dispatcher")
    ap.add_argument("action", choices=["help", "status", "share", "set", "peel", "add", "weekend", "monday", "legion"])
    ap.add_argument("box", nargs="?", default=None)
    ap.add_argument("side", nargs="?", default=None)
    ap.add_argument("--share", default=None)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    # allow natural "go" as apply
    if args.box and args.box.lower() in ("go", "apply"):
        args.apply = True
        args.box = None
    if args.side and args.side.lower() in ("go", "apply"):
        args.apply = True
        args.side = None

    # `fleet share 47` — positional lands in box, treat as share value
    if args.action == "share" and args.box is not None:
        args.share = args.box
        args.box = None

    try:
        farm = os.environ["HIVEOS_FARM_ID"]
    except KeyError:
        print("Missing HIVEOS_FARM_ID (tech .env not loaded)", file=sys.stderr)
        return 1

    try:
        if args.action == "help":
            print(cmd_help())
        elif args.action == "status":
            print(cmd_status(farm))
        elif args.action == "share":
            print(cmd_share(args))
        elif args.action == "set":
            print(cmd_set(args))
        elif args.action == "peel":
            print(cmd_peel_add(args, "peel"))
        elif args.action == "add":
            print(cmd_peel_add(args, "add"))
        elif args.action == "weekend":
            print(cmd_weekend())
        elif args.action == "monday":
            print(cmd_monday())
        elif args.action == "legion":
            print(cmd_legion(args))
        return 0
    except urllib.error.HTTPError as e:
        print(f"API error HTTP {e.code}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
