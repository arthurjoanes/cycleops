import { webcrypto } from "node:crypto";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvalidPayloadError } from "../../src/lib/contracts";
import { loadArtifact } from "../../src/streams/artifact";

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
    await expect(loadArtifact("synthetic")).rejects.toThrow(InvalidPayloadError);
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});
