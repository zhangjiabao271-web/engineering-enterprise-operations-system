# Creates a new, non-overwriting shortcut. Does not alter the old Tk application.
param([switch]$Writable)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskDesktop = [Environment]::GetFolderPath('Desktop')
$taskName = if ($Writable) { '工程经营工作台.lnk' } else { '工程经营工作台（新版只读）.lnk' }
$taskEntry = if ($Writable) { 'run_workbench.vbs' } else { 'run_desktop.vbs' }
$taskLink = Join-Path $taskDesktop $taskName
$taskLauncher = Join-Path $taskRoot $taskEntry
if (-not (Test-Path -LiteralPath $taskLauncher)) { throw '桌面启动入口不存在' }
if (Test-Path -LiteralPath $taskLink) {
    throw '同名快捷方式已存在，未覆盖。请核对原快捷方式。'
}
$taskShell = New-Object -ComObject WScript.Shell
$taskShortcut = $taskShell.CreateShortcut($taskLink)
$taskShortcut.TargetPath = Join-Path ([Environment]::GetFolderPath('Windows')) 'System32\wscript.exe'
$taskShortcut.Arguments = '"' + $taskLauncher + '"'
$taskShortcut.WorkingDirectory = $taskRoot
$taskShortcut.IconLocation = (Join-Path $taskRoot 'app.ico') + ',0'
$taskShortcut.Description = if ($Writable) {
    '工程经营工作台主入口；本机正式账本可录入，启动前完整备份，关闭窗口同时停止后台。'
} else {
    '本机网页桌面版，正式账本默认只读；关闭窗口同时停止后台。'
}
$taskShortcut.Save()
Write-Output $taskLink
