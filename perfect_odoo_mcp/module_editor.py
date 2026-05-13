import json
import os

import odoo.addons
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
    for path in (odoo_config.get("addons_path") or "").split(","):
        path = os.path.abspath(os.path.expanduser(path.strip()))
        if os.path.isdir(path) and path not in roots:
            roots.append(path)
    for path in odoo.addons.__path__:
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


def _find_module_path(module_name):
    for root in _addons_roots():
        path = os.path.abspath(os.path.join(root, module_name))
        if os.path.isfile(os.path.join(path, "__manifest__.py")) or os.path.isfile(
            os.path.join(path, "__openerp__.py")
        ):
            return path
    return ""


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

    raise ValueError("operation must be one of list_modules, list_files, read_file, write_file, delete_file.")
