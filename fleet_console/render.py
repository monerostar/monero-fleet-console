"""CLI tables + dark single-file HTML dashboard (Hive + HashVault)."""

from __future__ import annotations

import html
import json
from pathlib import Path

from .hashvault import HashVaultSnapshot
from .hive import FleetSnapshot, WorkerSnapshot
from .monerod_node import MoneroNodeSnapshot, monerod_to_dict


def _fmt_khs_val(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.2f}"


def _fmt_khs(w: WorkerSnapshot) -> str:
    return _fmt_khs_val(w.hashrate_khs)


def _fmt_temp(w: WorkerSnapshot) -> str:
    if w.cpu_temp_c is None:
        return "—"
    return f"{w.cpu_temp_c:.0f}°C"


def _fmt_shares(w: WorkerSnapshot) -> str:
    if w.accepted is None and w.rejected is None:
        return "—"
    a = w.accepted if w.accepted is not None else 0
    r = w.rejected if w.rejected is not None else 0
    return f"{a}/{r}"


def _fmt_xmr(v: float | None) -> str:
    if v is None:
        return "—"
    return f"{v:.6f}"


def _render_node_section(node: MoneroNodeSnapshot) -> list[str]:
    lines = [
        "",
        "=== Local monerod (node) ===",
        f"Fetched: {node.fetched_at}",
        f"Status: {node.status_label}",
    ]
    if not node.api_ok:
        lines.append(f"RPC: {node.error} · process={'yes' if node.process_running else 'no'}")
        lines.append(f"Binary: {'ok' if node.binary_present else 'missing'} · data {node.data_dir or '—'}")
        return lines
    lines.append(
        f"Height: {node.height}/{node.target_height} "
        f"(behind {node.behind}) · synced={node.synchronized} · busy={node.busy_syncing}"
    )
    lines.append(
        f"Peers: out {node.peers_out} / in {node.peers_in} · "
        f"txs {node.tx_count} · db {node.database_gb} GB · {node.version or '—'}"
    )
    lines.append(f"RPC {node.rpc_url} · data {node.data_dir or '—'}")
    return lines


def render_table(
    snap: FleetSnapshot,
    hv: HashVaultSnapshot | None = None,
    node: MoneroNodeSnapshot | None = None,
) -> str:
    lines = []
    title = snap.farm_name or f"farm {snap.farm_id}"
    lines.append(f"Monero Fleet — {title}")
    lines.append(f"Fetched: {snap.fetched_at}")

    if node is not None:
        lines.extend(_render_node_section(node))

    if not snap.api_ok:
        lines.append(f"Hive API ERROR: {snap.error}")
    else:
        header = (
            f"{'STATUS':<7} {'NAME':<10} {'ROLE':<11} {'ONLINE':<7} "
            f"{'kH/s':>8} {'TEMP':>6} {'SHARES':>10} {'IP':<15} FS/MINER"
        )
        lines.append("")
        lines.append("=== Hive OS (rig health) ===")
        lines.append(header)
        lines.append("-" * len(header))
        for w in snap.workers:
            online = "yes" if w.online else "no"
            extra = w.flight_sheet or w.miner or ""
            if w.miner and w.flight_sheet:
                extra = f"{w.flight_sheet} · {w.miner}"
            lines.append(
                f"{w.status_label:<7} {w.name:<10} {w.role:<11} {online:<7} "
                f"{_fmt_khs(w):>8} {_fmt_temp(w):>6} {_fmt_shares(w):>10} "
                f"{(w.lan_ip or '—'):<15} {extra}"
            )

        issues = []
        for w in snap.workers:
            for a in w.alerts:
                issues.append(f"ALERT  {w.name}: {a}")
            for wn in w.warns:
                issues.append(f"WARN   {w.name}: {wn}")
        if issues:
            lines.append("")
            lines.append("Hive issues:")
            lines.extend(issues)
        else:
            lines.append("")
            lines.append("Hive issues: none")

        lines.append(
            f"Hive summary: {snap.alert_count} alert(s), {snap.warn_count} warn(s), "
            f"{sum(1 for w in snap.workers if w.online)} online / {len(snap.workers)} tracked"
        )

    if hv is not None:
        lines.append("")
        lines.append("=== HashVault (pool edge) ===")
        lines.append(f"Fetched: {hv.fetched_at}")
        if not hv.api_ok:
            lines.append(f"HashVault API ERROR: {hv.error}")
        else:
            if hv.wallet_masked:
                lines.append(f"Wallet: {hv.wallet_masked}")
            lines.append(
                f"Wallet HR: {_fmt_khs_val(hv.hashrate_khs)} kH/s  "
                f"(1h {_fmt_khs_val(hv.avg1_khs)} · 24h {_fmt_khs_val(hv.avg24_khs)})"
            )
            lines.append(
                f"Balance: confirmed {_fmt_xmr(hv.confirmed_xmr)} XMR · "
                f"unconfirmed {_fmt_xmr(hv.unconfirmed_xmr)} · "
                f"paid today {_fmt_xmr(hv.daily_paid_xmr)} · "
                f"threshold {_fmt_xmr(hv.payout_threshold_xmr)}"
            )
            if hv.pool_hashrate_mhs is not None:
                lines.append(
                    f"Pool: {hv.pool_hashrate_mhs:.0f} MH/s · "
                    f"{hv.pool_miners or '—'} miners · "
                    f"effort {hv.pool_effort_pct or '—'}% · "
                    f"height {hv.network_height or '—'}"
                )
                if node and node.api_ok and node.height and hv.network_height:
                    delta = hv.network_height - node.height
                    lines.append(
                        f"Node vs pool tip: local {node.height} · pool/net {hv.network_height} · Δ {delta}"
                    )
            if hv.xmr_usd is not None:
                lines.append(f"XMR/USD: {hv.xmr_usd:.2f}")
            if hv.workers:
                h = (
                    f"{'NAME':<12} {'ROLE':<11} {'ON':<5} {'kH/s':>8} "
                    f"{'1h':>8} {'24h':>8} {'V/I/S':>12} NOTE"
                )
                lines.append(h)
                lines.append("-" * len(h))
                for w in hv.workers:
                    if w.expected and not w.online:
                        on = "exp"
                    else:
                        on = "yes" if w.online else "no"
                    shares = (
                        f"{w.valid_shares or 0}/{w.invalid_shares or 0}/{w.stale_shares or 0}"
                        if not w.expected
                        else "—/—/—"
                    )
                    note = (w.note or "")[:40]
                    lines.append(
                        f"{w.name:<12} {w.role:<11} {on:<5} {_fmt_khs_val(w.hashrate_khs):>8} "
                        f"{_fmt_khs_val(w.avg1_khs):>8} {_fmt_khs_val(w.avg24_khs):>8} "
                        f"{shares:>12} {note}"
                    )
    return "\n".join(lines)


def snapshot_to_dict(
    snap: FleetSnapshot,
    hv: HashVaultSnapshot | None = None,
    node: MoneroNodeSnapshot | None = None,
) -> dict:
    data: dict = {
        "fetched_at": snap.fetched_at,
        "farm_id": snap.farm_id,
        "farm_name": snap.farm_name,
        "api_ok": snap.api_ok,
        "error": snap.error,
        "alert_count": snap.alert_count,
        "warn_count": snap.warn_count,
        "workers": [
            {
                "id": w.worker_id,
                "name": w.name,
                "role": w.role,
                "owner": w.owner,
                "note": w.note,
                "status": w.status_label,
                "online": w.online,
                "lan_ip": w.lan_ip,
                "flight_sheet": w.flight_sheet,
                "miner": w.miner,
                "algo": w.algo,
                "hashrate_khs": w.hashrate_khs,
                "cpu_temp_c": w.cpu_temp_c,
                "accepted": w.accepted,
                "rejected": w.rejected,
                "share_ratio": w.share_ratio,
                "needs_upgrade": w.needs_upgrade,
                "alerts": w.alerts,
                "warns": w.warns,
            }
            for w in snap.workers
        ],
    }
    if hv is not None:
        data["hashvault"] = {
            "fetched_at": hv.fetched_at,
            "api_ok": hv.api_ok,
            "error": hv.error,
            "wallet_masked": hv.wallet_masked,
            "hashrate_khs": hv.hashrate_khs,
            "avg1_khs": hv.avg1_khs,
            "avg24_khs": hv.avg24_khs,
            "confirmed_xmr": hv.confirmed_xmr,
            "unconfirmed_xmr": hv.unconfirmed_xmr,
            "total_paid_xmr": hv.total_paid_xmr,
            "daily_paid_xmr": hv.daily_paid_xmr,
            "payout_threshold_xmr": hv.payout_threshold_xmr,
            "pool_hashrate_mhs": hv.pool_hashrate_mhs,
            "pool_miners": hv.pool_miners,
            "pool_effort_pct": hv.pool_effort_pct,
            "network_height": hv.network_height,
            "xmr_usd": hv.xmr_usd,
            "workers": [
                {
                    "name": w.name,
                    "online": w.online,
                    "hashrate_khs": w.hashrate_khs,
                    "avg1_khs": w.avg1_khs,
                    "avg24_khs": w.avg24_khs,
                    "valid_shares": w.valid_shares,
                    "invalid_shares": w.invalid_shares,
                    "stale_shares": w.stale_shares,
                    "role": w.role,
                    "owner": w.owner,
                    "note": w.note,
                    "os": w.os_label,
                    "expected": w.expected,
                    "canonical": w.canonical,
                }
                for w in hv.workers
            ],
        }
    if node is not None:
        data["monerod"] = monerod_to_dict(node)
    return data


def render_json(
    snap: FleetSnapshot,
    hv: HashVaultSnapshot | None = None,
    node: MoneroNodeSnapshot | None = None,
    pretty: bool = True,
) -> str:
    data = snapshot_to_dict(snap, hv, node)
    if pretty:
        return json.dumps(data, indent=2)
    return json.dumps(data, separators=(",", ":"))


def _status_class(label: str) -> str:
    return {
        "OK": "ok",
        "WARN": "warn",
        "ALERT": "alert",
        "DOWN": "alert",
        "OFF": "off",
        "SYNCED": "ok",
        "SYNCING": "warn",
        "STARTING": "warn",
        "UP": "ok",
        "UNKNOWN": "off",
    }.get(label, "off")


def render_html(
    snap: FleetSnapshot,
    hv: HashVaultSnapshot | None = None,
    node: MoneroNodeSnapshot | None = None,
) -> str:
    data_json = html.escape(render_json(snap, hv, node, pretty=False))
    cards = []

    # Local node card first
    if node is not None:
        sc = _status_class(node.status_label)
        if node.api_ok:
            node_body = f"""
  <dl>
    <div><dt>Height</dt><dd>{node.height} / {node.target_height}</dd></div>
    <div><dt>Behind</dt><dd>{node.behind}</dd></div>
    <div><dt>Synced</dt><dd>{'yes' if node.synchronized else 'no'}</dd></div>
    <div><dt>Peers out/in</dt><dd>{node.peers_out} / {node.peers_in}</dd></div>
    <div><dt>DB size</dt><dd>{node.database_gb} GB</dd></div>
    <div><dt>Version</dt><dd>{html.escape(node.version or '—')}</dd></div>
    <div><dt>RPC</dt><dd>{html.escape(node.rpc_url)}</dd></div>
    <div><dt>Data</dt><dd>{html.escape(node.data_dir or '—')}</dd></div>
  </dl>
  <p class="note">Local monerod · leave running for wallet/privacy</p>"""
        else:
            node_body = f"""
  <dl>
    <div><dt>Process</dt><dd>{'yes' if node.process_running else 'no'}</dd></div>
    <div><dt>Binary</dt><dd>{'ok' if node.binary_present else 'missing'}</dd></div>
    <div><dt>Data</dt><dd>{html.escape(node.data_dir or '—')}</dd></div>
    <div><dt>Error</dt><dd>{html.escape(node.error or '—')}</dd></div>
  </dl>
  <p class="note">Start: <code>python fleet_status.py --start-node</code> or main monerod-start.py</p>"""
        cards.append(
            f"""
<article class="card {sc}">
  <header>
    <span class="badge">{html.escape(node.status_label)}</span>
    <h2>monerod</h2>
    <p class="meta">local node · K:\\Monero</p>
  </header>
  {node_body}
</article>"""
        )

    for w in snap.workers:
        sc = _status_class(w.status_label)
        issues = "".join(
            f'<li class="alert">{html.escape(a)}</li>' for a in w.alerts
        ) + "".join(f'<li class="warn">{html.escape(x)}</li>' for x in w.warns)
        if not issues:
            issues = '<li class="muted">No issues</li>'
        hv_line = ""
        if hv and hv.api_ok:
            for hw in hv.workers:
                if hw.name.lower() == w.name.lower():
                    hv_line = (
                        f"<div><dt>Pool kH/s</dt><dd>{html.escape(_fmt_khs_val(hw.hashrate_khs))}"
                        f" <span class='muted'>(HV)</span></dd></div>"
                    )
                    break
        cards.append(
            f"""
<article class="card {sc}">
  <header>
    <span class="badge">{html.escape(w.status_label)}</span>
    <h2>{html.escape(w.name)}</h2>
    <p class="meta">{html.escape(w.role)} · {html.escape(w.owner or '—')} · Hive</p>
  </header>
  <dl>
    <div><dt>Online</dt><dd>{'yes' if w.online else 'no'}</dd></div>
    <div><dt>Hive kH/s</dt><dd>{html.escape(_fmt_khs(w))}</dd></div>
    {hv_line}
    <div><dt>CPU temp</dt><dd>{html.escape(_fmt_temp(w))}</dd></div>
    <div><dt>Shares</dt><dd>{html.escape(_fmt_shares(w))}</dd></div>
    <div><dt>LAN</dt><dd>{html.escape(w.lan_ip or '—')}</dd></div>
    <div><dt>Flight</dt><dd>{html.escape(w.flight_sheet or '—')}</dd></div>
    <div><dt>Miner</dt><dd>{html.escape(w.miner or '—')}</dd></div>
  </dl>
  <p class="note">{html.escape(w.note or '')}</p>
  <ul class="issues">{issues}</ul>
</article>"""
        )

    # HashVault-only / expected seats (not already rendered as Hive cards)
    hive_names = {w.name.lower() for w in snap.workers}
    if hv and hv.api_ok:
        for hw in hv.workers:
            if hw.name.lower() in hive_names:
                continue
            if hw.expected and not hw.online:
                sc = "off"
                badge = "EXP"
            elif hw.online:
                sc = "ok"
                badge = "OK"
            else:
                sc = "off"
                badge = "OFF"
            role_line = f"{hw.role} · {hw.owner or '—'} · {hw.os_label or 'pool'}"
            cards.append(
                f"""
<article class="card {sc}">
  <header>
    <span class="badge">{badge}</span>
    <h2>{html.escape(hw.name)}</h2>
    <p class="meta">{html.escape(role_line)}</p>
  </header>
  <dl>
    <div><dt>Online</dt><dd>{'yes' if hw.online else ('expected' if hw.expected else 'no')}</dd></div>
    <div><dt>Pool kH/s</dt><dd>{html.escape(_fmt_khs_val(hw.hashrate_khs))}</dd></div>
    <div><dt>1h avg</dt><dd>{html.escape(_fmt_khs_val(hw.avg1_khs))}</dd></div>
    <div><dt>24h avg</dt><dd>{html.escape(_fmt_khs_val(hw.avg24_khs))}</dd></div>
    <div><dt>Shares V/I/S</dt><dd>{'—' if hw.expected else f'{hw.valid_shares or 0}/{hw.invalid_shares or 0}/{hw.stale_shares or 0}'}</dd></div>
  </dl>
  <p class="note">{html.escape(hw.note or ('HashVault worker' if not hw.expected else 'Reserved seat — will light up when mining'))}</p>
</article>"""
            )

    err_block = ""
    if not snap.api_ok:
        err_block += f'<div class="banner alert">Hive API error: {html.escape(snap.error or "unknown")}</div>'
    if hv is not None and not hv.api_ok:
        err_block += f'<div class="banner alert">HashVault API error: {html.escape(hv.error or "unknown")}</div>'
    if node is not None and not node.api_ok:
        err_block += f'<div class="banner alert">monerod: {html.escape(node.error or "down")}</div>'

    farm = html.escape(snap.farm_name or f"farm {snap.farm_id}")

    pool_panel = ""
    if hv and hv.api_ok:
        conf_usd = ""
        if hv.confirmed_xmr is not None and hv.xmr_usd is not None:
            conf_usd = f" (~${hv.confirmed_xmr * hv.xmr_usd:.2f})"
        node_vs = ""
        if node and node.api_ok and node.height and hv.network_height:
            node_vs = f'<div class="stat"><span class="lbl">Node Δ tip</span><span class="val">{hv.network_height - node.height} blk</span></div>'
        pool_panel = f"""
<section class="pool">
  <h2>HashVault</h2>
  <div class="pool-grid">
    <div class="stat"><span class="lbl">Wallet</span><span class="val">{html.escape(hv.wallet_masked or '—')}</span></div>
    <div class="stat"><span class="lbl">Wallet HR</span><span class="val">{html.escape(_fmt_khs_val(hv.hashrate_khs))} kH/s</span></div>
    <div class="stat"><span class="lbl">1h / 24h</span><span class="val">{html.escape(_fmt_khs_val(hv.avg1_khs))} / {html.escape(_fmt_khs_val(hv.avg24_khs))}</span></div>
    <div class="stat"><span class="lbl">Confirmed</span><span class="val">{html.escape(_fmt_xmr(hv.confirmed_xmr))} XMR{html.escape(conf_usd)}</span></div>
    <div class="stat"><span class="lbl">Unconfirmed</span><span class="val">{html.escape(_fmt_xmr(hv.unconfirmed_xmr))} XMR</span></div>
    <div class="stat"><span class="lbl">Paid today</span><span class="val">{html.escape(_fmt_xmr(hv.daily_paid_xmr))} XMR</span></div>
    <div class="stat"><span class="lbl">Pool</span><span class="val">{html.escape(f'{(hv.pool_hashrate_mhs or 0):.0f}' if hv.pool_hashrate_mhs is not None else '—')} MH/s · {hv.pool_miners or '—'} miners</span></div>
    <div class="stat"><span class="lbl">Effort / height</span><span class="val">{html.escape(str(hv.pool_effort_pct or '—'))}% · {hv.network_height or '—'}</span></div>
    {node_vs}
  </div>
</section>"""

    node_pill = "—"
    if node is not None:
        node_pill = node.status_label

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Monero Fleet Console</title>
<style>
  :root {{
    --bg: #0b0f14;
    --panel: #121821;
    --text: #e7eef7;
    --muted: #8b9bb0;
    --ok: #3dd68c;
    --warn: #f5c542;
    --alert: #ff6b6b;
    --off: #6b7c93;
    --line: #1e2836;
    --accent: #7aa2ff;
    --pool: #f39c4a;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: "Segoe UI", system-ui, sans-serif;
    background: radial-gradient(1200px 600px at 10% -10%, #152033 0%, var(--bg) 55%);
    color: var(--text); min-height: 100vh;
  }}
  header.top {{
    padding: 1.5rem 1.75rem 0.5rem; display: flex; flex-wrap: wrap;
    gap: 1rem; align-items: flex-end; justify-content: space-between;
  }}
  h1 {{ margin: 0; font-size: 1.5rem; letter-spacing: 0.02em; }}
  .sub {{ color: var(--muted); font-size: 0.95rem; }}
  .stats {{ display: flex; gap: 0.75rem; flex-wrap: wrap; }}
  .pill {{
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 999px; padding: 0.35rem 0.85rem; font-size: 0.85rem;
  }}
  .pill strong {{ color: var(--accent); }}
  .banner {{
    margin: 1rem 1.75rem 0; padding: 0.75rem 1rem; border-radius: 10px;
    border: 1px solid var(--line); background: #2a1518; color: #ffd0d0;
  }}
  .pool {{
    margin: 1rem 1.75rem 0; padding: 1rem 1.2rem; border-radius: 16px;
    border: 1px solid var(--line); background: linear-gradient(180deg,#1a140e,var(--panel));
    border-top: 3px solid var(--pool);
  }}
  .pool h2 {{ margin: 0 0 0.75rem; font-size: 1.1rem; color: var(--pool); }}
  .pool-grid {{
    display: grid; gap: 0.75rem;
    grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  }}
  .stat .lbl {{ display:block; color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; }}
  .stat .val {{ font-weight: 650; font-variant-numeric: tabular-nums; }}
  main {{
    display: grid; gap: 1rem; padding: 1.25rem 1.75rem 2rem;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  }}
  .card {{
    background: linear-gradient(180deg, #151c27 0%, var(--panel) 100%);
    border: 1px solid var(--line); border-radius: 16px; padding: 1rem 1.1rem;
    box-shadow: 0 10px 30px rgba(0,0,0,0.25);
  }}
  .card.ok {{ border-top: 3px solid var(--ok); }}
  .card.warn {{ border-top: 3px solid var(--warn); }}
  .card.alert {{ border-top: 3px solid var(--alert); }}
  .card.off {{ border-top: 3px solid var(--off); }}
  .badge {{
    display: inline-block; font-size: 0.72rem; font-weight: 700;
    letter-spacing: 0.08em; padding: 0.2rem 0.5rem; border-radius: 6px;
    background: #0e131a; color: var(--muted);
  }}
  .card.ok .badge {{ color: var(--ok); }}
  .card.warn .badge {{ color: var(--warn); }}
  .card.alert .badge {{ color: var(--alert); }}
  h2 {{ margin: 0.35rem 0 0.15rem; font-size: 1.35rem; }}
  .meta {{ margin: 0; color: var(--muted); font-size: 0.85rem; }}
  dl {{
    display: grid; grid-template-columns: 1fr 1fr; gap: 0.55rem 0.75rem;
    margin: 1rem 0 0.75rem;
  }}
  dt {{ color: var(--muted); font-size: 0.72rem; text-transform: uppercase; letter-spacing: 0.06em; }}
  dd {{ margin: 0.1rem 0 0; font-variant-numeric: tabular-nums; font-weight: 600; }}
  .note {{ color: var(--muted); font-size: 0.85rem; min-height: 1.2em; }}
  ul.issues {{ margin: 0.5rem 0 0; padding-left: 1.1rem; font-size: 0.88rem; }}
  li.alert {{ color: var(--alert); }}
  li.warn {{ color: var(--warn); }}
  li.muted {{ color: var(--muted); }}
  .muted {{ color: var(--muted); font-weight: 500; font-size: 0.85em; }}
  footer {{
    padding: 0 1.75rem 2rem; color: var(--muted); font-size: 0.8rem;
  }}
  code {{ color: #b7c7ff; }}
</style>
</head>
<body>
  <header class="top">
    <div>
      <h1>Monero Fleet Console</h1>
      <div class="sub">{farm} · monerod + Hive + HashVault</div>
    </div>
    <div class="stats">
      <div class="pill"><strong>{html.escape(node_pill)}</strong> node</div>
      <div class="pill"><strong>{snap.alert_count}</strong> hive alerts</div>
      <div class="pill"><strong>{snap.warn_count}</strong> hive warns</div>
      <div class="pill"><strong>{html.escape(_fmt_khs_val(hv.hashrate_khs) if hv and hv.api_ok else '—')}</strong> pool kH/s</div>
    </div>
  </header>
  {err_block}
  {pool_panel}
  <main>
    {''.join(cards)}
  </main>
  <footer>
    Read-only Hive + HashVault + local monerod RPC · wallet masked · local file only<br/>
    Generate: <code>python fleet_status.py --html</code> · start node: <code>--start-node</code>
  </footer>
  <!-- snapshot: {data_json} -->
</body>
</html>
"""


def write_html(
    snap: FleetSnapshot,
    path: Path,
    hv: HashVaultSnapshot | None = None,
    node: MoneroNodeSnapshot | None = None,
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(snap, hv, node), encoding="utf-8")
    return path
