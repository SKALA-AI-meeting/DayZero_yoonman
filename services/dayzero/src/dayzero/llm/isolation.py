"""Local macOS launcher; it never copies ChatGPT credentials or relaxes sandboxing.

Linux/container support is deliberately unavailable until independently verified.
"""
import os
import platform
import shutil
import subprocess
from pathlib import Path
from dayzero.llm.schemas import ModelError

PROFILE = 'macos-seatbelt-v1'
CLI_VERSION = 'codex-cli 0.153.4'


def native_binary(configured):
    found = shutil.which(configured)
    if not found:
        raise ModelError('cli_unavailable')
    path = Path(found).resolve()
    if path.name == 'codex.js':
        path = path.parents[1] / 'node_modules/@openai/codex-darwin-arm64/vendor/aarch64-apple-darwin/bin/codex'
    if not path.is_file():
        raise ModelError('cli_native_binary_required')
    with path.open('rb') as binary:
        is_script = binary.read(2) == b'#!'
    if is_script:
        raise ModelError('cli_native_binary_required')
    return path


def profile(binary: Path, runtime: Path, auth: Path):
    def literal(path):
        # All values are OS-derived paths; still escape SBPL string literals.
        return str(path).replace('\\', '\\\\').replace('"', '\\"')
    return '''(version 1)
(deny default)
(import "system.sb")
(allow process-fork)
(allow process-exec (literal "BINARY"))
(allow file-map-executable (literal "BINARY"))
(allow sysctl-read)
(allow mach-lookup)
(allow network*)
(allow file-read-metadata)
(allow file-read* (literal "BINARY") (literal "AUTH") (subpath "RUNTIME")
 (literal "/private/etc/resolv.conf") (literal "/private/etc/hosts")
 (subpath "/private/etc/ssl"))
(allow file-write* (subpath "RUNTIME") (literal "/dev/null"))
'''.replace('BINARY',literal(binary)).replace('RUNTIME',literal(runtime)).replace('AUTH',literal(auth))


def prepare(configured: str, runtime: Path):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64' or not Path('/usr/bin/sandbox-exec').exists():
        raise ModelError('cli_platform_unsupported')
    binary = native_binary(configured)
    version = subprocess.run([str(binary), '--version'], capture_output=True, text=True, timeout=5,
        env={k: v for k, v in os.environ.items() if k in {'PATH', 'HOME', 'LANG'}})
    if version.returncode or version.stdout.strip() != CLI_VERSION:
        raise ModelError('cli_version_changed')
    auth_root = Path(os.environ.get('CODEX_HOME', Path.home()/'.codex')).resolve()
    auth = auth_root/'auth.json'
    if not auth.is_file():
        raise ModelError('cli_login_required')
    runtime = runtime.resolve()
    cli_home = runtime/'codex'
    cli_home.mkdir()
    (cli_home/'auth.json').symlink_to(auth)
    return binary, cli_home, profile(binary, runtime, auth)


DISABLED = ['shell_tool', 'apps', 'plugins', 'hooks', 'multi_agent', 'browser_use', 'browser_use_external',
            'computer_use', 'image_generation', 'in_app_browser', 'in_app_chat', 'memories',
            'skill_search', 'skill_mcp_dependency_install', 'tool_suggest', 'sleep_tool', 'goals']


def flags():
    result = ['-c', 'approval_policy="never"', '-c', 'web_search="disabled"', '-c', 'features.view_image=false']
    for name in DISABLED:
        result += ['-c', 'features.'+name+'=false']
    return result
