import { createHash, webcrypto } from "node:crypto";
import { gzipSync } from "node:zlib";
import { afterEach, describe, expect, it, vi } from "vitest";
import { InvalidPayloadError, type FeedDiagnostics } from "../../src/lib/contracts";
import { fetchDiagnostics } from "../../src/streams/api";
import { loadArtifact } from "../../src/streams/artifact";
import { catalog, catalogStation, event, iso, manifest } from "./factory";

function diagnostics(partial: Partial<FeedDiagnostics> = {}): FeedDiagnostics {
  return {
    schemaVersion: 1,
    streamId: "test:00000000",
    mode: "synthetic",
    windowStart: iso(0),
    windowEnd: iso(3600),
    observedStart: null,
    observedEnd: null,
    totals: {
      attempts: 0,
      httpResponses: 0,
      successfulResponses: 0,
      httpFailures: 0,
      transportFailures: 0,
      contractFailures: 0,
      acceptedPublications: 0,
      rejectedPublications: 0,
      duplicatePublications: 0,
      correctionPublications: 0,
      outOfOrderPublications: 0,
      futurePublications: 0,
      notModified: 0,
      stationRecords: null,
      validStationRecords: null,
      invalidStationRecords: null,
    },
    feeds: [],
    ...partial,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("feed diagnostics API", () => {
  it("validates the response and ignores a reply from a superseded stream", async () => {
    const body = diagnostics();
    const fetch = vi.fn(async (url: string) => {
      expect(url).toContain("api/v1/feed-diagnostics");
      return Response.json(body);
    });
    vi.stubGlobal("fetch", fetch);
    const signal = new AbortController().signal;
    expect(await fetchDiagnostics(body.streamId, signal)).toEqual(body);
    expect(await fetchDiagnostics("another-stream", signal)).toBeNull();
    expect(fetch.mock.calls[0]?.[0]).toContain("api/v1/feed-diagnostics");
    vi.stubGlobal("fetch", async () => Response.json({ ...body, totals: {} }));
    await expect(fetchDiagnostics(body.streamId, signal)).rejects.toThrow(InvalidPayloadError);
  });
});

describe("replay diagnostics binding", () => {
  function serve(body: FeedDiagnostics | null, overrideHash?: string) {
    vi.stubGlobal("crypto", webcrypto);
    const content = JSON.stringify(body);
    const checksum = createHash("sha256").update(content).digest("hex");
    const catalogText = JSON.stringify(catalog({ v1: [catalogStation("A")] }));
    const eventText = JSON.stringify(event(1, []));
    const files: Record<string, BodyInit> = {
      "manifest.json": JSON.stringify(manifest({
        eventCount: 1,
        catalogSha256: createHash("sha256").update(catalogText).digest("hex"),
        eventsSha256: createHash("sha256").update(eventText).digest("hex"),
        ...(body ? { diagnosticsSha256: overrideHash ?? checksum } : {}),
      })),
      "catalog.json": catalogText,
      "events.ndjson.gz": gzipSync(eventText),
      "diagnostics.json": content,
    };
    const fetch = vi.fn(async (url: string) => new Response(files[url.split("/").pop() ?? ""]));
    vi.stubGlobal("fetch", fetch);
    return fetch;
  }

  it("loads diagnostics for the same stream, mode and recording period", async () => {
    const body = diagnostics();
    serve(body);
    expect((await loadArtifact("synthetic")).diagnostics).toEqual(body);
  });

  it("supports earlier manifests without requesting a diagnostics file", async () => {
    const fetch = serve(null);
    expect((await loadArtifact("synthetic")).diagnostics).toBeNull();
    expect(fetch.mock.calls.some(([url]) => url.endsWith("diagnostics.json"))).toBe(false);
  });

  it("rejects a changed file or a valid file from another stream, mode or period", async () => {
    serve(diagnostics(), "f".repeat(64));
    await expect(loadArtifact("synthetic")).rejects.toThrow(InvalidPayloadError);
    for (const partial of [
      { streamId: "different-stream" },
      { mode: "recorded_replay" as const },
      { windowStart: iso(1) },
      { windowEnd: iso(3599) },
    ]) {
      serve(diagnostics(partial));
      await expect(loadArtifact("synthetic")).rejects.toThrow(InvalidPayloadError);
    }
  });
});
