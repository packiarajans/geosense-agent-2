@echo off
REM Stop the GeoSense servers (ports 8000, 5173, 8501).
powershell -NoProfile -Command "foreach($p in 8000,5173,8501){ Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } }"
echo GeoSense servers stopped.
timeout /t 3 >nul
