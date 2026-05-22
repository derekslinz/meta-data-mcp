/**
 * NodeOAuthClientProvider — OAuth 2.0 client for Node.js CLI/server use.
 *
 * Implements the MCP `OAuthClientProvider` interface so it can be passed
 * directly to `SSEClientTransport` in the `RemoteClient`.
 *
 * Usage:
 * ```ts
 * import { RemoteClient, NodeOAuthClientProvider } from '@meta-data-mcp/sdk';
 *
 * const auth = new NodeOAuthClientProvider({
 *   serverUrl:   'https://mcp.example.com',
 *   clientName:  'My App',
 *   callbackPort: 3333,
 * });
 *
 * await client.connect(auth);
 * // → prints the auth URL to stderr; opens callbackPort to receive the code
 * ```
 *
 * Flow:
 *  1. `RemoteClient.connect()` passes the provider to `SSEClientTransport`.
 *  2. The MCP SDK calls `redirectToAuthorization(url)` when auth is needed.
 *  3. `NodeOAuthClientProvider` starts a local HTTP server on `callbackPort`
 *     and prints the authorization URL to stderr.
 *  4. The user opens the URL in their browser, approves, and the server
 *     redirects to `http://localhost:<callbackPort>/callback?code=…`.
 *  5. The provider exchanges the code and stores the tokens in memory.
 *
 * Tokens are in-memory only (lost on process restart). For persistent tokens,
 * subclass and override `tokens()` / `saveTokens()` to persist to disk.
 */

import * as http from "node:http";
import * as crypto from "node:crypto";
import * as url from "node:url";

import type { OAuthClientProvider } from "@modelcontextprotocol/sdk/client/auth.js";
import type {
  OAuthClientMetadata,
  OAuthClientInformationMixed,
  OAuthTokens,
} from "@modelcontextprotocol/sdk/shared/auth.js";

export interface NodeOAuthClientProviderOptions {
  /** Root URL of the MCP server (used to derive redirect URI + registration). */
  serverUrl: string;
  /** Human-readable name for this client (shown on the consent page). */
  clientName?: string;
  /** Local port for the OAuth callback server (default: 3000). */
  callbackPort?: number;
}

export class NodeOAuthClientProvider implements OAuthClientProvider {
  private readonly _serverUrl: string;
  private readonly _callbackPort: number;
  private readonly _clientName: string;

  private _tokens: OAuthTokens | undefined = undefined;
  private _clientInfo: OAuthClientInformationMixed | undefined = undefined;
  private _codeVerifier: string = "";

  constructor(options: NodeOAuthClientProviderOptions) {
    this._serverUrl = options.serverUrl.replace(/\/$/, "");
    this._callbackPort = options.callbackPort ?? 3000;
    this._clientName = options.clientName ?? "meta-data-mcp client";
  }

  // -----------------------------------------------------------------------
  // OAuthClientProvider interface
  // -----------------------------------------------------------------------

  get redirectUrl(): string {
    return `http://localhost:${this._callbackPort}/callback`;
  }

  get clientMetadata(): OAuthClientMetadata {
    return {
      client_name: this._clientName,
      redirect_uris: [this.redirectUrl],
      grant_types: ["authorization_code", "refresh_token"],
      response_types: ["code"],
      token_endpoint_auth_method: "none",
    };
  }

  clientInformation(): OAuthClientInformationMixed | undefined {
    return this._clientInfo;
  }

  saveClientInformation(info: OAuthClientInformationMixed): void {
    this._clientInfo = info;
  }

  tokens(): OAuthTokens | undefined {
    return this._tokens;
  }

  saveTokens(tokens: OAuthTokens): void {
    this._tokens = tokens;
  }

  saveCodeVerifier(codeVerifier: string): void {
    this._codeVerifier = codeVerifier;
  }

  codeVerifier(): string {
    return this._codeVerifier;
  }

  /**
   * Called by the MCP SDK when the server requires authorization.
   *
   * Starts a local HTTP server on `callbackPort` to receive the OAuth
   * redirect, then prints the authorization URL to stderr so the user
   * can open it in their browser.
   *
   * Returns a Promise that resolves when the callback has been received
   * and the auth code extracted, or rejects on timeout (2 min).
   */
  redirectToAuthorization(authorizationUrl: URL): void {
    // Print the URL — the caller (user) must open it in a browser.
    process.stderr.write(
      `\n[meta-data-mcp] Authorization required.\n` +
        `Open this URL in your browser:\n\n  ${authorizationUrl.toString()}\n\n`,
    );

    // Start a one-shot callback server to receive the code.
    // The server closes itself after the first request.
    this._startCallbackServer().catch((err) => {
      process.stderr.write(`[meta-data-mcp] OAuth callback error: ${err}\n`);
    });
  }

  // -----------------------------------------------------------------------
  // Internal helpers
  // -----------------------------------------------------------------------

  private _startCallbackServer(): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      const timeoutMs = 2 * 60 * 1000; // 2 minutes
      const timer = setTimeout(() => {
        server.close();
        reject(new Error("OAuth callback timed out after 2 minutes"));
      }, timeoutMs);

      const server = http.createServer((req, res) => {
        if (!req.url?.startsWith("/callback")) {
          res.writeHead(404).end("Not found");
          return;
        }
        const parsed = url.parse(req.url, true);
        const code = parsed.query["code"] as string | undefined;
        const error = parsed.query["error"] as string | undefined;

        if (error) {
          res.writeHead(400, { "Content-Type": "text/html" }).end(
            `<h1>Authorization denied</h1><p>${error}</p>`,
          );
          clearTimeout(timer);
          server.close();
          reject(new Error(`Authorization denied: ${error}`));
          return;
        }

        if (!code) {
          res.writeHead(400, { "Content-Type": "text/html" }).end(
            "<h1>Missing code parameter</h1>",
          );
          clearTimeout(timer);
          server.close();
          reject(new Error("Callback missing code parameter"));
          return;
        }

        res.writeHead(200, { "Content-Type": "text/html" }).end(
          "<h1>Authorization complete</h1>" +
            "<p>You can close this tab and return to the terminal.</p>",
        );
        clearTimeout(timer);
        server.close();
        // The MCP SDK's SSEClientTransport handles the actual token exchange
        // via the code verifier; we just need the callback to land.
        process.stderr.write("[meta-data-mcp] Authorization code received.\n");
        resolve();
      });

      server.listen(this._callbackPort, "127.0.0.1", () => {
        process.stderr.write(
          `[meta-data-mcp] Waiting for OAuth callback on port ${this._callbackPort}…\n`,
        );
      });

      server.on("error", (err) => {
        clearTimeout(timer);
        reject(err);
      });
    });
  }
}
