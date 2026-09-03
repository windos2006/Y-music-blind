@echo off
python -m PyInstaller --onefile updater.py > pyinstaller_updater_log.txt 2>&1
exit