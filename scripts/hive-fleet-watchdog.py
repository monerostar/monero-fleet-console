#!/usr/bin/env python3
"""Fail-only Hive fleet watchdog for cron (--no-agent style).

Silent when configured always-on rigs are healthy (or same fingerprint
already delivered). Prints a short report when they go offline, overheat,
or drop below hashrate floors. On-demand / flex offline is normal.

Exit code contract:
  0  healthy, or same alert fingerprint as last run (deduped, silent)
  1  fresh alert printed to stdout — something needs a human
  2  repo/env not found

Environment:
  HIVEOS_API_TOKEN, HIVEOS_FARM_ID  (or project .env)
  FLEET_CONSOLE_ROOT  optional override to repo path
  FLEET_CONFIG         optional fleet.local.json path
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Resolve monero-fleet-console repo
CANDIDATES = []
if os.environ.get("FLEET_CONSOLE_ROOT"):
    CANDIDATES.append(Path(os.environ["FLEET_CONSOLE_ROOT"]))
CANDIDATES.extend(
    [
        Path(__file__).resolve().parent.parent,
        Path.home() / "src" / "monero-fleet-console",
    ]
)

root = next((p for p in CANDIDATES if (p / "fleet_console").is_dir()), None)
if root is None:
    print("Monero fleet watchdog: monero-fleet-console not found", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, str(root))

from fleet_console.config import default_state_path  # noqa: E402
from fleet_console.hive import fetch_fleet  # noqa: E402
from fleet_console.watchdog import run_watchdog  # noqa: E402


def main() -> int:
    force = os.environ.get("FLEET_WATCHDOG_FORCE", "").strip() in {"1", "true", "yes"}
    alerts_only = os.environ.get("FLEET_WATCHDOG_ALERTS_ONLY", "").strip() in {
        "1",
        "true",
        "yes",
    }
    snap = fetch_fleet()
    code, out = run_watchdog(
        snap,
        default_state_path(),
        include_warns=not alerts_only,
        force=force,
    )
    if out:
        # stdout only — no_agent / cron delivers non-empty stdout
        sys.stdout.write(out)
        if not out.endswith("\n"):
            sys.stdout.write("\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
