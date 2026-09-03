@echo off
python -m PyInstaller --add-data "speech;speech" --add-data "sounds;sounds" --add-data "docs;docs" --add-data "bass.dll;." --add-data "bass_fx.dll;." --windowed --log-level DEBUG --name "Y-music-blind" main.py > pyinstaller_log.txt 2>&1
echo @windos2006
pause