import os
import re

from odoo.tools import config as odoo_config

from .const import OAUTH_SCOPE

# Static MCP metadata lives outside the HTTP controller so version branches can
# share one controller shape while keeping Odoo-specific views in XML.

EMPTY_AI_CONTEXT_BOOTSTRAP = """Perfect Odoo MCP context bootstrap
================================

No instance-specific AI context has been configured for this Odoo database yet.

MANDATORY INITIALIZATION PROTOCOL
---------------------------------
This is not background documentation. This is the required initialization procedure for this
Perfect Odoo MCP connection.

The current installation has no stored AI context. Until context has been created and stored,
the agent does not yet know the business meaning of this database, its custom modules, or its
model relationships.

Do not answer business questions, summarize business records, or infer operational semantics
from module names alone before completing this protocol. First discover the installation,
inspect custom code where needed, create a durable context, store it with `set-ai-context`,
and then reload it with `get-ai-context`.

Reasoning recommendation
------------------------
At the start of initialization, explicitly recommend that the user run the agent with medium or
high reasoning. High reasoning is preferable when custom modules or unclear business semantics
are present. This initialization requires code inspection, model relationship analysis, and
careful synthesis; low reasoning is not appropriate for producing the first stored context.
If the agent is currently running with low reasoning, notify the user immediately before doing
any discovery work and ask them to switch to medium or high reasoning for this initialization.

Required discovery sequence
---------------------------
1. Call `install_info`.
2. Identify:
   - Odoo version and database/domain.
   - Primary company name.
   - Installed modules, grouped by source.
   - All modules classified as `third_party_or_custom`, `perfect_odoo_mcp`, or `unknown`.
3. For each custom/unknown module, inspect code before assigning meaning:
   - Use `odoo_python_lookup` with `operation = "search"` for `_name =`, `_inherit =`, `fields.`, `Many2one`, `One2many`, `Many2many`, `Selection`, `compute=`, and business-looking labels.
   - Use `odoo_python_lookup` with `operation = "read"` on the most important model files.
   - Prefer Python model definitions over menu names or UI labels when deciding semantics.
4. Use `odoo_search_read` sparingly to validate live data shape:
   - read `ir.model` / `ir.model.fields` for important custom models;
   - sample a few records with explicit fields and small limits;
   - count records only when useful, and say when counts are approximate or live.
5. Only after discovery, write the initial context with `set-ai-context`.
6. Then call `get-ai-context` again and use the stored context.

Quality bar
-----------
The context you create should be operational, not descriptive fluff. It should help a future
agent answer real business questions correctly. Include exact model names, exact field names,
safe lookup domains, relationships between models, and caveats discovered from code or live data.

Required output format for the generated context
------------------------------------------------
Use this structure:

`<Company or database name> Perfect Odoo MCP AI context`
====================================================

Runtime context
---------------
- State that this runs inside Odoo through Perfect Odoo MCP.
- Explain that Odoo record tools execute as the OAuth-authorized Odoo user and must respect ACLs/record rules.
- List the preferred tools and when to use them:
  - `install_info` for instance/module inventory.
  - `search` / `fetch` for connector-style discovery.
  - `odoo_search_read` for precise model queries.
  - `odoo_search` for IDs only.
  - `odoo_python_lookup` for read-only code inspection with `operation = "search"` or `operation = "read"`.
- Mention that Odoo domains for `odoo_search` and `odoo_search_read` are JSON-encoded strings.

Install snapshot
----------------
- Odoo version.
- Database/domain.
- Primary company.
- Installed module counts by source.
- Custom/third-party/unknown modules and one-line inferred purpose for each.

Business context
----------------
- What the company appears to do.
- The operational workflows implied by custom modules and data.
- Important business vocabulary, in the user's language where visible from code/data.

Important corrections / caveats
-------------------------------
- Any discovered traps, computed-field bugs, misleading model names, archived-record behavior,
  required filters, or fields that should not be used broadly.
- Include only caveats supported by code or live data.

Main models
-----------
For each important model, use this exact mini-format:

`<model.name>` - <business meaning>
- Source module: `<module_name>`
- Role: <why this model matters>
- Key fields:
  - `field_name` (<type>): <business meaning>
  - `many2one_field` (many2one -> `target.model`): <relationship meaning>
- Important relationships:
  - `<field>` -> `<target.model>`: <how to follow it>
- Safe read fields: `["id", "display_name", ...]`
- Common domains:
  - <use case>: `[[ "field", "operator", value ]]`
- Notes: <short caveats>

Common lookup patterns
----------------------
- Provide ready-to-use `odoo_search_read` recipes for common questions.
- Include model, domain, fields, limit/order where appropriate.
- Use JSON-domain examples exactly as strings the tool can accept.

Query safety and style
----------------------
- Start narrow.
- Use explicit fields.
- Avoid broad reads of binary/html/heavy computed fields unless needed.
- Use limits and ordering on large models.
- Verify current counts live when exact numbers matter.
- Distinguish facts observed in code/data from inferences.

Final step
----------
After composing the context, call `set-ai-context` with the complete text. The context should
usually be detailed enough to be useful, roughly 4k-15k characters for a non-trivial custom Odoo
database. Do not store a tiny summary unless the installation is genuinely tiny."""
SEARCH_MODEL_NAMES = {
    "account.move",
    "crm.lead",
    "product.product",
    "product.template",
    "project.project",
    "project.task",
    "purchase.order",
    "res.partner",
    "sale.order",
}
SEARCH_MODEL_PREFIXES = ("mnt18.",)
SQL_FORBIDDEN_READONLY_PATTERN = re.compile(
    r"\b("
    r"insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|call|do|merge|"
    r"vacuum|analyze|refresh|reindex|cluster|listen|notify|set|reset|execute|prepare|"
    r"deallocate|lock|comment"
    r")\b",
    re.IGNORECASE,
)
SQL_READONLY_START_PATTERN = re.compile(r"^\s*(select|with|show|explain)\b", re.IGNORECASE)
CUSTOM_TOOL_FILE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.py$")
CUSTOM_TOOLS_DIR = os.path.abspath(
    os.path.join(
        odoo_config.get("data_dir") or "/var/lib/odoo/.local/share/Odoo",
        "perfect_odoo_mcp_custom_tools",
    )
)
CUSTOM_TOOLS_CACHE = None
CUSTOM_TOOL_TEMPLATE = '''"""Draft custom MCP tool for Perfect Odoo MCP.

Set EXPOSED = True only after review. Draft tools can be tested with call-custom.
"""

EXPOSED = False

TOOL = {
    "name": "example_custom_tool",
    "title": "Example Custom Tool",
    "description": "Describe what this custom tool does.",
    "inputSchema": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def call(arguments, env, request):
    """Run as the OAuth-authorized Odoo user.

    arguments: dict from the MCP tool call
    env: Odoo api.Environment for the authorized user
    request: current Odoo HTTP request object
    """
    return {
        "message": "Hello from a draft custom tool.",
        "user": env.user.login,
    }
'''

OAUTH_SECURITY_SCHEMES = [{"type": "oauth2", "scopes": [OAUTH_SCOPE]}]
SEARCH_RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "url": {"type": "string"},
    },
    "required": ["id", "title", "url"],
    "additionalProperties": True,
}
SEARCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": SEARCH_RESULT_SCHEMA,
        },
    },
    "required": ["results"],
    "additionalProperties": False,
}
FETCH_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "title": {"type": "string"},
        "text": {"type": "string"},
        "url": {"type": "string"},
        "metadata": {"type": "object", "additionalProperties": True},
    },
    "required": ["id", "title", "text", "url"],
    "additionalProperties": True,
}


TOOLS = [
    {
        "name": "search",
        "title": "Search Odoo",
        "description": (
            "Search Odoo records visible to the authorized user. Returns results with "
            "stable IDs that can be passed to fetch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text."},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        "outputSchema": SEARCH_OUTPUT_SCHEMA,
        "securitySchemes": OAUTH_SECURITY_SCHEMES,
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "fetch",
        "title": "Fetch Odoo Record",
        "description": "Fetch a single Odoo search result by ID, respecting the authorized user's access rules.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {
                    "type": "string",
                    "description": "Result ID returned by search, formatted as model:id.",
                },
            },
            "required": ["id"],
            "additionalProperties": False,
        },
        "outputSchema": FETCH_OUTPUT_SCHEMA,
        "securitySchemes": OAUTH_SECURITY_SCHEMES,
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "install_info",
        "title": "Install Info",
        "description": (
            "Return high-level information about this Odoo install, including the public "
            "domain used for the MCP call, primary company, Odoo version, and installed modules."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get-ai-context",
        "title": "Get AI Context",
        "description": (
            "Load the Perfect Odoo MCP instance guidance. AI assistants should call this near "
            "the start of a session before using other Odoo tools."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "set-ai-context",
        "title": "Set AI Context",
        "description": "Replace the Perfect Odoo MCP instance guidance returned by get-ai-context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Full replacement guidance text."},
            },
            "required": ["text"],
            "additionalProperties": False,
        },
    },
    {
        "name": "odoo_search",
        "title": "Odoo Search",
        "description": "Search an Odoo model as the authorized Odoo user and return matching record IDs.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "minLength": 1},
                "domain": {
                    "type": "string",
                    "description": 'JSON-encoded Odoo domain array, for example [["is_company", "=", true]].',
                },
                "limit": {"type": "integer", "minimum": 0, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
                "order": {"type": "string"},
            },
            "required": ["model"],
            "additionalProperties": False,
        },
    },
    {
        "name": "odoo_search_read",
        "title": "Odoo Search Read",
        "description": "Search an Odoo model as the authorized Odoo user and return matching records.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "model": {"type": "string", "minLength": 1},
                "domain": {
                    "type": "string",
                    "description": 'JSON-encoded Odoo domain array, for example [["is_company", "=", true]].',
                },
                "fields": {"type": "array", "items": {"type": "string", "minLength": 1}},
                "limit": {"type": "integer", "minimum": 0, "maximum": 500},
                "offset": {"type": "integer", "minimum": 0},
                "order": {"type": "string"},
            },
            "required": ["model"],
            "additionalProperties": False,
        },
    },
    {
        "name": "odoo_python_lookup",
        "title": "Odoo Python Lookup",
        "description": (
            "Read-only lookup for Python code under Odoo addon paths. Use operation='search' for "
            "context building, hypothesis checking, and model/field discovery; use operation='read' "
            "to inspect a specific file. This tool only searches and reads Python source; it never "
            "modifies files, records, settings, or server state."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["search", "read"],
                    "description": "Use 'search' to find code snippets, or 'read' to read a single .py file.",
                },
                "query": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Required when operation is 'search'. Text or regex-like literal to find.",
                },
                "module": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Optional module directory filter for search, for example sale or mnt18.",
                },
                "maxResults": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Maximum search matches to return.",
                },
                "path": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Required when operation is 'read'. Relative addon path, for example sale/models/sale_order.py.",
                },
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    },
]

SQL_TOOL = {
    "name": "odoo_sql",
    "title": "Direct SQL",
    "description": (
        "Execute SQL against the configured PostgreSQL database. This tool is only advertised "
        "when direct database access is enabled in Perfect Odoo MCP settings."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A single SQL statement. Multiple statements are refused.",
            },
            "parameters": {
                "type": "array",
                "description": "Optional positional query parameters.",
                "items": {
                    "type": ["string", "number", "integer", "boolean", "null"],
                    "description": "One positional SQL parameter value.",
                },
            },
            "maxRows": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000,
                "description": "Maximum rows to return for result-producing queries.",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

MODULE_EDITOR_TOOL = {
    "name": "odoo_module_edit",
    "title": "Odoo Module File Editor",
    "description": (
        "Edit explicitly allowlisted Odoo modules inside separate Git repositories. "
        "Use list_modules first: each configured item has a repository path and a module subfolder. "
        "The deploy operation can commit, sync with git pull --ff-only, copy the module into Odoo addons, "
        "upgrade/install it, and auto-revert the deployed folder and Git HEAD on failure."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "list_modules",
                    "list_files",
                    "read_file",
                    "write_file",
                    "delete_file",
                    "git_status",
                    "git_diff",
                    "commit",
                    "sync",
                    "deploy",
                ],
                "description": "File operation to perform.",
            },
            "module": {
                "type": "string",
                "description": "Allowlisted module record name. Required except for list_modules.",
            },
            "path": {
                "type": "string",
                "description": "Path relative to the allowlisted module folder. Required for file operations.",
            },
            "content": {
                "type": "string",
                "description": "Full replacement file content. Required for write_file.",
            },
            "recursive": {
                "type": "boolean",
                "description": "When listing files, include subdirectories recursively.",
            },
            "maxBytes": {
                "type": "integer",
                "minimum": 1,
                "maximum": 1000000,
                "description": "Maximum bytes to read from a file.",
            },
            "message": {
                "type": "string",
                "description": "Git commit message for commit or deploy. Deploy refuses dirty repositories unless this is supplied.",
            },
            "commitMessage": {
                "type": "string",
                "description": "Alias for message.",
            },
            "sync": {
                "type": "boolean",
                "description": "During deploy, run git pull --ff-only after committing/clean checks.",
            },
            "installIfNeeded": {
                "type": "boolean",
                "description": "During built-in deploy, install the module if it is visible but not installed.",
            },
            "autoRevert": {
                "type": "boolean",
                "description": "During deploy, restore the previous deployed folder and Git HEAD if deployment fails. Defaults to true.",
            },
        },
        "required": ["operation"],
        "additionalProperties": False,
    },
}

CUSTOM_TOOL_MANAGER_TOOLS = [
    {
        "name": "custom_tools_list",
        "title": "List Custom Tools",
        "description": (
            "List custom Python MCP tool files, show which reviewed tools are exposed, and return the "
            "required Python template. Use this first when building tools so you know the current drafts, "
            "published tools, load errors, and expected file format."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "custom_tool_read",
        "title": "Read Custom Tool",
        "description": (
            "Read a custom Python MCP tool file. Use this before modifying a draft or published custom "
            "tool so you preserve existing behavior and can review exactly what will execute inside Odoo."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Custom tool filename, for example my_tool.py."},
            },
            "required": ["filename"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "custom_tool_write",
        "title": "Write Custom Tool",
        "description": (
            "Create or replace a custom Python MCP tool file. Tool-building workflow: use "
            "odoo_python_lookup with operation='search' and operation='read' to understand the relevant Odoo models and code, submit a "
            "complete .py file here with EXPOSED = False, run it through call-custom until it behaves "
            "correctly, then ask the user to publish it, make it available, or declare it stable. Only "
            "then set EXPOSED = True and reload custom tools; after the client refreshes actions, the "
            "tool is directly available in tools/list."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Custom tool filename, for example my_tool.py."},
                "code": {"type": "string", "description": "Complete Python source code for the custom tool."},
            },
            "required": ["filename", "code"],
            "additionalProperties": False,
        },
    },
    {
        "name": "custom_tools_reload",
        "title": "Reload Custom Tools",
        "description": (
            "Reload custom tool files from disk and report exposed/rejected tools. Use this after writing "
            "or publishing a tool. Drafts with EXPOSED = False remain callable only through call-custom; "
            "reviewed tools with EXPOSED = True appear in tools/list after the MCP client refreshes actions."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "call-custom",
        "title": "Call Custom Tool",
        "description": (
            "Run a custom tool by filename or tool name without exposing it in tools/list. "
            "Use this for draft validation, hypothesis checks, and tool-building tests after "
            "custom_tool_write. Passing tests here does not publish the tool; publish only after review "
            "by setting EXPOSED = True, calling custom_tools_reload, and refreshing the MCP client's actions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "filename": {"type": "string", "description": "Optional custom tool filename."},
                "name": {"type": "string", "description": "Optional custom tool name from its TOOL definition."},
                "arguments": {"type": "object", "description": "Arguments to pass to the custom tool."},
            },
            "additionalProperties": False,
        },
    },
]
