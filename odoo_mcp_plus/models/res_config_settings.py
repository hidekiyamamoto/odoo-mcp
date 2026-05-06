from odoo import api, fields, models

from ..controllers.mcp import MCP_PATH


OPENAI_API_KEY_PARAM = "odoo_mcp_plus.openai_api_key"
GEMINI_API_KEY_PARAM = "odoo_mcp_plus.gemini_api_key"


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odoo_mcp_plus_mcp_url = fields.Char(
        string="MCP URL",
        compute="_compute_odoo_mcp_plus_mcp_url",
        readonly=True,
    )
    odoo_mcp_plus_openai_api_key = fields.Char(
        string="OpenAI API Key",
        help="Enter a new key to replace the stored OpenAI API key. Leave empty to keep the current key.",
    )
    odoo_mcp_plus_gemini_api_key = fields.Char(
        string="Gemini API Key",
        help="Enter a new key to replace the stored Gemini API key. Leave empty to keep the current key.",
    )
    odoo_mcp_plus_has_openai_api_key = fields.Boolean(
        string="OpenAI API Key Stored",
        compute="_compute_odoo_mcp_plus_key_status",
    )
    odoo_mcp_plus_has_gemini_api_key = fields.Boolean(
        string="Gemini API Key Stored",
        compute="_compute_odoo_mcp_plus_key_status",
    )

    @api.depends()
    def _compute_odoo_mcp_plus_mcp_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        mcp_url = f"{base_url.rstrip('/')}{MCP_PATH}" if base_url else MCP_PATH
        for settings in self:
            settings.odoo_mcp_plus_mcp_url = mcp_url

    @api.depends()
    def _compute_odoo_mcp_plus_key_status(self):
        params = self.env["ir.config_parameter"].sudo()
        has_openai_key = bool(params.get_param(OPENAI_API_KEY_PARAM))
        has_gemini_key = bool(params.get_param(GEMINI_API_KEY_PARAM))
        for settings in self:
            settings.odoo_mcp_plus_has_openai_api_key = has_openai_key
            settings.odoo_mcp_plus_has_gemini_api_key = has_gemini_key

    def set_values(self):
        super().set_values()
        params = self.env["ir.config_parameter"].sudo()
        if self.odoo_mcp_plus_openai_api_key:
            params.set_param(OPENAI_API_KEY_PARAM, self.odoo_mcp_plus_openai_api_key)
        if self.odoo_mcp_plus_gemini_api_key:
            params.set_param(GEMINI_API_KEY_PARAM, self.odoo_mcp_plus_gemini_api_key)

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
