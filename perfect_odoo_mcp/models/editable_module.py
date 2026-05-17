import importlib.util
import os

import odoo.addons
import odoo.modules
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


def _module_path(module_name):
    if not module_name:
        return ""
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


class PerfectOdooMcpEditableModule(models.Model):
    _name = "perfect.odoo.mcp.editable.module"
    _description = "Perfect Odoo MCP Editable Module"
    _rec_name = "module_name"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    module_id = fields.Many2one(
        "ir.module.module",
        string="Module",
        domain=[("state", "=", "installed")],
        required=True,
        ondelete="cascade",
        help="Installed Odoo module that the MCP module editor may modify.",
    )
    module_name = fields.Char(compute="_compute_module_paths", readonly=True)
    addons_dir = fields.Char(string="Addons Directory", compute="_compute_module_paths", readonly=True)
    module_path = fields.Char(string="Module Folder", compute="_compute_module_paths", readonly=True)
    module_path_source = fields.Char(string="Path Source", compute="_compute_module_paths", readonly=True)
    module_path_candidates = fields.Text(string="Detected Module Folders", compute="_compute_module_paths", readonly=True)

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
            python_path = _python_module_path(module_name) if module_name else ""
            odoo_path = _odoo_module_path(module_name) if module_name else ""
            if python_path:
                path = python_path
                source = "python package"
            elif odoo_path:
                path = os.path.abspath(os.path.expanduser(odoo_path))
                source = "odoo.modules.get_module_path"
            else:
                path = _module_path(module_name)
                source = "addons path scan" if path else ""
            candidates = _module_path_candidates(module_name)
            record.module_name = module_name
            record.module_path = path
            record.addons_dir = os.path.dirname(path) if path else ""
            record.module_path_source = source
            record.module_path_candidates = "\n".join(candidates)

    @api.depends("module_name", "module_id")
    def _compute_display_name(self):
        for record in self:
            record.display_name = record.module_name or record.module_id.display_name or "New"

    def name_get(self):
        return [
            (record.id, record.module_name or record.module_id.display_name or "New")
            for record in self
        ]
