"""Run a Python service with literal log paths, independent of PowerShell redirection."""
from pathlib import Path
import runpy
import sys


if __name__ == '__main__':
    stdout, stderr, script, *args = sys.argv[1:]
    sys.stderr = open(stderr, 'w', encoding='utf-8', buffering=1)
    sys.stdout = open(stdout, 'w', encoding='utf-8', buffering=1)
    sys.argv = [script, *args]
    sys.path[0] = str(Path(script).parent)
    runpy.run_path(script, run_name='__main__')
