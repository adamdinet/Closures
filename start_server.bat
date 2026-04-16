@echo off
echo ============================================
echo  IntelHub Local Server
echo  Open browser to: http://localhost:8765
echo  Press Ctrl+C to stop
echo ============================================
cd /d "%~dp0"
python -m http.server 8765
pause
