@echo off
rem Always start with sample data, even if data\config.yml exists.
call "%~dp0START_WINDOWS.bat" --demo %*
