# WinCro Autopilot Script
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = "C:\Projects\wincro\logs\autopilot_$timestamp.log"

New-Item -ItemType Directory -Force -Path "C:\Projects\wincro\logs" | Out-Null

Write-Host "========================================"
Write-Host "   WinCro Autopilot Start"
Write-Host "   Time: $(Get-Date)"
Write-Host "   Log: $logFile"
Write-Host "========================================"

Set-Location "C:\Projects\wincro"

$prompt = Get-Content "autopilot_prompt.txt" -Raw -Encoding UTF8

Write-Host ""
Write-Host "[INFO] Claude Code running..."
Write-Host "[INFO] Check PROGRESS.md for status"
Write-Host "[INFO] Press Ctrl+C to stop"
Write-Host ""

try {
    claude --dangerously-skip-permissions -p $prompt 2>&1 | Tee-Object -FilePath $logFile
    Write-Host ""
    Write-Host "========================================"
    Write-Host "   WinCro Autopilot Complete"
    Write-Host "   Time: $(Get-Date)"
    Write-Host "========================================"
}
catch {
    Write-Host ""
    Write-Host "[ERROR] Error occurred: $_"
    $_ | Out-File -Append -FilePath $logFile
}

[console]::beep(1000, 500)
