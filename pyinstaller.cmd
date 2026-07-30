@echo off
python -m PyInstaller --add-data "sounds;sounds" --add-data "dox;dox" --add-data "bass.dll;." --windowed --log-level DEBUG --name "Y-music-blind" main.py > pyinstaller_log.txt 2>&1
echo @windos2006
pause