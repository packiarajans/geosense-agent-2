@echo off
set "E=%USERPROFILE%\anaconda3\envs\geosense2"
set "PATH=%E%\Library\bin;%E%\Library\mingw-w64\bin;%E%\Scripts;%E%;%PATH%"
set "PYTHONHOME="
set "UV_INTERNAL__PYTHONHOME="
set "VIRTUAL_ENV="
cd /d F:\GeoSense_Agent
"%E%\python.exe" -W ignore src\phase4_agent\mcp_server.py >> outputs\agent_logs\server_auto.log 2>&1
