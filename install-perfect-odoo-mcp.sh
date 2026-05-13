#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/hidekiyamamoto/odoo-mcp"
INSTALLER_VERSION="v0.5"
MODULE_NAME="perfect_odoo_mcp"
LEGACY_MODULE_NAMES=("odoo_mcp")
SCRIPT_NAME="install-perfect-odoo-mcp.sh"
WORKDIR=""
FORCE=0
DATABASE="${DATABASE:-}"
ODOO_BIN="${ODOO_BIN:-}"
ODOO_RUNNER="${ODOO_RUNNER:-}"
ADDONS_DIR="${ADDONS_DIR:-}"
BRANCH="${BRANCH:-}"
CONFIG_FILE="${CONFIG_FILE:-}"
LIVE_ODOO_BIN=""
LIVE_ODOO_RUNNER=""
LIVE_CONFIG_FILE=""
LIVE_ODOO_USER=""
ODOO_RUN_USER=""

usage() {
    cat <<EOF
Install Perfect Odoo MCP into a local Odoo addons directory.

Usage:
  ./$SCRIPT_NAME [options]

Options:
  -d, --database DB       Refresh Odoo's app list and upgrade the module in this database after copying.
  --addons-dir PATH       Target Odoo addons directory. Defaults to auto-detection.
  --odoo-bin PATH         Odoo executable to use. Auto-detected from PATH, live processes, systemd, or common paths.
  --config PATH           Odoo config file to inspect for addons_path fallback candidates.
  --branch BRANCH         Git branch to clone. Defaults to the detected Odoo major version, e.g. 17.0.
  -f, --force             Remove an existing perfect_odoo_mcp/odoo_mcp directory before copying.
  -h, --help              Show this help.

Examples:
  ./$SCRIPT_NAME
  ./$SCRIPT_NAME -d my_database
  /bin/bash ./$SCRIPT_NAME -f -d my_database
  ./$SCRIPT_NAME --addons-dir /mnt/extra-addons -d my_database
  ./$SCRIPT_NAME --addons-dir /mnt/extra-addons --config /etc/odoo/odoo.conf
EOF
}

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

branch_exists() {
    git ls-remote --exit-code --heads "$REPO_URL" "$1" >/dev/null 2>&1
}

select_branch() {
    if [[ -n "$BRANCH" ]]; then
        echo "$BRANCH"
        return
    fi

    local candidates=()
    if [[ -n "${ODOO_SERIES:-}" ]]; then
        candidates+=("$ODOO_SERIES")
    fi
    if [[ -n "${ODOO_FULL_VERSION:-}" && "$ODOO_FULL_VERSION" != "${ODOO_SERIES:-}" ]]; then
        candidates+=("$ODOO_FULL_VERSION")
    fi
    if [[ -n "${ODOO_MAJOR:-}" ]]; then
        candidates+=("${ODOO_MAJOR}.0" "$ODOO_MAJOR")
    fi

    local candidate
    local seen=" "
    for candidate in "${candidates[@]}"; do
        [[ -n "$candidate" ]] || continue
        if [[ "$seen" == *" $candidate "* ]]; then
            continue
        fi
        seen="$seen$candidate "
        if branch_exists "$candidate"; then
            echo "$candidate"
            return
        fi
    done

    echo ""
}

cleanup() {
    if [[ -n "$WORKDIR" && -d "$WORKDIR" ]]; then
        rm -rf "$WORKDIR"
    fi
}
trap cleanup EXIT

handle_existing_addon() {
    local target_dir="$1"
    local label="$2"

    [[ -e "$target_dir" ]] || return 0
    if [[ "$FORCE" -eq 1 ]]; then
        echo "$label found. Removing $target_dir because --force was provided."
        rm -rf "$target_dir"
        return 0
    fi

    cat >&2 <<EOF
ERROR: $label already exists at:
  $target_dir

The installer will not overwrite or back up existing addon directories automatically.
Remove it yourself, or rerun with --force to delete it before installing:
  curl -fsSL $REPO_URL/main/$SCRIPT_NAME | bash -s -- --force --database ${DATABASE:-YOUR_DATABASE}

If you saved the installer locally, use:
  /bin/bash ./$SCRIPT_NAME --force --database ${DATABASE:-YOUR_DATABASE}
EOF
    exit 1
}

reference_addon_dir() {
    local candidate
    for candidate in base web base_setup; do
        if [[ -d "$ADDONS_DIR/$candidate" && "$ADDONS_DIR/$candidate" != "$TARGET_DIR" ]]; then
            echo "$ADDONS_DIR/$candidate"
            return 0
        fi
    done

    for candidate in "$ADDONS_DIR"/*; do
        if [[ -d "$candidate" && "$candidate" != "$TARGET_DIR" ]]; then
            echo "$candidate"
            return 0
        fi
    done
}

match_addon_filesystem_rights() {
    local target_dir="$1"
    local reference_dir
    reference_dir="$(reference_addon_dir || true)"
    [[ -n "$reference_dir" ]] || fail "Could not find a reference addon in $ADDONS_DIR for filesystem permissions."

    local reference_file=""
    for candidate in "$reference_dir/__manifest__.py" "$reference_dir/__openerp__.py"; do
        if [[ -f "$candidate" ]]; then
            reference_file="$candidate"
            break
        fi
    done
    if [[ -z "$reference_file" ]]; then
        reference_file="$(find "$reference_dir" -type f -print -quit)"
    fi
    [[ -n "$reference_file" ]] || fail "Reference addon has no files for filesystem permissions: $reference_dir"

    echo "Matching filesystem owner and permissions from $(basename "$reference_dir")."
    chown -R --reference="$reference_dir" "$target_dir"
    find "$target_dir" -type d -exec chmod --reference="$reference_dir" {} +
    find "$target_dir" -type f -exec chmod --reference="$reference_file" {} +
}

find_live_odoo_process() {
    ODOO_BIN="$ODOO_BIN" CONFIG_FILE="$CONFIG_FILE" ADDONS_DIR="$ADDONS_DIR" python3 - <<'PY'
import os
import pwd
import shlex


def read_cmdline(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as handle:
            data = handle.read().rstrip(b"\0")
    except OSError:
        return []
    return [part.decode(errors="replace") for part in data.split(b"\0") if part]


def looks_like_odoo_path(path):
    base = os.path.basename(path)
    lowered = path.lower()
    return base in {"odoo", "odoo-bin"} or "/odoo-bin" in lowered or lowered.endswith("/odoo")


def command_parts(argv):
    runner = ""
    odoo_bin = ""
    if not argv:
        return runner, odoo_bin

    first_base = os.path.basename(argv[0])
    if first_base.startswith("python") or first_base in {"python", "python3"}:
        for arg in argv[1:6]:
            if arg.startswith("-"):
                continue
            if looks_like_odoo_path(arg):
                runner = argv[0]
                odoo_bin = arg
                break

    if not odoo_bin:
        for arg in argv[:8]:
            if looks_like_odoo_path(arg):
                odoo_bin = arg
                break

    return runner, odoo_bin


def config_arg(argv):
    for index, arg in enumerate(argv):
        if arg in {"-c", "--config"} and index + 1 < len(argv):
            return argv[index + 1]
        if arg.startswith("--config="):
            return arg.split("=", 1)[1]
    return ""


def process_user(pid):
    try:
        return pwd.getpwuid(os.stat(f"/proc/{pid}").st_uid).pw_name
    except Exception:
        return ""


odoo_bin = os.path.realpath(os.environ.get("ODOO_BIN") or "")
config_file = os.path.realpath(os.environ.get("CONFIG_FILE") or "")
addons_dir = os.path.realpath(os.environ.get("ADDONS_DIR") or "")
matches = []

for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    argv = read_cmdline(name)
    cmd = " ".join(shlex.quote(part) for part in argv)
    lowered = " ".join(argv).lower()
    if "odoo" not in lowered or "install-odoo-module" in lowered or "install-perfect-odoo-mcp" in lowered:
        continue
    if "postgres:" in lowered or "node " in lowered:
        continue

    score = 0
    if "/odoo" in lowered or "odoo-bin" in lowered:
        score += 20
    normalized_argv = " ".join(os.path.realpath(part) for part in argv)
    if config_file and config_file in normalized_argv:
        score += 60
    if odoo_bin and odoo_bin in normalized_argv:
        score += 40
    if addons_dir and addons_dir in normalized_argv:
        score += 10
    if "--config" in argv or "-c" in argv or any(part.startswith("--config=") for part in argv):
        score += 5
    if score:
        runner, matched_bin = command_parts(argv)
        matches.append((score, int(name), cmd, runner, matched_bin, config_arg(argv), process_user(name)))

if not matches:
    raise SystemExit(1)

matches.sort(key=lambda item: (-item[0], item[1]))
_, pid, cmd, runner, matched_bin, matched_config, matched_user = matches[0]
print(f"pid={pid}")
print(f"cmd={cmd}")
print(f"runner={runner}")
print(f"bin={matched_bin}")
print(f"config={matched_config}")
print(f"user={matched_user}")
PY
}

discover_odoo_bin() {
    CONFIG_FILE="$CONFIG_FILE" python3 - <<'PY'
import glob
import os
import re
import shlex
import shutil
import subprocess
import sys


def is_file(path):
    return path and os.path.isfile(path)


def is_executable_file(path):
    return is_file(path) and os.access(path, os.X_OK)


def normalize(path):
    if not path:
        return ""
    return os.path.realpath(os.path.abspath(os.path.expanduser(path)))


def add(candidates, path, score, source, runner=""):
    if path and not os.path.isabs(path):
        path = shutil.which(path) or path
    if runner and not os.path.isabs(runner):
        runner = shutil.which(runner) or runner
    path = normalize(path)
    runner = normalize(runner)
    if runner:
        if is_executable_file(runner) and is_file(path):
            candidates.append((score, path, source, runner))
    elif is_executable_file(path):
        candidates.append((score, path, source, ""))


def looks_like_odoo_path(path):
    base = os.path.basename(path)
    lowered = path.lower()
    return base in {"odoo", "odoo-bin"} or "/odoo-bin" in lowered or lowered.endswith("/odoo")


def command_candidates_from_argv(argv):
    if not argv:
        return []

    candidates = []
    first = argv[0]
    if looks_like_odoo_path(first):
        candidates.append((first, ""))

    first_base = os.path.basename(first)
    if first_base.startswith("python") or first_base in {"python", "python3"}:
        for arg in argv[1:6]:
            if arg.startswith("-"):
                continue
            if looks_like_odoo_path(arg):
                candidates.append((arg, first))
                break

    for arg in argv[:8]:
        if looks_like_odoo_path(arg):
            candidates.append((arg, ""))

    return candidates


def add_execstart_paths(candidates, text, score, source):
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("ExecStart"):
            continue
        value = line.split("=", 1)[1] if "=" in line else line
        try:
            argv = shlex.split(value)
        except ValueError:
            argv = []
        for path, runner in command_candidates_from_argv(argv):
            add(candidates, path, score, source, runner)
        for path in re.findall(r"(/[^\s;]*odoo(?:-bin)?)", line):
            if looks_like_odoo_path(path):
                add(candidates, path, score, source)


def read_proc_cmdlines(candidates):
    proc_dir = "/proc"
    if not os.path.isdir(proc_dir):
        return

    wanted_config = normalize(os.environ.get("CONFIG_FILE", ""))
    for name in os.listdir(proc_dir):
        if not name.isdigit():
            continue
        try:
            with open(os.path.join(proc_dir, name, "cmdline"), "rb") as handle:
                raw = handle.read().rstrip(b"\0")
        except OSError:
            continue
        if not raw:
            continue

        argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        lowered = " ".join(argv).lower()
        if "odoo" not in lowered or "install-odoo-module" in lowered or "install-perfect-odoo-mcp" in lowered:
            continue
        if "postgres:" in lowered or "node " in lowered:
            continue

        score = 120
        if wanted_config and wanted_config in " ".join(normalize(part) for part in argv):
            score += 30
        if "--config" in argv or "-c" in argv or any(part.startswith("--config=") for part in argv):
            score += 5
        for path, runner in command_candidates_from_argv(argv):
            add(candidates, path, score, f"process:{name}", runner)


def read_systemd_units(candidates):
    if not shutil.which("systemctl"):
        return
    try:
        units = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).stdout
    except Exception:
        return

    for raw_line in units.splitlines():
        parts = raw_line.split()
        if not parts:
            continue
        unit = parts[0]
        if "odoo" not in raw_line.lower():
            continue
        try:
            show = subprocess.run(
                ["systemctl", "show", "-p", "ExecStart", "--value", unit],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            ).stdout
        except Exception:
            continue

        add_execstart_paths(candidates, show, 110, f"systemd:{unit}")
        for path in re.findall(r"(?:argv\[\]=|path=)?(/[^\s;]+)", show):
            if looks_like_odoo_path(path):
                add(candidates, path, 110, f"systemd:{unit}")

        try:
            cat = subprocess.run(
                ["systemctl", "cat", unit],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            ).stdout
        except Exception:
            cat = ""
        if cat:
            add_execstart_paths(candidates, cat, 115, f"systemd-cat:{unit}")

        try:
            unit_path = subprocess.run(
                ["systemctl", "show", "-p", "FragmentPath", "--value", unit],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            ).stdout.strip()
        except Exception:
            continue
        if os.path.isfile(unit_path):
            try:
                with open(unit_path, encoding="utf-8") as handle:
                    add_execstart_paths(candidates, handle.read(), 115, f"systemd-file:{unit}")
            except OSError:
                pass

        for directory in ("/etc/systemd/system", "/run/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system"):
            direct_unit_path = os.path.join(directory, unit)
            if os.path.isfile(direct_unit_path):
                try:
                    with open(direct_unit_path, encoding="utf-8") as handle:
                        add_execstart_paths(candidates, handle.read(), 115, f"systemd-file:{unit}")
                except OSError:
                    pass


def read_systemd_unit_files(candidates):
    unit_paths = []
    for directory in ("/etc/systemd/system", "/run/systemd/system", "/usr/lib/systemd/system", "/lib/systemd/system"):
        unit_paths.extend(glob.glob(os.path.join(directory, "*odoo*.service")))

    for unit_path in unit_paths:
        try:
            with open(unit_path, encoding="utf-8") as handle:
                add_execstart_paths(candidates, handle.read(), 75, f"systemd-file:{os.path.basename(unit_path)}")
        except OSError:
            continue


candidates = []

for name in ("odoo", "odoo-bin"):
    add(candidates, shutil.which(name), 100, "PATH")

read_proc_cmdlines(candidates)
read_systemd_units(candidates)
read_systemd_unit_files(candidates)

common_patterns = (
    "/usr/bin/odoo",
    "/usr/bin/odoo-bin",
    "/usr/local/bin/odoo",
    "/usr/local/bin/odoo-bin",
    "/opt/odoo/odoo-bin",
    "/opt/odoo/odoo/odoo-bin",
    "/opt/odoo*/odoo-bin",
    "/opt/odoo*/odoo/odoo-bin",
    "/opt/odoo*/venv/bin/odoo",
    "/opt/odoo*/.venv/bin/odoo",
    "/home/odoo/odoo-bin",
    "/home/odoo/odoo/odoo-bin",
    "/srv/odoo*/odoo-bin",
    "/srv/odoo*/odoo/odoo-bin",
)
for pattern in common_patterns:
    for path in glob.glob(pattern):
        add(candidates, path, 50, "common")

deduped = {}
for score, path, source, runner in candidates:
    key = (path, runner)
    current = deduped.get(key)
    if current is None or score > current[0]:
        deduped[key] = (score, source)

ranked = sorted(
    ((score, path, source, runner) for (path, runner), (score, source) in deduped.items()),
    reverse=True,
)
for _, path, source, runner in ranked:
    try:
        command = [runner, path, "--version"] if runner else [path, "--version"]
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        )
    except Exception:
        continue
    if result.returncode == 0 and "odoo" in result.stdout.lower():
        print(path)
        print(source)
        print(runner)
        sys.exit(0)

if ranked:
    _, path, source, runner = ranked[0]
    print(path)
    print(f"{source}:unverified")
    print(runner)
    sys.exit(0)

sys.exit(1)
PY
}

systemd_unit_for_pid() {
    local pid="$1"
    command -v systemctl >/dev/null 2>&1 || return 1

    local unit main_pid
    while read -r unit _; do
        [[ -n "$unit" ]] || continue
        main_pid="$(systemctl show -p MainPID --value "$unit" 2>/dev/null || true)"
        if [[ "$main_pid" == "$pid" ]]; then
            echo "$unit"
            return 0
        fi
    done < <(systemctl list-units --type=service --state=running --no-legend --no-pager 2>/dev/null)
    return 1
}

docker_container_for_pid() {
    local pid="$1"
    command -v docker >/dev/null 2>&1 || return 1

    local container container_pid
    for container in $(docker ps -q 2>/dev/null); do
        container_pid="$(docker inspect --format '{{.State.Pid}}' "$container" 2>/dev/null || true)"
        if [[ "$container_pid" == "$pid" ]]; then
            echo "$container"
            return 0
        fi
    done
    return 1
}

restart_live_odoo() {
    echo "Finding live Odoo process to restart."

    local process_info pid cmd unit container
    if ! process_info="$(find_live_odoo_process)"; then
        fail "Could not find a live Odoo process to restart. Start Odoo manually, then rerun the installer."
    fi
    pid="$(printf '%s\n' "$process_info" | sed -n 's/^pid=//p')"
    cmd="$(printf '%s\n' "$process_info" | sed -n 's/^cmd=//p')"
    LIVE_ODOO_RUNNER="$(printf '%s\n' "$process_info" | sed -n 's/^runner=//p')"
    LIVE_ODOO_BIN="$(printf '%s\n' "$process_info" | sed -n 's/^bin=//p')"
    LIVE_CONFIG_FILE="$(printf '%s\n' "$process_info" | sed -n 's/^config=//p')"
    LIVE_ODOO_USER="$(printf '%s\n' "$process_info" | sed -n 's/^user=//p')"
    echo "Selected live Odoo process PID $pid: $cmd"

    if unit="$(systemd_unit_for_pid "$pid")"; then
        echo "Restarting systemd unit $unit."
        systemctl restart "$unit"
        systemctl is-active --quiet "$unit" || fail "systemd unit $unit did not become active after restart."
        echo "Restarted $unit."
        return 0
    fi

    if container="$(docker_container_for_pid "$pid")"; then
        echo "Restarting Docker container $container."
        docker restart "$container" >/dev/null
        [[ "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null)" == "true" ]] \
            || fail "Docker container $container did not become running after restart."
        echo "Restarted Docker container $container."
        return 0
    fi

    cat >&2 <<EOF
ERROR: Found a live Odoo process but could not identify a restart manager.
PID: $pid
Command: $cmd

Restart Odoo manually, or run this installer on the host/container where Odoo is managed by systemd or Docker.
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -d|--database)
            DATABASE="${2:-}"
            [[ -n "$DATABASE" ]] || fail "Missing value for $1"
            shift 2
            ;;
        --odoo-bin)
            ODOO_BIN="${2:-}"
            [[ -n "$ODOO_BIN" ]] || fail "Missing value for $1"
            shift 2
            ;;
        --addons-dir)
            ADDONS_DIR="${2:-}"
            [[ -n "$ADDONS_DIR" ]] || fail "Missing value for $1"
            shift 2
            ;;
        --config)
            CONFIG_FILE="${2:-}"
            [[ -n "$CONFIG_FILE" ]] || fail "Missing value for $1"
            shift 2
            ;;
        --branch)
            BRANCH="${2:-}"
            [[ -n "$BRANCH" ]] || fail "Missing value for $1"
            shift 2
            ;;
        -f|--force)
            FORCE=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "Unknown option: $1"
            ;;
    esac
done

if [[ -z "$ODOO_BIN" ]]; then
    discovery_result="$(discover_odoo_bin || true)"
    if [[ -n "$discovery_result" ]]; then
        ODOO_BIN="$(printf '%s\n' "$discovery_result" | sed -n '1p')"
        ODOO_BIN_SOURCE="$(printf '%s\n' "$discovery_result" | sed -n '2p')"
        ODOO_RUNNER="$(printf '%s\n' "$discovery_result" | sed -n '3p')"
        if [[ -n "$ODOO_RUNNER" ]]; then
            echo "Discovered Odoo executable from ${ODOO_BIN_SOURCE:-auto-detection}: $ODOO_RUNNER $ODOO_BIN"
        else
            echo "Discovered Odoo executable from ${ODOO_BIN_SOURCE:-auto-detection}: $ODOO_BIN"
        fi
    else
        fail "installer $INSTALLER_VERSION: Could not find a runnable Odoo executable. Checked PATH, live Odoo processes, systemd Odoo services, and common install paths. Pass --odoo-bin /path/to/odoo-bin."
    fi
fi

if [[ -n "$ODOO_RUNNER" ]]; then
    [[ -x "$ODOO_RUNNER" ]] || fail "Odoo runner is not runnable: $ODOO_RUNNER"
    [[ -f "$ODOO_BIN" ]] || fail "Odoo script does not exist: $ODOO_BIN"
else
    [[ -x "$ODOO_BIN" ]] || fail "Odoo executable is not runnable: $ODOO_BIN"
fi
command -v git >/dev/null 2>&1 || fail "git is required."

run_odoo() {
    local command=()
    if [[ -n "$ODOO_RUNNER" ]]; then
        command=("$ODOO_RUNNER" "$ODOO_BIN" "$@")
    else
        command=("$ODOO_BIN" "$@")
    fi

    if [[ -n "$ODOO_RUN_USER" && "$(id -un)" != "$ODOO_RUN_USER" ]]; then
        if command -v runuser >/dev/null 2>&1; then
            runuser -u "$ODOO_RUN_USER" -- "${command[@]}"
        elif command -v sudo >/dev/null 2>&1; then
            sudo -H -u "$ODOO_RUN_USER" -- "${command[@]}"
        else
            fail "Need runuser or sudo to run Odoo as $ODOO_RUN_USER for database peer authentication."
        fi
    else
        "${command[@]}"
    fi
}

ODOO_VERSION="$(run_odoo --version 2>/dev/null | head -n 1 || true)"
ODOO_FULL_VERSION="$(printf '%s\n' "$ODOO_VERSION" | sed -nE 's/.* ([0-9]+(\.[0-9]+)+).*/\1/p')"
ODOO_SERIES="$(printf '%s\n' "$ODOO_FULL_VERSION" | sed -nE 's/^([0-9]+\.[0-9]+).*/\1/p')"
ODOO_MAJOR="$(printf '%s\n' "$ODOO_FULL_VERSION" | sed -nE 's/^([0-9]+).*/\1/p')"

if [[ -z "$ODOO_MAJOR" ]]; then
    ODOO_MAJOR="$(printf '%s\n%s\n%s\n' "$ODOO_BIN" "$ODOO_RUNNER" "${ODOO_BIN_SOURCE:-}" | sed -nE 's/.*odoo[-_]?([0-9]{2})([^0-9].*)?$/\1/p' | head -n 1)"
    if [[ -n "$ODOO_MAJOR" ]]; then
        ODOO_FULL_VERSION="${ODOO_MAJOR}.0"
        ODOO_SERIES="${ODOO_MAJOR}.0"
    fi
fi

if [[ -z "$ODOO_MAJOR" ]]; then
    ODOO_FULL_VERSION="$(ODOO_BIN="$ODOO_BIN" ODOO_RUNNER="$ODOO_RUNNER" python3 - <<'PY'
import os
import subprocess

odoo_bin = os.environ.get("ODOO_BIN", "")
odoo_runner = os.environ.get("ODOO_RUNNER", "")
commands = []
if odoo_runner and odoo_bin:
    commands.append([odoo_runner, odoo_bin, "shell", "--help"])
if odoo_bin:
    commands.append([odoo_bin, "shell", "--help"])

for command in commands:
    try:
        output = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=15,
        ).stdout
    except Exception:
        continue
    if output:
        import re
        match = re.search(r"Odoo[^0-9]*([0-9]+(?:\.[0-9]+)*)", output, re.I)
        if match:
            print(match.group(1))
            raise SystemExit(0)
PY
)"
    ODOO_SERIES="$(printf '%s\n' "$ODOO_FULL_VERSION" | sed -nE 's/^([0-9]+\.[0-9]+).*/\1/p')"
    ODOO_MAJOR="$(printf '%s\n' "$ODOO_FULL_VERSION" | sed -nE 's/^([0-9]+).*/\1/p')"
fi

if [[ -z "$ODOO_MAJOR" ]]; then
    ODOO_FULL_VERSION="$(run_odoo shell --help 2>&1 | sed -nE 's/.*Odoo[^0-9]*([0-9]+(\.[0-9]+)*).*/\1/ip' | head -n 1 || true)"
    ODOO_SERIES="$(printf '%s\n' "$ODOO_FULL_VERSION" | sed -nE 's/^([0-9]+\.[0-9]+).*/\1/p')"
    ODOO_MAJOR="$(printf '%s\n' "$ODOO_FULL_VERSION" | sed -nE 's/^([0-9]+).*/\1/p')"
fi

[[ -n "$ODOO_MAJOR" ]] || fail "installer $INSTALLER_VERSION: Could not detect Odoo version from --version, service path, or shell help."
SELECTED_BRANCH="$(select_branch)"

if [[ -z "$ADDONS_DIR" ]]; then
    ADDONS_DIR="$(CONFIG_FILE="$CONFIG_FILE" python3 - <<'PY'
import glob
import os
import sys

known_core_modules = (
    "base",
    "web",
    "base_setup",
    "bus",
    "mail",
    "portal",
    "auth_signup",
)
high_confidence_score = 5


def normalize(path):
    return os.path.abspath(os.path.expanduser(path.strip()))


def add_candidate(candidates, path, source):
    if not path:
        return
    path = normalize(path)
    if os.path.isdir(path):
        candidates.append((path, source))


def config_addons_paths(config_file):
    if not config_file or not os.path.isfile(config_file):
        return []

    paths = []
    current_value = None
    with open(config_file, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith(("#", ";")):
                continue
            if line.startswith("[") and line.endswith("]"):
                continue
            if "=" not in line:
                if current_value is not None:
                    current_value += line
                continue
            key, value = line.split("=", 1)
            if key.strip() == "addons_path":
                current_value = value.strip()

    if current_value:
        paths.extend(part.strip() for part in current_value.split(","))
    return paths


def score(path):
    return sum(
        1
        for module in known_core_modules
        if os.path.isfile(os.path.join(path, module, "__manifest__.py"))
        or os.path.isfile(os.path.join(path, module, "__openerp__.py"))
    )


candidates = []

try:
    import odoo.addons
except Exception:
    odoo = None
else:
    for path in odoo.addons.__path__:
        add_candidate(candidates, path, "odoo")

common_globs = (
    "/usr/lib/python*/dist-packages/odoo/addons",
    "/usr/local/lib/python*/dist-packages/odoo/addons",
    "/opt/odoo/odoo/addons",
    "/opt/odoo*/odoo/addons",
    "/usr/lib/odoo/addons",
)
for pattern in common_globs:
    for path in glob.glob(pattern):
        add_candidate(candidates, path, "common")

config_files = []
env_config = os.environ.get("CONFIG_FILE")
if env_config:
    config_files.append(env_config)
config_files.extend(("/etc/odoo/odoo.conf", "/etc/odoo.conf"))

for config_file in config_files:
    for path in config_addons_paths(config_file):
        add_candidate(candidates, path, "config")

deduped = {}
for path, source in candidates:
    deduped.setdefault(path, set()).add(source)

scored = []
for path, sources in deduped.items():
    path_score = score(path)
    source_rank = 0 if sources & {"odoo", "common"} else 1
    scored.append((path_score, source_rank, path))

high_confidence = [item for item in scored if item[0] >= high_confidence_score and item[1] == 0]
pool = high_confidence or scored
if not pool:
    sys.exit(1)

pool.sort(key=lambda item: (-item[0], item[1], item[2]))
print(pool[0][2])
PY
)"
fi

[[ -n "$ADDONS_DIR" ]] || fail "Could not detect Odoo addons directory. Pass --addons-dir /path/to/addons."
[[ -d "$ADDONS_DIR" ]] || fail "Addons directory does not exist: $ADDONS_DIR"
[[ -w "$ADDONS_DIR" ]] || fail "Addons directory is not writable: $ADDONS_DIR. Run with sudo or pass a writable --addons-dir."

WORKDIR="$(mktemp -d)"
CLONE_DIR="$WORKDIR/odoo-mcp"

echo "Detected Odoo: ${ODOO_VERSION:-Odoo $ODOO_MAJOR}"
echo "Using addons directory: $ADDONS_DIR"
if [[ -n "$SELECTED_BRANCH" ]]; then
    echo "Cloning $REPO_URL branch $SELECTED_BRANCH"
else
    echo "No matching version branch was found. Cloning the repository default branch."
fi

if [[ -n "$SELECTED_BRANCH" ]]; then
    git clone --depth 1 --branch "$SELECTED_BRANCH" "$REPO_URL" "$CLONE_DIR"
else
    git clone --depth 1 "$REPO_URL" "$CLONE_DIR"
fi

[[ -d "$CLONE_DIR/$MODULE_NAME" ]] || fail "The cloned repository does not contain $MODULE_NAME."

TARGET_DIR="$ADDONS_DIR/$MODULE_NAME"
handle_existing_addon "$TARGET_DIR" "Existing module"

for LEGACY_MODULE_NAME in "${LEGACY_MODULE_NAMES[@]}"; do
    handle_existing_addon "$ADDONS_DIR/$LEGACY_MODULE_NAME" "Legacy module directory"
done

cp -a "$CLONE_DIR/$MODULE_NAME" "$TARGET_DIR"
match_addon_filesystem_rights "$TARGET_DIR"

echo "Installed $MODULE_NAME into $TARGET_DIR"
restart_live_odoo

if [[ -n "$DATABASE" ]]; then
    if [[ -n "$LIVE_ODOO_BIN" ]]; then
        ODOO_BIN="$LIVE_ODOO_BIN"
        ODOO_RUNNER="$LIVE_ODOO_RUNNER"
        echo "Using live Odoo command for app-list refresh: ${ODOO_RUNNER:+$ODOO_RUNNER }$ODOO_BIN"
    fi
    if [[ -z "$CONFIG_FILE" && -n "$LIVE_CONFIG_FILE" ]]; then
        CONFIG_FILE="$LIVE_CONFIG_FILE"
        echo "Using live Odoo config for app-list refresh: $CONFIG_FILE"
    fi
    if [[ -n "$LIVE_ODOO_USER" ]]; then
        ODOO_RUN_USER="$LIVE_ODOO_USER"
        echo "Using live Odoo user for app-list refresh: $ODOO_RUN_USER"
    fi
    echo "Refreshing Odoo app list for database $DATABASE"
    ODOO_SHELL_ARGS=(shell -d "$DATABASE" --no-http)
    if [[ -n "$CONFIG_FILE" ]]; then
        ODOO_SHELL_ARGS=(shell -c "$CONFIG_FILE" -d "$DATABASE" --no-http)
    fi
    run_odoo "${ODOO_SHELL_ARGS[@]}" <<'PY'
env["ir.module.module"].update_list()
module = env["ir.module.module"].search([("name", "=", "perfect_odoo_mcp")], limit=1)
if module and module.state == "installed":
    module.button_immediate_upgrade()
env.cr.commit()
PY
    echo "Odoo app list refreshed. Installed module was upgraded if already present."
else
    echo "No database was provided, so the Odoo app list was not refreshed automatically."
fi

cat <<EOF

All set. Perfect Odoo MCP is in place.

Next:
  1. Odoo has been restarted so Python models/controllers are reloaded.
  2. Open Apps, remove any app search filter if needed, and install "Perfect Odoo MCP".
EOF

if [[ -z "$DATABASE" ]]; then
    cat <<EOF

To refresh the app list from the command line, rerun with:
  ./$SCRIPT_NAME --database YOUR_DATABASE
EOF
fi
