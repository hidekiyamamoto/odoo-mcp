import base64
import hashlib
import json
import logging
import os
import secrets
import time
from datetime import timedelta
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

from odoo import api, http, release
from odoo.fields import Datetime
from odoo.http import Response, request

from ..custom_tools import (
    custom_tool_read,
    custom_tool_result,
    custom_tool_write,
    custom_tools_cache,
    custom_tools_enabled,
    custom_tools_list,
    custom_tools_reload,
    test_custom_tool,
)
from ..const import (
    AI_CONTEXT_PARAM,
    AUTH_CODE_PARAM_PREFIX,
    MCP_PATH,
    MODULE_NAME,
    OAUTH_AUTHORIZE_PATH,
    OAUTH_REGISTER_PATH,
    OAUTH_TOKEN_PATH,
    SQL_DATABASE_PARAM,
    SQL_ENABLED_PARAM,
    SQL_HOST_PARAM,
    SQL_PASSWORD_PARAM,
    SQL_PORT_PARAM,
    SQL_READONLY_PARAM,
    SQL_USER_PARAM,
)
from ..mcp_defs import (
    CUSTOM_TOOLS_DIR,
    CUSTOM_TOOL_MANAGER_TOOLS,
    EMPTY_AI_CONTEXT_BOOTSTRAP,
    SEARCH_MODEL_NAMES,
    SEARCH_MODEL_PREFIXES,
    SQL_FORBIDDEN_READONLY_PATTERN,
    SQL_READONLY_START_PATTERN,
    SQL_TOOL,
    TOOLS,
)


_logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2024-11-05"
PROTECTED_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
PROTECTED_RESOURCE_METADATA_SCOPED_PATH = f"{PROTECTED_RESOURCE_METADATA_PATH}{MCP_PATH}"
AUTHORIZATION_SERVER_METADATA_PATH = "/.well-known/oauth-authorization-server"
OAUTH_SCOPE = "odoo:read"
AUTH_CODE_TTL_SECONDS = 5 * 60
ACCESS_TOKEN_TTL_SECONDS = 60 * 60 * 8


def _json_response(payload, status=200):
    body = json.dumps(payload, separators=(",", ":"))
    return Response(
        body,
        status=status,
        headers=[
            ("Content-Type", "application/json"),
            ("Access-Control-Allow-Origin", "*"),
            ("Access-Control-Allow-Headers", "Content-Type, Authorization"),
            ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ],
    )


def _html_response(body, status=200):
    return Response(
        body,
        status=status,
        headers=[
            ("Content-Type", "text/html; charset=utf-8"),
        ],
    )


def _redirect_response(location, status=303):
    return Response(
        "",
        status=status,
        headers=[
            ("Location", location),
        ],
    )


def _json_rpc_result(request_id, result):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def _json_rpc_error(request_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _tool_text(text):
    return {"content": [{"type": "text", "text": text}]}


def _tool_json(payload):
    return _tool_text(json.dumps(payload, indent=2, default=str))


def _tool_error(error):
    return {
        "isError": True,
        "content": [{"type": "text", "text": str(error)}],
    }


def _param_bool(name, default=False):
    value = request.env["ir.config_parameter"].sudo().get_param(name)
    if value in (None, ""):
        return default
    return str(value).lower() in ("1", "true", "yes", "on")


def _installed_module_version(env):
    module = env["ir.module.module"].sudo().search([("name", "=", MODULE_NAME)], limit=1)
    return module.installed_version or module.latest_version or "1.0.0"


def _sql_enabled():
    return _param_bool(SQL_ENABLED_PARAM)


def _sql_readonly():
    return _param_bool(SQL_READONLY_PARAM, default=True)


def _available_tools():
    tools = list(TOOLS)
    if _sql_enabled():
        sql_tool = dict(SQL_TOOL)
        if _sql_readonly():
            sql_tool["annotations"] = {"readOnlyHint": True}
        tools.append(sql_tool)
    if custom_tools_enabled():
        tools.extend(CUSTOM_TOOL_MANAGER_TOOLS)
        tools.extend(custom_tools_cache()["exposed_tools"])
    return tools


def _request_scheme_host():
    try:
        httprequest = request.httprequest
    except RuntimeError:
        return None, None

    forwarded_proto = (httprequest.headers.get("X-Forwarded-Proto") or "").split(",", 1)[0].strip()
    forwarded_host = (httprequest.headers.get("X-Forwarded-Host") or "").split(",", 1)[0].strip()
    forwarded_ssl = (httprequest.headers.get("X-Forwarded-Ssl") or "").lower().strip()

    scheme = forwarded_proto or httprequest.scheme
    if forwarded_ssl == "on":
        scheme = "https"

    return scheme, forwarded_host or httprequest.host


def _is_local_http_host(host):
    hostname = (host or "").split(":", 1)[0].lower()
    return hostname in {"localhost", "127.0.0.1", "::1"} or hostname.endswith(".local")


def _public_base_url(base_url):
    base_url = (base_url or "").rstrip("/")
    scheme, host = _request_scheme_host()

    if not base_url:
        return f"{scheme}://{host}" if scheme and host else ""

    parsed = urlparse(base_url)
    if parsed.scheme == "http" and (scheme == "https" or not _is_local_http_host(parsed.netloc)):
        parsed = parsed._replace(scheme="https")
    if host and parsed.netloc != host:
        parsed = parsed._replace(netloc=host)
    return urlunparse(parsed).rstrip("/")


def _base_url():
    stored_base_url = request.env["ir.config_parameter"].sudo().get_param("web.base.url", "")
    return _public_base_url(stored_base_url)


def _absolute_url(path):
    base_url = _base_url()
    return f"{base_url}{path}" if base_url else path


def _current_full_path():
    return request.httprequest.full_path.rstrip("?")


def _is_internal_user():
    return bool(request.env.user and request.env.user.has_group("base.group_user"))


def _is_public_user():
    user = request.env.user
    if not user:
        return True

    is_public = getattr(user, "_is_public", None)
    if callable(is_public):
        return bool(is_public())

    return not bool(getattr(request, "session", None) and request.session.uid)


def _escape_html(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _base64_url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_challenge(code_verifier):
    return _base64_url(hashlib.sha256(code_verifier.encode()).digest())


def _store_auth_code(code, values):
    request.env["ir.config_parameter"].sudo().set_param(
        f"{AUTH_CODE_PARAM_PREFIX}{code}",
        json.dumps(values, separators=(",", ":")),
    )


def _consume_auth_code(code):
    params = request.env["ir.config_parameter"].sudo()
    key = f"{AUTH_CODE_PARAM_PREFIX}{code}"
    raw_value = params.get_param(key)
    if not raw_value:
        return None

    params.set_param(key, "")
    try:
        values = json.loads(raw_value)
    except json.JSONDecodeError:
        return None

    if values.get("expires_at", 0) < time.time():
        return None
    return values


def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def _create_access_token(code_data):
    token = secrets.token_urlsafe(48)
    expires_at = Datetime.now() + timedelta(seconds=ACCESS_TOKEN_TTL_SECONDS)
    request.env["perfect.odoo.mcp.oauth.token"].sudo().create(
        {
            "name": f"Perfect Odoo MCP - {code_data['client_id']}",
            "token_hash": _hash_token(token),
            "user_id": code_data["uid"],
            "client_id": code_data["client_id"],
            "scope": code_data["scope"],
            "audience": _absolute_url(MCP_PATH),
            "expires_at": expires_at,
        }
    )
    return token


def _bearer_token():
    authorization = request.httprequest.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def _authorized_mcp_user():
    token = _bearer_token()
    if not token:
        return request.env["res.users"].sudo().browse()

    oauth_token = request.env["perfect.odoo.mcp.oauth.token"].sudo().search(
        [
            ("token_hash", "=", _hash_token(token)),
            ("audience", "=", _absolute_url(MCP_PATH)),
            ("expires_at", ">", Datetime.now()),
            ("revoked_at", "=", False),
        ],
        limit=1,
    )
    if (
        not oauth_token
        or not oauth_token.user_id.active
        or OAUTH_SCOPE not in (oauth_token.scope or "").split()
    ):
        return request.env["res.users"].sudo().browse()

    oauth_token.write({"last_used_at": Datetime.now()})
    return oauth_token.user_id


def _unauthorized_response():
    return Response(
        json.dumps({"error": "Unauthorized"}),
        status=401,
        headers=[
            ("Content-Type", "application/json"),
            (
                "WWW-Authenticate",
                f'Bearer resource_metadata="{_absolute_url(PROTECTED_RESOURCE_METADATA_PATH)}", scope="{OAUTH_SCOPE}"',
            ),
        ],
    )


def _authorize_page(params, error=None):
    hidden_inputs = []
    for name in (
        "response_type",
        "client_id",
        "redirect_uri",
        "state",
        "scope",
        "resource",
        "code_challenge",
        "code_challenge_method",
    ):
        hidden_inputs.append(
            f'<input type="hidden" name="{name}" value="{_escape_html(params.get(name, ""))}">'
        )

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Authorize Perfect Odoo MCP</title>
    <style>
      body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1f2937; }}
      main {{ max-width: 38rem; margin: 0 auto; }}
      button, .button {{ display: inline-block; margin-top: 1rem; padding: .75rem 1rem; font-weight: 650; color: #fff; background: #714b67; border: 0; border-radius: .25rem; text-decoration: none; }}
      .error {{ color: #b91c1c; font-weight: 650; }}
      code {{ background: #f3f4f6; padding: .1rem .25rem; border-radius: .25rem; }}
    </style>
  </head>
  <body>
    <main>
      <h1>Authorize Perfect Odoo MCP</h1>
      <p>This grants MCP access to <code>{_escape_html(_absolute_url(MCP_PATH))}</code>.</p>
      <p>Signed in as <strong>{_escape_html(request.env.user.display_name)}</strong>.</p>
      {f'<p class="error">{_escape_html(error)}</p>' if error else ''}
      <form method="post" action="{OAUTH_AUTHORIZE_PATH}">
        {''.join(hidden_inputs)}
        <button type="submit">Authorize</button>
      </form>
    </main>
  </body>
</html>"""


def _internal_login_url(logout_first=False):
    login_url = f"/web/login?{urlencode({'redirect': _current_full_path()})}"
    if logout_first:
        return f"/web/session/logout?redirect={quote(login_url, safe='')}"
    return login_url


def _internal_required_page(logout_first=False):
    login_url = _internal_login_url(logout_first=logout_first)
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Odoo Login Required</title>
    <style>
      body {{ font-family: system-ui, sans-serif; margin: 2rem; color: #1f2937; }}
      main {{ max-width: 36rem; margin: 0 auto; }}
      .button {{ display: inline-block; margin-top: 1rem; padding: .75rem 1rem; font-weight: 650; color: #fff; background: #714b67; border-radius: .25rem; text-decoration: none; }}
    </style>
  </head>
  <body>
    <main>
      <h1>Odoo login required</h1>
      <p>Continue with an internal Odoo user to authorize Perfect Odoo MCP.</p>
      <a class="button" href="{_escape_html(login_url)}">Login and continue</a>
    </main>
  </body>
</html>"""


def _redirect_with_authorization_error(redirect_uri, state, error):
    parts = urlparse(_redirect_location_for_client(redirect_uri))
    query = parse_qs(parts.query)
    query["error"] = [error]
    if state:
        query["state"] = [state]
    return _redirect_response(urlunparse(parts._replace(query=urlencode(query, doseq=True))))


def _redirect_location_for_client(redirect_uri):
    parts = urlparse(redirect_uri)
    if parts.scheme and parts.netloc:
        return redirect_uri

    # ChatGPT custom connector setup can hand back a browser callback path
    # without the chatgpt.com origin. Do not let Odoo resolve that path locally.
    if parts.path.startswith("/connector/oauth"):
        return urlunparse(parts._replace(scheme="https", netloc="chatgpt.com"))

    return redirect_uri


def _redirect_uri_matches(stored_redirect_uri, provided_redirect_uri):
    if stored_redirect_uri == provided_redirect_uri:
        return True
    return _redirect_location_for_client(stored_redirect_uri) == provided_redirect_uri


def _parse_domain(value):
    if value in (None, "", False):
        return []
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, list):
        raise ValueError("Odoo domain must be an array or JSON-encoded array.")
    return parsed


def _addons_roots():
    roots = []
    try:
        import odoo.addons
    except Exception:
        return roots

    for root in odoo.addons.__path__:
        if os.path.isdir(root) and root not in roots:
            roots.append(root)
    return roots


def _resolve_addons_file(relative_path):
    if not relative_path.endswith(".py"):
        raise ValueError("Only .py files can be read.")
    if os.path.isabs(relative_path) or "\0" in relative_path:
        raise ValueError("Path must be relative to an Odoo addons path.")

    normalized = os.path.normpath(relative_path)
    if normalized == "." or normalized.startswith("..") or f"{os.sep}.." in normalized:
        raise ValueError("Path escapes the Odoo addons path.")

    for root in _addons_roots():
        candidate = os.path.abspath(os.path.join(root, normalized))
        root_abs = os.path.abspath(root)
        if candidate == root_abs or not candidate.startswith(root_abs + os.sep):
            continue
        if os.path.isfile(candidate):
            return root, candidate

    raise FileNotFoundError(f"Could not find {relative_path} under Odoo addons paths.")


def _iter_python_files(root):
    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            dirname
            for dirname in dirnames
            if dirname != "__pycache__" and not dirname.startswith(".")
        ]
        for filename in filenames:
            if filename.endswith(".py"):
                yield os.path.join(current_root, filename)


def _search_python_code(query, module=None, max_results=50):
    query_lower = query.lower()
    matches = []
    roots = _addons_roots()

    for root in roots:
        search_root = os.path.join(root, module) if module else root
        if not os.path.isdir(search_root):
            continue

        for file_path in _iter_python_files(search_root):
            try:
                with open(file_path, encoding="utf-8") as handle:
                    lines = handle.read().splitlines()
            except UnicodeDecodeError:
                with open(file_path, encoding="latin-1") as handle:
                    lines = handle.read().splitlines()

            for index, line in enumerate(lines, start=1):
                if query_lower not in line.lower():
                    continue
                matches.append(
                    {
                        "path": os.path.relpath(file_path, root),
                        "addonsRoot": root,
                        "line": index,
                        "text": line.strip(),
                    }
                )
                if len(matches) >= max_results:
                    return matches
    return matches


def _record_url(model_name, record_id):
    return f"{_base_url()}/web#id={record_id}&model={quote(model_name)}&view_type=form"


def _search_odoo_records(query, user_env, limit=10):
    results = []
    seen = set()
    models = request.env["ir.model"].sudo().search(
        [
            ("transient", "=", False),
            ("model", "not ilike", "ir.%"),
            ("model", "not ilike", "base.%"),
        ],
        order="model",
    )

    for model_info in models:
        if len(results) >= limit:
            break

        model_name = model_info.model
        if model_name not in SEARCH_MODEL_NAMES and not model_name.startswith(SEARCH_MODEL_PREFIXES):
            continue
        if model_name in seen:
            continue
        seen.add(model_name)

        try:
            matches = user_env[model_name].name_search(
                name=query,
                args=[],
                operator="ilike",
                limit=max(1, limit - len(results)),
            )
        except Exception:
            continue

        for record_id, display_name in matches:
            results.append(
                {
                    "id": f"{model_name}:{record_id}",
                    "title": f"{display_name} ({model_info.name})",
                    "url": _record_url(model_name, record_id),
                }
            )
            if len(results) >= limit:
                break

    return results


def _fetch_odoo_record(result_id, user_env):
    if not isinstance(result_id, str) or ":" not in result_id:
        raise ValueError("id must use the model:id format returned by search.")

    model_name, raw_id = result_id.rsplit(":", 1)
    if not model_name or not raw_id.isdigit():
        raise ValueError("id must use the model:id format returned by search.")

    record_id = int(raw_id)
    record = user_env[model_name].browse(record_id).exists()
    if not record:
        raise ValueError("Record not found or not visible to the authorized user.")

    display_name = record.display_name
    fields = record.fields_get()
    readable_fields = [
        name
        for name, definition in fields.items()
        if not definition.get("deprecated")
        and definition.get("type") in ("char", "text", "html", "selection", "boolean", "integer", "float", "monetary", "date", "datetime", "many2one")
    ][:40]

    data = record.read(readable_fields)[0] if readable_fields else {"id": record_id}
    return {
        "id": result_id,
        "title": display_name,
        "url": _record_url(model_name, record_id),
        "text": json.dumps(data, indent=2, default=str),
        "metadata": {
            "model": model_name,
            "record_id": record_id,
        },
    }


def _module_source(module):
    author = (module.author or "").strip()
    website = (module.website or "").strip()
    author_lower = author.lower()
    website_lower = website.lower()

    if module.name == "perfect_odoo_mcp":
        return "perfect_odoo_mcp"
    if "odoo" in author_lower or "odoo.com" in website_lower:
        return "odoo"
    if author or website:
        return "third_party_or_custom"
    return "unknown"


def _installed_modules_info(user_env):
    modules = request.env["ir.module.module"].sudo().search(
        [("state", "=", "installed")],
        order="name",
    )
    module_items = []
    counts = {}

    for module in modules:
        source = _module_source(module)
        counts[source] = counts.get(source, 0) + 1
        module_items.append(
            {
                "name": module.name,
                "displayName": module.shortdesc,
                "version": module.installed_version or module.latest_version,
                "author": module.author or None,
                "website": module.website or None,
                "category": module.category_id.display_name if module.category_id else None,
                "application": bool(module.application),
                "source": source,
                "isOdooIncludedLikely": source == "odoo",
            }
        )

    httprequest = request.httprequest
    headers = httprequest.headers
    company = user_env.company
    return {
        "odoo": {
            "version": release.version,
            "database": request.env.cr.dbname,
        },
        "mcp": {
            "endpoint": _absolute_url(MCP_PATH),
            "baseUrl": _base_url(),
            "path": httprequest.path,
            "method": httprequest.method,
            "directDatabaseAccess": {
                "enabled": _sql_enabled(),
                "readonly": _sql_readonly(),
                "toolName": "odoo_sql" if _sql_enabled() else None,
            },
            "customTools": {
                "enabled": custom_tools_enabled(),
                "directory": CUSTOM_TOOLS_DIR if custom_tools_enabled() else None,
            },
        },
        "request": {
            "urlRoot": httprequest.url_root.rstrip("/") if httprequest.url_root else None,
            "host": headers.get("Host"),
            "origin": headers.get("Origin"),
            "referer": headers.get("Referer"),
            "xForwardedProto": headers.get("X-Forwarded-Proto"),
            "xForwardedHost": headers.get("X-Forwarded-Host"),
            "xForwardedFor": headers.get("X-Forwarded-For"),
        },
        "company": {
            "id": company.id,
            "name": company.name,
        },
        "user": {
            "id": user_env.user.id,
            "login": user_env.user.login,
            "name": user_env.user.name,
        },
        "modules": {
            "count": len(module_items),
            "countsBySource": counts,
            "classificationNote": (
                "source is inferred from ir.module.module author/website metadata. "
                "Modules authored by Odoo or linking to odoo.com are marked as odoo; "
                "all others are third_party_or_custom unless unknown."
            ),
            "items": module_items,
        },
    }

def _sql_config():
    params = request.env["ir.config_parameter"].sudo()
    database = params.get_param(SQL_DATABASE_PARAM) or request.env.cr.dbname
    return {
        "host": params.get_param(SQL_HOST_PARAM) or "127.0.0.1",
        "port": int(params.get_param(SQL_PORT_PARAM) or 5432),
        "dbname": database,
        "user": params.get_param(SQL_USER_PARAM) or "",
        "password": params.get_param(SQL_PASSWORD_PARAM) or "",
    }


def _single_sql_statement(query):
    stripped = query.strip()
    if not stripped:
        raise ValueError("SQL query is required.")

    without_trailing = stripped[:-1].rstrip() if stripped.endswith(";") else stripped
    if ";" in without_trailing:
        raise ValueError("Refusing SQL with multiple statements or embedded semicolons.")
    return without_trailing


def _validate_sql_allowed(query):
    readonly = _sql_readonly()
    normalized = _single_sql_statement(query)

    if not readonly:
        return normalized

    if not SQL_READONLY_START_PATTERN.search(normalized):
        raise ValueError(
            "Direct SQL is configured as readonly. Only SELECT, WITH, SHOW, and EXPLAIN statements are allowed."
        )

    match = SQL_FORBIDDEN_READONLY_PATTERN.search(normalized)
    if match:
        raise ValueError(
            f"Direct SQL readonly protection refused this query because it contains `{match.group(1)}`."
        )

    return normalized


def _execute_direct_sql(arguments):
    if not _sql_enabled():
        raise ValueError("Direct database access is not enabled in Perfect Odoo MCP settings.")

    query = arguments.get("query")
    if not isinstance(query, str):
        raise ValueError("query must be a string.")
    query = _validate_sql_allowed(query)

    parameters = arguments.get("parameters") or []
    if not isinstance(parameters, list):
        raise ValueError("parameters must be an array.")

    max_rows = int(arguments.get("maxRows") or 100)
    max_rows = max(1, min(max_rows, 1000))
    readonly = _sql_readonly()
    config = _sql_config()
    if not config["user"]:
        raise ValueError("Direct database access is enabled but SQL user is not configured.")

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as error:
        raise ValueError("psycopg2 is not available in this Odoo Python environment.") from error

    connection = psycopg2.connect(
        host=config["host"],
        port=config["port"],
        dbname=config["dbname"],
        user=config["user"],
        password=config["password"],
        connect_timeout=5,
    )
    try:
        connection.set_session(readonly=readonly, autocommit=readonly)
        with connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
            cursor.execute(query, parameters)
            if cursor.description:
                rows = cursor.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                rows = rows[:max_rows]
                return _tool_json(
                    {
                        "readonly": readonly,
                        "query": query,
                        "rowCount": len(rows),
                        "truncated": truncated,
                        "rows": rows,
                    }
                )

            if readonly:
                raise ValueError("Readonly SQL query did not produce a result set.")

            connection.commit()
            return _tool_json(
                {
                    "readonly": readonly,
                    "query": query,
                    "status": cursor.statusmessage,
                    "rowCount": cursor.rowcount,
                }
            )
    except Exception:
        if not readonly:
            connection.rollback()
        raise
    finally:
        connection.close()


def _call_tool(name, arguments, user_env):
    arguments = arguments or {}
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be an object.")

    if name == "search":
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query is required.")
        results = _search_odoo_records(query.strip(), user_env)
        return _tool_json({"results": results})

    if name == "fetch":
        return _tool_json(_fetch_odoo_record(arguments.get("id"), user_env))

    if name == "install_info":
        return _tool_json(_installed_modules_info(user_env))

    if name == "odoo_sql":
        return _execute_direct_sql(arguments)

    if name == "custom_tools_list":
        return custom_tools_list()

    if name == "custom_tool_read":
        return custom_tool_read(arguments)

    if name == "custom_tool_write":
        return custom_tool_write(arguments)

    if name == "custom_tools_reload":
        return custom_tools_reload()

    if name == "call-custom":
        return test_custom_tool(arguments, user_env)

    custom_item = custom_tools_cache()["tools"].get(name) if custom_tools_enabled() else None
    if custom_item and custom_item["exposed"]:
        return custom_tool_result(custom_item["call"](arguments, user_env, request))

    if name == "get-ai-context":
        text = request.env["ir.config_parameter"].sudo().get_param(AI_CONTEXT_PARAM, "")
        return _tool_text(text or EMPTY_AI_CONTEXT_BOOTSTRAP)

    if name == "set-ai-context":
        text = arguments.get("text")
        if not isinstance(text, str):
            raise ValueError("text must be a string.")
        request.env["ir.config_parameter"].sudo().set_param(AI_CONTEXT_PARAM, text)
        return _tool_text("Updated Perfect Odoo MCP AI context.")

    if name == "odoo_search":
        model = arguments.get("model")
        if not isinstance(model, str) or not model:
            raise ValueError("model is required.")
        domain = _parse_domain(arguments.get("domain"))
        limit = arguments.get("limit")
        offset = arguments.get("offset", 0)
        order = arguments.get("order")
        ids = user_env[model].search(
            domain,
            offset=offset or 0,
            limit=limit if limit is not None else None,
            order=order if order else None,
        ).ids
        return _tool_json({"model": model, "domain": domain, "ids": ids})

    if name == "odoo_search_read":
        model = arguments.get("model")
        if not isinstance(model, str) or not model:
            raise ValueError("model is required.")
        domain = _parse_domain(arguments.get("domain"))
        fields = arguments.get("fields")
        limit = arguments.get("limit")
        offset = arguments.get("offset", 0)
        order = arguments.get("order")
        records = user_env[model].search_read(
            domain,
            fields=fields if fields else None,
            offset=offset or 0,
            limit=limit if limit is not None else None,
            order=order if order else None,
        )
        return _tool_json({"model": model, "domain": domain, "records": records})

    if name in ("odoo_python_lookup", "odoo_python_code_lookup"):
        operation = arguments.get("operation")
        if operation == "search":
            query = arguments.get("query")
            if not isinstance(query, str) or not query:
                raise ValueError("query is required when operation is 'search'.")
            module = arguments.get("module")
            max_results = int(arguments.get("maxResults") or 50)
            max_results = max(1, min(max_results, 200))
            matches = _search_python_code(query, module=module, max_results=max_results)
            return _tool_json(
                {
                    "operation": operation,
                    "addonsRoots": _addons_roots(),
                    "query": query,
                    "module": module or None,
                    "count": len(matches),
                    "matches": matches,
                }
            )

        if operation == "read":
            relative_path = arguments.get("path")
            if not isinstance(relative_path, str) or not relative_path:
                raise ValueError("path is required when operation is 'read'.")
            root, file_path = _resolve_addons_file(relative_path)
            with open(file_path, encoding="utf-8") as handle:
                text = handle.read()
            return _tool_text(text)

        raise ValueError("operation must be 'search' or 'read'.")

    if name == "odoo_python_code_search":
        query = arguments.get("query")
        if not isinstance(query, str) or not query:
            raise ValueError("query is required.")
        module = arguments.get("module")
        max_results = int(arguments.get("maxResults") or 50)
        max_results = max(1, min(max_results, 200))
        matches = _search_python_code(query, module=module, max_results=max_results)
        return _tool_json(
            {
                "addonsRoots": _addons_roots(),
                "query": query,
                "module": module or None,
                "count": len(matches),
                "matches": matches,
            }
        )

    if name == "odoo_python_code_read":
        relative_path = arguments.get("path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("path is required.")
        root, file_path = _resolve_addons_file(relative_path)
        with open(file_path, encoding="utf-8") as handle:
            text = handle.read()
        return _tool_text(text)

    raise ValueError(f"Unknown tool: {name}")


class OdooMcpPlusController(http.Controller):
    @http.route(
        ["/connector/oauth", "/connector/oauth/<path:callback_path>"],
        type="http",
        auth="public",
        csrf=False,
        methods=["GET"],
    )
    def openai_connector_oauth_callback(self, callback_path=None, **kwargs):
        target = "https://chatgpt.com" + request.httprequest.full_path.rstrip("?")
        return _redirect_response(target)

    @http.route(MCP_PATH, type="http", auth="public", csrf=False, methods=["GET", "POST", "OPTIONS"])
    def mcp_endpoint(self, **kwargs):
        if request.httprequest.method == "OPTIONS":
            return _json_response({})

        if request.httprequest.method == "GET":
            authorized_user = _authorized_mcp_user()
            if not authorized_user:
                return _unauthorized_response()

            return _json_response(
                {
                    "name": "Perfect Odoo MCP",
                    "status": "ok",
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "url": _absolute_url(MCP_PATH),
                }
            )

        authorized_user = _authorized_mcp_user()
        if not authorized_user:
            return _unauthorized_response()

        try:
            payload = json.loads(request.httprequest.get_data(as_text=True) or "{}")
        except json.JSONDecodeError:
            return _json_response(_json_rpc_error(None, -32700, "Parse error"), status=400)

        if isinstance(payload, list):
            return _json_response(
                [self._dispatch_message(message, authorized_user) for message in payload]
            )

        return _json_response(self._dispatch_message(payload, authorized_user))

    def _dispatch_message(self, message, authorized_user):
        if not isinstance(message, dict):
            return _json_rpc_error(None, -32600, "Invalid Request")

        user_env = api.Environment(request.env.cr, authorized_user.id, dict(request.env.context))
        request_id = message.get("id")
        method = message.get("method")

        if method == "initialize":
            return _json_rpc_result(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": "perfect-odoo-mcp",
                        "version": _installed_module_version(user_env),
                    },
                },
            )

        if method == "notifications/initialized":
            return _json_rpc_result(request_id, {})

        if method == "tools/list":
            return _json_rpc_result(request_id, {"tools": _available_tools()})

        if method == "tools/call":
            params = message.get("params") or {}
            if not isinstance(params, dict):
                return _json_rpc_error(request_id, -32602, "Invalid params")

            try:
                result = _call_tool(
                    params.get("name"),
                    params.get("arguments") or {},
                    user_env,
                )
            except Exception as error:
                _logger.exception("MCP tool call failed")
                result = _tool_error(error)
            return _json_rpc_result(request_id, result)

        _logger.debug("Unsupported MCP method for user %s: %s", user_env.user.id, method)
        return _json_rpc_error(request_id, -32601, "Method not found")

    @http.route(
        [PROTECTED_RESOURCE_METADATA_PATH, PROTECTED_RESOURCE_METADATA_SCOPED_PATH],
        type="http",
        auth="public",
        csrf=False,
        methods=["GET"],
    )
    def protected_resource_metadata(self, **kwargs):
        return _json_response(
            {
                "resource": _absolute_url(MCP_PATH),
                "authorization_servers": [_base_url()],
                "scopes_supported": [OAUTH_SCOPE],
                "bearer_methods_supported": ["header"],
                "resource_name": "Perfect Odoo MCP",
            }
        )

    @http.route(
        AUTHORIZATION_SERVER_METADATA_PATH,
        type="http",
        auth="public",
        csrf=False,
        methods=["GET"],
    )
    def authorization_server_metadata(self, **kwargs):
        return _json_response(
            {
                "issuer": _base_url(),
                "authorization_endpoint": _absolute_url(OAUTH_AUTHORIZE_PATH),
                "token_endpoint": _absolute_url(OAUTH_TOKEN_PATH),
                "registration_endpoint": _absolute_url(OAUTH_REGISTER_PATH),
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code"],
                "token_endpoint_auth_methods_supported": ["none"],
                "code_challenge_methods_supported": ["S256"],
                "scopes_supported": [OAUTH_SCOPE],
            }
        )

    @http.route(
        OAUTH_AUTHORIZE_PATH,
        type="http",
        auth="public",
        csrf=False,
        methods=["GET", "POST"],
    )
    def oauth_authorize(self, **kwargs):
        if _is_public_user():
            return _html_response(_internal_required_page(), status=401)

        if not _is_internal_user():
            return _html_response(_internal_required_page(logout_first=True), status=403)

        if request.httprequest.method == "GET":
            return _html_response(_authorize_page(kwargs))

        redirect_uri = kwargs.get("redirect_uri", "")
        state = kwargs.get("state")
        client_id = kwargs.get("client_id", "")
        response_type = kwargs.get("response_type", "")
        code_challenge = kwargs.get("code_challenge", "")
        code_challenge_method = kwargs.get("code_challenge_method", "")
        scope = kwargs.get("scope") or OAUTH_SCOPE
        resource = kwargs.get("resource") or _absolute_url(MCP_PATH)

        if (
            response_type != "code"
            or not client_id
            or not redirect_uri
            or not code_challenge
            or code_challenge_method != "S256"
            or resource != _absolute_url(MCP_PATH)
        ):
            if redirect_uri:
                return _redirect_with_authorization_error(redirect_uri, state, "invalid_request")
            return _html_response(_authorize_page(kwargs, "Invalid authorization request."), status=400)

        code = secrets.token_urlsafe(32)
        _store_auth_code(code, {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_challenge": code_challenge,
            "scope": scope,
            "resource": resource,
            "uid": request.env.user.id,
            "expires_at": time.time() + AUTH_CODE_TTL_SECONDS,
        })

        parts = urlparse(_redirect_location_for_client(redirect_uri))
        query = parse_qs(parts.query)
        query["code"] = [code]
        if state:
            query["state"] = [state]
        return _redirect_response(urlunparse(parts._replace(query=urlencode(query, doseq=True))))

    @http.route(OAUTH_TOKEN_PATH, type="http", auth="public", csrf=False, methods=["POST"])
    def oauth_token(self, **kwargs):
        grant_type = kwargs.get("grant_type")
        code = kwargs.get("code", "")
        redirect_uri = kwargs.get("redirect_uri", "")
        client_id = kwargs.get("client_id", "")
        code_verifier = kwargs.get("code_verifier", "")
        code_data = _consume_auth_code(code)

        if (
            grant_type != "authorization_code"
            or not code_data
            or not _redirect_uri_matches(code_data["redirect_uri"], redirect_uri)
            or code_data["client_id"] != client_id
            or code_data["code_challenge"] != _pkce_challenge(code_verifier)
        ):
            return _json_response({"error": "invalid_grant"}, status=400)

        token = _create_access_token(code_data)
        return _json_response(
            {
                "access_token": token,
                "token_type": "Bearer",
                "expires_in": ACCESS_TOKEN_TTL_SECONDS,
                "scope": code_data["scope"],
            }
        )

    @http.route(OAUTH_REGISTER_PATH, type="http", auth="public", csrf=False, methods=["POST"])
    def oauth_register(self, **kwargs):
        try:
            payload = json.loads(request.httprequest.get_data(as_text=True) or "{}")
        except json.JSONDecodeError:
            return _json_response({"error": "invalid_client_metadata"}, status=400)

        redirect_uris = payload.get("redirect_uris")
        if not isinstance(redirect_uris, list) or not redirect_uris:
            return _json_response({"error": "invalid_client_metadata"}, status=400)

        client_id = secrets.token_urlsafe(24)
        return _json_response(
            {
                "client_id": client_id,
                "client_id_issued_at": int(time.time()),
                "redirect_uris": redirect_uris,
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
                "scope": OAUTH_SCOPE,
            },
            status=201,
        )
