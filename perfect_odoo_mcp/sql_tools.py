import json

from odoo.http import request

from .const import (
    SQL_DATABASE_PARAM,
    SQL_ENABLED_PARAM,
    SQL_HOST_PARAM,
    SQL_PASSWORD_PARAM,
    SQL_PORT_PARAM,
    SQL_READONLY_PARAM,
    SQL_USER_PARAM,
)
from .mcp_defs import SQL_FORBIDDEN_READONLY_PATTERN, SQL_READONLY_START_PATTERN


def _tool_text(text):
    return {"content": [{"type": "text", "text": text}]}


def _tool_json(payload):
    return _tool_text(json.dumps(payload, indent=2, default=str))


def _param_bool(name, default=False):
    value = request.env["ir.config_parameter"].sudo().get_param(name)
    if value in (None, ""):
        return default
    return str(value).lower() in ("1", "true", "yes", "on")


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
    readonly = _param_bool(SQL_READONLY_PARAM, default=True)
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


def execute_direct_sql(arguments):
    if not _param_bool(SQL_ENABLED_PARAM):
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
    readonly = _param_bool(SQL_READONLY_PARAM, default=True)
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


