from odoo import SUPERUSER_ID, api


MODULE = "perfect_odoo_mcp"
VIEW_NAME = "res.config.settings.view.form.inherit.perfect.odoo.mcp"
SETTINGS_URL = "/perfect_odoo_mcp/settings"
LEGACY_XMLIDS = [
    "action_perfect_odoo_mcp_settings",
    "action_perfect_odoo_mcp_settings_url",
]
CURRENT_XMLIDS = [
    "menu_perfect_odoo_mcp_settings",
    "action_perfect_odoo_mcp_settings_window",
    "res_config_settings_view_form",
]
CONFIG_PARAMETERS = [
    "perfect_odoo_mcp.custom_tools_enabled",
    "perfect_odoo_mcp.sql_enabled",
    "perfect_odoo_mcp.sql_readonly",
    "perfect_odoo_mcp.sql_host",
    "perfect_odoo_mcp.sql_port",
    "perfect_odoo_mcp.sql_database",
    "perfect_odoo_mcp.sql_user",
    "perfect_odoo_mcp.sql_password",
    "perfect_odoo_mcp.ai_context",
]


def _hook_env(*args):
    if len(args) == 1 and hasattr(args[0], "cr"):
        return args[0]
    return api.Environment(args[0], SUPERUSER_ID, {})


def _unlink_xmlids(env, names, unlink_records=False):
    xmlids = env["ir.model.data"].sudo().search(
        [
            ("module", "=", MODULE),
            ("name", "in", names),
        ]
    )
    for xmlid in xmlids:
        record = env[xmlid.model].sudo().browse(xmlid.res_id) if unlink_records else None
        xmlid.unlink()
        if record and record.exists():
            record.unlink()


def _cleanup_records(env, include_current_records=False):
    _unlink_xmlids(env, LEGACY_XMLIDS, unlink_records=True)
    if include_current_records:
        _unlink_xmlids(env, CURRENT_XMLIDS, unlink_records=True)

    stale_views = env["ir.ui.view"].search(
        [
            "|",
            ("name", "=", VIEW_NAME),
            ("arch_db", "ilike", "perfect_odoo_mcp_"),
        ]
    )
    if stale_views:
        stale_views.unlink()

    url_actions = env["ir.actions.act_url"].search([("url", "=", SETTINGS_URL)])
    if url_actions:
        url_actions.unlink()

    legacy_menus = env["ir.ui.menu"].search(
        [
            ("name", "=", "Perfect Odoo MCP"),
            "|",
            ("action", "ilike", "ir.actions.act_url,"),
            ("action", "=", False),
        ]
    )
    if legacy_menus:
        legacy_menus.unlink()

    if include_current_records:
        menus = env["ir.ui.menu"].search([("name", "=", "Perfect Odoo MCP")])
        if menus:
            menus.unlink()

        settings_actions = env["ir.actions.act_window"].search(
            [
                ("name", "=", "Perfect Odoo MCP"),
                ("res_model", "=", "res.config.settings"),
            ]
        )
        if settings_actions:
            settings_actions.unlink()

    stale_assets = env["ir.attachment"].search(
        [
            ("url", "like", "/web/assets/%"),
            "|",
            ("name", "ilike", "web.assets_backend"),
            ("url", "ilike", "web.assets_backend"),
        ]
    )
    if stale_assets:
        stale_assets.unlink()


def post_init_hook(*args):
    env = _hook_env(*args)
    _cleanup_records(env)


def uninstall_hook(*args):
    env = _hook_env(*args)
    _cleanup_records(env, include_current_records=True)

    params = env["ir.config_parameter"].sudo().search([("key", "in", CONFIG_PARAMETERS)])
    if params:
        params.unlink()
