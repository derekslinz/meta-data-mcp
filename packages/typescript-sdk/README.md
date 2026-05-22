# @meta-data-mcp/sdk

TypeScript/JavaScript discovery client for [meta-data-mcp](https://github.com/derekslinz/meta-data-mcp) — the single MCP server for 60+ open-data providers.

```bash
npm install @meta-data-mcp/sdk
```

## Usage

```ts
import { RemoteClient } from '@meta-data-mcp/sdk';

// One-shot (new MCP session per call)
const client = new RemoteClient('https://your-server.example.com', { token: 'your-token' });
const results = await client.findProviders({ query: 'earthquake data', limit: 5 });
console.log(results[0].entry.id); // → 'us_usgs_earthquake'

// Persistent session (recommended for multiple calls in the same request)
await client.connect();
try {
  const results  = await client.findProviders({ query: 'earthquake' });
  const domains  = await client.listDomains();
  const snapshot = await client.healthSnapshot();
} finally {
  await client.disconnect();
}
```

## API

### `new RemoteClient(baseUrl, options?)`

| Option | Default | Description |
|---|---|---|
| `token` | `META_DATA_MCP_AUTH_TOKEN` env | Bearer token for server auth. Pass `token: ""` to disable auth on open servers. Mutually exclusive with `authProvider`. |
| `authProvider` | — | OAuth 2.0 client provider (e.g. `NodeOAuthClientProvider`). Mutually exclusive with `token`. |
| `timeoutMs` | `30_000` | Connection timeout in ms. |

### Methods

| Method | Returns | Description |
|---|---|---|
| `findProviders(opts?)` | `Promise<ProviderResult[]>` | Ranked provider list for a query/domain/region |
| `listDomains()` | `Promise<string[]>` | All domain tags in the registry |
| `listRegions()` | `Promise<string[]>` | All region tags in the registry |
| `describeProvider(id)` | `Promise<ProviderEntry \| null>` | One provider entry, or null |
| `activateProvider(id)` | `Promise<ActivateProviderResult>` | Lazy-load a provider on the server |
| `healthSnapshot()` | `Promise<Record<string, number>>` | `{providerId: score}` for all providers |
| `connect()` | `Promise<void>` | Open a persistent MCP session |
| `disconnect()` | `Promise<void>` | Close the active session |

### `FindProvidersOptions`

```ts
{
  query?: string;   // free-text search
  domain?: string;  // hard filter (e.g. "health", "finance")
  region?: string;  // hard filter (e.g. "us", "eu", "global")
  limit?: number;   // max results (default 20)
  explain?: boolean; // include per-strategy breakdown
}
```

### `ProviderResult`

```ts
{
  entry: ProviderEntry;
  breakdown: Record<string, number> | null; // populated when explain: true
}
```

> **Note:** The MCP tool protocol does not expose composite routing scores, so there is no `score` field. Ranking order is always preserved (best match first).

## Auth

### Bearer token (simple)

Pass `token: "your-token"` in the constructor options, or set the `META_DATA_MCP_AUTH_TOKEN` environment variable. On unauthenticated servers, omit the token or pass `token: ""`.

### OAuth 2.0 (Authorization Code + PKCE)

When the server has `META_DATA_MCP_OAUTH_ISSUER` configured, use `NodeOAuthClientProvider` for full OAuth:

```ts
import { RemoteClient, NodeOAuthClientProvider } from '@meta-data-mcp/sdk';

const auth = new NodeOAuthClientProvider({
  serverUrl:    'https://mcp.example.com',
  clientName:   'My CLI',
  callbackPort: 3333,   // local port for the redirect (default 3000)
});

const client = new RemoteClient('https://mcp.example.com', { authProvider: auth });
await client.connect();
// → prints the authorization URL to stderr
// → starts a local HTTP server on port 3333 to receive the callback
// → user opens URL in browser, approves, and the session is established
const results = await client.findProviders({ query: 'earthquake' });
await client.disconnect();
```

`NodeOAuthClientProvider` options:

| Option | Default | Description |
|---|---|---|
| `serverUrl` | required | Root URL of the MCP server |
| `clientName` | `"meta-data-mcp client"` | Name shown on the consent page |
| `callbackPort` | `3000` | Local port for the OAuth redirect |

## Requirements

- Node.js ≥ 18
- A running [meta-data-mcp](https://github.com/derekslinz/meta-data-mcp) server
