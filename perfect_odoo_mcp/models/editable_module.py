import os
import sys

import odoo.addons
from odoo import api, fields, models
from odoo.tools import config as odoo_config


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


def _manifest_dir_from_path(path, module_name):
    if not path:
        return ""
    path = os.path.abspath(os.path.expanduser(path))
    if os.path.basename(path) == "__init__.py":
        path = os.path.dirname(path)
    elif path.endswith((".pyc", ".pyo")):
        parts = path.split(os.sep)
        if "__pycache__" in parts:
            path = os.sep.join(parts[: parts.index("__pycache__")])
        else:
            path = os.path.dirname(path)
    elif os.path.isfile(path):
        path = os.path.dirname(path)

    while path and os.path.basename(path) != module_name:
        parent = os.path.dirname(path)
        if parent == path:
            return ""
        path = parent

    if os.path.isfile(os.path.join(path, "__manifest__.py")) or os.path.isfile(os.path.join(path, "__openerp__.py")):
        return path
    return ""


def _runtime_module_path(module_name):
    prefix = f"odoo.addons.{module_name}"
    for name in (prefix, f"{prefix}.models", f"{prefix}.controllers"):
        module = sys.modules.get(name)
        locations = getattr(module, "__path__", None) if module else None
        if locations:
            for location in locations:
                path = _manifest_dir_from_path(location, module_name)
                if path:
                    return path
        for attr in ("__file__", "__cached__"):
            path = _manifest_dir_from_path(getattr(module, attr, ""), module_name) if module else ""
            if path:
                return path

    for name, module in sorted(sys.modules.items()):
        if not name.startswith(prefix + "."):
            continue
        for attr in ("__file__", "__cached__"):
            path = _manifest_dir_from_path(getattr(module, attr, ""), module_name)
            if path:
                return path
    return ""


def _module_path(module_name):
    if not module_name:
        return ""
    return _runtime_module_path(module_name)


class PerfectOdooMcpEditableModule(models.Model):
    _name = "perfect.odoo.mcp.editable.module"
    _description = "Perfect Odoo MCP Editable Module"
    _rec_name = "module_id"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    module_id = fields.Many2one(
        "ir.module.module",
        string="Module",
        domain=[("state", "=", "installed")],
        ondelete="cascade",
        help="Installed Odoo module that the MCP module editor may modify.",
    )
    module_name = fields.Char(compute="_compute_module_paths", readonly=True)
    addons_dir = fields.Char(string="Addons Directory", compute="_compute_module_paths", readonly=True)
    module_path = fields.Char(string="Module Folder", compute="_compute_module_paths", readonly=True)
    module_path_source = fields.Char(string="Path Source", compute="_compute_module_paths", readonly=True)

    _sql_constraints = [
        (
            "module_id_unique",
            "unique(module_id)",
            "Each installed module can only be allowlisted once.",
        )
    ]

    @api.depends("module_id", "module_id.name")
    def _compute_module_paths(self):
        for record in self:
            module_name = record.module_id.name or ""
            runtime_path = _runtime_module_path(module_name) if module_name else ""
            if runtime_path:
                path = runtime_path
                source = "loaded Python module"
            else:
                path = ""
                source = "not loaded in Python runtime"
            record.module_name = module_name
            record.module_path = path
            record.addons_dir = os.path.dirname(path) if path else ""
            record.module_path_source = source
