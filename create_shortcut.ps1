$desktop = [Environment]::GetFolderPath('Desktop')
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("$desktop\WinCro 개발.lnk")
$Shortcut.TargetPath = 'cmd.exe'
$Shortcut.Arguments = '/c cd /d C:\Projects\wincro && python -m src.main'
$Shortcut.WorkingDirectory = 'C:\Projects\wincro'
$Shortcut.IconLocation = 'C:\Projects\wincro\icon.ico'
$Shortcut.Save()
Write-Host "바로가기 생성 완료: $desktop\WinCro 개발.lnk"
