@echo off
REM ============================================================
REM  write-robot launcher for Windows
REM
REM  Usage:
REM    wr.bat preview "text"      build preview  -> out\preview.html
REM    wr.bat gcode   "text"      build G-code   -> out\output.nc
REM    wr.bat run     "text"      connect to machine and write
REM    wr.bat ports                list serial ports
REM
REM  Two Windows-specific notes:
REM
REM  1) This file is ASCII-only on purpose. The CMD console uses a legacy
REM     code page (GBK on zh-CN) which corrupts UTF-8 text inside .bat
REM     files. All Chinese help text lives in the Python CLI instead.
REM
REM  2) The Python module is invoked as "python -m writerobot.cli" and
REM     NEVER as the bare "writerobot" command. If this launcher were
REM     named writerobot.bat, CMD would resolve the bare command to
REM     THIS file (the current directory is searched first), causing
REM     infinite recursion and a hang.
REM
REM  Branching uses goto labels instead of parenthesised blocks,
REM  because %VAR% is expanded at parse time inside blocks.
REM ============================================================

cd /d "%~dp0"

set "WR=python -m writerobot.cli"

if "%~1"=="" goto menu
if "%~1"=="help" goto usage
if "%~1"=="preview" goto preview
if "%~1"=="gcode" goto gcode
if "%~1"=="run" goto run

%WR% %*
goto :eof

:usage
echo.
echo   write-robot - writing robot controller
echo   --------------------------------------------
echo   wr.bat                    interactive menu (Chinese)
echo   wr.bat preview "text"    trajectory preview (no hardware needed)
echo   wr.bat gcode   "text"    generate G-code to out\output.nc
echo   wr.bat run     "text"    connect to machine and write
echo   wr.bat ports             list serial ports
echo.
echo   Run "wr.bat preview --help" for all options.
echo.
pause
goto :eof

:menu
REM  The interactive menu lives in Python so that Chinese text is not
REM  corrupted by the CMD code page. Python also keeps the window open
REM  at the end, so double-clicking this file shows something.
%WR% menu
goto :eof

:preview
if "%~3"=="" goto preview_default
%WR% preview "%~2" -o "%~3"
goto preview_end

:preview_default
%WR% preview "%~2" -o out\preview.html

:preview_end
echo.
echo   Open out\preview.html in your browser to see the animation.
goto :eof

:gcode
if "%~3"=="" goto gcode_default
%WR% gcode "%~2" -o "%~3"
goto :eof

:gcode_default
%WR% gcode "%~2" -o out\output.nc
goto :eof

:run
%WR% run %*
goto :eof
