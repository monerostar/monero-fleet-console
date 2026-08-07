# fleet-status-write.ps1 — per-box status writer (Windows hosts)
# Reads local xmrig API (127.0.0.1:4028) and writes a tiny JSON status file.
# Run from Scheduled Task every 3 minutes.
# Writes to: C:\xmrig\fleet-status.json
$ErrorActionPreference = 'Continue'
$Port = if ($env:FLEET_STATUS_PORT) { $env:FLEET_STATUS_PORT } else { '4028' }
$Out = if ($env:FLEET_STATUS_FILE) { $env:FLEET_STATUS_FILE } else { 'C:\xmrig\fleet-status.json' }
$Box = if ($env:FLEET_STATUS_BOX) { $env:FLEET_STATUS_BOX } else { $env:COMPUTERNAME }

$data = @{
  box = $Box
  worker = $null
  hr_10s = 0
  pool = $null
  online = $false
  ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}

try {
  $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 6 -Uri "http://127.0.0.1:$Port/1/summary"
  $d = $r.Content | ConvertFrom-Json
  $data.worker = $d.worker_id
  if ($d.hashrate.total.Count -gt 0) { $data.hr_10s = [math]::Round([double]$d.hashrate.total[0], 1) }
  if ($d.connection.pool) {
    $data.pool = $d.connection.pool
    $data.online = $true
  }
} catch {
  $data.error = $_.Exception.Message
}

$data | ConvertTo-Json | Set-Content -Path $Out -Encoding utf8
