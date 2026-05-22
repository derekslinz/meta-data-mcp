/**
 * Types for the meta-data-mcp discovery SDK.
 *
 * These mirror the Python ProviderEntry dataclass and the tool response
 * shapes returned by the meta-data-mcp server's discovery tools.
 */

/** One row in the provider registry. */
export interface ProviderEntry {
  id: string;
  server_name: string;
  title: string;
  description: string;
  domains: string[];
  regions: string[];
  keywords: string[];
  homepage: string;
  license_note: string;
  requires_env: string[];
}

/**
 * A ranked provider result from find_providers.
 *
 * Note: `score` is only present when using the embedded Python SDK (SDK-1).
 * The remote SDK (SDK-2 Python and TypeScript) does not expose scores because
 * the MCP tool protocol does not include the composite routing score.
 * Ranking order is always preserved (best match first).
 */
export interface ProviderResult {
  entry: ProviderEntry;
  /** Per-strategy score breakdown. Only present when `explain: true`. */
  breakdown: Record<string, number> | null;
}

/** Options for `findProviders`. */
export interface FindProvidersOptions {
  /** Free-text search query. */
  query?: string;
  /** Hard filter by domain tag (e.g. `"health"`, `"finance"`). */
  domain?: string;
  /** Hard filter by region tag (e.g. `"us"`, `"eu"`, `"global"`). */
  region?: string;
  /** Maximum results (default: 20). */
  limit?: number;
  /**
   * When true, populates `breakdown` on each result with per-strategy scores
   * (token, fuzzy, metadata, semantic, health).
   */
  explain?: boolean;
}

/** Status dict returned by `activateProvider`. */
export interface ActivateProviderResult {
  status: "activated" | "already_active" | "error";
  provider_id: string;
  tools_added?: number;
  new_tool_names?: string[];
  error?: string;
}

/** Options for constructing a `RemoteClient`. */
export interface RemoteClientOptions {
  /**
   * Bearer token for the server's auth middleware.
   * Falls back to the `META_DATA_MCP_AUTH_TOKEN` environment variable.
   * Pass `token: ""` to disable auth on open servers.
   * Mutually exclusive with `authProvider`.
   */
  token?: string;
  /**
   * OAuth client provider for authorization-code + PKCE flows.
   * When set, the MCP SSE transport uses this provider for authentication
   * instead of the static bearer token.
   * Mutually exclusive with `token`.
   */
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  authProvider?: any;
  /** Connection timeout in milliseconds (default: 30_000). */
  timeoutMs?: number;
}
