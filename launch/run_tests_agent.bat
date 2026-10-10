@echo off
set "PATH=C:\Users\Siva\anaconda3\envs\geosense2\Library\bin;C:\Users\Siva\anaconda3\envs\geosense2\Library\mingw-w64\bin;C:\Users\Siva\anaconda3\envs\geosense2\Scripts;C:\Users\Siva\anaconda3\envs\geosense2;%PATH%"
set "PYTHONHOME="
cd /d F:\GeoSense_Agent
"C:\Users\Siva\anaconda3\envs\geosense2\python.exe" -m pytest tests\test_phase4_agent.py -v -m agent -p no:cacheprovider > outputs\agent_logs\pytest_agent.out 2>&1
