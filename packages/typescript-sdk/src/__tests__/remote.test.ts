/**
 * Tests for RemoteClient.
 *
 * All tests mock the private `callTool` method to avoid needing a live server.
 * Transport-level integration tests live in tests/live/ (run manually).
 */

import { describe, expect, it, jest, beforeEach } from "@jest/globals";
import { RemoteClient } from "../remote.js";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Inject a mock callTool that returns preset payloads per tool name. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mockCallTool(client: RemoteClient, responses: Record<string, Record<string, unknown>>): any {
  const mock = jest.fn(async (...args: unknown[]) => {
    const name = args[0] as string;
    if (name in responses) return responses[name];
    throw new Error(`Unexpected tool call: ${name}`);
  });
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  (client as any).callTool = mock;
  return mock;
}

const USGS_ENTRY = {
  id: "us_usgs_earthquake",
  server_name: "us-usgs-earthquake",
  title: "USGS Earthquake Hazards",
  description: "Real-time earthquake data.",
  domains: ["earth-science"],
  regions: ["us", "global"],
  keywords: ["earthquake"],
  homepage: "https://earthquake.usgs.gov/",
  license_note: "",
  requires_env: [],
};

const ARXIV_ENTRY = {
  id: "global_arxiv",
  server_name: "global-arxiv",
  title: "arXiv",
  description: "Academic preprints.",
  domains: ["scholarly-literature"],
  regions: ["global"],
  keywords: ["papers", "research"],
  homepage: "https://arxiv.org/",
  license_note: "",
  requires_env: [],
};

// ---------------------------------------------------------------------------
// Construction
// ---------------------------------------------------------------------------

describe("RemoteClient construction", () => {
  it("strips trailing slash from baseUrl", () => {
    const c = new RemoteClient("https://mcp.example.com/", { token: "tok" });
    expect((c as unknown as { baseUrl: string }).baseUrl).toBe("https://mcp.example.com");
  });

  it("stores explicit token", () => {
    const c = new RemoteClient("https://mcp.example.com", { token: "secret" });
    expect((c as unknown as { token: string }).token).toBe("secret");
  });

  it("uses empty-string token to disable auth", () => {
    const c = new RemoteClient("https://mcp.example.com", { token: "" });
    expect((c as unknown as { token: string | undefined }).token).toBeUndefined();
  });

  it("defaults timeoutMs to 30 000", () => {
    const c = new RemoteClient("https://mcp.example.com");
    expect((c as unknown as { timeoutMs: number }).timeoutMs).toBe(30_000);
  });
});

// ---------------------------------------------------------------------------
// findProviders
// ---------------------------------------------------------------------------

describe("findProviders", () => {
  let client: RemoteClient;
  beforeEach(() => {
    client = new RemoteClient("https://mcp.example.com", { token: "tok" });
  });

  it("returns ranked list parsed from server payload", async () => {
    mockCallTool(client, {
      "opendata-find-providers": {
        count: 2,
        providers: [USGS_ENTRY, ARXIV_ENTRY],
      },
    });

    const results = await client.findProviders({ query: "earthquake", limit: 2 });

    expect(results).toHaveLength(2);
    expect(results[0].entry.id).toBe("us_usgs_earthquake");
    expect(results[0].breakdown).toBeNull(); // explain not requested
  });

  it("populates breakdown when explain: true", async () => {
    mockCallTool(client, {
      "opendata-find-providers": {
        count: 1,
        providers: [USGS_ENTRY],
        breakdowns: { us_usgs_earthquake: { token: 0.9, fuzzy: 0.7 } },
      },
    });

    const results = await client.findProviders({ query: "earthquake", explain: true });

    expect(results[0].breakdown).toEqual({ token: 0.9, fuzzy: 0.7 });
  });

  it("returns empty list when no providers match", async () => {
    mockCallTool(client, {
      "opendata-find-providers": { count: 0, providers: [] },
    });

    expect(await client.findProviders({ query: "zzz_no_match" })).toEqual([]);
  });

  it("omits undefined query/domain/region from args", async () => {
    const spy = mockCallTool(client, {
      "opendata-find-providers": { count: 0, providers: [] },
    });

    await client.findProviders({ limit: 5 });

    const args = (spy.mock.calls[0] as unknown[])[1] as Record<string, unknown>;
    expect(args).not.toHaveProperty("query");
    expect(args).not.toHaveProperty("domain");
    expect(args).not.toHaveProperty("region");
    expect(args.limit).toBe(5);
    expect(args.activate_top).toBe(0);
  });

  it("applies domain and region filters", async () => {
    const spy = mockCallTool(client, {
      "opendata-find-providers": { count: 0, providers: [] },
    });

    await client.findProviders({ domain: "health", region: "us" });

    const args = (spy.mock.calls[0] as unknown[])[1] as Record<string, unknown>;
    expect(args.domain).toBe("health");
    expect(args.region).toBe("us");
  });
});

// ---------------------------------------------------------------------------
// listDomains / listRegions
// ---------------------------------------------------------------------------

describe("listDomains", () => {
  it("returns domain array from server", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    mockCallTool(client, {
      "opendata-list-domains": { domains: ["earth-science", "finance", "health"] },
    });

    expect(await client.listDomains()).toEqual(["earth-science", "finance", "health"]);
  });
});

describe("listRegions", () => {
  it("returns region array from server", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    mockCallTool(client, {
      "opendata-list-regions": { regions: ["eu", "global", "us"] },
    });

    expect(await client.listRegions()).toEqual(["eu", "global", "us"]);
  });
});

// ---------------------------------------------------------------------------
// describeProvider
// ---------------------------------------------------------------------------

describe("describeProvider", () => {
  it("returns entry when found — server returns flat entry dict at top level", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    // The server returns entry fields directly, not nested under a "provider" key.
    mockCallTool(client, { "opendata-describe-provider": USGS_ENTRY });

    const entry = await client.describeProvider("us_usgs_earthquake");
    expect(entry?.id).toBe("us_usgs_earthquake");
    expect(entry?.title).toBe("USGS Earthquake Hazards");
  });

  it("returns null when not found — server returns {error: 'Provider X not found'}", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    // Server returns {"error": "Provider 'not_real' not found"}.
    // _callWithClient throws; describeProvider catches and returns null.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (client as any).callTool = jest.fn(async () => {
      throw new Error("Tool \"opendata-describe-provider\" failed: Provider 'not_real' not found");
    });

    expect(await client.describeProvider("not_real")).toBeNull();
  });

  it("re-throws non-not-found errors", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (client as any).callTool = jest.fn(async () => {
      throw new Error("network error");
    });

    await expect(client.describeProvider("us_usgs_earthquake")).rejects.toThrow("network error");
  });
});

// ---------------------------------------------------------------------------
// activateProvider
// ---------------------------------------------------------------------------

describe("activateProvider", () => {
  it("returns status dict from server", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    mockCallTool(client, {
      "opendata-activate-provider": {
        status: "activated",
        provider_id: "us_usgs_earthquake",
        tools_added: 3,
        new_tool_names: ["usgs-list-events"],
      },
    });

    const result = await client.activateProvider("us_usgs_earthquake");
    expect(result.status).toBe("activated");
    expect(result.tools_added).toBe(3);
  });
});

// ---------------------------------------------------------------------------
// healthSnapshot
// ---------------------------------------------------------------------------

describe("healthSnapshot", () => {
  it("returns score map from server", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    mockCallTool(client, {
      "opendata-health-snapshot": {
        scores: { us_usgs_earthquake: 1.0, global_arxiv: 0.95 },
      },
    });

    const snap = await client.healthSnapshot();
    expect(snap["us_usgs_earthquake"]).toBe(1.0);
    expect(snap["global_arxiv"]).toBe(0.95);
  });
});

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

describe("error handling", () => {
  it("propagates tool errors as exceptions", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (client as any).callTool = jest.fn(async () => { throw new Error("connection refused"); });

    await expect(client.findProviders()).rejects.toThrow("connection refused");
  });
});

// ---------------------------------------------------------------------------
// _callWithClient content parsing (exercises the actual MCP result parsing)
// ---------------------------------------------------------------------------

describe("_callWithClient content parsing", () => {
  // Access private method via any cast for direct unit testing.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function callWith(client: RemoteClient, mockClient: Record<string, unknown>): (name: string, args: Record<string, unknown>) => Promise<Record<string, unknown>> {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return (name, args) => (client as any)._callWithClient(mockClient, name, args);
  }

  it("parses text/content result correctly", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    const mockMcpClient = {
      callTool: jest.fn(async () => ({
        isError: false,
        content: [{ type: "text", text: '{"domains":["health","finance"]}' }],
      })),
    };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const result = await (client as any)._callWithClient(mockMcpClient, "opendata-list-domains", {});
    expect(result).toEqual({ domains: ["health", "finance"] });
  });

  it("throws when isError is true", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    const mockMcpClient = {
      callTool: jest.fn(async () => ({
        isError: true,
        content: [{ type: "text", text: "internal error" }],
      })),
    };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await expect((client as any)._callWithClient(mockMcpClient, "any-tool", {}))
      .rejects.toThrow('returned an MCP error');
  });

  it("throws on legacy toolResult compatibility shape", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    const mockMcpClient = {
      callTool: jest.fn(async () => ({
        toolResult: { some: "data" },
        // no content key
      })),
    };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await expect((client as any)._callWithClient(mockMcpClient, "any-tool", {}))
      .rejects.toThrow("legacy toolResult format");
  });

  it("throws on tool payload with error key", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    const mockMcpClient = {
      callTool: jest.fn(async () => ({
        isError: false,
        content: [{ type: "text", text: '{"error":"Provider not found"}' }],
      })),
    };

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    await expect((client as any)._callWithClient(mockMcpClient, "any-tool", {}))
      .rejects.toThrow("Provider not found");
  });
});

// ---------------------------------------------------------------------------
// connect / disconnect lifecycle
// ---------------------------------------------------------------------------

describe("connect / disconnect lifecycle", () => {
  it("connect() is idempotent — second call is a no-op", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    const fakeMcpClient = { close: jest.fn() };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (client as any).client = fakeMcpClient;

    // connect() should return early without creating a new transport
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const connectSpy = jest.spyOn(client as any, "connect");
    await client.connect(); // should no-op because this.client is already set
    expect(fakeMcpClient.close).not.toHaveBeenCalled();
    connectSpy.mockRestore();
  });

  it("disconnect() clears the session", async () => {
    const client = new RemoteClient("https://mcp.example.com", { token: "tok" });
    const fakeMcpClient = { close: jest.fn(async () => {}) };
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (client as any).client = fakeMcpClient;

    await client.disconnect();

    expect(fakeMcpClient.close).toHaveBeenCalledTimes(1);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    expect((client as any).client).toBeNull();
  });

  it("disconnect() is safe when not connected", async () => {
    const client = new RemoteClient("https://mcp.example.com");
    await expect(client.disconnect()).resolves.toBeUndefined();
  });
});
