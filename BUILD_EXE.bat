@echo off
echo ================================================================
echo  Compiling DriveRecoveryTool to Standalone Single Windows .exe
echo ================================================================
python -m pip install pyinstaller
pyinstaller --clean --onefile --noconsole --name "DriveRecoveryTool" --add-data "raw_io.py;." --add-data "disk_layout.py;." --add-data "ntfs_parser.py;." --add-data "recovery_engine.py;." --add-data "gui.py;." --add-data "cli.py;." main.py
echo ================================================================
echo  Build finished! Executable is located at dist\DriveRecoveryTool.exe
echo ================================================================
pause
