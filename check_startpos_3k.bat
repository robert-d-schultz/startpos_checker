@echo off
rem Drag a Three Kingdoms start_pos .pack onto this file to check it.
rem Same as check_startpos.bat, with --game 3k.
set "STARTPOS_GAME=--game 3k"
call "%~dp0check_startpos.bat" %*
