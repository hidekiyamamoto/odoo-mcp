# Perfect Odoo MCP

Perfect Odoo MCP turns an Odoo database into a first-class MCP server.

It is an Odoo module, not a sidecar service. The MCP endpoint runs inside Odoo, authenticates with Odoo login, stores OAuth tokens in Odoo, and executes record operations as the authorized Odoo user. That means normal Odoo ACLs and record rules stay in charge.

## What You Get

### Features

- **Perfect Odoo MCP server**: exposes an Odoo database as an MCP endpoint for ChatGPT and other MCP clients.
- **Odoo login + ACLs**: users authorize with Odoo login, and record tools run as that Odoo user, respecting ACLs and record rules.
- **Odoo-aware tools**: search, fetch, model/domain queries, install inventory, AI context, and read-only Python addon lookup.
- **Automatic context bootstrap**: when no AI context exists, the module returns a strict initialization protocol that pushes the client to inspect the install, review custom modules, and save a durable database-specific context.
- **Optional direct database access**: exposes a PostgreSQL SQL tool only when enabled, with readonly safeguards available.
- **Custom tool development**: lets an AI draft, read, write, test, reload, and publish custom MCP tools as reviewed Python files.
- **Smart installer**: detects Odoo, chooses the right branch, copies the module, and refreshes the app list.
- **More coming soon**: the module is designed as a foundation for additional Odoo-native MCP capabilities.

## Why This Exists

Generic MCP servers can talk to Odoo from the outside. Perfect Odoo MCP lives inside Odoo instead.

That matters because:

- The current OAuth user is an actual Odoo user.
- Odoo access rules are respected by default.
- The server can inspect installed modules, models, fields, and addon code directly.
- Custom tools can be written against the real Odoo ORM.
- The integration can be installed, configured, backed up, and reviewed like any other Odoo module.

## Repository Layout

```text
.
├── install-odoo-module.sh      # Smart installer for local Odoo deployments
├── perfect_odoo_mcp/              # Odoo addon
│   ├── __manifest__.py
│   ├── controllers/mcp.py      # MCP, OAuth, tool dispatch
│   ├── models/                 # Settings and OAuth token model
│   ├── security/
│   └── views/                  # Settings page integration
└── README.md
```

The historical Node implementation may exist locally as `odoo-mcp-node/`, but this repository is now treated as the Odoo module repository.

## Requirements

- Odoo with `base` and `base_setup`.
- Python environment used by Odoo.
- `git` for the installer.
- A public HTTPS Odoo URL for OAuth clients such as ChatGPT.
- PostgreSQL credentials only if you enable the optional SQL tool.

This branch targets Odoo `16.0`. The installer is branch-aware, so Odoo 16 installations select this branch automatically when it is published.

## Installation

> **Local addon install required:** Perfect Odoo MCP is not compatible with Odoo's **Apps -> Import Module** upload flow. The module defines Python controllers, models, OAuth routes, and MCP endpoints, so it must be deployed as a normal local addon in an Odoo addons path and loaded by the Odoo server. This follows Odoo's security architecture: uploaded importable modules cannot safely register new Python route code.

From a machine that can access the Odoo installation:

Debian usage:

```bash
curl -fsSL https://raw.githubusercontent.com/hidekiyamamoto/odoo-mcp/main/install-perfect-odoo-mcp.sh | bash
```

Local checkout usage:

```bash
./install-odoo-module.sh -d YOUR_DATABASE
```

The installer will:

1. Find the local `odoo` or `odoo-bin` executable from PATH, live Odoo processes, systemd service definitions, or common install paths.
2. Detect the Odoo version.
3. Select the matching Git branch when available.
4. Prefer a high-confidence core addons directory over misleading empty config paths.
5. Clone `https://github.com/hidekiyamamoto/odoo-mcp`.
6. Copy `perfect_odoo_mcp` into the selected addons directory.
7. Back up any old `odoo_mcp` addon directory left from the pre-Perfect rename.
8. Refresh Odoo's app list if `--database` is provided.
9. Upgrade `perfect_odoo_mcp` automatically if it is already installed in that database.

Useful options:

```bash
./install-odoo-module.sh --help
./install-odoo-module.sh --addons-dir /mnt/extra-addons -d YOUR_DATABASE
./install-odoo-module.sh --config /etc/odoo/odoo.conf -d YOUR_DATABASE
./install-odoo-module.sh --odoo-bin /opt/odoo/odoo-bin -d YOUR_DATABASE
./install-odoo-module.sh --branch 17.0 -d YOUR_DATABASE
```

After copying, restart Odoo if the target addons directory is loaded by a running service. Then open Apps, remove the app search filter if needed, and install **Perfect Odoo MCP**.

## Odoo Settings

After installation, open:

```text
Settings -> Perfect Odoo MCP
```

The page shows the MCP endpoint:

```text
https://your-odoo-domain.example/perfect_odoo_mcp/mcp
```

It also exposes these options:

- **Allow Custom Tools Creation**
  Enables tools that can read, write, reload, and call custom Python MCP tools.

- **Enable Direct Database Access**
  Advertises the `odoo_sql` tool and enables PostgreSQL access using the configured connection details.

- **SQL Readonly**
  Keeps SQL sessions readonly and refuses write-looking statements.

- **SQL Host / Port / Database / User / Password**
  PostgreSQL connection settings used only by the optional SQL tool.

## Connecting an MCP Client

Use the MCP endpoint shown in Odoo Settings:

```text
https://your-odoo-domain.example/perfect_odoo_mcp/mcp
```

Perfect Odoo MCP exposes OAuth discovery metadata so autosensing clients can find:

- Protected resource metadata.
- Authorization server metadata.
- Dynamic client registration.
- Authorization endpoint.
- Token endpoint.

When a user connects, Odoo displays an authorization page. Internal Odoo users can authorize after login. The resulting access token is stored in Odoo as a `perfect.odoo.mcp.oauth.token` record and is bound to the Odoo user.

### ChatGPT

ChatGPT custom MCP connectors currently require Developer Mode/custom connector setup. Use ChatGPT on the web.

1. Make sure your Odoo endpoint is public HTTPS:

   ```text
   https://your-odoo-domain.example/perfect_odoo_mcp/mcp
   ```

2. In ChatGPT, enable Developer Mode.
   - Business / Enterprise / Edu: an admin or owner may need to enable custom MCP connector creation in workspace settings first.
   - Plus / Pro: Developer Mode may need to be enabled in personal ChatGPT settings before custom MCP connectors are available.

3. Create a new custom MCP connector/app.
   - Name: `Perfect Odoo MCP`
   - Server URL: `https://your-odoo-domain.example/perfect_odoo_mcp/mcp`
   - Authentication: OAuth / auto-discovered OAuth

4. Connect the app. ChatGPT should discover Perfect Odoo MCP's OAuth metadata, open the Odoo login/authorization page, and then show the connector with a `Dev` label while it is still private.

If a previously working ChatGPT connection disappears or starts asking to reconnect after several hours, upgrade the Odoo addon and reconnect the app once. Perfect Odoo MCP advertises `offline_access` and issues refresh tokens so ChatGPT can renew access tokens without losing the link.

5. After connecting, use **Refresh actions** whenever you enable SQL, enable custom tools, publish a custom tool, or update the module.

6. In the first chat, ask the model to run `get-ai-context`. If it returns the bootstrap protocol, follow it before asking business questions.

### Gemini

Gemini web/chat does not currently expose the same general custom MCP connector workflow as ChatGPT. For now, use Gemini CLI for Perfect Odoo MCP.

Install and configure Gemini CLI, then add the remote HTTP MCP server:

```bash
gemini mcp add --transport http perfect-odoo-mcp https://your-odoo-domain.example/perfect_odoo_mcp/mcp
```

Gemini CLI supports OAuth discovery for remote HTTP/SSE MCP servers. On first use it should detect the Odoo authorization requirement, open the browser OAuth flow, and store the connection locally.

Useful Gemini CLI checks:

```bash
gemini mcp list
gemini
/mcp
```

After the server is connected, start by asking Gemini to call `get-ai-context`. If the context is empty, use medium or high reasoning and let the bootstrap protocol create the first durable context with `set-ai-context`.

## Tool Overview

### `search`

Broad connector-style search over known business models visible to the authorized user. Returns stable IDs such as:

```text
res.partner:123
sale.order:456
```

Use this when the client does not yet know the exact model.

### `fetch`

Fetches one stable ID returned by `search`.

### `install_info`

Returns a high-level inventory of the Odoo installation:

- Odoo version.
- Request domain.
- Primary company.
- Installed modules.
- Module source classification.
- SQL and custom tool availability.

This is the first tool an AI should call when it needs to understand a new database.

### `get-ai-context`

Returns stored instance-specific guidance. If no context has been configured, it returns a strict initialization protocol that asks the AI to inspect the installation, inspect custom code, build a real context, and store it.

### `set-ai-context`

Replaces the stored AI context for the database.

Use this after discovery to save durable guidance such as important models, safe domains, business vocabulary, and caveats.

### `odoo_search`

Precise Odoo model search.

Input:

```json
{
  "model": "res.partner",
  "domain": "[[\"is_company\", \"=\", true]]",
  "limit": 20,
  "order": "name"
}
```

Returns matching record IDs.

### `odoo_search_read`

Precise Odoo model search with selected fields.

Input:

```json
{
  "model": "res.partner",
  "domain": "[[\"is_company\", \"=\", true]]",
  "fields": ["id", "display_name", "email"],
  "limit": 20,
  "order": "name"
}
```

Returns records visible to the authorized Odoo user.

### `odoo_python_lookup`

Read-only lookup for Python code under Odoo addon paths.

Search mode:

```json
{
  "operation": "search",
  "query": "_name =",
  "module": "sale",
  "maxResults": 50
}
```

Read mode:

```json
{
  "operation": "read",
  "path": "sale/models/sale_order.py"
}
```

This tool only searches and reads Python source. It never modifies files, records, settings, or server state.

## Optional SQL Tool

`odoo_sql` is advertised only when **Enable Direct Database Access** is checked.

Input:

```json
{
  "query": "select id, name from res_partner where active = %s limit 10",
  "parameters": [true],
  "maxRows": 10
}
```

Safety behavior:

- Multiple statements are refused.
- Embedded semicolons are refused.
- In readonly mode, only read-looking SQL starts are accepted.
- Dangerous keywords are blocked in readonly mode.
- PostgreSQL sessions are opened as readonly when readonly mode is enabled.

Direct SQL bypasses Odoo ORM semantics. Keep it disabled unless there is a clear operational reason to expose it.

## Optional Custom MCP Tools

Custom tools are enabled only when **Allow Custom Tools Creation** is checked.

When enabled, these tools are advertised:

- `custom_tools_list`
- `custom_tool_read`
- `custom_tool_write`
- `custom_tools_reload`
- `call-custom`

Custom tool files live in the Odoo data directory:

```text
<odoo data_dir>/perfect_odoo_mcp_custom_tools/
```

On many Debian-style installs this is:

```text
/var/lib/odoo/.local/share/Odoo/perfect_odoo_mcp_custom_tools/
```

They are intentionally outside the installed addon directory so Odoo can write them without modifying packaged module code.

### Custom Tool File Format

Each custom tool is a Python file:

```python
"""Draft custom MCP tool for Perfect Odoo MCP."""

EXPOSED = False

TOOL = {
    "name": "example_custom_tool",
    "title": "Example Custom Tool",
    "description": "Describe what this custom tool does.",
    "inputSchema": {
        "type": "object",
        "properties": {
            "message": {"type": "string"}
        },
        "additionalProperties": False,
    },
}


def call(arguments, env, request):
    return {
        "message": arguments.get("message"),
        "user": env.user.login,
        "company": env.company.name,
    }
```

`call(arguments, env, request)` receives:

- `arguments`: JSON arguments from the MCP call.
- `env`: Odoo `api.Environment` for the OAuth-authorized user.
- `request`: the current Odoo HTTP request.

### Custom Tool Lifecycle

1. Use `install_info` to understand the installation.
2. Use `odoo_python_lookup` to inspect relevant Odoo models and custom modules.
3. Create a complete `.py` custom tool with `EXPOSED = False`.
4. Submit it through `custom_tool_write`.
5. Test it with `call-custom`.
6. Review the code and behavior.
7. Ask the user to publish it, make it available, or declare it stable.
8. Set `EXPOSED = True`.
9. Call `custom_tools_reload`.
10. Refresh the MCP client's actions.

Only `EXPOSED = True` tools are advertised in `tools/list`. Draft tools stay hidden and are callable only through `call-custom`.

Custom tools are executable Python inside Odoo. Keep this feature disabled except during active creation and review.

## AI Context Workflow

Perfect Odoo MCP stores one durable AI context string in `ir.config_parameter`.

Recommended first-run flow for an AI:

1. Call `get-ai-context`.
2. If it returns the bootstrap protocol, call `install_info`.
3. Inspect custom modules with `odoo_python_lookup`.
4. Validate important live models with narrow `odoo_search_read` calls.
5. Write a useful operational context with `set-ai-context`.
6. Call `get-ai-context` again and use the stored result.

Good context should include:

- Exact model names.
- Exact field names.
- Important relationships.
- Safe domains.
- Common lookup recipes.
- Caveats discovered from code or live data.
- Clear distinction between observed facts and inferred meaning.

## Security Model

Perfect Odoo MCP is designed around Odoo's own security model.

- OAuth tokens are stored as `perfect.odoo.mcp.oauth.token`.
- Tokens are hashed at rest.
- Tokens are linked to Odoo users.
- Record tools run as the authorized Odoo user.
- Odoo ACLs and record rules apply to ORM reads.
- Internal Odoo users authorize through Odoo login.

High-risk capabilities are feature-gated:

- SQL is hidden unless direct database access is enabled.
- Custom tool management is hidden unless custom tool creation is enabled.
- Custom tools are draft-only until `EXPOSED = True`.

## Troubleshooting

### The module does not appear in Apps

Refresh the app list:

```bash
./install-odoo-module.sh -d YOUR_DATABASE
```

Or from Odoo UI, enable developer mode and update the Apps list.

### The Settings checkbox is saved but tools do not appear

Some MCP clients cache actions. After changing settings:

1. Save Odoo Settings.
2. Restart Odoo if needed.
3. Refresh/update actions in the MCP client.
4. Disconnect and reconnect the client if it still shows the old tool list.

### ChatGPT says a tool schema is invalid

Check `tools/list` for the named tool. Strict MCP clients reject incomplete JSON Schema. Common problems are arrays without `items`, object schemas without properties, or unsupported schema shapes.

### OAuth discovery fails

Verify:

- Odoo is reachable through public HTTPS.
- Reverse proxy forwards the correct host and scheme.
- The MCP URL in Settings uses the public domain.
- TLS certificate chain is valid from the client environment.

### SQL tool does not appear

Enable **Direct Database Access** in Settings and refresh the MCP client's actions.

### Custom tools do not appear

Enable **Allow Custom Tools Creation** in Settings and refresh actions.

Remember:

- Manager tools appear when the flag is enabled.
- Draft custom tools with `EXPOSED = False` do not appear in `tools/list`.
- Published custom tools require `EXPOSED = True` and `custom_tools_reload`.

## Development Notes

Deploy local changes to a live Odoo addons path:

```bash
cp -a perfect_odoo_mcp/. /usr/lib/python3/dist-packages/odoo/addons/perfect_odoo_mcp/
systemctl restart odoo
```

Upgrade the module when fields/views/security metadata change:

```bash
sudo -u odoo odoo -c /etc/odoo/odoo.conf -d YOUR_DATABASE -u perfect_odoo_mcp --stop-after-init --no-http
systemctl restart odoo
```

Run a quick syntax check:

```bash
python3 -m py_compile perfect_odoo_mcp/controllers/mcp.py
```

## License

LGPL-3, matching the module manifest.
