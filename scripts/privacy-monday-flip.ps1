# Privacy Monday flip — lottery foot back on (after weekend HV)
# Asher 3700X flex: apply privacy FS if online; offline is OK (dad/home).
# Scheduled: Mon ~08:00 MDT as privacy-monday-flip (optional; safe to run manual).
$ErrorActionPreference = 'Continue'
$Log = "C:\Users\Admin\src\monero-fleet-console\scripts\privacy-monday-flip-last.log"
function Log([string]$m) {
  $line = "$(Get-Date -Format o) $m"
  Add-Content -Path $Log -Value $line -Encoding utf8
  Write-Output $line
}

Log 'BEGIN privacy Monday flip (lottery foot)'

# Hive: privacy flight sheets
Log 'Hive: privacy FS 3950 + 3600XT + 5700X always-on + 3700X Asher flex'
python - << 'PY'
import os, json, urllib.request
from pathlib import Path
for p in [Path(r"C:\Users\Admin\AppData\Local\hermes\profiles\tech\.env"), Path(r"C:\Users\Admin\src\monero-fleet-console\.env")]:
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line=line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k,v=line.split("=",1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
token=os.environ["HIVEOS_API_TOKEN"]
farm=os.environ.get("HIVEOS_FARM_ID")  # required, gitignored
# privacy FS ids — IDs live in tech .env (gitignored), not in this repo
# Required env: HIVE_WORKER_3600XT, HIVE_WORKER_3950X, HIVE_WORKER_3700X,
#               HIVE_FS_3600XT_PRIV, HIVE_FS_3950X_PRIV, HIVE_FS_3700X_PRIV
pairs=[
    (int(os.environ["HIVE_WORKER_3950X"]), int(os.environ["HIVE_FS_3950X_PRIV"]), "3950 privacy"),
    (int(os.environ["HIVE_WORKER_3600XT"]), int(os.environ["HIVE_FS_3600XT_PRIV"]), "3600XT privacy 5t"),
    (int(os.environ["HIVE_WORKER_3700X"]), int(os.environ["HIVE_FS_3700X_PRIV"]), "3700X privacy 8t Asher flex"),
    (int(os.environ["HIVE_WORKER_5700X"]), int(os.environ["HIVE_FS_5700X_PRIV"]), "5700X privacy 15t"),
]

def hive(path, method="GET", body=None):
    url=f"https://api2.hiveos.farm/api/v2{path}"
    data=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(url, data=data, method=method)
    for k,v in {
        "Authorization": f"Bearer {token}",
        "Accept":"application/json",
        "Content-Type":"application/json",
        "User-Agent":"Mozilla/5.0",
        "Origin":"https://the.hiveos.farm",
        "Referer":"https://the.hiveos.farm/",
    }.items():
        req.add_header(k,v)
    with urllib.request.urlopen(req, timeout=45) as r:
        return r.status

for wid, fs, name in pairs:
    try:
        st = hive(f"/farms/{farm}/workers/{wid}", method="PATCH", body={"fs_id": fs})
        print(name, "HTTP", st)
    except Exception as e:
        print(name, "FAIL (offline OK for Asher)", type(e).__name__, e)
print("HIVE_PRIVACY_OK")
PY

# 5800X: optional day privacy — leave manual (desktop king); do not auto day-mine
Log '5800x: skip auto privacy (manual / night HV policy)'

# Family: lottery 10t if host up
Log 'Family: start lottery 10t if reachable'
try {
  ssh -o BatchMode=yes -o ConnectTimeout=12 family-7600x 'schtasks /End /tn family-hashvault-10t 2>nul & taskkill /F /IM xmrig.exe /T 2>nul & schtasks /Run /tn family-lottery-10t 2>nul || (schtasks /Create /tn family-lottery-10t /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\xmrig\xmrig-6.26.0\family-lottery-10t.ps1" /sc once /st 00:00 /ru SYSTEM /rl HIGHEST /f & schtasks /Run /tn family-lottery-10t) & echo FAMILY_LOTTERY'
} catch { Log "Family skip: $_" }

# Legion: leave OFF by default (Niall handheld test-only)
Log 'Legion: leave stopped (test-only; start manual if wanted)'

Log 'DONE Monday flip'
Log 'Always-on: 3950+3600XT+5700X. Asher 3700X flex ~10d/mo when home; offline OK'
