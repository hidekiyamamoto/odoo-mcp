{
    "name": "Perfect Odoo MCP",
    "summary": "Odoo-native MCP server with OAuth, ACL-aware tools, SQL, and custom tool development.",
    "version": "17.0.1.0.0",
    "category": "Technical",
    "author": "Hideki Andrea Yamamoto, 18 Montenapoleone",
    "website": "https://github.com/hidekiyamamoto/odoo-mcp",
    "license": "LGPL-3",
    "sequence": 1,
    "depends": ["base", "base_setup"],
    "data": [
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
    ],
    "images": [
        "static/description/thumbnail.svg",
        "static/description/banner.svg",
    ],
    "installable": True,
    "application": True,
}
