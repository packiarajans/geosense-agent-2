@echo off
REM GeoSense Agent 2.0 - start MCP server (8000), 3D twin (5173) and Streamlit demo (8501) in minimized windows.
REM   start_all.bat           -> start everything and open the demo in the browser
REM   start_all.bat startup   -> start everything quietly (used by the Windows Startup entry)
if not exist F:\GeoSense_Agent\outputs\agent_logs mkdir F:\GeoSense_Agent\outputs\agent_logs

netstat -ano | findstr /R /C:":8000 .*LISTENING" >nul
if errorlevel 1 (start "GeoSense MCP server" /min cmd /c F:\GeoSense_Agent\launch\svc_mcp.bat) else echo MCP server already running.

netstat -ano | findstr /R /C:":5173 .*LISTENING" >nul
if errorlevel 1 (start "GeoSense 3D twin" /min cmd /c F:\GeoSense_Agent\launch\svc_twin.bat) else echo 3D twin already running.

netstat -ano | findstr /R /C:":8501 .*LISTENING" >nul
if errorlevel 1 (start "GeoSense Streamlit demo" /min cmd /c F:\GeoSense_Agent\launch\svc_app.bat) else echo Streamlit already running.

if /I "%~1"=="startup" goto :eof
timeout /t 20 /nobreak >nul
start "" http://localhost:8501
