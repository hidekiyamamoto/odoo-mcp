import json
import os

from odoo import release
from odoo.http import request
from odoo.tools import config as odoo_config

from .const import MODULE_EDITING_ENABLED_PARAM, MODULE_EDITING_MODULES_PARAM


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


def _raw_module_entries():
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
    raise ValueError("Editable modules must be a JSON list, JSON object, or name=folder lines.")


def _resolve_module_folder(folder):
    if not isinstance(folder, str) or not folder.strip():
        raise ValueError("Editable module folder is required.")
    folder = os.path.expanduser(folder.strip())
    if os.path.isabs(folder):
        return os.path.abspath(folder)

    for root in _addons_roots():
        candidate = os.path.abspath(os.path.join(root, folder))
        if os.path.isdir(candidate):
            return candidate
    return os.path.abspath(folder)


def editable_modules():
    modules = {}
    for item in _raw_module_entries():
        if isinstance(item, str):
            item = {"name": item, "folder": item}
        if not isinstance(item, dict):
            raise ValueError("Each editable module entry must be an object with name and folder.")

        name = item.get("name")
        folder = item.get("folder")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Each editable module entry must include a non-empty name.")

        name = name.strip()
        folder = _resolve_module_folder(folder or name)
        if not os.path.isdir(folder):
            raise ValueError(f"Editable module folder does not exist for {name}: {folder}")
        modules[name] = folder
    return modules


def _require_enabled():
    if not module_editing_enabled():
        raise ValueError("Modules editing is not enabled in Perfect Odoo MCP settings.")


def _module_root(module):
    modules = editable_modules()
    root = modules.get(module)
    if not root:
        raise ValueError(f"Module is not allowlisted for editing: {module}")
    return os.path.abspath(root)


def _resolve_file_path(module, relative_path):
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("path is required.")
    if os.path.isabs(relative_path) or "\0" in relative_path:
        raise ValueError("path must be relative to the allowlisted module folder.")

    normalized = os.path.normpath(relative_path)
    if normalized == "." or normalized.startswith("..") or f"{os.sep}.." in normalized:
        raise ValueError("path must stay inside the allowlisted module folder.")

    root = _module_root(module)
    path = os.path.abspath(os.path.join(root, normalized))
    if path == root or not path.startswith(root + os.sep):
        raise ValueError("path must stay inside the allowlisted module folder.")
    return root, path


def _list_files(module, recursive=False):
    root = _module_root(module)
    files = []
    if recursive:
        for current_root, dirnames, filenames in os.walk(root):
            dirnames[:] = [name for name in dirnames if name not in {".git", "__pycache__", ".pytest_cache"}]
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
                    {"name": name, "folder": folder}
                    for name, folder in sorted(editable_modules().items())
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
        root, path = _resolve_file_path(module, arguments.get("path"))
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
                "path": os.path.relpath(path, root),
                "truncated": truncated,
                "content": text,
            }
        )

    if operation == "write_file":
        content = arguments.get("content")
        if not isinstance(content, str):
            raise ValueError("content must be a string.")
        root, path = _resolve_file_path(module, arguments.get("path"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return _tool_json({"module": module, "path": os.path.relpath(path, root), "status": "written"})

    if operation == "delete_file":
        root, path = _resolve_file_path(module, arguments.get("path"))
        if not os.path.isfile(path):
            raise FileNotFoundError(f"File not found: {arguments.get('path')}")
        os.remove(path)
        return _tool_json({"module": module, "path": os.path.relpath(path, root), "status": "deleted"})

    raise ValueError("operation must be one of list_modules, list_files, read_file, write_file, delete_file.")
