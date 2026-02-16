@echo off
echo Finding processes using port 1420...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :1420 ^| findstr LISTENING') do (
    echo Killing process %%a...
    taskkill /F /PID %%a
)
echo Done
pause
