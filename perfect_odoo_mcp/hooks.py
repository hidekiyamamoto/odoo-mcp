from odoo import SUPERUSER_ID, api, release


MODULE = "perfect_odoo_mcp"
VIEW_NAME = "res.config.settings.view.form.inherit.perfect.odoo.mcp"
VIEW_XMLID = "res_config_settings_view_form_installed"
SETTINGS_URL = "/perfect_odoo_mcp/settings"
LEGACY_XMLIDS = [
    "res_config_settings_view_form",
    VIEW_XMLID,
    "action_perfect_odoo_mcp_settings",
    "action_perfect_odoo_mcp_settings_url",
]
CURRENT_XMLIDS = [
    "menu_perfect_odoo_mcp_settings",
    "action_perfect_odoo_mcp_settings_window",
    VIEW_XMLID,
]
REQUIRED_SETTINGS_FIELDS = {
    "perfect_odoo_mcp_mcp_url",
    "perfect_odoo_mcp_custom_tools_enabled",
    "perfect_odoo_mcp_sql_enabled",
    "perfect_odoo_mcp_sql_readonly",
    "perfect_odoo_mcp_sql_host",
    "perfect_odoo_mcp_sql_port",
    "perfect_odoo_mcp_sql_database",
    "perfect_odoo_mcp_sql_user",
    "perfect_odoo_mcp_sql_password",
}
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


def _major_version():
    try:
        return int(release.version_info[0])
    except Exception:
        return 17


def _old_settings_arch():
    return """
<xpath expr="//div[hasclass('settings')]" position="inside">
    <div class="app_settings_block" data-string="Perfect Odoo MCP" string="Perfect Odoo MCP" data-key="perfect_odoo_mcp">
        <h2>MCP Server</h2>
        <div class="row mt16 o_settings_container">
            <div class="col-12 col-lg-6 o_setting_box" id="perfect_odoo_mcp_mcp_url">
                <div class="o_setting_left_pane"/>
                <div class="o_setting_right_pane">
                    <label for="perfect_odoo_mcp_mcp_url"/>
                    <div class="text-muted">Use this URL to connect an MCP client to this Odoo database.</div>
                    <div class="content-group mt16">
                        <field name="perfect_odoo_mcp_mcp_url" readonly="1" nolabel="1" class="w-100"/>
                    </div>
                </div>
            </div>
            <div class="col-12 col-lg-6 o_setting_box" id="perfect_odoo_mcp_custom_tools_enabled_setting">
                <div class="o_setting_left_pane">
                    <field name="perfect_odoo_mcp_custom_tools_enabled"/>
                </div>
                <div class="o_setting_right_pane">
                    <label for="perfect_odoo_mcp_custom_tools_enabled"/>
                    <div class="text-muted">Expose MCP tools that let an authorized MCP client read, write, reload, and test custom Python MCP tools.</div>
                    <div class="text-warning mt8" attrs="{'invisible': [('perfect_odoo_mcp_custom_tools_enabled', '=', False)]}">
                        Custom tools are executable Python files. Keep this disabled unless you are actively creating or reviewing tools.
                    </div>
                </div>
            </div>
        </div>

        <h2>Database Access</h2>
        <div class="row mt16 o_settings_container">
            <div class="col-12 col-lg-6 o_setting_box" id="perfect_odoo_mcp_sql_enabled_setting">
                <div class="o_setting_left_pane">
                    <field name="perfect_odoo_mcp_sql_enabled"/>
                </div>
                <div class="o_setting_right_pane">
                    <label for="perfect_odoo_mcp_sql_enabled"/>
                    <div class="text-muted">Expose a direct PostgreSQL MCP tool. Keep this disabled unless you explicitly need database-level access.</div>
                    <div class="text-warning mt8" attrs="{'invisible': [('perfect_odoo_mcp_sql_enabled', '=', False)]}">
                        Direct SQL access bypasses Odoo ORM protections. Use a restricted PostgreSQL user whenever possible.
                    </div>
                </div>
            </div>
            <div class="col-12 col-lg-6 o_setting_box" id="perfect_odoo_mcp_sql_readonly_setting" attrs="{'invisible': [('perfect_odoo_mcp_sql_enabled', '=', False)]}">
                <div class="o_setting_left_pane">
                    <field name="perfect_odoo_mcp_sql_readonly"/>
                </div>
                <div class="o_setting_right_pane">
                    <label for="perfect_odoo_mcp_sql_readonly"/>
                    <div class="text-muted">Refuse write-like SQL operations and open PostgreSQL sessions in readonly mode.</div>
                </div>
            </div>
            <div class="col-12 col-lg-6 o_setting_box" id="perfect_odoo_mcp_sql_connection_setting" attrs="{'invisible': [('perfect_odoo_mcp_sql_enabled', '=', False)]}">
                <div class="o_setting_left_pane"/>
                <div class="o_setting_right_pane">
                    <span class="o_form_label">SQL Connection</span>
                    <div class="text-muted">PostgreSQL connection used by the direct SQL MCP tool.</div>
                    <div class="content-group mt16">
                        <div class="row mt8">
                            <label for="perfect_odoo_mcp_sql_host" class="col-lg-3 o_light_label"/>
                            <field name="perfect_odoo_mcp_sql_host" placeholder="127.0.0.1"/>
                        </div>
                        <div class="row mt8">
                            <label for="perfect_odoo_mcp_sql_port" class="col-lg-3 o_light_label"/>
                            <field name="perfect_odoo_mcp_sql_port" placeholder="5432"/>
                        </div>
                        <div class="row mt8">
                            <label for="perfect_odoo_mcp_sql_database" class="col-lg-3 o_light_label"/>
                            <field name="perfect_odoo_mcp_sql_database"/>
                        </div>
                        <div class="row mt8">
                            <label for="perfect_odoo_mcp_sql_user" class="col-lg-3 o_light_label"/>
                            <field name="perfect_odoo_mcp_sql_user"/>
                        </div>
                        <div class="row mt8">
                            <label for="perfect_odoo_mcp_sql_password" class="col-lg-3 o_light_label"/>
                            <field name="perfect_odoo_mcp_sql_password" password="True"/>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    </div>
</xpath>
"""


def _new_settings_arch():
    return """
<xpath expr="//form" position="inside">
    <app data-string="Perfect Odoo MCP" string="Perfect Odoo MCP" name="perfect_odoo_mcp" id="perfect_odoo_mcp_settings">
        <block title="MCP Server" name="perfect_odoo_mcp_mcp_server">
            <setting id="perfect_odoo_mcp_mcp_url" string="MCP Endpoint" help="Use this URL to connect an MCP client to this Odoo database.">
                <div class="content-group">
                    <div class="mt16">
                        <field name="perfect_odoo_mcp_mcp_url" readonly="1" nolabel="1" class="w-100"/>
                    </div>
                </div>
            </setting>
            <setting id="perfect_odoo_mcp_custom_tools_enabled" string="Allow Custom Tools Creation" help="Expose MCP tools that let an authorized MCP client read, write, reload, and test custom Python MCP tools.">
                <field name="perfect_odoo_mcp_custom_tools_enabled"/>
                <div class="text-warning mt8" invisible="not perfect_odoo_mcp_custom_tools_enabled">
                    Custom tools are executable Python files. Keep this disabled unless you are actively creating or reviewing tools.
                </div>
            </setting>
        </block>
        <block title="Database Access" name="perfect_odoo_mcp_database_access">
            <setting id="perfect_odoo_mcp_sql_enabled" string="Direct SQL" help="Expose a direct PostgreSQL MCP tool. Keep this disabled unless you explicitly need database-level access.">
                <field name="perfect_odoo_mcp_sql_enabled"/>
                <div class="text-warning mt8" invisible="not perfect_odoo_mcp_sql_enabled">
                    Direct SQL access bypasses Odoo ORM protections. Use a restricted PostgreSQL user whenever possible.
                </div>
            </setting>
            <setting id="perfect_odoo_mcp_sql_readonly" string="Readonly SQL" help="Refuse write-like SQL operations and open PostgreSQL sessions in readonly mode." invisible="not perfect_odoo_mcp_sql_enabled">
                <field name="perfect_odoo_mcp_sql_readonly"/>
            </setting>
            <setting id="perfect_odoo_mcp_sql_connection" string="SQL Connection" help="PostgreSQL connection used by the direct SQL MCP tool." invisible="not perfect_odoo_mcp_sql_enabled">
                <div class="content-group">
                    <div class="row mt16">
                        <label for="perfect_odoo_mcp_sql_host" class="col-lg-3 o_light_label"/>
                        <field name="perfect_odoo_mcp_sql_host" placeholder="127.0.0.1"/>
                    </div>
                    <div class="row mt8">
                        <label for="perfect_odoo_mcp_sql_port" class="col-lg-3 o_light_label"/>
                        <field name="perfect_odoo_mcp_sql_port" placeholder="5432"/>
                    </div>
                    <div class="row mt8">
                        <label for="perfect_odoo_mcp_sql_database" class="col-lg-3 o_light_label"/>
                        <field name="perfect_odoo_mcp_sql_database"/>
                    </div>
                    <div class="row mt8">
                        <label for="perfect_odoo_mcp_sql_user" class="col-lg-3 o_light_label"/>
                        <field name="perfect_odoo_mcp_sql_user"/>
                    </div>
                    <div class="row mt8">
                        <label for="perfect_odoo_mcp_sql_password" class="col-lg-3 o_light_label"/>
                        <field name="perfect_odoo_mcp_sql_password" password="True"/>
                    </div>
                </div>
            </setting>
        </block>
    </app>
</xpath>
"""


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

    view_domain = [
        "|",
        ("name", "=", VIEW_NAME),
        ("arch_db", "ilike", "perfect_odoo_mcp_"),
    ]
    views = env["ir.ui.view"].search(view_domain)
    if views:
        views.unlink()

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


def _settings_fields_ready(env):
    settings_fields = env["res.config.settings"]._fields
    return REQUIRED_SETTINGS_FIELDS.issubset(settings_fields)


def _create_settings_view(env):
    if not _settings_fields_ready(env):
        return

    parent = env.ref("base_setup.res_config_settings_view_form", raise_if_not_found=False)
    if not parent:
        return

    arch = _old_settings_arch() if _major_version() <= 16 else _new_settings_arch()
    view = env["ir.ui.view"].create(
        {
            "name": VIEW_NAME,
            "type": "form",
            "model": "res.config.settings",
            "inherit_id": parent.id,
            "arch": arch,
        }
    )
    env["ir.model.data"].sudo().create(
        {
            "module": MODULE,
            "name": VIEW_XMLID,
            "model": "ir.ui.view",
            "res_id": view.id,
            "noupdate": False,
        }
    )


def post_init_hook(*args):
    env = _hook_env(*args)
    _cleanup_records(env)
    _create_settings_view(env)


def uninstall_hook(*args):
    env = _hook_env(*args)
    _cleanup_records(env, include_current_records=True)

    params = env["ir.config_parameter"].sudo().search([("key", "in", CONFIG_PARAMETERS)])
    if params:
        params.unlink()
