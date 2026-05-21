/**
 * RemoteClient — TypeScript discovery client for meta-data-mcp (SDK-2, Level 1).
 *
 * Connects to a running meta-data-mcp SSE server via the MCP protocol and
 * exposes the same six discovery operations as the embedded Python SDK.
 *
 * ```ts
 * import { RemoteClient } from '@meta-data-mcp/sdk';
 *
 * // One-shot (new session per call)
 * const client = new RemoteClient('https://mcp.example.com', { token: 'secret' });
 * const results = await client.findProviders({ query: 'earthquake', limit: 5 });
 *
 * // Persistent session (recommended for multiple calls)
 * await client.connect();
 * try {
 *   const results  = await client.findProviders({ query: 'earthquake' });
 *   const domains  = await client.listDomains();
 *   const snapshot = await client.healthSnapshot();
 * } finally {
 *   await client.disconnect();
 * }
 * ```
 */

import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js";

import type {
  ActivateProviderResult,
  FindProvidersOptions,
  ProviderEntry,
  ProviderResult,
  RemoteClientOptions,
} from "./types.js";

/** @internal */
function getEnvToken(): string | undefined {
  // Works in Node.js; gracefully absent in browser/Deno environments.
  try {
    return (
      (typeof process !== "undefined" && process.env?.META_DATA_MCP_AUTH_TOKEN) ||
      undefined
    );
  } catch {
    return undefined;
  }
}

export class RemoteClient {
  private readonly baseUrl: string;
  private readonly token: string | undefined;
  private readonly timeoutMs: number;
  private client: Client | null = null;

  /**
   * @param baseUrl Root URL of the server (e.g. `"https://mcp.example.com"`).
   *                The `/sse` path is appended automatically.
   * @param options Auth token and timeout options.
   */
  constructor(baseUrl: string, options: RemoteClientOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.token =
      options.token !== undefined ? options.token || undefined : getEnvToken();
    this.timeoutMs = options.timeoutMs ?? 30_000;
  }

  // -----------------------------------------------------------------------
  // Session management
  // -----------------------------------------------------------------------

  /** Open a persistent MCP session. Must be paired with `disconnect()`. */
  async connect(): Promise<void> {
    if (this.client) return; // already connected
    const headers: Record<string, string> = {};
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;

    const transport = new SSEClientTransport(
      new URL(`${this.baseUrl}/sse`),
      { requestInit: { headers } },
    );
    this.client = new Client(
      { name: "@meta-data-mcp/sdk", version: "2.2.0" },
      { capabilities: {} },
    );
    await this.client.connect(transport);
  }

  /** Close the active MCP session. */
  async disconnect(): Promise<void> {
    if (this.client) {
      await this.client.close();
      this.client = null;
    }
  }

  // -----------------------------------------------------------------------
  // Internal — one-shot helper
  // -----------------------------------------------------------------------

  /**
   * Call one MCP tool and return the parsed JSON payload.
   *
   * Uses the persistent session when `connect()` has been called, otherwise
   * opens a fresh session for this call and closes it immediately after.
   */
  private async callTool(
    name: string,
    args: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    if (this.client) {
      return this._callWithClient(this.client, name, args);
    }

    // One-shot: open + close a temporary session.
    const headers: Record<string, string> = {};
    if (this.token) headers["Authorization"] = `Bearer ${this.token}`;

    const transport = new SSEClientTransport(
      new URL(`${this.baseUrl}/sse`),
      { requestInit: { headers } },
    );
    const tempClient = new Client(
      { name: "@meta-data-mcp/sdk", version: "2.2.0" },
      { capabilities: {} },
    );
    await tempClient.connect(transport);
    try {
      return await this._callWithClient(tempClient, name, args);
    } finally {
      await tempClient.close();
    }
  }

  private async _callWithClient(
    client: Client,
    name: string,
    args: Record<string, unknown>,
  ): Promise<Record<string, unknown>> {
    const result = await client.callTool({ name, arguments: args });

    // The SDK return type uses an index signature so we cast the known fields.
    const isError = result.isError as boolean | undefined;
    const content = result.content as Array<{ type: string; text?: string }> | undefined;

    if (isError) {
      throw new Error(`Tool "${name}" returned an MCP error: ${JSON.stringify(content)}`);
    }
    if (!content || content.length === 0) {
      return {};
    }

    const first = content[0];
    if (first.type !== "text" || first.text == null) {
      throw new Error(`Tool "${name}" returned unexpected content type: ${first.type}`);
    }
    const payload = JSON.parse(first.text) as Record<string, unknown>;
    if ("error" in payload) {
      throw new Error(`Tool "${name}" failed: ${payload.error}`);
    }
    return payload;
  }

  // -----------------------------------------------------------------------
  // Public API
  // -----------------------------------------------------------------------

  /**
   * Find providers matching the given criteria.
   *
   * Returns ranked results (best match first). Each result includes the
   * provider entry dict and, when `explain: true`, a per-strategy breakdown.
   * No `score` field is included — the MCP protocol does not expose the
   * composite routing score; ranking order is preserved.
   */
  async findProviders(options: FindProvidersOptions = {}): Promise<ProviderResult[]> {
    const args: Record<string, unknown> = { limit: options.limit ?? 20, activate_top: 0 };
    if (options.query !== undefined) args.query = options.query;
    if (options.domain !== undefined) args.domain = options.domain;
    if (options.region !== undefined) args.region = options.region;

    const payload = await this.callTool("opendata-find-providers", args);
    const providers = (payload.providers ?? []) as ProviderEntry[];
    const breakdowns = (payload.breakdowns ?? {}) as Record<string, Record<string, number>>;

    return providers.map((p) => ({
      entry: p,
      breakdown: options.explain ? (breakdowns[p.id] ?? null) : null,
    }));
  }

  /** Return all domain tags in the server's registry. */
  async listDomains(): Promise<string[]> {
    const payload = await this.callTool("opendata-list-domains", {});
    return (payload.domains ?? []) as string[];
  }

  /** Return all region tags in the server's registry. */
  async listRegions(): Promise<string[]> {
    const payload = await this.callTool("opendata-list-regions", {});
    return (payload.regions ?? []) as string[];
  }

  /**
   * Return the registry entry for the given provider, or `null` if not found.
   * Accepts both snake_case ids (`us_usgs_earthquake`) and kebab-case server
   * names (`us-usgs-earthquake`).
   */
  async describeProvider(providerId: string): Promise<ProviderEntry | null> {
    const payload = await this.callTool("opendata-describe-provider", {
      provider_id: providerId,
    });
    return (payload.provider as ProviderEntry) ?? null;
  }

  /**
   * Activate the named provider on the remote server (lazy-load its tools).
   * Returns the server's status dict.
   */
  async activateProvider(providerId: string): Promise<ActivateProviderResult> {
    const payload = await this.callTool("opendata-activate-provider", {
      provider_id: providerId,
    });
    return payload as unknown as ActivateProviderResult;
  }

  /**
   * Return a `{ providerId: healthScore }` snapshot from the server.
   * Scores are in `[0, 1]` — 1.0 means no recorded failures.
   */
  async healthSnapshot(): Promise<Record<string, number>> {
    const payload = await this.callTool("opendata-health-snapshot", {});
    return (payload.scores ?? {}) as Record<string, number>;
  }
}
