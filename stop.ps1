$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    $pythonPath = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
}
& $pythonPath (Join-Path $PSScriptRoot 'desktop.py') --quit
