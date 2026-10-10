@echo off
REM Ask the GeoSense agent questions (the MCP server must already be running via run_mcp_server.bat)
set "E=%USERPROFILE%\anaconda3\envs\geosense2"
set "PATH=%E%\Library\bin;%E%\Library\mingw-w64\bin;%E%\Scripts;%E%;%PATH%"
set "PYTHONHOME="
cd /d F:\GeoSense_Agent
"%E%\python.exe" -W ignore src\phase4_agent\geoai_agent.py %*
pause
