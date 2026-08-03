# Monero Fleet Console

Read-only **Hive OS + HashVault + local monerod** status toolkit for quiet Monero fleet ops.

CLI tables, JSON snapshots, a dark single-file HTML dashboard, and a fail-only watchdog suitable for cron.

**Owner:** [monerostar](https://github.com/monerostar)  
**License:** MIT

| Piece | What |
|--------|------|
| `fleet_status.py` | CLI table / JSON / HTML dashboard |
| `fleet_console/` | Hive, HashVault, monerod RPC, render, fail-only watchdog |
| `scripts/hive-fleet-watchdog.py` | Cron entrypoint (stdout only when unhealthy) |
| `fleet.example.json` | Template worker/role map |
| `.env.example` | Env var names (no secrets) |

## Safety

- **Never commit** API tokens, full wallet addresses, or your real `fleet.local.json`
- Dashboard masks wallets (`abcd12…wxyz`)
- Read-only Hive GET + public HashVault GET + local monerod RPC
- HTML is a local file only (no server)

## Requirements

- Python 3.11+ (stdlib only — no pip deps)
- `HIVEOS_API_TOKEN` + `HIVEOS_FARM_ID` in `.env` or the environment
- Optional: `HASHVAULT_WALLET` for pool worker/wallet stats
- Optional: `fleet.local.json` (copy from `fleet.example.json`) for roles, temp floors, reserved seats

## Quick start

```bash
git clone https://github.com/monerostar/monero-fleet-console.git
cd monero-fleet-console
cp .env.example .env
# edit .env — token + farm id (+ optional wallet)

cp fleet.example.json fleet.local.json
# edit fleet.local.json — your Hive worker IDs + names

python fleet_status.py                 # monerod + Hive + HashVault
python fleet_status.py --json
python fleet_status.py --html --open
python fleet_status.py --start-node    # optional detached start helper
python fleet_status.py --no-node
python fleet_status.py --hive-only
python fleet_status.py --pool-only
python fleet_status.py --watchdog --force
```

## What each source answers

| Source | Good for |
|--------|----------|
| **monerod** | Local node height / sync / peers (`127.0.0.1:18081` by default) |
| **Hive** | Rig online, CPU temp, LAN IP, flight sheet, miner version |
| **HashVault** | Pool-accepted hashrate per worker name, wallet balance/paid, pool effort |

Match HashVault worker names to your xmrig `--pass` / worker names.

## Units

- Hive RandomX `hash` → **kH/s** as reported
- HashVault wallet/worker `hashRate` → **H/s** (divide by 1000 → kH/s)
- Balances → atomic ÷ 1e12 → XMR

## Role map

Roles live in `fleet.local.json` (not in git):

| Role | Offline meaning |
|------|-----------------|
| `always_on` | **ALERT** when offline / over temp / under hashrate floor |
| `flex` | OK / OFF — optional seats on the board |
| `on_demand` | Offline is normal |

## Fail-only watchdog

```bash
python fleet_status.py --watchdog          # silent if healthy / de-duped
python scripts/hive-fleet-watchdog.py      # same idea for cron wrappers
```

**Exit code contract (both entrypoints):**

| Exit | Meaning |
|------|---------|
| `0` | Healthy, or the same alert was already delivered (deduped) — silent |
| `1` | Fresh alert — stdout has the report, something needs a human |
| `2` | Config / repo lookup failure (fleet_status.py `--watchdog`) |

Stdout only when a fresh alert fires, so a cron wrapper that delivers
non-empty stdout gets paged exactly when something changed. Soft-warns if
combined HashVault wallet HR drops below `pool_min_khs` (default 12), or
configured always-on pool workers go offline.

Wire it into your own scheduler (Hermes cron, Task Scheduler, systemd timer, …).

## Environment

See [`.env.example`](./.env.example). Useful extras:

| Var | Purpose |
|-----|---------|
| `FLEET_CONFIG` | Path to fleet JSON map |
| `FLEET_CONSOLE_ROOT` | Where the watchdog finds this repo |
| `MONEROD_RPC_URL` | Default `http://127.0.0.1:18081/json_rpc` |
| `MONEROD_DATA_DIR` / `MONEROD_BINARY` | Optional path overrides |
| `MONEROD_START_SCRIPT` | Optional helper script for `--start-node` |

## Layout

```text
fleet_status.py          CLI entry
fleet_console/           library
scripts/                 cron wrappers
fleet.example.json       public template
fleet.local.json         your map (gitignored)
.env                     your secrets (gitignored)
out/                     generated HTML (gitignored)
```

## Related

Personal portfolio / learning lab: [hermes-windows-lab](https://github.com/monerostar/hermes-windows-lab)

---

*Built for calm operator visibility — not hash-rate theater.*
