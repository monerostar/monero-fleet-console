"""Tunnel-path monerod health (offline, stdlib only).

Proves RPC via ssh-tunnel-5800x is healthy even when monerod.exe is missing
on Windows. No network, no tokens, no fleet.local.json required.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fleet_console.monerod_node import (
    MoneroNodeSnapshot,
    detect_endpoint,
    fetch_monerod,
    monerod_to_dict,
)

FETCHED = "2026-08-19T13:00:00-06:00"

RPC_INFO = {
    "height": 3_500_000,
    "target_height": 3_500_000,
    "synchronized": True,
    "busy_syncing": False,
    "outgoing_connections_count": 8,
    "incoming_connections_count": 2,
    "version": "0.18.4.0",
    "database_size": 200 * 1024**3,
    "nettype": "mainnet",
    "tx_count": 42,
    "offline": False,
    "update_available": False,
}


def _missing_exe(td: str) -> Path:
    return Path(td) / "not-installed" / "monerod.exe"


class TunnelEndpointTest(unittest.TestCase):
    def test_rpc_plus_tunnel_is_ssh_tunnel_5800x_without_local_exe(self) -> None:
        with patch("fleet_console.monerod_node.tunnel_listening", return_value=True):
            self.assertEqual(detect_endpoint(True, False), "ssh-tunnel-5800x")

    def test_local_exe_wins_when_both_paths_work(self) -> None:
        with patch("fleet_console.monerod_node.tunnel_listening", return_value=True):
            self.assertEqual(detect_endpoint(True, True), "local-monerod")


class TunnelFetchHealthTest(unittest.TestCase):
    def test_fetch_healthy_when_rpc_answers_via_tunnel_and_exe_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            binary = _missing_exe(td)
            self.assertFalse(binary.is_file())
            with (
                patch("fleet_console.monerod_node.process_running", return_value=False),
                patch("fleet_console.monerod_node.tunnel_listening", return_value=True),
                patch("fleet_console.monerod_node._rpc", return_value=dict(RPC_INFO)),
            ):
                snap = fetch_monerod(
                    rpc_url="http://127.0.0.1:18081/json_rpc",
                    data_dir=Path(td),
                    binary=binary,
                )

        self.assertTrue(snap.api_ok)
        self.assertIsNone(snap.error)
        self.assertEqual(snap.endpoint, "ssh-tunnel-5800x")
        self.assertTrue(snap.process_running)  # path_up: tunnel counts
        self.assertFalse(snap.binary_present)
        self.assertEqual(snap.status_label, "SYNCED")
        self.assertNotEqual(snap.status_label, "DOWN")

        dumped = monerod_to_dict(snap)
        self.assertEqual(dumped["endpoint"], "ssh-tunnel-5800x")
        self.assertEqual(dumped["status"], "SYNCED")
        self.assertTrue(dumped["api_ok"])
        self.assertTrue(dumped["process_running"])
        self.assertFalse(dumped["binary_present"])

    def test_snapshot_label_is_up_without_local_exe(self) -> None:
        snap = MoneroNodeSnapshot(
            fetched_at=FETCHED,
            api_ok=True,
            error=None,
            process_running=True,
            rpc_url="http://127.0.0.1:18081/json_rpc",
            synchronized=True,
            behind=0,
            binary_present=False,
            endpoint="ssh-tunnel-5800x",
        )
        self.assertEqual(snap.status_label, "SYNCED")
        self.assertFalse(snap.binary_present)


if __name__ == "__main__":
    unittest.main()
