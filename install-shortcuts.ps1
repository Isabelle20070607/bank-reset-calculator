$ErrorActionPreference = 'Stop'
$projectPath = $PSScriptRoot
$packagedExe = Join-Path $projectPath 'BankedReset.exe'
if (Test-Path -LiteralPath $packagedExe) {
    $targetPath = $packagedExe
    $arguments = ''
    $iconPath = $packagedExe
} else {
    $pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        $pythonPath = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    }
    $targetPath = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
    $arguments = '"' + (Join-Path $projectPath 'desktop.py') + '"'
    $iconPath = Join-Path $projectPath 'assets\bank-reset.ico'
}
$desktopPath = [Environment]::GetFolderPath('Desktop')
$programsPath = [Environment]::GetFolderPath('Programs')
$shellLinks = New-Object -ComObject WScript.Shell
$entries = @(
    @{Path=(Join-Path $projectPath 'Banked reset.lnk'); Hotkey=''},
    @{Path=(Join-Path $desktopPath 'Banked reset.lnk'); Hotkey='CTRL+ALT+B'},
    @{Path=(Join-Path $programsPath 'Banked reset.lnk'); Hotkey=''}
)
foreach ($entry in $entries) {
    if (Test-Path -LiteralPath $entry.Path) {
        $existing = $shellLinks.CreateShortcut($entry.Path)
        if ($existing.Arguments -ne $arguments) {
            throw "该位置已有其他快捷方式，未覆盖：$($entry.Path)"
        }
    }
    $shortcut = $shellLinks.CreateShortcut($entry.Path)
    $shortcut.TargetPath = $targetPath
    $shortcut.Arguments = $arguments
    $shortcut.WorkingDirectory = $projectPath
    $shortcut.IconLocation = $iconPath + ',0'
    $shortcut.Description = '自动更新用量与重置建议'
    $shortcut.WindowStyle = 1
    $shortcut.Hotkey = $entry.Hotkey
    $shortcut.Save()
    [pscustomobject]@{Path=$entry.Path; Hotkey=$entry.Hotkey; Target=$shortcut.TargetPath}
}
