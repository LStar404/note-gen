# 开机自启安装/卸载（可选）：把启动器快捷方式放进当前用户的 Startup 目录。
# 用法：
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1          # 安装
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -Remove  # 卸载
#   powershell -ExecutionPolicy Bypass -File scripts\install_autostart.ps1 -DryRun  # 只看会做什么
param(
  [switch]$Remove,
  [switch]$DryRun
)
$ErrorActionPreference = "Stop"

$root     = Split-Path -Parent $PSScriptRoot          # 项目根（note-gen）
$launcher = Join-Path (Split-Path -Parent $root) "启动自动笔记软件.bat"
$startup  = [Environment]::GetFolderPath("Startup")
$shortcut = Join-Path $startup "自动笔记软件.lnk"

if (-not (Test-Path $launcher)) {
  Write-Error "找不到启动器：$launcher"
}

if ($Remove) {
  if ($DryRun) { Write-Output "[dry-run] 将删除：$shortcut"; exit 0 }
  if (Test-Path $shortcut) { Remove-Item $shortcut -Force; Write-Output "已卸载自启：$shortcut" }
  else { Write-Output "本来就没安装：$shortcut" }
  exit 0
}

if ($DryRun) {
  Write-Output "[dry-run] 启动器：$launcher"
  Write-Output "[dry-run] 快捷方式：$shortcut"
  Write-Output "[dry-run] 目标：$launcher --no-browser（启动服务但不自动开浏览器）"
  exit 0
}

$shell = New-Object -ComObject WScript.Shell
$lnk = $shell.CreateShortcut($shortcut)
$lnk.TargetPath = $launcher
$lnk.Arguments = "--no-browser"
$lnk.WorkingDirectory = (Split-Path -Parent $launcher)
$lnk.IconLocation = "$env:SystemRoot\System32\shell32.dll,21"
$lnk.Description = "自动笔记软件（本地服务 http://127.0.0.1:8787）"
$lnk.Save()

Write-Output "已安装开机自启：$shortcut"
Write-Output "  下次登录 Windows 会自动启动服务（不弹浏览器）；用 -Remove 可卸载"
Write-Output "  想立刻验证：双击该快捷方式，然后打开 http://127.0.0.1:8787"
