#!/usr/bin/env node

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { createHash, createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { readFileSync } from "node:fs";
import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { readFile, readdir, stat, writeFile } from "node:fs/promises";
import { dirname, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { z } from "zod";

const appRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const credentialsFilePath = resolve(appRoot, "odoo-credentials.json");
const aiContextFilePath = resolve(appRoot, "ai-context.txt");
const authTokenFilePath = resolve(appRoot, "mcp-auth-token");
const mcpConfigFilePath = resolve(appRoot, "odoo-mcp.conf");

function readMcpConfig() {
  const rawConfig = readFileSync(mcpConfigFilePath, "utf8");
  const values = new Map<string, string>();

  rawConfig.split(/\r?\n/).forEach((line) => {
    const trimmed = line.trim();

    if (!trimmed || trimmed.startsWith("#")) {
      return;
    }

    const separatorIndex = trimmed.indexOf("=");

    if (separatorIndex === -1) {
      throw new Error(`Invalid config line in ${mcpConfigFilePath}: ${line}`);
    }

    values.set(
      trimmed.slice(0, separatorIndex).trim(),
      trimmed.slice(separatorIndex + 1).trim(),
    );
  });

  const httpHost = values.get("http_host");
  const httpPort = Number(values.get("http_port"));
  const mcpPath = values.get("mcp_path");
  const publicOrigin = values.get("public_origin");
  const addonsCodeRoot = values.get("addons_code_root");

  if (!httpHost) {
    throw new Error(`Missing http_host in ${mcpConfigFilePath}`);
  }

  if (!Number.isInteger(httpPort) || httpPort <= 0 || httpPort > 65535) {
    throw new Error(`Invalid http_port in ${mcpConfigFilePath}`);
  }

  if (!mcpPath?.startsWith("/")) {
    throw new Error(`mcp_path must start with "/" in ${mcpConfigFilePath}`);
  }

  if (!publicOrigin?.startsWith("https://")) {
    throw new Error(`public_origin must start with "https://" in ${mcpConfigFilePath}`);
  }

  if (!addonsCodeRoot?.startsWith("/")) {
    throw new Error(`addons_code_root must be an absolute path in ${mcpConfigFilePath}`);
  }

  return { httpHost, httpPort, mcpPath, publicOrigin, addonsCodeRoot };
}

const { httpHost, httpPort, mcpPath, publicOrigin, addonsCodeRoot } = readMcpConfig();
const resourceUri = `${publicOrigin}${mcpPath}`;
const protectedResourceMetadataPath = "/.well-known/oauth-protected-resource";
const authorizationServerMetadataPath = "/.well-known/oauth-authorization-server";
const oauthAuthorizePath = "/oauth/authorize";
const oauthTokenPath = "/oauth/token";
const oauthRegisterPath = "/oauth/register";
const oauthScope = "odoo:read";
const authCodeTtlMs = 5 * 60 * 1000;
const accessTokenTtlSeconds = 60 * 60 * 8;
const authCodes = new Map<
  string,
  {
    clientId: string;
    redirectUri: string;
    codeChallenge: string;
    scope: string;
    resource: string;
    expiresAt: number;
  }
>();

const odooCredentialsSchema = z.object({
  url: z.string().min(1),
  database: z.string().min(1),
  username: z.string().min(1),
  password: z.string(),
});

type OdooCredentials = z.infer<typeof odooCredentialsSchema>;
type JsonRpcResponse<T> = {
  jsonrpc: "2.0";
  id: number;
  result?: T;
  error?: {
    code: number;
    message: string;
    data?: {
      name?: string;
      debug?: string;
      message?: string;
      arguments?: unknown[];
    };
  };
};
type TokenPayload = {
  aud: string;
  client_id: string;
  exp: number;
  iat: number;
  scope: string;
};

const odooDomainSchema = z
  .string()
  .optional()
  .describe("JSON-encoded Odoo domain array, for example [[\"is_company\", \"=\", true]].");

const odooSearchSchema = z.object({
  model: z.string().min(1).describe("Odoo model name, for example res.partner."),
  domain: odooDomainSchema,
  limit: z.number().int().min(0).max(500).optional().describe("Maximum records to return."),
  offset: z.number().int().min(0).optional().describe("Number of records to skip."),
  order: z.string().optional().describe("Odoo order string, for example name asc."),
});

const odooSearchReadSchema = odooSearchSchema.extend({
  fields: z
    .array(z.string().min(1))
    .optional()
    .describe("Fields to return, for example ['name', 'email']."),
});

const aiContextSchema = z.object({
  text: z.string().describe("Full replacement text to store in ai-context.txt."),
});

const odooPythonCodeSearchSchema = z.object({
  query: z.string().min(1).describe("Case-insensitive text to search for in .py files."),
  module: z
    .string()
    .min(1)
    .optional()
    .describe("Optional Odoo module subfolder under addons_code_root, for example filologico."),
  maxResults: z
    .number()
    .int()
    .min(1)
    .max(200)
    .optional()
    .describe("Maximum matching lines to return. Defaults to 50."),
});

const odooPythonCodeReadSchema = z.object({
  path: z
    .string()
    .min(1)
    .describe("Relative path to a .py file under addons_code_root, for example filologico/models/res_partner.py."),
});

async function readOdooCredentials(): Promise<
  | { ok: true; credentials: OdooCredentials }
  | { ok: false; error: string }
> {
  let rawCredentials: string;

  try {
    rawCredentials = await readFile(credentialsFilePath, "utf8");
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") {
      return {
        ok: false,
        error: `Missing credentials file: ${credentialsFilePath}`,
      };
    }

    return {
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    };
  }

  let parsedCredentials: unknown;

  try {
    parsedCredentials = JSON.parse(rawCredentials);
  } catch (error) {
    return {
      ok: false,
      error: `Invalid JSON in ${credentialsFilePath}: ${
        error instanceof Error ? error.message : String(error)
      }`,
    };
  }

  const validation = odooCredentialsSchema.safeParse(parsedCredentials);

  if (!validation.success) {
    const invalidFields = validation.error.issues
      .map((issue) => issue.path.join("."))
      .filter(Boolean)
      .join(", ");

    return {
      ok: false,
      error: invalidFields
        ? `Invalid credentials in ${credentialsFilePath}. Check: ${invalidFields}`
        : `Invalid credentials in ${credentialsFilePath}.`,
    };
  }

  return {
    ok: true,
    credentials: validation.data,
  };
}

function getOdooJsonRpcUrl(credentials: OdooCredentials): string {
  return new URL("/jsonrpc", credentials.url).toString();
}

async function odooJsonRpc<T>(
  credentials: OdooCredentials,
  params: Record<string, unknown>,
): Promise<T> {
  const url = getOdooJsonRpcUrl(credentials);
  let response: Response;

  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        jsonrpc: "2.0",
        method: "call",
        params,
        id: Date.now(),
      }),
    });
  } catch (error) {
    const cause =
      error instanceof Error && error.cause instanceof Error
        ? `: ${error.cause.message}`
        : "";
    throw new Error(
      `Could not reach Odoo JSON-RPC endpoint at ${url}${cause}`,
    );
  }

  if (!response.ok) {
    throw new Error(`Odoo JSON-RPC HTTP ${response.status}: ${response.statusText}`);
  }

  const payload = (await response.json()) as JsonRpcResponse<T>;

  if (payload.error) {
    const details =
      payload.error.data?.message ??
      payload.error.data?.debug ??
      payload.error.message;
    throw new Error(`Odoo JSON-RPC error ${payload.error.code}: ${details}`);
  }

  return payload.result as T;
}

async function authenticateOdoo(credentials: OdooCredentials): Promise<number> {
  const uid = await odooJsonRpc<number | false>(credentials, {
    service: "common",
    method: "login",
    args: [credentials.database, credentials.username, credentials.password],
  });

  if (!uid) {
    throw new Error("Odoo authentication failed. Check database, username, and password.");
  }

  return uid;
}

async function executeOdooKw<T>(
  credentials: OdooCredentials,
  model: string,
  method: string,
  args: unknown[] = [],
  kwargs: Record<string, unknown> = {},
): Promise<T> {
  const uid = await authenticateOdoo(credentials);

  return odooJsonRpc<T>(credentials, {
    service: "object",
    method: "execute_kw",
    args: [
      credentials.database,
      uid,
      credentials.password,
      model,
      method,
      args,
      kwargs,
    ],
  });
}

function parseOdooDomain(domain: string | undefined): unknown[] {
  if (!domain?.trim()) {
    return [];
  }

  const parsedDomain = JSON.parse(domain) as unknown;

  if (!Array.isArray(parsedDomain)) {
    throw new Error("Odoo domain must be a JSON array.");
  }

  return parsedDomain;
}

async function readAiContext(): Promise<string> {
  try {
    return await readFile(aiContextFilePath, "utf8");
  } catch (error) {
    if (error instanceof Error && "code" in error && error.code === "ENOENT") {
      return "";
    }

    throw error;
  }
}

async function writeAiContext(text: string): Promise<void> {
  await writeFile(aiContextFilePath, text, "utf8");
}

function resolveAddonsPath(relativePath = "."): string {
  if (relativePath.startsWith("/") || relativePath.includes("\0")) {
    throw new Error("Path must be relative to addons_code_root.");
  }

  const resolvedPath = resolve(addonsCodeRoot, relativePath);
  const pathFromRoot = relative(addonsCodeRoot, resolvedPath);

  if (
    pathFromRoot === ".." ||
    pathFromRoot.startsWith(`..${sep}`) ||
    pathFromRoot.startsWith("/") ||
    pathFromRoot.includes("\0")
  ) {
    throw new Error("Path escapes addons_code_root.");
  }

  return resolvedPath;
}

function toAddonsRelativePath(absolutePath: string): string {
  return relative(addonsCodeRoot, absolutePath);
}

async function listPythonFiles(rootPath: string): Promise<string[]> {
  const results: string[] = [];
  const entries = await readdir(rootPath, { withFileTypes: true });

  for (const entry of entries) {
    if (entry.name === "__pycache__" || entry.name.startsWith(".")) {
      continue;
    }

    const entryPath = resolve(rootPath, entry.name);

    if (entry.isDirectory()) {
      results.push(...(await listPythonFiles(entryPath)));
      continue;
    }

    if (entry.isFile() && entry.name.endsWith(".py")) {
      results.push(entryPath);
    }
  }

  return results;
}

async function searchOdooPythonCode(params: {
  query: string;
  module?: string;
  maxResults?: number;
}) {
  const searchRoot = resolveAddonsPath(params.module ?? ".");
  const searchRootStats = await stat(searchRoot);

  if (!searchRootStats.isDirectory()) {
    throw new Error("Search root must be a directory.");
  }

  const normalizedQuery = params.query.toLowerCase();
  const maxResults = params.maxResults ?? 50;
  const matches: Array<{ path: string; line: number; text: string }> = [];

  for (const filePath of await listPythonFiles(searchRoot)) {
    const fileText = await readFile(filePath, "utf8");
    const lines = fileText.split(/\r?\n/);

    for (let index = 0; index < lines.length; index += 1) {
      if (!lines[index].toLowerCase().includes(normalizedQuery)) {
        continue;
      }

      matches.push({
        path: toAddonsRelativePath(filePath),
        line: index + 1,
        text: lines[index].trim(),
      });

      if (matches.length >= maxResults) {
        return matches;
      }
    }
  }

  return matches;
}

async function readOdooPythonCodeFile(relativePath: string): Promise<string> {
  if (!relativePath.endsWith(".py")) {
    throw new Error("Only .py files can be read.");
  }

  const filePath = resolveAddonsPath(relativePath);
  const fileStats = await stat(filePath);

  if (!fileStats.isFile()) {
    throw new Error("Path must point to a .py file.");
  }

  return readFile(filePath, "utf8");
}

async function getCredentialsOrToolResponse(): Promise<
  | { ok: true; credentials: OdooCredentials }
  | {
      ok: false;
      response: {
        content: Array<{ type: "text"; text: string }>;
        isError: true;
      };
    }
> {
  const credentialsResult = await readOdooCredentials();

  if (!credentialsResult.ok) {
    return {
      ok: false,
      response: {
        isError: true,
        content: [
          {
            type: "text",
            text: JSON.stringify(
              {
                configured: false,
                credentialsFile: credentialsFilePath,
                error: credentialsResult.error,
              },
              null,
              2,
            ),
          },
        ],
      },
    };
  }

  return credentialsResult;
}

function createMcpServer(): McpServer {
  const server = new McpServer({
    name: "odoo-nodemcp",
    version: "0.1.0",
  });

  server.registerTool(
    "get-ai-context",
    {
      title: "Get AI Context",
      description:
        "Load the server-local ai-context.txt guidance. AI assistants should strongly prefer calling this at least once near the start of every session before using other Odoo tools, because it may contain install-specific conventions, constraints, and operating context.",
      inputSchema: {},
    },
    async () => {
      try {
        return {
          content: [
            {
              type: "text",
              text: await readAiContext(),
            },
          ],
        };
      } catch (error) {
        return {
          isError: true,
          content: [
            {
              type: "text",
              text: error instanceof Error ? error.message : String(error),
            },
          ],
        };
      }
    },
  );

  server.registerTool(
    "set-ai-context",
    {
      title: "Set AI Context",
      description:
        "Replace the server-local ai-context.txt guidance returned by get-ai-context. Use this to maintain install-specific instructions for future AI sessions.",
      inputSchema: aiContextSchema.shape,
    },
    async ({ text }) => {
      try {
        await writeAiContext(text);

        return {
          content: [
            {
              type: "text",
              text: `Updated ${aiContextFilePath}`,
            },
          ],
        };
      } catch (error) {
        return {
          isError: true,
          content: [
            {
              type: "text",
              text: error instanceof Error ? error.message : String(error),
            },
          ],
        };
      }
    },
  );

  server.registerTool(
    "odoo_search",
    {
      title: "Odoo Search",
      description: "Search an Odoo model and return matching record IDs.",
      inputSchema: odooSearchSchema.shape,
    },
    async ({ model, domain, limit, offset, order }) => {
      const credentialsResult = await getCredentialsOrToolResponse();

      if (!credentialsResult.ok) {
        return credentialsResult.response;
      }

      const kwargs = {
        ...(limit === undefined ? {} : { limit }),
        ...(offset === undefined ? {} : { offset }),
        ...(order === undefined ? {} : { order }),
    };

    try {
      const parsedDomain = parseOdooDomain(domain);
      const ids = await executeOdooKw<number[]>(
        credentialsResult.credentials,
        model,
        "search",
        [parsedDomain],
        kwargs,
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ model, domain: parsedDomain, ids }, null, 2),
          },
        ],
      };
      } catch (error) {
        return {
          isError: true,
          content: [
            {
              type: "text",
              text: error instanceof Error ? error.message : String(error),
            },
          ],
        };
      }
    },
  );

  server.registerTool(
    "odoo_search_read",
    {
      title: "Odoo Search Read",
      description: "Search an Odoo model and return matching records.",
      inputSchema: odooSearchReadSchema.shape,
    },
    async ({ model, domain, fields, limit, offset, order }) => {
      const credentialsResult = await getCredentialsOrToolResponse();

      if (!credentialsResult.ok) {
        return credentialsResult.response;
      }

      const kwargs = {
        ...(fields === undefined ? {} : { fields }),
        ...(limit === undefined ? {} : { limit }),
        ...(offset === undefined ? {} : { offset }),
        ...(order === undefined ? {} : { order }),
    };

    try {
      const parsedDomain = parseOdooDomain(domain);
      const records = await executeOdooKw<Array<Record<string, unknown>>>(
        credentialsResult.credentials,
        model,
        "search_read",
        [parsedDomain],
        kwargs,
      );

      return {
        content: [
          {
            type: "text",
            text: JSON.stringify({ model, domain: parsedDomain, records }, null, 2),
          },
        ],
      };
      } catch (error) {
        return {
          isError: true,
          content: [
            {
              type: "text",
              text: error instanceof Error ? error.message : String(error),
            },
          ],
        };
      }
    },
  );

  server.registerTool(
    "odoo_python_code_search",
    {
      title: "Odoo Python Code Search",
      description:
        "Read-only search across .py files under the configured Odoo addons code root. Use module to limit the search to a module subfolder.",
      inputSchema: odooPythonCodeSearchSchema.shape,
    },
    async ({ query, module, maxResults }) => {
      try {
        const matches = await searchOdooPythonCode({ query, module, maxResults });

        return {
          content: [
            {
              type: "text",
              text: JSON.stringify(
                {
                  addonsCodeRoot,
                  query,
                  module: module ?? null,
                  count: matches.length,
                  matches,
                },
                null,
                2,
              ),
            },
          ],
        };
      } catch (error) {
        return {
          isError: true,
          content: [
            {
              type: "text",
              text: error instanceof Error ? error.message : String(error),
            },
          ],
        };
      }
    },
  );

  server.registerTool(
    "odoo_python_code_read",
    {
      title: "Odoo Python Code Read",
      description:
        "Read a single .py file under the configured Odoo addons code root. This tool is read-only and rejects paths outside that root.",
      inputSchema: odooPythonCodeReadSchema.shape,
    },
    async ({ path }) => {
      try {
        return {
          content: [
            {
              type: "text",
              text: await readOdooPythonCodeFile(path),
            },
          ],
        };
      } catch (error) {
        return {
          isError: true,
          content: [
            {
              type: "text",
              text: error instanceof Error ? error.message : String(error),
            },
          ],
        };
      }
    },
  );

  return server;
}

async function readMcpAuthToken(): Promise<string> {
  const token = (await readFile(authTokenFilePath, "utf8")).trim();

  if (!token) {
    throw new Error(`MCP auth token file is empty: ${authTokenFilePath}`);
  }

  return token;
}

function isMcpPath(pathname: string): boolean {
  return pathname === mcpPath || pathname.startsWith(`${mcpPath}/`);
}

function isProtectedResourceMetadataPath(pathname: string): boolean {
  return (
    pathname === protectedResourceMetadataPath ||
    pathname === `${protectedResourceMetadataPath}${mcpPath}`
  );
}

function isAuthorizationServerMetadataPath(pathname: string): boolean {
  return pathname === authorizationServerMetadataPath;
}

function isOAuthPath(pathname: string): boolean {
  return [
    oauthAuthorizePath,
    oauthTokenPath,
    oauthRegisterPath,
  ].includes(pathname);
}

function writeJson(
  res: ServerResponse,
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
) {
  res.writeHead(status, {
    "Content-Type": "application/json",
    ...headers,
  });
  res.end(JSON.stringify(body));
}

function writeHtml(
  res: ServerResponse,
  status: number,
  html: string,
) {
  res.writeHead(status, {
    "Content-Type": "text/html; charset=utf-8",
  });
  res.end(html);
}

function base64Url(input: Buffer | string): string {
  return Buffer.from(input)
    .toString("base64")
    .replace(/\+/g, "-")
    .replace(/\//g, "_")
    .replace(/=+$/g, "");
}

function safeEquals(left: string, right: string): boolean {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);

  return (
    leftBuffer.length === rightBuffer.length &&
    timingSafeEqual(leftBuffer, rightBuffer)
  );
}

function getBearerToken(authorizationHeader: string | undefined): string | null {
  const match = authorizationHeader?.match(/^Bearer\s+(.+)$/i);
  return match?.[1] ?? null;
}

function createAccessToken(authToken: string, payload: TokenPayload): string {
  const encodedPayload = base64Url(JSON.stringify(payload));
  const signature = base64Url(
    createHmac("sha256", authToken).update(encodedPayload).digest(),
  );

  return `${encodedPayload}.${signature}`;
}

function verifyAccessToken(authToken: string, token: string): boolean {
  const [encodedPayload, signature] = token.split(".");

  if (!encodedPayload || !signature) {
    return false;
  }

  const expectedSignature = base64Url(
    createHmac("sha256", authToken).update(encodedPayload).digest(),
  );

  if (!safeEquals(signature, expectedSignature)) {
    return false;
  }

  let payload: TokenPayload;

  try {
    payload = JSON.parse(
      Buffer.from(encodedPayload, "base64url").toString("utf8"),
    ) as TokenPayload;
  } catch {
    return false;
  }

  return payload.aud === resourceUri && payload.exp > Math.floor(Date.now() / 1000);
}

function isAuthorizedRequest(authToken: string, authorizationHeader: string | undefined): boolean {
  const token = getBearerToken(authorizationHeader);

  if (!token) {
    return false;
  }

  return safeEquals(token, authToken) || verifyAccessToken(authToken, token);
}

function getUnauthorizedChallenge(): string {
  return `Bearer resource_metadata="${publicOrigin}${protectedResourceMetadataPath}", scope="${oauthScope}"`;
}

function getProtectedResourceMetadata() {
  return {
    resource: resourceUri,
    authorization_servers: [publicOrigin],
    scopes_supported: [oauthScope],
    bearer_methods_supported: ["header"],
    resource_name: "PERSONA Odoo MCP",
  };
}

function getAuthorizationServerMetadata() {
  return {
    issuer: publicOrigin,
    authorization_endpoint: `${publicOrigin}${oauthAuthorizePath}`,
    token_endpoint: `${publicOrigin}${oauthTokenPath}`,
    registration_endpoint: `${publicOrigin}${oauthRegisterPath}`,
    response_types_supported: ["code"],
    grant_types_supported: ["authorization_code"],
    token_endpoint_auth_methods_supported: ["none"],
    code_challenge_methods_supported: ["S256"],
    scopes_supported: [oauthScope],
  };
}

async function readRequestBody(req: IncomingMessage): Promise<string> {
  const chunks: Buffer[] = [];

  for await (const chunk of req) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }

  return Buffer.concat(chunks).toString("utf8");
}

async function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const rawBody = await readRequestBody(req);

  if (!rawBody.trim()) {
    return {};
  }

  return JSON.parse(rawBody) as Record<string, unknown>;
}

async function readFormBody(req: IncomingMessage): Promise<URLSearchParams> {
  return new URLSearchParams(await readRequestBody(req));
}

function getPkceChallenge(codeVerifier: string): string {
  return base64Url(createHash("sha256").update(codeVerifier).digest());
}

function createAuthorizationCode(params: {
  clientId: string;
  redirectUri: string;
  codeChallenge: string;
  scope: string;
  resource: string;
}): string {
  const code = base64Url(randomBytes(32));
  authCodes.set(code, {
    ...params,
    expiresAt: Date.now() + authCodeTtlMs,
  });
  return code;
}

function consumeAuthorizationCode(code: string) {
  const codeData = authCodes.get(code);
  authCodes.delete(code);

  if (!codeData || codeData.expiresAt < Date.now()) {
    return null;
  }

  return codeData;
}

function getAuthorizeHtml(params: URLSearchParams, error?: string): string {
  const hiddenInputs = [
    "response_type",
    "client_id",
    "redirect_uri",
    "state",
    "scope",
    "resource",
    "code_challenge",
    "code_challenge_method",
  ]
    .map((name) => {
      const value = params.get(name) ?? "";
      return `<input type="hidden" name="${name}" value="${escapeHtml(value)}">`;
    })
    .join("\n");

  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Authorize PERSONA MCP</title>
    <style>
      body { font-family: system-ui, sans-serif; margin: 2rem; color: #1f2937; }
      main { max-width: 36rem; margin: 0 auto; }
      label { display: block; font-weight: 650; margin-top: 1rem; }
      input { box-sizing: border-box; width: 100%; padding: .75rem; margin-top: .4rem; }
      button { margin-top: 1rem; padding: .75rem 1rem; font-weight: 650; }
      .error { color: #b91c1c; font-weight: 650; }
      code { background: #f3f4f6; padding: .1rem .25rem; border-radius: .25rem; }
    </style>
  </head>
  <body>
    <main>
      <h1>Authorize PERSONA MCP</h1>
      <p>This grants access to <code>${escapeHtml(resourceUri)}</code> with scope <code>${oauthScope}</code>.</p>
      ${error ? `<p class="error">${escapeHtml(error)}</p>` : ""}
      <form method="post" action="${oauthAuthorizePath}">
        ${hiddenInputs}
        <label for="access_token">MCP auth token</label>
        <input id="access_token" name="access_token" type="password" autocomplete="current-password" autofocus required>
        <button type="submit">Authorize</button>
      </form>
    </main>
  </body>
</html>`;
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function redirectWithAuthorizationError(
  res: ServerResponse,
  redirectUri: string,
  state: string | null,
  error: string,
) {
  const redirectUrl = new URL(redirectUri);
  redirectUrl.searchParams.set("error", error);

  if (state) {
    redirectUrl.searchParams.set("state", state);
  }

  res.writeHead(302, { Location: redirectUrl.toString() });
  res.end();
}

async function handleOAuthAuthorize(
  req: IncomingMessage,
  res: ServerResponse,
  requestUrl: URL,
  authToken: string,
) {
  if (req.method === "GET") {
    writeHtml(res, 200, getAuthorizeHtml(requestUrl.searchParams));
    return;
  }

  if (req.method !== "POST") {
    writeJson(res, 405, { error: "method_not_allowed" });
    return;
  }

  const form = await readFormBody(req);
  const accessToken = form.get("access_token") ?? "";
  const redirectUri = form.get("redirect_uri") ?? "";
  const state = form.get("state");
  const clientId = form.get("client_id") ?? "";
  const responseType = form.get("response_type") ?? "";
  const codeChallenge = form.get("code_challenge") ?? "";
  const codeChallengeMethod = form.get("code_challenge_method") ?? "";
  const scope = form.get("scope") || oauthScope;
  const resource = form.get("resource") || resourceUri;

  if (!safeEquals(accessToken, authToken)) {
    writeHtml(res, 401, getAuthorizeHtml(form, "Invalid MCP auth token."));
    return;
  }

  if (
    responseType !== "code" ||
    !clientId ||
    !redirectUri ||
    !codeChallenge ||
    codeChallengeMethod !== "S256" ||
    resource !== resourceUri
  ) {
    if (redirectUri) {
      redirectWithAuthorizationError(res, redirectUri, state, "invalid_request");
      return;
    }

    writeJson(res, 400, { error: "invalid_request" });
    return;
  }

  const code = createAuthorizationCode({
    clientId,
    redirectUri,
    codeChallenge,
    scope,
    resource,
  });
  const redirectUrl = new URL(redirectUri);
  redirectUrl.searchParams.set("code", code);

  if (state) {
    redirectUrl.searchParams.set("state", state);
  }

  res.writeHead(302, { Location: redirectUrl.toString() });
  res.end();
}

async function handleOAuthToken(
  req: IncomingMessage,
  res: ServerResponse,
  authToken: string,
) {
  if (req.method !== "POST") {
    writeJson(res, 405, { error: "method_not_allowed" });
    return;
  }

  const form = await readFormBody(req);
  const grantType = form.get("grant_type");
  const code = form.get("code") ?? "";
  const redirectUri = form.get("redirect_uri") ?? "";
  const clientId = form.get("client_id") ?? "";
  const codeVerifier = form.get("code_verifier") ?? "";
  const codeData = consumeAuthorizationCode(code);

  if (
    grantType !== "authorization_code" ||
    !codeData ||
    codeData.redirectUri !== redirectUri ||
    codeData.clientId !== clientId ||
    codeData.codeChallenge !== getPkceChallenge(codeVerifier)
  ) {
    writeJson(res, 400, { error: "invalid_grant" });
    return;
  }

  const now = Math.floor(Date.now() / 1000);
  const accessToken = createAccessToken(authToken, {
    aud: resourceUri,
    client_id: clientId,
    exp: now + accessTokenTtlSeconds,
    iat: now,
    scope: codeData.scope,
  });

  writeJson(res, 200, {
    access_token: accessToken,
    token_type: "Bearer",
    expires_in: accessTokenTtlSeconds,
    scope: codeData.scope,
  });
}

async function handleOAuthRegister(
  req: IncomingMessage,
  res: ServerResponse,
) {
  if (req.method !== "POST") {
    writeJson(res, 405, { error: "method_not_allowed" });
    return;
  }

  const body = await readJsonBody(req);
  const redirectUris = Array.isArray(body.redirect_uris)
    ? body.redirect_uris.filter((uri): uri is string => typeof uri === "string")
    : [];

  if (!redirectUris.length) {
    writeJson(res, 400, { error: "invalid_client_metadata" });
    return;
  }

  writeJson(
    res,
    201,
    {
      client_id: `persona-mcp-${base64Url(randomBytes(16))}`,
      client_id_issued_at: Math.floor(Date.now() / 1000),
      redirect_uris: redirectUris,
      grant_types: ["authorization_code"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
      scope: oauthScope,
    },
    { "Cache-Control": "no-store" },
  );
}

async function handleDiscoveryAndOAuth(
  req: IncomingMessage,
  res: ServerResponse,
  requestUrl: URL,
  authToken: string,
): Promise<boolean> {
  if (isProtectedResourceMetadataPath(requestUrl.pathname)) {
    writeJson(res, 200, getProtectedResourceMetadata());
    return true;
  }

  if (isAuthorizationServerMetadataPath(requestUrl.pathname)) {
    writeJson(res, 200, getAuthorizationServerMetadata());
    return true;
  }

  if (requestUrl.pathname === oauthAuthorizePath) {
    await handleOAuthAuthorize(req, res, requestUrl, authToken);
    return true;
  }

  if (requestUrl.pathname === oauthTokenPath) {
    await handleOAuthToken(req, res, authToken);
    return true;
  }

  if (requestUrl.pathname === oauthRegisterPath) {
    await handleOAuthRegister(req, res);
    return true;
  }

  return false;
}

async function startHttpServer() {
  const authToken = await readMcpAuthToken();
  const httpServer = createServer(async (req, res) => {
    const requestUrl = new URL(req.url ?? "/", `http://${req.headers.host ?? httpHost}`);

    if (await handleDiscoveryAndOAuth(req, res, requestUrl, authToken)) {
      return;
    }

    if (!isMcpPath(requestUrl.pathname)) {
      res.writeHead(404, { "Content-Type": "text/plain" });
      res.end("Not found");
      return;
    }

    if (!isAuthorizedRequest(authToken, req.headers.authorization)) {
      res.writeHead(401, {
        "Content-Type": "application/json",
        "WWW-Authenticate": getUnauthorizedChallenge(),
      });
      res.end(JSON.stringify({ error: "Unauthorized" }));
      return;
    }

    const transport = new StreamableHTTPServerTransport({
      sessionIdGenerator: undefined,
      enableJsonResponse: true,
    });
    const server = createMcpServer();

    try {
      await server.connect(transport);
      await transport.handleRequest(req, res);
    } catch (error) {
      if (!res.headersSent) {
        res.writeHead(500, { "Content-Type": "application/json" });
      }
      res.end(
        JSON.stringify({
          error: error instanceof Error ? error.message : String(error),
        }),
      );
    } finally {
      await transport.close().catch(() => undefined);
    }
  });

  await new Promise<void>((resolveListen) => {
    httpServer.listen(httpPort, httpHost, resolveListen);
  });

  console.error(`odoo-nodemcp HTTP listening on http://${httpHost}:${httpPort}${mcpPath}`);
}

async function startStdioServer() {
  const server = createMcpServer();
  const transport = new StdioServerTransport();
  await server.connect(transport);
}

async function main() {
  if (process.argv.includes("--http")) {
    await startHttpServer();
    return;
  }

  await startStdioServer();
}

main().catch((error) => {
  console.error("Failed to start odoo-nodemcp:", error);
  process.exit(1);
});
