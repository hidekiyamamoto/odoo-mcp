import json
import os
import shlex
import shutil
import subprocess
import tempfile

from odoo import release
from odoo.http import request
from odoo.tools import config as odoo_config

from .const import MODULE_EDITING_ENABLED_PARAM, MODULE_EDITING_MODULES_PARAM


SKIPPED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _tool_text(text):
    return {"content": [{"type": "text", "text": text}]}


def _tool_json(payload):
    return _tool_text(json.dumps(payload, indent=2, default=str))


def module_editing_enabled():
    value = request.env["ir.config_parameter"].sudo().get_param(MODULE_EDITING_ENABLED_PARAM)
    return str(value).lower() in ("1", "true", "yes", "on")


def _addons_roots():
    roots = []
    for path in (odoo_config.get("addons_path") or "").split(","):
        path = os.path.abspath(os.path.expanduser(path.strip()))
        if os.path.isdir(path) and path not in roots:
            roots.append(path)
    for path in release.addons_paths:
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(path) and path not in roots:
            roots.append(path)
    return roots


def _safe_subpath(value):
    value = (value or "").strip().strip("/")
    if not value or os.path.isabs(value) or "\0" in value:
        return ""
    normalized = os.path.normpath(value)
    if normalized == "." or normalized.startswith("..") or f"{os.sep}.." in normalized:
        return ""
    return normalized


def _run(command, cwd=None, timeout=180):
    result = subprocess.run(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        output = "\n".join(part for part in (result.stdout, result.stderr) if part).strip()
        raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}\n{output}")
    return result.stdout.strip()


def _git(repository, *args, timeout=180):
    return _run(["git", *args], cwd=repository, timeout=timeout)


def _parse_module_lines(raw_value):
    items = []
    for line in raw_value.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, folder = line.split("=", 1)
        elif ":" in line:
            name, folder = line.split(":", 1)
        else:
            name, folder = line, line
        folder = os.path.abspath(os.path.expanduser(folder.strip()))
        items.append({"name": name.strip(), "repository": os.path.dirname(folder), "module": os.path.basename(folder)})
    return items


def _legacy_module_entries():
    raw_value = request.env["ir.config_parameter"].sudo().get_param(MODULE_EDITING_MODULES_PARAM, "") or ""
    if not raw_value.strip():
        return []

    try:
        parsed = json.loads(raw_value)
    except json.JSONDecodeError:
        return _parse_module_lines(raw_value)

    if isinstance(parsed, dict):
        parsed = [{"name": name, "folder": folder} for name, folder in parsed.items()]
    if not isinstance(parsed, list):
        raise ValueError("Editable modules must be configured as rows or legacy JSON entries.")

    items = []
    for item in parsed:
        if isinstance(item, str):
            item = {"name": item, "folder": item}
        if not isinstance(item, dict):
            raise ValueError("Each editable module entry must be an object.")
        folder = item.get("folder") or item.get("path") or item.get("module") or item.get("name")
        folder = os.path.abspath(os.path.expanduser(str(folder).strip()))
        items.append(
            {
                "name": item.get("name") or os.path.basename(folder),
                "repository": item.get("repository") or os.path.dirname(folder),
                "module": item.get("module") or os.path.basename(folder),
                "branch": item.get("branch") or "",
                "addons_dir": item.get("addons_dir") or "",
                "install_command": item.get("install_command") or "",
                "deploy_upgrade": item.get("deploy_upgrade", True),
                "legacy": True,
            }
        )
    return items


def _record_entries():
    records = request.env["perfect.odoo.mcp.editable.module"].sudo().search([("active", "=", True)])
    return [
        {
            "id": record.id,
            "name": record.name,
            "repository": record.repository_path,
            "module": record.module_name,
            "branch": record.branch or "",
            "addons_dir": record.addons_dir or "",
            "install_command": record.install_command or "",
            "deploy_upgrade": bool(record.deploy_upgrade),
            "legacy": False,
        }
        for record in records
    ]


def editable_modules():
    entries = _record_entries()
    if not entries:
        entries = _legacy_module_entries()

    modules = {}
    for entry in entries:
        name = str(entry.get("name") or entry.get("module") or "").strip()
        repository = os.path.abspath(os.path.expanduser(str(entry.get("repository") or "").strip()))
        module = _safe_subpath(entry.get("module"))
        if not name:
            raise ValueError("Each editable module entry must include a name.")
        if not repository or not os.path.isdir(repository):
            raise ValueError(f"Editable module repository does not exist for {name}: {repository}")
        if not os.path.isdir(os.path.join(repository, ".git")):
            raise ValueError(f"Editable module repository is not a Git working copy for {name}: {repository}")
        if not module:
            raise ValueError(f"Editable module must be a relative subfolder for {name}.")
        module_path = os.path.abspath(os.path.join(repository, module))
        if not module_path.startswith(repository + os.sep) or not os.path.isdir(module_path):
            raise ValueError(f"Editable module folder does not exist for {name}: {module_path}")
        modules[name] = {
            **entry,
            "name": name,
            "repository": repository,
            "module": module,
            "module_path": module_path,
        }
    return modules


def _require_enabled():
    if not module_editing_enabled():
        raise ValueError("Modules editing is not enabled in Perfect Odoo MCP settings.")


def _module_entry(module):
    modules = editable_modules()
    entry = modules.get(module)
    if not entry:
        raise ValueError(f"Module is not allowlisted for editing: {module}")
    return entry


def _resolve_file_path(module, relative_path):
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("path is required.")
    normalized = _safe_subpath(relative_path)
    if not normalized:
        raise ValueError("path must stay inside the allowlisted module folder.")

    entry = _module_entry(module)
    root = entry["module_path"]
    path = os.path.abspath(os.path.join(root, normalized))
    if path == root or not path.startswith(root + os.sep):
        raise ValueError("path must stay inside the allowlisted module folder.")
    return entry, path


def _list_files(module, recursive=False):
    root = _module_entry(module)["module_path"]
    files = []
    if recursive:
        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in SKIPPED_DIRS]
            for filename in filenames:
                path = os.path.join(current_root, filename)
                files.append(os.path.relpath(path, root))
    else:
        for name in os.listdir(root):
            path = os.path.join(root, name)
            files.append(name + "/" if os.path.isdir(path) else name)
    return sorted(files)


def _git_status(entry):
    return _git(entry["repository"], "status", "--short", "--branch")


def _git_dirty(entry):
    return bool(_git(entry["repository"], "status", "--porcelain"))


def _current_branch(entry):
    try:
        return _git(entry["repository"], "branch", "--show-current")
    except RuntimeError:
        return ""


def _commit_module(entry, message):
    if not isinstance(message, str) or not message.strip():
        raise ValueError("message is required for commit.")
    _git(entry["repository"], "add", "--", entry["module"])
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=entry["repository"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
        check=False,
    )
    if staged.returncode == 0:
        return {"status": "unchanged", "head": _git(entry["repository"], "rev-parse", "--short", "HEAD")}
    if staged.returncode != 1:
        raise RuntimeError(staged.stderr.strip() or "Could not inspect staged Git changes.")
    _git(entry["repository"], "commit", "-m", message.strip(), timeout=300)
    return {"status": "committed", "head": _git(entry["repository"], "rev-parse", "--short", "HEAD")}


def _sync_repo(entry):
    if _git_dirty(entry):
        raise ValueError("Repository has uncommitted changes. Commit or revert them before sync.")
    expected_branch = (entry.get("branch") or "").strip()
    current_branch = _current_branch(entry)
    if expected_branch and current_branch and expected_branch != current_branch:
        raise ValueError(f"Repository is on branch {current_branch}, expected {expected_branch}.")
    output = _git(entry["repository"], "pull", "--ff-only", timeout=300)
    return {"status": "synced", "branch": current_branch, "output": output}


def _resolve_addons_dir(entry):
    configured = os.path.abspath(os.path.expanduser(entry.get("addons_dir") or "")) if entry.get("addons_dir") else ""
    if configured:
        if not os.path.isdir(configured):
            raise ValueError(f"Configured addons directory does not exist: {configured}")
        return configured

    module_basename = os.path.basename(entry["module"])
    for root in _addons_roots():
        if os.path.isdir(os.path.join(root, module_basename)):
            return root
    for root in _addons_roots():
        if os.access(root, os.W_OK):
            return root
    raise ValueError("No writable Odoo addons directory was found. Set Addons Directory on the editable module.")


def _copy_module_with_backup(source, target):
    backup_parent = tempfile.mkdtemp(prefix="perfect_odoo_mcp_deploy_")
    backup = os.path.join(backup_parent, os.path.basename(target))
    had_target = os.path.exists(target)
    try:
        if had_target:
            shutil.copytree(target, backup, symlinks=True)
            shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"))
        return backup_parent, backup, had_target
    except Exception:
        if os.path.exists(target):
            shutil.rmtree(target)
        if had_target and os.path.exists(backup):
            shutil.copytree(backup, target, symlinks=True)
        shutil.rmtree(backup_parent, ignore_errors=True)
        raise


def _restore_backup(target, backup_parent, backup, had_target):
    if os.path.exists(target):
        shutil.rmtree(target)
    if had_target and os.path.exists(backup):
        shutil.copytree(backup, target, symlinks=True)
    shutil.rmtree(backup_parent, ignore_errors=True)


def _upgrade_deployed_module(env, module_name, install_if_needed=False):
    env["ir.module.module"].update_list()
    module = env["ir.module.module"].search([("name", "=", module_name)], limit=1)
    if not module:
        raise ValueError(f"Odoo module was copied but is not visible in Apps: {module_name}")
    if module.state == "installed":
        module.button_immediate_upgrade()
        return "upgraded"
    if install_if_needed and module.state in ("uninstalled", "uninstallable", "to install"):
        module.button_immediate_install()
        return "installed"
    return f"available:{module.state}"


def _run_install_command(entry, addons_dir):
    command = (entry.get("install_command") or "").strip()
    if not command:
        return None
    formatted = command.format(
        repository=shlex.quote(entry["repository"]),
        module=shlex.quote(entry["module"]),
        module_path=shlex.quote(entry["module_path"]),
        addons_dir=shlex.quote(addons_dir),
    )
    return _run(["/bin/bash", "-lc", formatted], cwd=entry["repository"], timeout=900)


def _deploy(entry, arguments):
    previous_head = _git(entry["repository"], "rev-parse", "HEAD")
    auto_revert = arguments.get("autoRevert", True) is not False
    message = arguments.get("message") or arguments.get("commitMessage")
    backup_parent = backup = target = None
    had_target = False

    try:
        if message:
            commit_result = _commit_module(entry, message)
        elif _git_dirty(entry):
            raise ValueError("Repository has uncommitted changes. Pass message to commit before deploy.")
        else:
            commit_result = {"status": "clean", "head": _git(entry["repository"], "rev-parse", "--short", "HEAD")}

        sync_result = None
        if arguments.get("sync"):
            sync_result = _sync_repo(entry)

        addons_dir = _resolve_addons_dir(entry)
        install_output = _run_install_command(entry, addons_dir)
        module_basename = os.path.basename(entry["module"])
        target = os.path.join(addons_dir, module_basename)

        if install_output is None:
            backup_parent, backup, had_target = _copy_module_with_backup(entry["module_path"], target)
            upgrade_status = None
            if entry.get("deploy_upgrade", True):
                upgrade_status = _upgrade_deployed_module(
                    request.env,
                    module_basename,
                    install_if_needed=bool(arguments.get("installIfNeeded")),
                )
            shutil.rmtree(backup_parent, ignore_errors=True)
        else:
            upgrade_status = "custom_install_command"

        return _tool_json(
            {
                "module": entry["name"],
                "repository": entry["repository"],
                "moduleFolder": entry["module"],
                "target": target,
                "commit": commit_result,
                "sync": sync_result,
                "upgrade": upgrade_status,
                "installOutput": install_output,
                "status": "deployed",
            }
        )
    except Exception:
        if auto_revert:
            if target and backup_parent:
                _restore_backup(target, backup_parent, backup, had_target)
            _git(entry["repository"], "reset", "--hard", previous_head, timeout=300)
        raise


def module_edit(arguments):
    _require_enabled()
    operation = arguments.get("operation")

    if operation == "list_modules":
        return _tool_json(
            {
                "modules": [
                    {
                        "name": entry["name"],
                        "repository": entry["repository"],
                        "module": entry["module"],
                        "modulePath": entry["module_path"],
                        "branch": entry.get("branch") or _current_branch(entry),
                        "addonsDir": entry.get("addons_dir") or "",
                        "legacy": bool(entry.get("legacy")),
                    }
                    for entry in sorted(editable_modules().values(), key=lambda item: item["name"])
                ]
            }
        )

    module = arguments.get("module")
    if not isinstance(module, str) or not module:
        raise ValueError("module is required.")

    if operation == "list_files":
        return _tool_json(
            {
                "module": module,
                "files": _list_files(module, recursive=bool(arguments.get("recursive"))),
            }
        )

    if operation == "read_file":
        entry, path = _resolve_file_path(module, arguments.get("path"))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {arguments.get('path')}")
        max_bytes = int(arguments.get("maxBytes") or 200000)
        max_bytes = max(1, min(max_bytes, 1000000))
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
        truncated = len(data) > max_bytes
        text = data[:max_bytes].decode("utf-8", errors="replace")
        return _tool_json(
            {
                "module": module,
                "repository": entry["repository"],
                "path": os.path.relpath(path, entry["module_path"]),
                "truncated": truncated,
                "content": text,
            }
        )

    if operation == "write_file":
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string.")
        entry, path = _resolve_file_path(module, arguments.get("path"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return _tool_json({"module": module, "path": os.path.relpath(path, entry["module_path"]), "status": "written"})

    if operation == "delete_file":
        entry, path = _resolve_file_path(module, arguments.get("path"))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {arguments.get('path')}")
        os.remove(path)
        return _tool_json({"module": module, "path": os.path.relpath(path, entry["module_path"]), "status": "deleted"})

    entry = _module_entry(module)
    if operation == "git_status":
        return _tool_json({"module": module, "status": _git_status(entry)})
    if operation == "git_diff":
        return _tool_json({"module": module, "diff": _git(entry["repository"], "diff", "--", entry["module"], timeout=120)})
    if operation == "commit":
        return _tool_json({"module": module, **_commit_module(entry, arguments.get("message") or arguments.get("commitMessage"))})
    if operation == "sync":
        return _tool_json({"module": module, **_sync_repo(entry)})
    if operation == "deploy":
        return _deploy(entry, arguments)

    raise ValueError(
        "operation must be one of list_modules, list_files, read_file, write_file, delete_file, "
        "git_status, git_diff, commit, sync, deploy."
    )
