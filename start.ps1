$ErrorActionPreference = 'Stop'
$runtimeDir = Join-Path $PSScriptRoot 'runtime'
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}
$pythonWindowPath = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
$desktopPath = Join-Path $PSScriptRoot 'desktop.py'
Start-Process -FilePath $pythonWindowPath -ArgumentList @('"' + $desktopPath + '"') -WorkingDirectory $PSScriptRoot -WindowStyle Normal | Out-Null
for ($attempt = 0; $attempt -lt 8; $attempt++) {
    $status = & $pythonPath $desktopPath --status
    if ($LASTEXITCODE -eq 0) { Write-Output $status; exit 0 }
    Start-Sleep -Milliseconds 200
}
throw "桌面计算器尚未就绪，请查看 $runtimeDir\calculator.log。"
