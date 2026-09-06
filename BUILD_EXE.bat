@echo off
echo ================================================================
echo  Compiling DriveRecoveryTool to Standalone Single Windows .exe
echo ================================================================
python -m pip install pyinstaller
pyinstaller --clean --onefile --noconsole --name "DriveRecoveryTool" --add-data "diskio.py;." --add-data "partitions.py;." --add-data "ntfs.py;." --add-data "engine.py;." --add-data "gui.py;." --add-data "cli.py;." --add-data "dashboard.html;." main.py
echo ================================================================
echo  Build finished! Executable is located at dist\DriveRecoveryTool.exe
echo ================================================================
pause
