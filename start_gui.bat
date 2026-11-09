@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem Nexus service console launcher.
rem The visible UI lives in gui_launcher.py and shares the light industrial
rem design language of testApp_gui: warm neutral surfaces, industrial orange
rem accent, flat controls and a single always-visible service workspace.

cd /d "%~dp0"
set "APP=gui_launcher.py"
set "LOG_DIR=%LOCALAPPDATA%\NexusFieldDeck"
set "LOG_FILE=%LOG_DIR%\launcher.log"
set "PYW="
set "PYW_ARGS="

if not exist "%APP%" goto missing_app

rem Prefer an isolated project runtime when one is present.
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "import tkinter" >nul 2>&1
    if not errorlevel 1 (
        set "PYW=.venv\Scripts\pythonw.exe"
        goto runtime_ready
    )
)
if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" -c "import tkinter" >nul 2>&1
    if not errorlevel 1 (
        set "PYW=venv\Scripts\pythonw.exe"
        goto runtime_ready
    )
)

rem The py launcher is the safest system-wide choice. Verify Tk support first,
rem because some embedded Python distributions expose pythonw.exe without Tk.
where py.exe >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import tkinter" >nul 2>&1
    if not errorlevel 1 (
        where pyw.exe >nul 2>&1
        if not errorlevel 1 (
            set "PYW=pyw.exe"
            set "PYW_ARGS=-3"
            goto runtime_ready
        )
    )
)

rem Fall back to each pythonw.exe on PATH whose matching python.exe has Tk.
for /f "delims=" %%P in ('where pythonw.exe 2^>nul') do (
    set "PY_CANDIDATE=%%~dpPpython.exe"
    if exist "!PY_CANDIDATE!" (
        "!PY_CANDIDATE!" -c "import tkinter" >nul 2>&1
        if not errorlevel 1 set "PYW=%%P"
    )
)
if defined PYW goto runtime_ready
goto missing_runtime

:runtime_ready
if /I "%~1"=="--check" (
    echo Launcher check: OK
    echo Entry point: %APP%
    echo Python runtime: %PYW% %PYW_ARGS%
    exit /b 0
)

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
start "" "%PYW%" %PYW_ARGS% "%APP%"
exit /b 0

:missing_app
call :record "Entry point not found: %APP%"
mshta "javascript:var s=new ActiveXObject('WScript.Shell');s.Popup('Nexus launcher cannot find gui_launcher.py.\n\nSee launcher.log for details.',10,'Nexus launcher error',16);close();"
exit /b 1

:missing_runtime
call :record "No Python GUI runtime found. Expected pythonw.exe or pyw.exe."
mshta "javascript:var s=new ActiveXObject('WScript.Shell');s.Popup('Python GUI runtime was not found.\n\nInstall Python or create .venv, then retry.',10,'Nexus launcher error',16);close();"
exit /b 1

:record
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
echo [%date% %time%] %~1>>"%LOG_FILE%"
exit /b 0
