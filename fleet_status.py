#!/usr/bin/env python3
"""Monero Fleet Console CLI (Hive OS + HashVault + local monerod).

Examples:
  python fleet_status.py
  python fleet_status.py --json
  python fleet_status.py --html --open
  python fleet_status.py --start-node
  python fleet_status.py --watchdog --force
  python fleet_status.py --hive-only
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_console.config import (  # noqa: E402
    default_dashboard_path,
    default_state_path,
    discover_env,
)
from fleet_console.hashvault import fetch_hashvault  # noqa: E402
from fleet_console.hive import fetch_fleet  # noqa: E402
from fleet_console.monerod_node import fetch_monerod, start_monerod  # noqa: E402
from fleet_console.render import render_json, render_table, write_html  # noqa: E402
from fleet_console.watchdog import run_watchdog  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Monero fleet status via Hive OS + HashVault + local monerod"
    )
    p.add_argument("--json", action="store_true", help="JSON snapshot to stdout")
    p.add_argument("--html", action="store_true", help="Write dark HTML dashboard")
    p.add_argument("--out", type=Path, default=None, help="HTML output path")
    p.add_argument("--open", action="store_true", help="Open HTML in default browser")
    p.add_argument(
        "--watchdog",
        action="store_true",
        help="Fail-only mode (silent if healthy/deduped)",
    )
    p.add_argument("--force", action="store_true", help="Ignore watchdog de-dupe")
    p.add_argument(
        "--alerts-only",
        action="store_true",
        help="With --watchdog, ignore WARNs",
    )
    p.add_argument("--state", type=Path, default=None, help="Watchdog state JSON path")
    p.add_argument("--hive-only", action="store_true", help="Skip HashVault pool pull")
    p.add_argument(
        "--pool-only",
        action="store_true",
        help="Skip Hive (HashVault pool/wallet only)",
    )
    p.add_argument(
        "--no-node",
        action="store_true",
        help="Skip local monerod RPC",
    )
    p.add_argument(
        "--start-node",
        action="store_true",
        help="Idempotently start monerod (detached) then continue status",
    )
    args = p.parse_args(argv)

    if args.start_node:
        code, msg = start_monerod()
        if msg:
            print(msg, file=sys.stderr)
        if code != 0:
            print("monerod start failed; continuing status anyway", file=sys.stderr)

    snap = None
    hv = None
    node = None

    if not args.pool_only:
        try:
            discover_env()
        except SystemExit as e:
            print(str(e), file=sys.stderr)
            return 2
        snap = fetch_fleet()
    else:
        from fleet_console.hive import FleetSnapshot

        snap = FleetSnapshot(
            fetched_at="",
            farm_id=0,
            farm_name="(hive skipped)",
            workers=[],
            api_ok=True,
            error=None,
        )

    if not args.hive_only:
        hv = fetch_hashvault()

    if not args.no_node:
        node = fetch_monerod()

    if args.watchdog:
        if args.pool_only:
            print("Watchdog requires Hive data (omit --pool-only).", file=sys.stderr)
            return 2
        state = args.state or default_state_path()
        code, out = run_watchdog(
            snap,
            state,
            include_warns=not args.alerts_only,
            force=args.force,
            hv=hv,
            node=node,
        )
        if out:
            print(out)
        return code

    if args.json:
        print(render_json(snap, hv, node))
    else:
        print(render_table(snap, hv, node))

    if args.html or args.open:
        path = args.out or default_dashboard_path()
        write_html(snap, path, hv, node)
        print(f"\nDashboard: {path}", file=sys.stderr)
        if args.open:
            webbrowser.open(path.resolve().as_uri())

    if snap.alert_count and not args.json:
        return 1
    if not snap.api_ok or (hv is not None and not hv.api_ok and not args.hive_only):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
