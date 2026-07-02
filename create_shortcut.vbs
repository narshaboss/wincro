Set WshShell = CreateObject("WScript.Shell")

' Get desktop path
strDesktop = WshShell.SpecialFolders("Desktop")

' Create shortcut
Set oShortcut = WshShell.CreateShortcut(strDesktop & "\WinCro.lnk")
oShortcut.TargetPath = "pythonw.exe"
oShortcut.Arguments = "-m src.main"
oShortcut.WorkingDirectory = "C:\Projects\wincro"
oShortcut.IconLocation = "C:\Projects\wincro\icon.ico,0"
oShortcut.Description = "WinCro RPA Automation"
oShortcut.Save

WScript.Echo "WinCro shortcut created on Desktop!"
