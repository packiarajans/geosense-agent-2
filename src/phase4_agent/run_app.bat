@echo off
REM Streamlit demo (start run_mcp_server.bat and "npm run dev" in frontend\digital_twin first)
set "E=%USERPROFILE%\anaconda3\envs\geosense2"
set "PATH=%E%\Library\bin;%E%\Library\mingw-w64\bin;%E%\Scripts;%E%;%PATH%"
set "PYTHONHOME="
cd /d F:\GeoSense_Agent
"%E%\python.exe" -m streamlit run src\phase4_agent\app.py
pause
