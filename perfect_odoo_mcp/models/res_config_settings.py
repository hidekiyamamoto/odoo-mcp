from odoo import models

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

    def _get_perfect_odoo_mcp_mcp_url(self):
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
        base_url = _public_base_url(base_url)
        return f"{base_url.rstrip('/')}{MCP_PATH}" if base_url else MCP_PATH

    def _get_bool_param(self, key, default=False):
        value = self.env["ir.config_parameter"].sudo().get_param(key)
        if value is None:
            return default
        return str(value).lower() in ("1", "true", "yes", "on")

    def _get_int_param(self, key, default=0):
        value = self.env["ir.config_parameter"].sudo().get_param(key)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def get_values(self):
        values = super().get_values()
        params = self.env["ir.config_parameter"].sudo()
        values.update(
            {
                "x_perfect_odoo_mcp_mcp_url": self._get_perfect_odoo_mcp_mcp_url(),
                "x_perfect_odoo_mcp_custom_tools_enabled": self._get_bool_param(CUSTOM_TOOLS_ENABLED_PARAM),
                "x_perfect_odoo_mcp_sql_enabled": self._get_bool_param(SQL_ENABLED_PARAM),
                "x_perfect_odoo_mcp_sql_readonly": self._get_bool_param(SQL_READONLY_PARAM, default=True),
                "x_perfect_odoo_mcp_sql_host": params.get_param(SQL_HOST_PARAM) or "127.0.0.1",
                "x_perfect_odoo_mcp_sql_port": self._get_int_param(SQL_PORT_PARAM, default=5432),
                "x_perfect_odoo_mcp_sql_database": params.get_param(SQL_DATABASE_PARAM) or self.env.cr.dbname,
                "x_perfect_odoo_mcp_sql_user": params.get_param(SQL_USER_PARAM) or "",
                "x_perfect_odoo_mcp_sql_password": params.get_param(SQL_PASSWORD_PARAM) or "",
            }
        )
        return values

    def set_values(self):
        result = super().set_values()
        params = self.env["ir.config_parameter"].sudo()
        params.set_param(CUSTOM_TOOLS_ENABLED_PARAM, "1" if self.x_perfect_odoo_mcp_custom_tools_enabled else "0")
        params.set_param(SQL_ENABLED_PARAM, "1" if self.x_perfect_odoo_mcp_sql_enabled else "0")
        params.set_param(SQL_READONLY_PARAM, "1" if self.x_perfect_odoo_mcp_sql_readonly else "0")
        params.set_param(SQL_HOST_PARAM, self.x_perfect_odoo_mcp_sql_host or "127.0.0.1")
        params.set_param(SQL_PORT_PARAM, str(self.x_perfect_odoo_mcp_sql_port or 5432))
        params.set_param(SQL_DATABASE_PARAM, self.x_perfect_odoo_mcp_sql_database or self.env.cr.dbname)
        params.set_param(SQL_USER_PARAM, self.x_perfect_odoo_mcp_sql_user or "")
        params.set_param(SQL_PASSWORD_PARAM, self.x_perfect_odoo_mcp_sql_password or "")
        return result
