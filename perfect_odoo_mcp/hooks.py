from odoo import SUPERUSER_ID, api


def post_init_hook(cr, registry):
    env = api.Environment(cr, SUPERUSER_ID, {})
    model = env["ir.model"].search([("model", "=", "perfect.odoo.mcp.oauth.token")], limit=1)
    if not model:
        return

    group = env.ref("base.group_system")
    access = env["ir.model.access"].search(
        [
            ("name", "=", "perfect.odoo.mcp.oauth.token system"),
            ("model_id", "=", model.id),
            ("group_id", "=", group.id),
        ],
        limit=1,
    )
    values = {
        "name": "perfect.odoo.mcp.oauth.token system",
        "model_id": model.id,
        "group_id": group.id,
        "perm_read": True,
        "perm_write": True,
        "perm_create": True,
        "perm_unlink": True,
    }
    if access:
        access.write(values)
    else:
        env["ir.model.access"].create(values)
