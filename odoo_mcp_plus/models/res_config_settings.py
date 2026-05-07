from odoo import fields, models

from ..controllers.mcp import (
    CUSTOM_TOOLS_ENABLED_PARAM,
    MCP_PATH,
    SQL_DATABASE_PARAM,
    SQL_ENABLED_PARAM,
    SQL_HOST_PARAM,
    SQL_PASSWORD_PARAM,
    SQL_PORT_PARAM,
    SQL_READONLY_PARAM,
    SQL_USER_PARAM,
)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odoo_mcp_plus_mcp_url = fields.Char(
        string="MCP URL",
        default=lambda self: self._get_odoo_mcp_plus_mcp_url(),
        readonly=True,
    )
    odoo_mcp_plus_custom_tools_enabled = fields.Boolean(
        string="Allow Custom Tools Creation",
        config_parameter=CUSTOM_TOOLS_ENABLED_PARAM,
        help="Expose MCP tools that can read, write, reload, and test custom Python MCP tools.",
    )
    odoo_mcp_plus_sql_enabled = fields.Boolean(
        string="Enable Direct Database Access",
        config_parameter=SQL_ENABLED_PARAM,
        help="Expose the direct SQL MCP tool and allow it to connect to PostgreSQL.",
    )
    odoo_mcp_plus_sql_readonly = fields.Boolean(
        string="SQL Readonly",
        config_parameter=SQL_READONLY_PARAM,
        default=True,
        help="When enabled, the SQL tool refuses write operations and opens PostgreSQL sessions in readonly mode.",
    )
    odoo_mcp_plus_sql_host = fields.Char(
        string="SQL Host",
        config_parameter=SQL_HOST_PARAM,
        default="127.0.0.1",
    )
    odoo_mcp_plus_sql_port = fields.Integer(
        string="SQL Port",
        config_parameter=SQL_PORT_PARAM,
        default=5432,
    )
    odoo_mcp_plus_sql_database = fields.Char(
        string="SQL Database",
        config_parameter=SQL_DATABASE_PARAM,
        default=lambda self: self.env.cr.dbname,
    )
    odoo_mcp_plus_sql_user = fields.Char(
        string="SQL User",
        config_parameter=SQL_USER_PARAM,
    )
    odoo_mcp_plus_sql_password = fields.Char(
        string="SQL Password",
        config_parameter=SQL_PASSWORD_PARAM,
    )

    def _get_odoo_mcp_plus_mcp_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return f"{base_url.rstrip('/')}{MCP_PATH}" if base_url else MCP_PATH

    def get_values(self):
        values = super().get_values()
        values["odoo_mcp_plus_mcp_url"] = self._get_odoo_mcp_plus_mcp_url()
        return values
