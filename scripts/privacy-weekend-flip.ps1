# Privacy lottery -> weekend HashVault flip
# Target: after Friday daily draw (~15:00 MDT). Scheduled on tech as privacy-weekend-flip.
# Actions:
#   Legion: STOP mine (monitor can keep logging)
#   Family7600X: privacy lottery OFF -> HashVault 10t ON
#   5800X: privacy OFF -> HashVault 10t ON
#   Hive 3950 + 3600XT + Asher 3700X: HashVault flight sheets
#     3700X is flex (Asher home vs dad) — offline OK; apply FS when online
$ErrorActionPreference = 'Continue'
$Log = "C:\Users\Admin\src\monero-fleet-console\scripts\privacy-weekend-flip-last.log"
function Log([string]$m) {
  $line = "$(Get-Date -Format o) $m"
  Add-Content -Path $Log -Value $line -Encoding utf8
  Write-Output $line
}

Log 'BEGIN privacy weekend flip'

# --- Legion stop ---
Log 'Legion: stop xmrig'
try {
  ssh -o BatchMode=yes -o ConnectTimeout=15 legion-go 'taskkill /F /IM xmrig.exe /T 2>nul & schtasks /End /tn legion-privacy-6t 2>nul & schtasks /End /tn legion-privacy-4t 2>nul & echo LEGION_OK'
} catch { Log "Legion SSH warn: $_" }

# --- Family: stop lottery, start HV 10t ---
Log 'Family: stop privacy lottery + start HashVault 10t'
$famCmd = @'
taskkill /F /IM xmrig.exe /T 2>nul
schtasks /End /tn family-lottery-10t 2>nul
schtasks /End /tn family-10t-1h-soak 2>nul
C:\Users\dusti\AppData\Local\Programs\Python\Python312\python.exe -c "import json,pathlib;p=pathlib.Path(r'C:\\xmrig\\xmrig-6.26.0\\config.json');c=json.loads(p.read_text(encoding='utf-8-sig'));c['autosave']=False;c['background']=False;c['cpu']['rx']=list(range(10));c['api']['worker-id']='7600X-2';c['pools'][0]['pass']='7600X-2';c['pools'][0]['rig-id']='7600X-2';c['log-file']=r'C:\\xmrig\\xmrig-6.26.0\\xmrig-hv-10t.log';pathlib.Path(r'C:\\xmrig\\xmrig-6.26.0\\config-10t-hashvault.json').write_text(json.dumps(c,indent=2),encoding='utf-8');print('hv10t')"
schtasks /Delete /tn family-hashvault-10t /f 2>nul
schtasks /Create /tn family-hashvault-10t /tr "C:\xmrig\xmrig-6.26.0\xmrig.exe -c C:\xmrig\xmrig-6.26.0\config-10t-hashvault.json" /sc once /st 00:00 /ru SYSTEM /rl HIGHEST /f
schtasks /Run /tn family-hashvault-10t
ping -n 10 127.0.0.1 >nul
curl -s http://127.0.0.1:4028/1/summary
echo FAMILY_DONE
'@
$famCmd | ssh -o BatchMode=yes -o ConnectTimeout=40 family-7600x 'powershell -NoProfile -Command "$input | Out-File -Encoding ascii C:\xmrig\xmrig-6.26.0\_flip.cmd; cmd /c C:\xmrig\xmrig-6.26.0\_flip.cmd"'

# --- 5800X HashVault 10t ---
Log '5800x: stop privacy, start config-10t HashVault'
ssh -o BatchMode=yes -o ConnectTimeout=20 linux-5800x @'
set +e
if pgrep -x xmrig >/dev/null; then
  sudo -n /usr/bin/kill $(pgrep -x xmrig) 2>/dev/null || pkill -x xmrig
  sleep 2
fi
CFG=/home/hermes/xmrig/build/xmrig/build/config-10t.json
BIN=/home/hermes/xmrig/build/xmrig/build/xmrig
echo 3 | sudo -n tee /sys/kernel/mm/hugepages/hugepages-1048576kB/nr_hugepages >/dev/null 2>&1
# background true in config may already daemonize; force bg
python3 - <<PY
import json
from pathlib import Path
p=Path("/home/hermes/xmrig/build/xmrig/build/config-10t.json")
c=json.loads(p.read_text())
c["background"]=True
p.write_text(json.dumps(c, indent=2))
print("bg ok")
PY
sudo -n "$BIN" -c "$CFG"
sleep 3
pgrep -a xmrig
curl -s http://127.0.0.1:4028/1/summary | python3 -c "import sys,json;d=json.load(sys.stdin);print('pool',d['connection']['pool'],'kH',round((d['hashrate']['total'][0] or 0)/1000,2),'lanes',len(d['hashrate']['threads']))"
echo OK_5800
'@

# --- Hive FS ---
Log 'Hive: HashVault flight sheets'
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
# worker_id, fs_id, label — IDs live in tech .env (gitignored), not in this repo
# Required env: HIVE_WORKER_3600XT, HIVE_WORKER_3950X, HIVE_WORKER_3700X,
#               HIVE_FS_3600XT_HV, HIVE_FS_3950X_HV, HIVE_FS_3700X_HV
pairs=[
    (int(os.environ["HIVE_WORKER_3600XT"]), int(os.environ["HIVE_FS_3600XT_HV"]), "3600XT cool HV"),
    (int(os.environ["HIVE_WORKER_3950X"]), int(os.environ["HIVE_FS_3950X_HV"]), "3950 tuned HV"),
    (int(os.environ["HIVE_WORKER_3700X"]), int(os.environ["HIVE_FS_3700X_HV"]), "3700X HV 8t Asher flex"),
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
        print(name, "FAIL", type(e).__name__, e)
print("HIVE_OK")
PY

Log "DONE — see $Log"
Log 'Next: python fleet_status.py ; check legion has no xmrig'
Log 'Monday: run privacy-monday-flip.ps1 (schtask privacy-monday-flip)'
