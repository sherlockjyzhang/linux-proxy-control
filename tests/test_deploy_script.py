import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY_SCRIPT = ROOT / "deploy" / "update-and-restart.sh"
TRANSITION_NGINX = ROOT / "deploy" / "nginx.pi.conf"


def _git_bash():
    """Return Git for Windows Bash when it is installed."""
    candidates = []
    git = shutil.which("git")
    if git:
        git_path = Path(git).resolve()
        candidates.extend((git_path.parent.parent / "bin" / "bash.exe", git_path.parent / "bash.exe"))
    candidates.extend(
        Path(path) / "Git" / "bin" / "bash.exe"
        for path in os.environ.get("ProgramFiles", "").split(os.pathsep)
        if path
    )
    candidates.append(Path(shutil.which("bash")) if shutil.which("bash") else None)
    return next((str(path) for path in candidates if path and path.is_file()), None)


@pytest.fixture(scope="module")
def bash():
    bash_path = _git_bash()
    if bash_path is None:
        pytest.skip("Git for Windows Bash is unavailable")
    return bash_path


def run_deploy(bash, *args, deploy_ref=None):
    env = os.environ.copy()
    if deploy_ref is None:
        env.pop("DEPLOY_REF", None)
    else:
        env["DEPLOY_REF"] = deploy_ref
    return subprocess.run(
        [bash, str(DEPLOY_SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def run_ref_validation(bash, *args, deploy_ref=None):
    """Run only update-and-restart.sh's ref-validation prefix."""
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    validation_prefix = script.split("restore_config_file() {", 1)[0]
    env = os.environ.copy()
    if deploy_ref is None:
        env.pop("DEPLOY_REF", None)
    else:
        env["DEPLOY_REF"] = deploy_ref
    return subprocess.run(
        [bash, "-s", "--", *args],
        cwd=ROOT,
        env=env,
        input=validation_prefix,
        capture_output=True,
        text=True,
        check=False,
    )


def run_nginx_default_server_parser(bash, nginx_dump):
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    parser_start = script.index('printf \'%s\\n\' "$nginx_dump" | awk')
    awk_command_start = script.index("| awk", parser_start)
    program_start = script.index("'", awk_command_start)
    program_end = script.index("' || default_server_status=$?", program_start)
    awk_program = script[program_start + 1 : program_end]
    harness = (
        'nginx_dump="$1"\n'
        "printf '%s\\n' \"$nginx_dump\" | awk "
        '-v managed_site="$2" -v managed_link="$3" '
        f"'{awk_program}'\n"
    )
    return subprocess.run(
        [
            bash,
            "-s",
            "--",
            nginx_dump,
            "/etc/nginx/sites-available/rpb5-proxy-control",
            "/etc/nginx/sites-enabled/rpb5-proxy-control",
        ],
        cwd=ROOT,
        input=harness,
        capture_output=True,
        text=True,
        check=False,
    )


def run_env_file_ownership_guard(bash, service_uid, service_gid, root_gid, env_uid, env_gid):
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    read_marker = 'read -r env_uid env_gid env_mode <<<"$env_stat"'
    read_start = script.index(read_marker)
    guard_start = script.index("\n", read_start) + 1
    guard_end = script.index('\n\nif [[ "$SERVICE_USER" == "pi" ]]', guard_start)
    guard = script[guard_start:guard_end]
    harness = (
        'SERVICE_USER_UID="$1"\n'
        'SERVICE_USER_GID="$2"\n'
        'ROOT_GROUP_GID="$3"\n'
        'env_uid="$4"\n'
        'env_gid="$5"\n'
        'env_mode="$6"\n'
        'fail() { exit 1; }\n'
        + guard
        + "\n"
    )
    return subprocess.run(
        [bash, "-s", "--", service_uid, service_gid, root_gid, env_uid, env_gid, "600"],
        cwd=ROOT,
        input=harness,
        capture_output=True,
        text=True,
        check=False,
    )


def test_deploy_ref_and_positional_ref_are_rejected(bash):
    result = run_deploy(bash, "abc123", deploy_ref="def456")

    assert result.returncode == 1
    assert "DEPLOY_REF and a positional deployment ref cannot be used together" in result.stderr


def test_multiple_positional_refs_are_rejected(bash):
    result = run_deploy(bash, "abc123", "def456")

    assert result.returncode == 1
    assert "Usage: DEPLOY_REF=<commit-or-ref>" in result.stderr


def test_empty_deploy_ref_is_rejected(bash):
    result = run_deploy(bash, deploy_ref="")

    assert result.returncode == 1
    assert "DEPLOY_REF must not be empty" in result.stderr


@pytest.mark.parametrize("ref", ["main", "v1.2.3"])
def test_explicit_branch_or_tag_ref_is_rejected_at_validation(bash, ref):
    result = run_ref_validation(bash, deploy_ref=ref)

    assert result.returncode == 1
    assert "Explicit deployment ref must be a full 40-hex commit SHA" in result.stderr
    assert "sudo" not in result.stdout + result.stderr


@pytest.mark.parametrize(
    ("args", "deploy_ref"),
    [
        ((), "0123456789abcdef0123456789abcdef01234567"),
        (("0123456789abcdef0123456789abcdef01234567",), None),
    ],
)
def test_full_sha_ref_is_accepted_at_validation_without_system_operations(bash, args, deploy_ref):
    result = run_ref_validation(bash, *args, deploy_ref=deploy_ref)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    "listen_address",
    [
        "0.0.0.0:80",
        "*:80",
        "127.0.0.1:80",
        "[::]:80",
        "[::1]:80",
        "[2001:db8::1]:80",
    ],
)
def test_nginx_default_server_parser_detects_address_qualified_listens(bash, listen_address):
    nginx_dump = (
        "# configuration file /etc/nginx/conf.d/unmanaged.conf:\n"
        "server {\n"
        f"    listen {listen_address} default_server;\n"
        "}\n"
    )
    result = run_nginx_default_server_parser(bash, nginx_dump)

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("env_uid", "env_gid", "expected_returncode"),
    [
        ("0", "0", 0),
        ("1001", "1002", 0),
        ("0", "1002", 1),
        ("1001", "0", 1),
        ("1001", "1003", 1),
        ("1003", "1002", 1),
    ],
)
def test_env_file_ownership_accepts_root_or_service_primary_group(
    bash, env_uid, env_gid, expected_returncode
):
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'if ! SERVICE_USER_GID="$(id -g "$SERVICE_USER" 2>/dev/null)"; then' in script
    assert 'if ! env_stat="$(sudo stat -c \'%u %g %a\' -- "$ENV_FILE" 2>/dev/null)"; then' in script
    assert 'if ! ROOT_GROUP_GID="$(id -g root 2>/dev/null)"; then' in script
    assert 'read -r env_uid env_gid env_mode <<<"$env_stat"' in script
    assert 'sudo -u "$SERVICE_USER" -- test -r "$ENV_FILE"' not in script
    result = run_env_file_ownership_guard(bash, "1001", "1002", "0", env_uid, env_gid)

    assert result.returncode == expected_returncode
    assert result.stdout == ""
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("mode", "expected_returncode"),
    [
        ("600", 0),
        ("400", 1),
        ("640", 1),
        ("644", 1),
        ("660", 1),
        ("700", 1),
    ],
)
def test_env_file_mode_guard_accepts_only_0600(bash, mode, expected_returncode):
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    guard = re.search(
        r'if \[\[ "\$env_mode" != "(?P<required>[0-7]{3})" \]\]; then\n.*?\nfi',
        script,
        re.DOTALL,
    )

    assert guard is not None
    assert guard.group("required") == "600"
    result = subprocess.run(
        [bash, "-s", "--", mode],
        cwd=ROOT,
        input='env_mode="$1"\nfail() { exit 1; }\n' + guard.group(0) + "\n",
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == expected_returncode
    assert result.stdout == ""
    assert result.stderr == ""


def test_transition_nginx_has_no_default_server():
    config = TRANSITION_NGINX.read_text(encoding="utf-8")

    assert "default_server" not in config


def test_sudo_preflight_is_noninteractive_with_tty_only_fallback():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    helper_start = script.index("ensure_sudo_access() {")
    helper_end = script.index("\n}", helper_start) + 2
    preflight = script[helper_start:helper_end]

    assert "if sudo -n true >/dev/null 2>&1; then" in preflight
    assert "if [[ -t 0 && -t 1 ]]; then" in preflight
    assert preflight.index("sudo -n true") < preflight.index("sudo -v")
    assert preflight.count("sudo -v") == 1
    assert preflight.index("sudo -v") > preflight.index("if [[ -t 0 && -t 1 ]]; then")
    assert "Noninteractive deployment requires passwordless sudo; sudo -n true failed." in preflight
    assert script.count("sudo -v") == 1
    assert "\nensure_sudo_access\n" in script


def test_deploy_script_retains_preflight_snapshot_rollback_and_asset_guards():
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")

    assert 'if [[ ! -f "$ENV_FILE" ]]; then' in script
    assert 'if sudo test -L "$ENV_FILE"; then' in script
    assert 'if ! sudo test -f "$ENV_FILE"; then' in script
    assert 'if ! env_stat="$(sudo stat -c \'%u %g %a\' -- "$ENV_FILE" 2>/dev/null)"; then' in script
    assert 'if ! SERVICE_USER_UID="$(id -u "$SERVICE_USER" 2>/dev/null)"; then' in script
    assert 'if [[ "$env_uid" == "0" ]]; then' in script
    assert 'if [[ ! "$env_uid" =~ ^[0-9]+$ || ! "$env_gid" =~ ^[0-9]+$ ]]; then' in script
    assert 'if [[ "$env_gid" != "${ROOT_GROUP_GID:-0}" ]]; then' in script
    assert 'elif [[ "$env_uid" == "$SERVICE_USER_UID" ]]; then' in script
    assert 'if [[ "$env_gid" != "$SERVICE_USER_GID" ]]; then' in script
    assert 'if [[ "$env_mode" != "600" ]]; then' in script
    assert "Environment file must have mode 0600" in script
    assert 'sudo -u "$SERVICE_USER" -- test -r "$ENV_FILE"' not in script
    assert 'if [[ "$DEPLOY_REF_EXPLICIT" -eq 1 && ! "$DEPLOY_REF" =~ ^[[:xdigit:]]{40}$ ]]; then' in script
    assert 'if sudo test -L "$DEPLOY_SNAPSHOT_ROOT"; then' in script
    assert 'if [[ "$root_uid" != "0" ]]; then' in script
    assert 'if [[ -z "$root_mode" || "${root_mode: -2}" != "00" ]]; then' in script
    assert "default_server_status=0" in script
    assert "Conflicting nginx port-80 default_server exists outside the managed site" in script
    assert 'SNAPSHOT_READY=1' in script
    assert "rollback_install()" in script
    assert '[[ -f "$SERVICE_ASSET" ]] || fail' in script
    assert '[[ -f "$NGINX_ASSET" ]] || fail' in script
