from odoo import fields, models

MCP_PATH = "/perfect_odoo_mcp/mcp"
CUSTOM_TOOLS_ENABLED_PARAM = "perfect_odoo_mcp.custom_tools_enabled"
SQL_ENABLED_PARAM = "perfect_odoo_mcp.sql_enabled"
SQL_READONLY_PARAM = "perfect_odoo_mcp.sql_readonly"
SQL_HOST_PARAM = "perfect_odoo_mcp.sql_host"
SQL_PORT_PARAM = "perfect_odoo_mcp.sql_port"
SQL_DATABASE_PARAM = "perfect_odoo_mcp.sql_database"
SQL_USER_PARAM = "perfect_odoo_mcp.sql_user"
SQL_PASSWORD_PARAM = "perfect_odoo_mcp.sql_password"


def _public_base_url(base_url):
    return base_url.replace("http://", "https://", 1) if base_url.startswith("http://") else base_url


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    perfect_odoo_mcp_mcp_url = fields.Char(
        string="MCP URL",
        default=lambda self: self._get_perfect_odoo_mcp_mcp_url(),
        readonly=True,
    )
    perfect_odoo_mcp_custom_tools_enabled = fields.Boolean(
        string="Allow Custom Tools Creation",
        config_parameter=CUSTOM_TOOLS_ENABLED_PARAM,
        help="Expose MCP tools that can read, write, reload, and test custom Python MCP tools.",
    )
    perfect_odoo_mcp_sql_enabled = fields.Boolean(
        string="Enable Direct Database Access",
        config_parameter=SQL_ENABLED_PARAM,
        help="Expose the direct SQL MCP tool and allow it to connect to PostgreSQL.",
    )
    perfect_odoo_mcp_sql_readonly = fields.Boolean(
        string="SQL Readonly",
        config_parameter=SQL_READONLY_PARAM,
        default=True,
        help="When enabled, the SQL tool refuses write operations and opens PostgreSQL sessions in readonly mode.",
    )
    perfect_odoo_mcp_sql_host = fields.Char(
        string="SQL Host",
        config_parameter=SQL_HOST_PARAM,
        default="127.0.0.1",
    )
    perfect_odoo_mcp_sql_port = fields.Integer(
        string="SQL Port",
        config_parameter=SQL_PORT_PARAM,
        default=5432,
    )
    perfect_odoo_mcp_sql_database = fields.Char(
        string="SQL Database",
        config_parameter=SQL_DATABASE_PARAM,
        default=lambda self: self.env.cr.dbname,
    )
    perfect_odoo_mcp_sql_user = fields.Char(
        string="SQL User",
        config_parameter=SQL_USER_PARAM,
    )
    perfect_odoo_mcp_sql_password = fields.Char(
        string="SQL Password",
        config_parameter=SQL_PASSWORD_PARAM,
    )

    def _get_perfect_odoo_mcp_mcp_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        base_url = _public_base_url(base_url)
        return f"{base_url.rstrip('/')}{MCP_PATH}" if base_url else MCP_PATH

    def get_values(self):
        values = super().get_values()
        values["perfect_odoo_mcp_mcp_url"] = self._get_perfect_odoo_mcp_mcp_url()
        return values
