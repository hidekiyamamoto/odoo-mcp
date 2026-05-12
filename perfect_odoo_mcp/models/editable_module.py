import os

from odoo import api, fields, models
from odoo.exceptions import ValidationError


def _safe_subpath(value):
    value = (value or "").strip().strip("/")
    return value and not os.path.isabs(value) and ".." not in value.split("/")


class PerfectOdooMcpEditableModule(models.Model):
    _name = "perfect.odoo.mcp.editable.module"
    _description = "Perfect Odoo MCP Editable Module"
    _order = "sequence, name, id"

    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    name = fields.Char(required=True)
    repository_path = fields.Char(
        required=True,
        help="Absolute path to the Git repository working copy used for AI edits.",
    )
    module_name = fields.Char(
        required=True,
        help="Module folder inside the repository, for example perfect_odoo_mcp or addons/my_module.",
    )
    branch = fields.Char(help="Optional expected Git branch for this editable module.")
    addons_dir = fields.Char(
        help="Optional Odoo addons directory to deploy into. If empty, the tool uses the installed module location or a writable addons path.",
    )
    install_command = fields.Char(
        help=(
            "Optional custom deploy command. Placeholders: {repository}, {module}, {module_path}, "
            "{addons_dir}. Leave empty to use the built-in safe copy and Odoo module update."
        ),
    )
    deploy_upgrade = fields.Boolean(
        string="Upgrade After Deploy",
        default=True,
        help="After built-in deploy, update the apps list and upgrade the installed module when possible.",
    )

    module_path = fields.Char(compute="_compute_module_path")

    @api.depends("repository_path", "module_name")
    def _compute_module_path(self):
        for record in self:
            repository = os.path.abspath(os.path.expanduser(record.repository_path or ""))
            record.module_path = os.path.abspath(os.path.join(repository, record.module_name or ""))

    @api.constrains("repository_path", "module_name", "addons_dir")
    def _check_paths(self):
        for record in self:
            repository = os.path.abspath(os.path.expanduser(record.repository_path or ""))
            if record.repository_path and not os.path.isabs(os.path.expanduser(record.repository_path)):
                raise ValidationError("Repository path must be absolute.")
            if record.module_name and not _safe_subpath(record.module_name):
                raise ValidationError("Module name must be a relative folder inside the repository.")
            if record.addons_dir and not os.path.isabs(os.path.expanduser(record.addons_dir)):
                raise ValidationError("Addons directory must be absolute when set.")
            if repository and record.module_name:
                module_path = os.path.abspath(os.path.join(repository, record.module_name))
                if not module_path.startswith(repository + os.sep):
                    raise ValidationError("Module folder must stay inside the repository.")
