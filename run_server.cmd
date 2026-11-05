@echo off
REM Launch the bridge server on stdio.
REM
REM Claude Desktop starts this for you once it is registered (see README).
REM This file is here so you can start the server by hand when debugging. Do NOT
REM add anything that prints to stdout here: stdout is the JSON-RPC channel, and a
REM stray line of text breaks the transport.
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" "%~dp0server.py"
