# Lianxin AI2 backend log monitor.
# Tails logs/debug.log live and closes when the main UI (lianxin-ui.exe) exits.
$ErrorActionPreference = 'SilentlyContinue'
$logFile = 'E:\Desktop\Lianxin-AI\logs\debug.log'
$pos = 0
$uiSeen = $false
$deadline = (Get-Date).AddSeconds(40)

if (Test-Path $logFile) {
    $pos = (Get-Item $logFile).Length
}

Write-Host 'Lianxin AI2 - backend log monitor'
Write-Host 'Tail: logs\debug.log  (this window closes when the main UI exits)'
Write-Host '----------------------------------------'

while ($true) {
    if (Test-Path $logFile) {
        try {
            $len = (Get-Item $logFile).Length
            if ($len -lt $pos) { $pos = 0 }
            if ($len -gt $pos) {
                $fs = [System.IO.File]::Open($logFile, 'Open', 'Read', 'ReadWrite')
                $fs.Seek($pos, 'Begin') | Out-Null
                $reader = New-Object System.IO.StreamReader($fs, [System.Text.Encoding]::UTF8)
                while (-not $reader.EndOfStream) {
                    $line = $reader.ReadLine()
                    if ($line) { Write-Host $line }
                }
                $pos = $fs.Position
                $reader.Close()
                $fs.Close()
            }
        } catch { }
    }
    $ui = Get-Process -Name 'lianxin-ui' -ErrorAction SilentlyContinue
    if ($ui) { $uiSeen = $true }
    if ($uiSeen -and -not $ui) { break }
    if (-not $uiSeen -and (Get-Date) -gt $deadline) { break }
    Start-Sleep -Milliseconds 500
}

Write-Host ''
Write-Host 'Main UI closed - log monitor exiting.'
Start-Sleep -Seconds 2
