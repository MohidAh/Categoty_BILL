@echo off
cd /d C:\billbook
call venv\Scripts\activate
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/backup', data=b'', method='POST')"
echo Backup triggered.
