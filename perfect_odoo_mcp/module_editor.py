import json
import importlib.util
import os
import tempfile

import odoo.addons
import odoo.modules
from odoo.http import request
from odoo.tools import config as odoo_config

from .const import MODULE_EDITING_ENABLED_PARAM, MODULE_EDITING_MODULES_PARAM


SKIPPED_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def _tool_text(text):
    return {"content": [{"type": "text", "text": text}]}


def _tool_json(payload):
    return _tool_text(json.dumps(payload, indent=2, default=str))


def module_editing_enabled():
    value = request.env["ir.config_parameter"].sudo().get_param(MODULE_EDITING_ENABLED_PARAM)
    return str(value).lower() in ("1", "true", "yes", "on")


def _addons_roots():
    roots = []
    for path in odoo.addons.__path__:
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(path) and path not in roots:
            roots.append(path)
    addons_path = odoo_config.get("addons_path") or []
    if isinstance(addons_path, str):
        addons_paths = addons_path.split(",")
    else:
        addons_paths = addons_path
    for path in addons_paths:
        path = str(path).strip()
        if not path:
            continue
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(path) and path not in roots:
            roots.append(path)
    return roots


def _safe_subpath(value):
    value = (value or "").strip()
    if not value or os.path.isabs(value) or "\0" in value:
        return ""
    value = value.replace("\\", "/")
    if value.startswith("/") or "/../" in f"/{value}/":
        return ""
    normalized = os.path.normpath(value)
    if normalized == "." or normalized.startswith("..") or f"{os.sep}.." in normalized:
        return ""
    return normalized


def _is_inside_path(path, root):
    try:
        return os.path.commonpath([root, path]) == root
    except ValueError:
        return False


def _odoo_module_path(module_name):
    try:
        return odoo.modules.get_module_path(module_name, display_warning=False)
    except TypeError:
        return odoo.modules.get_module_path(module_name)


def _python_module_path(module_name):
    try:
        spec = importlib.util.find_spec(f"odoo.addons.{module_name}")
    except (ImportError, ValueError):
        return ""
    locations = getattr(spec, "submodule_search_locations", None) if spec else None
    if not locations:
        return ""
    path = os.path.abspath(os.path.expanduser(list(locations)[0]))
    if os.path.isfile(os.path.join(path, "__manifest__.py")) or os.path.isfile(os.path.join(path, "__openerp__.py")):
        return path
    return ""


def _find_module_path(module_name):
    path = _python_module_path(module_name)
    if path:
        return path
    path = _odoo_module_path(module_name)
    if path:
        return os.path.abspath(os.path.expanduser(path))
    for root in _addons_roots():
        path = os.path.abspath(os.path.join(root, module_name))
        if os.path.isfile(os.path.join(path, "__manifest__.py")) or os.path.isfile(
            os.path.join(path, "__openerp__.py")
        ):
            return path
    return ""


def _module_path_candidates(module_name):
    if not module_name:
        return []
    candidates = []
    for root in _addons_roots():
        path = os.path.abspath(os.path.join(root, module_name))
        if os.path.isfile(os.path.join(path, "__manifest__.py")) or os.path.isfile(
            os.path.join(path, "__openerp__.py")
        ):
            if path not in candidates:
                candidates.append(path)
    return candidates


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
        items.append({"name": name.strip(), "folder": folder.strip()})
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
        return [{"name": name, "folder": folder} for name, folder in parsed.items()]
    if isinstance(parsed, list):
        return parsed
    raise ValueError("Editable modules must be configured as rows or legacy JSON entries.")


def _legacy_folder(folder):
    folder = os.path.expanduser(str(folder or "").strip())
    if os.path.isabs(folder):
        return os.path.abspath(folder)
    for root in _addons_roots():
        candidate = os.path.abspath(os.path.join(root, folder))
        if os.path.isdir(candidate):
            return candidate
    return os.path.abspath(folder)


def _record_entries():
    records = request.env["perfect.odoo.mcp.editable.module"].sudo().search(
        [("active", "=", True), ("module_id.state", "=", "installed")]
    )
    return [
        {
            "name": record.module_id.name,
            "module": record.module_id.name,
            "module_path": record.module_path or _find_module_path(record.module_id.name),
            "addons_dir": record.addons_dir,
            "path_source": record.module_path_source,
            "path_candidates": _module_path_candidates(record.module_id.name),
            "legacy": False,
        }
        for record in records
        if record.module_id
    ]


def editable_modules():
    entries = _record_entries()
    if not entries:
        entries = _legacy_module_entries()

    modules = {}
    for entry in entries:
        if isinstance(entry, str):
            entry = {"name": entry, "folder": entry}
        if not isinstance(entry, dict):
            raise ValueError("Each editable module entry must be an object.")

        name = str(entry.get("name") or entry.get("module") or "").strip()
        module_path = entry.get("module_path")
        if module_path:
            module_path = os.path.abspath(os.path.expanduser(module_path))
        else:
            module_path = _legacy_folder(entry.get("folder") or entry.get("module") or name)

        if not name:
            raise ValueError("Each editable module entry must include a name.")
        if not os.path.isdir(module_path):
            raise ValueError(f"Editable module folder does not exist for {name}: {module_path}")

        modules[name] = {
            "name": name,
            "module": entry.get("module") or name,
            "module_path": module_path,
            "addons_dir": entry.get("addons_dir") or os.path.dirname(module_path),
            "path_source": entry.get("path_source") or ("legacy" if entry.get("legacy") else "configured"),
            "path_candidates": entry.get("path_candidates") or _module_path_candidates(name),
            "legacy": bool(entry.get("legacy")),
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


def _file_name_arg(arguments):
    file_name = arguments.get("fileName")
    if file_name is None:
        file_name = arguments.get("path")
    return file_name


def _resolve_file_path(module, file_name):
    if not isinstance(file_name, str) or not file_name:
        raise ValueError("fileName is required.")
    normalized = _safe_subpath(file_name)
    if not normalized:
        raise ValueError("fileName must be relative and must not contain '..' path traversal.")

    entry = _module_entry(module)
    root = os.path.abspath(entry["module_path"])
    real_root = os.path.realpath(root)
    path = os.path.abspath(os.path.join(root, normalized))
    if path == root or not _is_inside_path(path, root):
        raise ValueError("fileName must stay inside the allowlisted module folder.")

    real_path = os.path.realpath(path)
    if os.path.exists(path):
        if not _is_inside_path(real_path, real_root):
            raise ValueError("fileName resolves outside the allowlisted module folder.")

    return entry, path, normalized


def _nearest_existing_parent(path, root):
    parent = os.path.dirname(path)
    while parent and not os.path.exists(parent):
        if parent == root or not _is_inside_path(parent, root):
            break
        parent = os.path.dirname(parent)
    return parent if parent and os.path.isdir(parent) else ""


def _can_create_temp_file(directory):
    if not directory or not os.path.isdir(directory):
        return False, "Directory does not exist."
    temp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".perfect_odoo_mcp_write_test_",
            delete=False,
        ) as handle:
            temp_path = handle.name
            handle.write("ok")
        os.remove(temp_path)
        return True, ""
    except OSError as error:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        return False, str(error)


def _write_permission_report(entry, path, normalized):
    root = os.path.abspath(entry["module_path"])
    real_root = os.path.realpath(root)
    parent = os.path.dirname(path)
    target_exists = os.path.exists(path)
    nearest_parent = _nearest_existing_parent(path, root)
    parent_exists = os.path.isdir(parent)
    parent_real = os.path.realpath(parent if parent_exists else nearest_parent)
    parent_inside = bool(parent_real and _is_inside_path(parent_real, real_root))

    directory_to_test = parent if parent_exists else nearest_parent
    can_create, create_error = _can_create_temp_file(directory_to_test)
    file_writable = os.access(path, os.W_OK) if target_exists else None
    writable = bool(parent_inside and (file_writable if target_exists else can_create))

    reason = ""
    if not parent_inside:
        reason = "Parent directory resolves outside the allowlisted module folder."
    elif target_exists and not file_writable:
        reason = "Existing file is not writable by the Odoo process."
    elif not target_exists and not can_create:
        reason = create_error or "Odoo process cannot create files in the nearest existing parent directory."

    return {
        "module": entry["name"],
        "modulePath": root,
        "fileName": normalized,
        "targetPath": path,
        "targetExists": target_exists,
        "parentPath": parent,
        "nearestExistingParent": nearest_parent,
        "parentInsideModule": parent_inside,
        "canCreateInParent": can_create,
        "fileWritable": file_writable,
        "writable": writable,
        "reason": reason,
    }


def _delete_permission_report(entry, path, normalized):
    root = os.path.abspath(entry["module_path"])
    real_root = os.path.realpath(root)
    parent = os.path.dirname(path)
    parent_real = os.path.realpath(parent)
    parent_inside = _is_inside_path(parent_real, real_root)
    target_exists = os.path.isfile(path)
    can_create, create_error = _can_create_temp_file(parent)
    deletable = bool(target_exists and parent_inside and can_create)

    reason = ""
    if not target_exists:
        reason = "File does not exist."
    elif not parent_inside:
        reason = "Parent directory resolves outside the allowlisted module folder."
    elif not can_create:
        reason = create_error or "Odoo process cannot modify entries in the parent directory."

    return {
        "module": entry["name"],
        "modulePath": root,
        "fileName": normalized,
        "targetPath": path,
        "targetExists": target_exists,
        "parentPath": parent,
        "parentInsideModule": parent_inside,
        "canModifyParent": can_create,
        "deletable": deletable,
        "reason": reason,
    }


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


def module_edit(arguments):
    _require_enabled()
    operation = arguments.get("operation")

    if operation == "list_modules":
        return _tool_json(
            {
                "modules": [
                    {
                        "name": entry["name"],
                        "module": entry["module"],
                        "modulePath": entry["module_path"],
                        "addonsDir": entry["addons_dir"],
                        "pathSource": entry.get("path_source"),
                        "pathCandidates": entry.get("path_candidates") or [],
                        "legacy": entry["legacy"],
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

    if operation == "check_permissions":
        file_name = _file_name_arg(arguments)
        if file_name:
            entry, path, normalized = _resolve_file_path(module, file_name)
            return _tool_json(
                {
                    "write": _write_permission_report(entry, path, normalized),
                    "delete": _delete_permission_report(entry, path, normalized),
                }
            )

        entry = _module_entry(module)
        can_create, reason = _can_create_temp_file(entry["module_path"])
        return _tool_json(
            {
                "module": module,
                "modulePath": entry["module_path"],
                "writable": can_create,
                "reason": reason,
            }
        )

    if operation == "read_file":
        file_name = _file_name_arg(arguments)
        entry, path, normalized = _resolve_file_path(module, file_name)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {file_name}")
        max_bytes = int(arguments.get("maxBytes") or 200000)
        max_bytes = max(1, min(max_bytes, 1000000))
        with open(path, "rb") as handle:
            data = handle.read(max_bytes + 1)
        truncated = len(data) > max_bytes
        text = data[:max_bytes].decode("utf-8", errors="replace")
        return _tool_json(
            {
                "module": module,
                "fileName": normalized,
                "path": normalized,
                "truncated": truncated,
                "content": text,
            }
        )

    if operation == "write_file":
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string.")
        entry, path, normalized = _resolve_file_path(module, _file_name_arg(arguments))
        report = _write_permission_report(entry, path, normalized)
        if not report["writable"]:
            raise PermissionError(f"File is not writable by the Odoo process: {report['reason']}")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not _is_inside_path(os.path.realpath(os.path.dirname(path)), os.path.realpath(entry["module_path"])):
            raise ValueError("fileName parent resolves outside the allowlisted module folder.")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return _tool_json({"module": module, "fileName": normalized, "path": normalized, "status": "written"})

    if operation == "delete_file":
        file_name = _file_name_arg(arguments)
        entry, path, normalized = _resolve_file_path(module, file_name)
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {file_name}")
        report = _delete_permission_report(entry, path, normalized)
        if not report["deletable"]:
            raise PermissionError(f"File is not deletable by the Odoo process: {report['reason']}")
        os.remove(path)
        return _tool_json({"module": module, "fileName": normalized, "path": normalized, "status": "deleted"})

    raise ValueError(
        "operation must be one of list_modules, list_files, check_permissions, read_file, write_file, delete_file."
    )
