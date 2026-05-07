from odoo import fields, models


class OdooMcpOAuthToken(models.Model):
    _name = "odoo.mcp.oauth.token"
    _description = "Odoo MCP OAuth Token"
    _order = "create_date desc"

    name = fields.Char(required=True)
    token_hash = fields.Char(required=True, index=True, copy=False)
    user_id = fields.Many2one(
        "res.users",
        required=True,
        ondelete="cascade",
        index=True,
    )
    client_id = fields.Char(required=True, index=True)
    scope = fields.Char(required=True)
    audience = fields.Char(required=True)
    expires_at = fields.Datetime(required=True, index=True)
    last_used_at = fields.Datetime(readonly=True)
    revoked_at = fields.Datetime(readonly=True)

    def action_revoke(self):
        self.write({"revoked_at": fields.Datetime.now()})
