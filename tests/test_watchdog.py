"""Watchdog exit-code contract tests (offline, stdlib only).

Proves the fail-only promise: exit 0 = healthy or deduped, exit 1 = fresh
alert. No network, no tokens, no fleet.local.json required.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fleet_console.hashvault import HashVaultSnapshot
from fleet_console.hive import FleetSnapshot, WorkerSnapshot
from fleet_console.monerod_node import MoneroNodeSnapshot
from fleet_console.watchdog import run_watchdog

FETCHED = "2026-08-02T20:00:00-06:00"


def healthy_snap() -> FleetSnapshot:
    return FleetSnapshot(
        fetched_at=FETCHED,
        farm_id=1,
        farm_name="test",
        workers=[],
        api_ok=True,
        error=None,
    )


def alert_snap() -> FleetSnapshot:
    w = WorkerSnapshot(
        worker_id=1,
        name="rig-1",
        role="always_on",
        owner="",
        note="",
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
        alerts=["24/7 worker offline"],
        warns=[],
    )
    return FleetSnapshot(
        fetched_at=FETCHED,
        farm_id=1,
        farm_name="test",
        workers=[w],
        api_ok=True,
        error=None,
    )


class WatchdogExitCodeTest(unittest.TestCase):
    def test_healthy_is_silent_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            code, out = run_watchdog(healthy_snap(), Path(td) / "state.json")
            self.assertEqual((code, out), (0, ""))

    def test_fresh_alert_exits_one(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            code, out = run_watchdog(alert_snap(), Path(td) / "state.json")
            self.assertEqual(code, 1)
            self.assertIn("ALERT", out)

    def test_deduped_alert_is_silent_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            code1, _ = run_watchdog(alert_snap(), state)
            self.assertEqual(code1, 1)
            code2, out2 = run_watchdog(alert_snap(), state)
            self.assertEqual((code2, out2), (0, ""))

    def test_force_repeats_alert(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state = Path(td) / "state.json"
            run_watchdog(alert_snap(), state)
            code, out = run_watchdog(alert_snap(), state, force=True)
            self.assertEqual(code, 1)
            self.assertIn("ALERT", out)

    def test_monerod_down_alerts(self) -> None:
        node = MoneroNodeSnapshot(
            fetched_at=FETCHED,
            api_ok=False,
            error="connection refused",
            process_running=False,
            rpc_url="http://127.0.0.1:18081/json_rpc",
        )
        with tempfile.TemporaryDirectory() as td:
            code, out = run_watchdog(alert_snap(), Path(td) / "state.json", node=node)
            self.assertEqual(code, 1)
            self.assertIn("monerod", out)

    def test_hv_ok_hv_worker_offline_alert(self) -> None:
        from fleet_console.hashvault import HashVaultWorker

        hv = HashVaultSnapshot(
            fetched_at=FETCHED,
            api_ok=True,
            error=None,
            wallet_masked="abc…wxyz",
            hashrate_khs=15.0,
            workers=[
                HashVaultWorker(
                    name="RIG-1",
                    online=False,
                    hashrate_khs=None,
                    avg1_khs=None,
                    avg24_khs=None,
                    valid_shares=None,
                    invalid_shares=None,
                    stale_shares=None,
                    last_share_ts=None,
                )
            ],
        )
        # Patch the watchdog module's own binding so RIG-1 counts as
        # always_on for the pool check (config globals are import-time copies).
        import fleet_console.watchdog as wd

        old = wd.ALWAYS_ON_POOL_NAMES
        wd.ALWAYS_ON_POOL_NAMES = frozenset({"RIG-1"})
        try:
            with tempfile.TemporaryDirectory() as td:
                code, out = run_watchdog(healthy_snap(), Path(td) / "state.json", hv=hv)
                self.assertEqual(code, 1)
                self.assertIn("HashVault worker offline", out)
        finally:
            wd.ALWAYS_ON_POOL_NAMES = old


if __name__ == "__main__":
    unittest.main()
