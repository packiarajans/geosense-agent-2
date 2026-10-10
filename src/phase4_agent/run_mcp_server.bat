@echo off
REM Launcher for the GeoSense Phase 4 MCP server (uses the geosense2 env without needing "conda activate")
set "E=%USERPROFILE%\anaconda3\envs\geosense2"
set "PATH=%E%\Library\bin;%E%\Library\mingw-w64\bin;%E%\Scripts;%E%;%PATH%"
set "PYTHONHOME="
cd /d F:\GeoSense_Agent
"%E%\python.exe" -W ignore src\phase4_agent\mcp_server.py
pause
