param(
    [string]$PythonExe = 'python',
    [string]$NodeExe = 'node',
    [ValidatePattern('^\d+\.\d+\.\d+$')][string]$Version = '0.4.1'
)
$ErrorActionPreference = 'Stop'
$PythonExe = (Get-Command $PythonExe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$projectRoot = Split-Path -Parent $PSScriptRoot
$buildRoot = Join-Path $projectRoot 'build'
$distRoot = Join-Path $projectRoot 'dist'
$licenseRoot = Join-Path $buildRoot 'licenses'
New-Item -ItemType Directory -Path $licenseRoot -Force | Out-Null
$nodePath = (Get-Command $NodeExe -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$nodeVersion = (& $nodePath --version).Trim().TrimStart('v')
$pythonVersion = (& $PythonExe -c 'import platform; print(platform.python_version())').Trim()
& $PythonExe -c 'from PySide6 import QtCore, QtGui, QtWidgets, QtNetwork; print("Qt preflight:", QtCore.qVersion())'
if ($LASTEXITCODE -ne 0) { throw '构建 Python 无法加载 Qt；请使用可正常导入 PySide6 的独立 CPython 环境。' }
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/nodejs/node/v$nodeVersion/LICENSE" -OutFile (Join-Path $licenseRoot 'Node-LICENSE.txt')
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/python/cpython/v$pythonVersion/LICENSE" -OutFile (Join-Path $licenseRoot 'Python-LICENSE.txt')
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/qt/qtbase/v6.11.0/LICENSES/LGPL-3.0-only.txt' -OutFile (Join-Path $licenseRoot 'Qt-LGPL-3.0.txt')
Invoke-WebRequest -Uri 'https://raw.githubusercontent.com/qt/qtbase/v6.11.0/LICENSES/GPL-3.0-only.txt' -OutFile (Join-Path $licenseRoot 'Qt-GPL-3.0.txt')
$env:BANK_RESET_BUILD_LICENSES = $licenseRoot
try {
    @'
from importlib.metadata import distribution
from pathlib import Path
import os, shutil
root = Path(os.environ['BANK_RESET_BUILD_LICENSES'])
for package in ('PySide6_Essentials', 'shiboken6', 'PyInstaller'):
    dist = distribution(package)
    for file in dist.files or []:
        if 'licenses' in (part.lower() for part in file.parts) or file.name.lower().startswith(('license', 'copying', 'notice')):
            target = root/package/str(file)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dist.locate_file(file), target)
'@ | & $PythonExe -
    if ($LASTEXITCODE -ne 0) { throw '收集运行时许可证失败。' }
} finally { Remove-Item Env:\BANK_RESET_BUILD_LICENSES }
$pyInstallerArguments = @(
    '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed', '--noupx',
    '--name', 'BankedReset', '--icon', (Join-Path $projectRoot 'assets\bank-reset.ico'),
    '--distpath', $distRoot, '--workpath', (Join-Path $buildRoot 'pyinstaller-native'), '--specpath', $buildRoot,
    '--add-data', ((Join-Path $projectRoot 'assets') + ':assets'),
    '--add-data', ((Join-Path $projectRoot 'calculator.js') + ':.'),
    '--add-data', ((Join-Path $projectRoot 'run-calculation.cjs') + ':.'),
    '--add-binary', ($nodePath + ':node'), (Join-Path $projectRoot 'desktop.py')
)
$buildSearchPath = $env:PATH
try {
    # Qt uses Windows' ICU. Do not collect a same-named DLL from unrelated runtimes.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    & $PythonExe @pyInstallerArguments
    $buildExitCode = $LASTEXITCODE
} finally { $env:PATH = $buildSearchPath }
if ($buildExitCode -ne 0) { throw 'Windows 打包失败。' }
$bundleRoot = Join-Path $distRoot 'BankedReset'
foreach ($file in @('README.md', 'THIRD_PARTY_NOTICES.md', 'install-shortcuts.ps1')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $bundleRoot
}
Copy-Item -LiteralPath $licenseRoot -Destination (Join-Path $bundleRoot 'licenses') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $projectRoot 'docs') -Destination (Join-Path $bundleRoot 'docs') -Recurse -Force
$report = Join-Path $buildRoot 'release-smoke.json'
if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report }
$smokeProcess = Start-Process -FilePath (Join-Path $bundleRoot 'BankedReset.exe') -ArgumentList @('--self-test', ('"' + $report + '"')) -PassThru -WindowStyle Hidden
if (-not $smokeProcess.WaitForExit(60000)) {
    $smokeProcess.Kill()
    $smokeProcess.WaitForExit()
    throw '发行包自检超时，已结束本次测试进程。'
}
if ($smokeProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report)) { throw '发行包自检失败。' }
$smoke = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if (-not $smoke.ok) { throw '发行包未通过自检。' }
$archive = Join-Path $distRoot "BankedReset-$Version-windows-x64.zip"
Compress-Archive -LiteralPath $bundleRoot -DestinationPath $archive -Force
Write-Output $archive
