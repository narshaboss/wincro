$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutName = 'WinCro ' + [char]0xAC1C + [char]0xBC1C + '.lnk'
$shortcutPath = Join-Path $desktop $shortcutName
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut($shortcutPath)
$Shortcut.TargetPath = 'cmd.exe'
$Shortcut.Arguments = '/c cd /d C:\Projects\wincro && python -m src.main'
$Shortcut.WorkingDirectory = 'C:\Projects\wincro'
$Shortcut.IconLocation = 'C:\Projects\wincro\icon.ico'
$Shortcut.Save()
Write-Host "Shortcut created: $shortcutPath"
