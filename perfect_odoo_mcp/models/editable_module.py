import os

import odoo.addons
from odoo import api, fields, models
from odoo.tools import config as odoo_config


def _addons_roots():
    roots = []
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
    for path in odoo.addons.__path__:
        path = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(path) and path not in roots:
            roots.append(path)
    return roots


def _module_path(module_name):
    if not module_name:
        return ""
    for root in _addons_roots():
        path = os.path.abspath(os.path.join(root, module_name))
        if os.path.isfile(os.path.join(path, "__manifest__.py")) or os.path.isfile(
            os.path.join(path, "__openerp__.py")
        ):
            return path
    return ""


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
            path = _module_path(module_name)
            record.module_name = module_name
            record.module_path = path
            record.addons_dir = os.path.dirname(path) if path else ""
