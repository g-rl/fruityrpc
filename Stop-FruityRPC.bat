@echo off
setlocal
powershell -NoProfile -Command ^
  "$p = Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'FruityRPC*.exe' -or $_.CommandLine -like '*fruityrpc.py*' };" ^
  "$p = $p | Where-Object { $_.ProcessId -ne $PID };" ^
  "if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host ('stopped pid ' + $_.ProcessId) } } else { Write-Host 'FruityRPC is not running.' }"
pause
