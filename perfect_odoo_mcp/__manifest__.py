{
    "name": "Perfect Odoo MCP",
    "summary": "Odoo-native MCP server with OAuth, ACL-aware tools, SQL, and custom tool development.",
    "description": (
        "Perfect Odoo MCP must be installed as a normal local Odoo addon from the addons path. "
        "It is not compatible with Apps > Import Module because Odoo's import flow does not load "
        "new Python controllers, models, and security-sensitive route code."
    ),
    "version": "18.0.1.0.5",
    "category": "Technical",
    "author": "Hideki Andrea Yamamoto, Davide Bottazzo",
    "website": "https://github.com/hidekiyamamoto/odoo-mcp",
    "license": "LGPL-3",
    "sequence": 1,
    "depends": ["base", "base_setup"],
    "data": [
        "views/res_config_settings_views.xml",
    ],
    "images": [
        "thumb.png",
        "static/description/thumbnail.svg",
        "static/description/banner.svg",
    ],
    "installable": True,
    "application": True,
    "auto_install": False,
}
