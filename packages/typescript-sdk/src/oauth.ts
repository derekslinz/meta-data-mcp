/**
 * NodeOAuthClientProvider — OAuth 2.0 client for Node.js CLI/server use.
 *
 * Implements the MCP `OAuthClientProvider` interface so it can be passed
 * directly to `SSEClientTransport` via `RemoteClient`'s `authProvider` option.
 *
 * ```ts
 * import { RemoteClient, NodeOAuthClientProvider } from '@meta-data-mcp/sdk';
 *
 * const auth = new NodeOAuthClientProvider({
 *   clientName:  'My App',
 *   callbackPort: 3333,
 * });
 *
 * const client = new RemoteClient('https://mcp.example.com', { authProvider: auth });
 * await client.connect();
 * // → prints the auth URL to stderr; opens callbackPort to receive the code
 * ```
 *
 * Flow:
 *  1. `RemoteClient.connect()` passes the provider to `SSEClientTransport`.
 *  2. The MCP SDK calls `redirectToAuthorization(url)` when auth is needed.
 *  3. `NodeOAuthClientProvider` prints the URL to stderr and starts a local
 *     HTTP server on `callbackPort` in the background.
 *  4. The user opens the URL in their browser, approves, and the server
 *     redirects to `http://localhost:<callbackPort>/callback?code=…`.
 *  5. The provider logs receipt of the code; the MCP SDK handles the exchange.
 *
 * Note: `redirectToAuthorization` returns void (required by the interface).
 * The callback server runs fire-and-forget in the background; errors are
 * logged to stderr. Tokens are in-memory only (lost on process restart).
 */

import * as http from "node:http";
import * as url from "node:url";

import type { OAuthClientProvider } from "@modelcontextprotocol/sdk/client/auth.js";
import type {
  OAuthClientMetadata,
  OAuthClientInformationMixed,
  OAuthTokens,
} from "@modelcontextprotocol/sdk/shared/auth.js";

export interface NodeOAuthClientProviderOptions {
  /** Human-readable name for this client (shown on the consent page). */
  clientName?: string;
  /** Local port for the OAuth callback server (default: 3000). */
  callbackPort?: number;
}

export class NodeOAuthClientProvider implements OAuthClientProvider {
  private readonly _callbackPort: number;
  private readonly _clientName: string;

  private _tokens: OAuthTokens | undefined = undefined;
  private _clientInfo: OAuthClientInformationMixed | undefined = undefined;
  private _codeVerifier: string = "";

  constructor(options: NodeOAuthClientProviderOptions = {}) {
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
   * Prints the authorization URL to stderr and starts a local HTTP server
   * on `callbackPort` in the background to receive the OAuth redirect.
   * Returns void (required by the `OAuthClientProvider` interface); the
   * callback server runs fire-and-forget and logs errors to stderr.
   */
  redirectToAuthorization(authorizationUrl: URL): void {
    process.stderr.write(
      `\n[meta-data-mcp] Authorization required.\n` +
        `Open this URL in your browser:\n\n  ${authorizationUrl.toString()}\n\n`,
    );
    // Fire-and-forget: start the callback server; errors are logged, not thrown.
    this._startCallbackServer().catch((err) => {
      process.stderr.write(`[meta-data-mcp] OAuth callback error: ${err}\n`);
    });
  }

  // -----------------------------------------------------------------------
  // Internal helpers
  // -----------------------------------------------------------------------

  private _startCallbackServer(): Promise<void> {
    return new Promise<void>((resolve, reject) => {
      // Declare server before setTimeout so the closure can reference it
      // without a temporal-dead-zone error under strict TypeScript.
      let server: http.Server;
      const timeoutMs = 2 * 60 * 1000; // 2 minutes

      const timer = setTimeout(() => {
        server?.close();
        reject(new Error("OAuth callback timed out after 2 minutes"));
      }, timeoutMs);

      server = http.createServer((req, res) => {
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
