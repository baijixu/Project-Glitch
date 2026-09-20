@echo off
rem Runs the whole test suite with Brain's own virtualenv. Extra arguments go to unittest,
rem e.g.  run-tests.bat tests.test_memory   or   run-tests.bat -v
cd /d "%~dp0"
brain\.venv\Scripts\python.exe -m unittest discover -s tests -t . %*
