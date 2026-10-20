"""Launch a local process with the API key loaded by the user's zsh startup.

Startup output and credentials stay in memory. Nothing is copied into .env.
"""
import os
import subprocess
import sys


def main():
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python scripts/with_zsh_api_key.py COMMAND [ARGS...]')
    loaded = subprocess.run(['/bin/zsh', '-lic',
        'printf "\\0DAYZERO_KEY\\0%s\\0" "$OPENAI_API_KEY"'],
        capture_output=True, timeout=20)
    marker = b'\0DAYZERO_KEY\0'
    key = loaded.stdout.rsplit(marker, 1)[-1].split(b'\0', 1)[0].decode() if marker in loaded.stdout else ''
    if loaded.returncode or not key:
        raise SystemExit('OPENAI_API_KEY was not loaded by zsh')
    os.execvpe(sys.argv[1], sys.argv[1:], {**os.environ, 'OPENAI_API_KEY': key})


if __name__ == '__main__':
    main()
