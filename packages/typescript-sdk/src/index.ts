/**
 * @meta-data-mcp/sdk — TypeScript/JavaScript discovery client for meta-data-mcp.
 *
 * ```ts
 * import { RemoteClient } from '@meta-data-mcp/sdk';
 *
 * const client = new RemoteClient('https://mcp.example.com', { token: 'secret' });
 * const results = await client.findProviders({ query: 'earthquake', limit: 5 });
 * console.log(results[0].entry.id); // → 'us_usgs_earthquake'
 * ```
 */

export { RemoteClient } from "./remote.js";
export type {
  ActivateProviderResult,
  FindProvidersOptions,
  ProviderEntry,
  ProviderResult,
  RemoteClientOptions,
} from "./types.js";
