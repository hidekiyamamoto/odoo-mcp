from odoo import api, models


class PerfectOdooMcpMaintenance(models.AbstractModel):
    _name = "perfect.odoo.mcp.maintenance"
    _description = "Perfect Odoo MCP Maintenance"

    @api.model
    def cleanup_legacy_settings(self):
        self.env.cr.execute(
            """
            SELECT id
              FROM ir_ui_view
             WHERE name = %s
                OR arch_db::text ILIKE %s
            """,
            [
                "res.config.settings.view.form.inherit.perfect.odoo.mcp",
                "%perfect_odoo_mcp_mcp_url%",
            ],
        )
        stale_view_ids = [row[0] for row in self.env.cr.fetchall()]
        if stale_view_ids:
            self.env["ir.ui.view"].browse(stale_view_ids).unlink()

        old_action_xmlid = self.env["ir.model.data"].search(
            [
                ("module", "=", "perfect_odoo_mcp"),
                ("name", "=", "action_perfect_odoo_mcp_settings"),
                ("model", "=", "ir.actions.act_window"),
            ],
            limit=1,
        )
        if old_action_xmlid:
            old_action = self.env["ir.actions.act_window"].browse(old_action_xmlid.res_id)
            old_action_xmlid.unlink()
            if old_action.exists():
                old_action.unlink()

        stale_assets = self.env["ir.attachment"].search(
            [
                ("url", "like", "/web/assets/%"),
                "|",
                ("name", "ilike", "web.assets_backend"),
                ("url", "ilike", "web.assets_backend"),
            ]
        )
        if stale_assets:
            stale_assets.unlink()

        try:
            self.env["ir.ui.view"].clear_caches()
        except Exception:
            pass
