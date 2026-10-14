import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync, gzipSync } from "node:zlib";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvalidPayloadError, validateReplayCatalog } from "../../src/lib/contracts";
import { loadArtifact, parseEvents } from "../../src/streams/artifact";

const base = resolve("public", "replay", "synthetic");
const content = {
  "manifest.json": readFileSync(resolve(base, "manifest.json"), "utf8"),
  "catalog.json": readFileSync(resolve(base, "catalog.json"), "utf8"),
  "diagnostics.json": readFileSync(resolve(base, "diagnostics.json"), "utf8"),
  "events.ndjson.gz": readFileSync(resolve(base, "events.ndjson.gz")),
};

function deferred() {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

afterEach(() => vi.unstubAllGlobals());

describe("artifact downloads", () => {
  it.each([false, true])("starts all files after manifest and waits for diagnostics, encoded=%s", async (encoded) => {
    vi.stubGlobal("crypto", webcrypto);
    const catalog = deferred();
    const events = deferred();
    const diagnostics = deferred();
    const pending: Record<string, ReturnType<typeof deferred>> = {
      "catalog.json": catalog, "events.ndjson.gz": events, "diagnostics.json": diagnostics,
    };
    const fetch = vi.fn(async (url: string, options?: RequestInit) => {
      expect(options?.signal).toBe(signal);
      const name = url.split("/").pop() ?? "";
      if (name === "manifest.json") return new Response(content[name]);
      const response = pending[name];
      if (!response) throw new Error(`Unexpected artifact file ${name}`);
      return response.promise;
    });
    const signal = new AbortController().signal;
    vi.stubGlobal("fetch", fetch);
    const returned = vi.fn();
    const loading = loadArtifact("synthetic", signal).then((artifact) => { returned(); return artifact; });
    // Catalog response is deliberately withheld: it cannot gate either other request.
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(4));
    expect(fetch.mock.calls.map(([url]) => url.split("/").pop())).toEqual([
      "manifest.json", "catalog.json", "events.ndjson.gz", "diagnostics.json",
    ]);
    const text = gunzipSync(content["events.ndjson.gz"]).toString("utf8");
    events.resolve(new Response(encoded ? text : content["events.ndjson.gz"], {
      headers: encoded ? { "Content-Encoding": "gzip" } : {},
    }));
    catalog.resolve(new Response(content["catalog.json"]));
    await new Promise<void>((done) => setTimeout(done, 0));
    expect(returned).not.toHaveBeenCalled();
    diagnostics.resolve(new Response(content["diagnostics.json"]));
    const artifact = await loading;
    expect(artifact.events).toHaveLength(artifact.manifest.eventCount);
    expect(artifact.diagnostics?.streamId).toBe(artifact.manifest.streamId);
    expect(returned).toHaveBeenCalledTimes(1);
  });

  it("refuses an invalid manifest before requesting any artifact files", async () => {
    const fetch = vi.fn(async () => Response.json({ schemaVersion: 2 }));
    vi.stubGlobal("fetch", fetch);
    await expect(loadArtifact("synthetic").then(() => undefined)).rejects.toThrow(InvalidPayloadError);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it.each(["catalog", "events"] as const)("rejects schema-valid changes to %s before returning an artifact", async (changed) => {
    vi.stubGlobal("crypto", webcrypto);
    const catalog = content["catalog.json"].replace("Estação sintética 001", "Estação renomeada");
    const events = gunzipSync(content["events.ndjson.gz"]).toString("utf8")
      .replace(/"bikesAvailable":(\d+)/, (_match, count: string) => `"bikesAvailable":${Number(count) + 1}`);
    expect(catalog).not.toBe(content["catalog.json"]);
    expect(validateReplayCatalog(JSON.parse(catalog))).toBe(true);
    expect(events).not.toBe(gunzipSync(content["events.ndjson.gz"]).toString("utf8"));
    expect(parseEvents(events)).toHaveLength(JSON.parse(content["manifest.json"]).eventCount);
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const name = url.split("/").pop() as keyof typeof content;
      if (name === "catalog.json" && changed === "catalog") return new Response(catalog);
      if (name === "events.ndjson.gz" && changed === "events") return new Response(gzipSync(events));
      return new Response(content[name]);
    }));
    await expect(loadArtifact("synthetic").then(() => undefined)).rejects.toThrow(InvalidPayloadError);
  });

  it.each([
    ["catalog.json", false], ["catalog.json", true],
    ["diagnostics.json", false], ["diagnostics.json", true],
    ["events.ndjson.gz", false], ["events.ndjson.gz", true],
  ] as const)("rejects an added UTF-8 BOM in %s, encoded=%s", async (changed, encoded) => {
    vi.stubGlobal("crypto", webcrypto);
    const bom = Buffer.from([0xef, 0xbb, 0xbf]);
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const name = url.split("/").pop() as keyof typeof content;
      if (name === "events.ndjson.gz") {
        const original = gunzipSync(content[name]);
        const bytes = changed === name ? Buffer.concat([bom, original]) : original;
        return new Response(encoded ? bytes : gzipSync(bytes), {
          headers: encoded ? { "Content-Encoding": "gzip" } : {},
        });
      }
      return new Response(changed === name ? Buffer.concat([bom, Buffer.from(content[name])]) : content[name]);
    }));
    await expect(loadArtifact("synthetic").then(() => undefined)).rejects.toThrow(InvalidPayloadError);
  });
});
