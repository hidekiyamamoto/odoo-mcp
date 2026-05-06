import base64
import hashlib
import json
import logging
import secrets
import time
from datetime import timedelta
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

from odoo import http
from odoo.fields import Datetime
from odoo.http import Response, request


_logger = logging.getLogger(__name__)

MCP_PATH = "/odoo_mcp_plus/mcp"
MCP_PROTOCOL_VERSION = "2024-11-05"
OAUTH_AUTHORIZE_PATH = "/odoo_mcp_plus/oauth/authorize"
OAUTH_TOKEN_PATH = "/odoo_mcp_plus/oauth/token"
OAUTH_REGISTER_PATH = "/odoo_mcp_plus/oauth/register"
PROTECTED_RESOURCE_METADATA_PATH = "/.well-known/oauth-protected-resource"
AUTHORIZATION_SERVER_METADATA_PATH = "/.well-known/oauth-authorization-server"
OAUTH_SCOPE = "odoo:read"
AUTH_CODE_TTL_SECONDS = 5 * 60
ACCESS_TOKEN_TTL_SECONDS = 60 * 60 * 8
AUTH_CODE_PARAM_PREFIX = "odoo_mcp_plus.oauth_code."


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


def _base_url():
    return request.env["ir.config_parameter"].sudo().get_param("web.base.url", "").rstrip("/")


def _absolute_url(path):
    base_url = _base_url()
    return f"{base_url}{path}" if base_url else path


def _current_full_path():
    return request.httprequest.full_path.rstrip("?")


def _is_internal_user():
    return bool(request.env.user and request.env.user.has_group("base.group_user"))


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
    request.env["odoo.mcp.oauth.token"].sudo().create(
        {
            "name": f"Odoo MCP Plus - {code_data['client_id']}",
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

    oauth_token = request.env["odoo.mcp.oauth.token"].sudo().search(
        [
            ("token_hash", "=", _hash_token(token)),
            ("audience", "=", _absolute_url(MCP_PATH)),
            ("scope", "=", OAUTH_SCOPE),
            ("expires_at", ">", Datetime.now()),
            ("revoked_at", "=", False),
        ],
        limit=1,
    )
    if not oauth_token or not oauth_token.user_id.active:
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
    <title>Authorize Odoo MCP Plus</title>
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
      <h1>Authorize Odoo MCP Plus</h1>
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
      <p>Continue with an internal Odoo user to authorize Odoo MCP Plus.</p>
      <a class="button" href="{_escape_html(login_url)}">Login and continue</a>
    </main>
  </body>
</html>"""


def _redirect_with_authorization_error(redirect_uri, state, error):
    parts = urlparse(redirect_uri)
    query = parse_qs(parts.query)
    query["error"] = [error]
    if state:
        query["state"] = [state]
    return request.redirect(urlunparse(parts._replace(query=urlencode(query, doseq=True))))


class OdooMcpPlusController(http.Controller):
    @http.route(MCP_PATH, type="http", auth="public", csrf=False, methods=["OPTIONS"])
    def options(self, **kwargs):
        return _json_response({})

    @http.route(MCP_PATH, type="http", auth="public", csrf=False, methods=["GET"])
    def info(self, **kwargs):
        return _json_response(
            {
                "name": "Odoo MCP Plus",
                "status": "ok",
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "url": _absolute_url(MCP_PATH),
            }
        )

    @http.route(MCP_PATH, type="http", auth="public", csrf=False, methods=["POST"])
    def mcp(self, **kwargs):
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

        user_env = request.env.with_user(authorized_user)
        request_id = message.get("id")
        method = message.get("method")

        if method == "initialize":
            return _json_rpc_result(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {},
                    },
                    "serverInfo": {
                        "name": "odoo-mcp-plus",
                        "version": "17.0.1.0.0",
                    },
                },
            )

        if method == "notifications/initialized":
            return _json_rpc_result(request_id, {})

        if method == "tools/list":
            return _json_rpc_result(request_id, {"tools": []})

        _logger.debug("Unsupported MCP method for user %s: %s", user_env.user.id, method)
        return _json_rpc_error(request_id, -32601, "Method not found")

    @http.route(
        PROTECTED_RESOURCE_METADATA_PATH,
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
                "resource_name": "Odoo MCP Plus",
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
        if request.env.user._is_public():
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

        parts = urlparse(redirect_uri)
        query = parse_qs(parts.query)
        query["code"] = [code]
        if state:
            query["state"] = [state]
        return request.redirect(urlunparse(parts._replace(query=urlencode(query, doseq=True))))

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
            or code_data["redirect_uri"] != redirect_uri
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
