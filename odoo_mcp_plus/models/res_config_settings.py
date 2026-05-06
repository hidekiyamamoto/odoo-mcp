from odoo import api, fields, models

from ..controllers.mcp import (
    MCP_PATH,
    SQL_DATABASE_PARAM,
    SQL_ENABLED_PARAM,
    SQL_HOST_PARAM,
    SQL_PASSWORD_PARAM,
    SQL_PORT_PARAM,
    SQL_READONLY_PARAM,
    SQL_USER_PARAM,
)


OPENAI_API_KEY_PARAM = "odoo_mcp_plus.openai_api_key"
GEMINI_API_KEY_PARAM = "odoo_mcp_plus.gemini_api_key"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odoo_mcp_plus_mcp_url = fields.Char(
        string="MCP URL",
        default=lambda self: self._get_odoo_mcp_plus_mcp_url(),
        readonly=True,
    )
    odoo_mcp_plus_openai_api_key = fields.Char(
        string="OpenAI API Key",
        config_parameter=OPENAI_API_KEY_PARAM,
        help="Stored OpenAI API key used by Odoo MCP Plus features.",
    )
    odoo_mcp_plus_gemini_api_key = fields.Char(
        string="Gemini API Key",
        config_parameter=GEMINI_API_KEY_PARAM,
        help="Stored Gemini API key used by Odoo MCP Plus features.",
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
    odoo_mcp_plus_has_openai_api_key = fields.Boolean(
        string="OpenAI API Key Stored",
        compute="_compute_odoo_mcp_plus_key_status",
    )
    odoo_mcp_plus_has_gemini_api_key = fields.Boolean(
        string="Gemini API Key Stored",
        compute="_compute_odoo_mcp_plus_key_status",
    )

    def _get_odoo_mcp_plus_mcp_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        return f"{base_url.rstrip('/')}{MCP_PATH}" if base_url else MCP_PATH

    def get_values(self):
        values = super().get_values()
        values["odoo_mcp_plus_mcp_url"] = self._get_odoo_mcp_plus_mcp_url()
        return values

    @api.depends()
    def _compute_odoo_mcp_plus_key_status(self):
        params = self.env["ir.config_parameter"].sudo()
        has_openai_key = bool(params.get_param(OPENAI_API_KEY_PARAM))
        has_gemini_key = bool(params.get_param(GEMINI_API_KEY_PARAM))
        for settings in self:
            settings.odoo_mcp_plus_has_openai_api_key = has_openai_key
            settings.odoo_mcp_plus_has_gemini_api_key = has_gemini_key

    def action_odoo_mcp_plus_clear_openai_key(self):
        self.env["ir.config_parameter"].sudo().set_param(OPENAI_API_KEY_PARAM, "")
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    def action_odoo_mcp_plus_clear_gemini_key(self):
        self.env["ir.config_parameter"].sudo().set_param(GEMINI_API_KEY_PARAM, "")
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }
