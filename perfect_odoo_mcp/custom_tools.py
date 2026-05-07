import hashlib
import importlib.util
import json
import logging
import os

from odoo.http import request

from .const import CUSTOM_TOOLS_ENABLED_PARAM
from .mcp_defs import (
    CUSTOM_TOOL_FILE_PATTERN,
    CUSTOM_TOOL_MANAGER_TOOLS,
    CUSTOM_TOOL_TEMPLATE,
    CUSTOM_TOOLS_CACHE,
    CUSTOM_TOOLS_DIR,
    SQL_TOOL,
    TOOLS,
)


_logger = logging.getLogger(__name__)


def _tool_text(text):
    return {"content": [{"type": "text", "text": text}]}


def _tool_json(payload):
    return _tool_text(json.dumps(payload, indent=2, default=str))


def custom_tools_enabled():
    value = request.env["ir.config_parameter"].sudo().get_param(CUSTOM_TOOLS_ENABLED_PARAM)
    return str(value).lower() in ("1", "true", "yes", "on")


def ensure_custom_tools_enabled():
    if not custom_tools_enabled():
        raise ValueError("Custom tools creation is not enabled in Perfect Odoo MCP settings.")


def _ensure_custom_tools_dir():
    os.makedirs(CUSTOM_TOOLS_DIR, exist_ok=True)


def _custom_tool_path(filename):
    if not isinstance(filename, str) or not CUSTOM_TOOL_FILE_PATTERN.match(filename):
        raise ValueError("filename must look like my_tool.py and contain only letters, numbers, and underscores.")
    _ensure_custom_tools_dir()
    path = os.path.abspath(os.path.join(CUSTOM_TOOLS_DIR, filename))
    if not path.startswith(CUSTOM_TOOLS_DIR + os.sep):
        raise ValueError("Invalid custom tool path.")
    return path


def _custom_tool_files():
    _ensure_custom_tools_dir()
    return sorted(filename for filename in os.listdir(CUSTOM_TOOLS_DIR) if CUSTOM_TOOL_FILE_PATTERN.match(filename))


def _load_custom_tool_file(filename):
    path = _custom_tool_path(filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Custom tool file not found: {filename}")

    module_name = f"perfect_odoo_mcp_custom_{filename[:-3]}_{hashlib.sha1(path.encode()).hexdigest()[:8]}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if not spec or not spec.loader:
        raise ValueError(f"Could not load custom tool file: {filename}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tool = getattr(module, "TOOL", None)
    call = getattr(module, "call", None)
    exposed = bool(getattr(module, "EXPOSED", False))

    if not isinstance(tool, dict):
        raise ValueError("Custom tool must define TOOL as a dictionary.")
    if not isinstance(tool.get("name"), str) or not tool["name"]:
        raise ValueError("Custom tool TOOL must include a non-empty string name.")
    if not isinstance(tool.get("inputSchema"), dict):
        raise ValueError("Custom tool TOOL must include an inputSchema dictionary.")
    if not callable(call):
        raise ValueError("Custom tool must define callable function call(arguments, env, request).")

    return {
        "filename": filename,
        "path": path,
        "module": module,
        "tool": tool,
        "call": call,
        "exposed": exposed,
    }


def _load_custom_tools():
    loaded = {}
    exposed_tools = []
    files = []
    errors = []
    builtin_names = {tool["name"] for tool in TOOLS}
    builtin_names.update(tool["name"] for tool in CUSTOM_TOOL_MANAGER_TOOLS)
    builtin_names.add(SQL_TOOL["name"])

    for filename in _custom_tool_files():
        files.append(filename)
        try:
            item = _load_custom_tool_file(filename)
            name = item["tool"]["name"]
            if name in builtin_names:
                raise ValueError(f"Custom tool name collides with a built-in tool: {name}")
            if name in loaded:
                raise ValueError(f"Duplicate custom tool name: {name}")
            loaded[name] = item
            if item["exposed"]:
                exposed_tools.append(item["tool"])
        except Exception as error:
            _logger.exception("Custom tool load failed for %s", filename)
            errors.append({"filename": filename, "error": str(error)})

    return {
        "tools": loaded,
        "exposed_tools": exposed_tools,
        "files": files,
        "errors": errors,
    }


def custom_tools_cache(force=False):
    global CUSTOM_TOOLS_CACHE
    if force or CUSTOM_TOOLS_CACHE is None:
        CUSTOM_TOOLS_CACHE = _load_custom_tools()
    return CUSTOM_TOOLS_CACHE


def custom_tool_result(value):
    if isinstance(value, dict) and ("content" in value or value.get("isError")):
        return value
    if isinstance(value, str):
        return _tool_text(value)
    return _tool_json(value)


def custom_tools_list():
    ensure_custom_tools_enabled()
    cache = custom_tools_cache(force=True)
    items = []
    for filename in cache["files"]:
        try:
            item = _load_custom_tool_file(filename)
            items.append(
                {
                    "filename": filename,
                    "name": item["tool"]["name"],
                    "title": item["tool"].get("title"),
                    "exposed": item["exposed"],
                }
            )
        except Exception as error:
            items.append({"filename": filename, "error": str(error), "exposed": False})
    return _tool_json(
        {
            "directory": CUSTOM_TOOLS_DIR,
            "files": items,
            "loadErrors": cache["errors"],
            "template": CUSTOM_TOOL_TEMPLATE,
        }
    )


def custom_tool_read(arguments):
    ensure_custom_tools_enabled()
    filename = arguments.get("filename")
    path = _custom_tool_path(filename)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Custom tool file not found: {filename}")
    with open(path, encoding="utf-8") as handle:
        return _tool_text(handle.read())


def custom_tool_write(arguments):
    ensure_custom_tools_enabled()
    filename = arguments.get("filename")
    code = arguments.get("code")
    if not isinstance(code, str):
        raise ValueError("code must be a string.")
    path = _custom_tool_path(filename)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(code)
        if not code.endswith("\n"):
            handle.write("\n")
    custom_tools_cache(force=True)
    return _tool_json({"filename": filename, "path": path, "status": "written"})


def custom_tools_reload():
    ensure_custom_tools_enabled()
    cache = custom_tools_cache(force=True)
    return _tool_json(
        {
            "directory": CUSTOM_TOOLS_DIR,
            "fileCount": len(cache["files"]),
            "exposedTools": [tool["name"] for tool in cache["exposed_tools"]],
            "errors": cache["errors"],
        }
    )


def test_custom_tool(arguments, user_env):
    ensure_custom_tools_enabled()
    filename = arguments.get("filename")
    name = arguments.get("name")
    call_arguments = arguments.get("arguments") or {}
    if not isinstance(call_arguments, dict):
        raise ValueError("arguments must be an object.")

    if filename:
        item = _load_custom_tool_file(filename)
    elif name:
        item = custom_tools_cache(force=True)["tools"].get(name)
        if not item:
            raise ValueError(f"Custom tool not found: {name}")
    else:
        raise ValueError("Provide filename or name.")

    return custom_tool_result(item["call"](call_arguments, user_env, request))


