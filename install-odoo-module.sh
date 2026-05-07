#!/usr/bin/env bash
set -euo pipefail

REPO_URL="https://github.com/hidekiyamamoto/odoo-mcp"
MODULE_NAME="perfect_odoo_mcp"
LEGACY_MODULE_NAMES=("odoo_mcp")
WORKDIR=""
FORCE=0
DATABASE=""
ODOO_BIN=""
ADDONS_DIR=""
BRANCH=""
CONFIG_FILE=""

usage() {
    cat <<'EOF'
Install Perfect Odoo MCP into a local Odoo addons directory.

Usage:
  ./install-odoo-module.sh [options]

Options:
  -d, --database DB       Refresh Odoo's app list for this database after copying.
  --odoo-bin PATH         Odoo executable to use. Defaults to odoo or odoo-bin in PATH.
  --addons-dir PATH       Target addons directory. Defaults to Odoo's core addons directory.
  --config PATH           Odoo config file to inspect for addons_path fallback candidates.
  --branch BRANCH         Git branch to clone. Defaults to the detected Odoo major version, e.g. 17.0.
  -f, --force             Remove an existing perfect_odoo_mcp/odoo_mcp directory before copying.
  -h, --help              Show this help.

Examples:
  ./install-odoo-module.sh
  ./install-odoo-module.sh -d my_database
  /bin/bash ./install-odoo-module.sh -f -d my_database
  ./install-odoo-module.sh --addons-dir /mnt/extra-addons -d my_database
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
  /bin/bash ./install-odoo-module.sh --force -d ${DATABASE:-YOUR_DATABASE}
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
    if command -v odoo >/dev/null 2>&1; then
        ODOO_BIN="$(command -v odoo)"
    elif command -v odoo-bin >/dev/null 2>&1; then
        ODOO_BIN="$(command -v odoo-bin)"
    else
        fail "Could not find odoo or odoo-bin in PATH. Pass --odoo-bin /path/to/odoo."
    fi
fi

[[ -x "$ODOO_BIN" ]] || fail "Odoo executable is not runnable: $ODOO_BIN"
command -v git >/dev/null 2>&1 || fail "git is required."

ODOO_VERSION="$("$ODOO_BIN" --version 2>/dev/null | head -n 1 || true)"
ODOO_FULL_VERSION="$(printf '%s\n' "$ODOO_VERSION" | sed -nE 's/.* ([0-9]+(\.[0-9]+)+).*/\1/p')"
ODOO_SERIES="$(printf '%s\n' "$ODOO_FULL_VERSION" | sed -nE 's/^([0-9]+\.[0-9]+).*/\1/p')"
ODOO_MAJOR="$(printf '%s\n' "$ODOO_FULL_VERSION" | sed -nE 's/^([0-9]+).*/\1/p')"

if [[ -z "$ODOO_MAJOR" ]]; then
    ODOO_FULL_VERSION="$(python3 - <<'PY'
try:
    import odoo
    print(str(odoo.release.version).split("-", 1)[0])
except Exception:
    pass
PY
)"
    ODOO_SERIES="$(printf '%s\n' "$ODOO_FULL_VERSION" | sed -nE 's/^([0-9]+\.[0-9]+).*/\1/p')"
    ODOO_MAJOR="$(printf '%s\n' "$ODOO_FULL_VERSION" | sed -nE 's/^([0-9]+).*/\1/p')"
fi

[[ -n "$ODOO_MAJOR" ]] || fail "Could not detect Odoo version."
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

echo "Installed $MODULE_NAME into $TARGET_DIR"

if [[ -n "$DATABASE" ]]; then
    echo "Refreshing Odoo app list for database $DATABASE"
    ODOO_SHELL_ARGS=(shell -d "$DATABASE" --no-http)
    if [[ -n "$CONFIG_FILE" ]]; then
        ODOO_SHELL_ARGS=(-c "$CONFIG_FILE" "${ODOO_SHELL_ARGS[@]}")
    fi
    "$ODOO_BIN" "${ODOO_SHELL_ARGS[@]}" <<'PY'
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
  1. Restart Odoo if this addons directory is loaded by a running service.
  2. Open Apps, remove any app search filter if needed, and install "Perfect Odoo MCP".
EOF

if [[ -z "$DATABASE" ]]; then
    cat <<EOF

To refresh the app list from the command line, rerun with:
  ./install-odoo-module.sh -d YOUR_DATABASE
EOF
fi
