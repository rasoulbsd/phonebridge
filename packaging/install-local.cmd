@echo off
rem Installs the newest personal build from dist\ for this Windows account, without dialogs.
rem Build it first with: python packaging\build.py --personal
setlocal
set "DIST=%~dp0..\dist"
for /f "delims=" %%f in ('dir /b /o-d "%DIST%\Phonebridge-Setup-*-personal.exe" 2^>nul') do (
    start "" "%DIST%\%%f" --silent
    goto :done
)
echo No personal build found in %DIST%. Run: python packaging\build.py --personal
:done
endlocal
