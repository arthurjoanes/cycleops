import {
  InvalidPayloadError,
  type FeedDiagnostics,
  type ReplayCatalog,
  type ReplayManifest,
  type StationBatchEvent,
  validateReplayCatalog,
  validateFeedDiagnostics,
  validateReplayManifest,
  validateStationBatchEvent,
} from "../lib/contracts";

export interface Artifact {
  manifest: ReplayManifest;
  catalog: ReplayCatalog;
  events: StationBatchEvent[];
  diagnostics?: FeedDiagnostics | null;
}

export type ArtifactKind = "recorded" | "synthetic";

export function artifactBase(kind: ArtifactKind): string {
  return `${import.meta.env.BASE_URL}replay/${kind}/`;
}

async function fetchOk(url: string, signal?: AbortSignal): Promise<Response> {
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`${url}: ${response.status}`);
  return response;
}

async function sha256(bytes: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (n) => n.toString(16).padStart(2, "0")).join("");
}

/** The recorded replay exists only when a recording passed acceptance. */
export async function artifactExists(kind: ArtifactKind, signal?: AbortSignal): Promise<boolean> {
  try {
    const response = await fetch(`${artifactBase(kind)}manifest.json`, { signal });
    // A server with an HTML fallback answers 200 for anything; the manifest is JSON.
    return response.ok && (response.headers.get("content-type") ?? "").includes("json");
  } catch {
    return false;
  }
}

/** Splits NDJSON and validates every event; one bad line rejects the artifact. */
export function parseEvents(text: string): StationBatchEvent[] {
  const events: StationBatchEvent[] = [];
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    const event: unknown = JSON.parse(line);
    if (!validateStationBatchEvent(event)) throw new InvalidPayloadError("event");
    events.push(event);
  }
  return events;
}

/** The validated manifest identifies the files downloaded together. The .gz is served
 * as a plain file (no Content-Encoding), so it is decompressed here. Nothing is returned
 * until every file and their shared stream/period have passed validation. */
export async function loadArtifact(kind: ArtifactKind, signal?: AbortSignal): Promise<Artifact> {
  const base = artifactBase(kind);
  const manifest: unknown = await (await fetchOk(`${base}manifest.json`, signal)).json();
  if (!validateReplayManifest(manifest)) throw new InvalidPayloadError("manifest");
  const [catalogBytes, eventBytes, diagnosticBytes] = await Promise.all([
    fetchOk(`${base}catalog.json`, signal).then((response) => response.arrayBuffer()),
    fetchOk(`${base}events.ndjson.gz`, signal).then(async (gz) => {
      if (!gz.body) throw new Error("events.ndjson.gz: empty body");
      // A server adding Content-Encoding has already had the browser decompress it.
      const encoded = (gz.headers.get("content-encoding") ?? "").includes("gzip");
      const body = encoded ? gz.body : gz.body.pipeThrough(new DecompressionStream("gzip"));
      return new Response(body).arrayBuffer();
    }),
    manifest.diagnosticsSha256
      ? fetchOk(`${base}diagnostics.json`, signal).then((response) => response.arrayBuffer())
      : Promise.resolve(null),
  ]);
  // Verify the original bytes: decoding first can discard a UTF-8 BOM or replace
  // malformed sequences, changing the material covered by the manifest's digest.
  const [catalogHash, eventsHash] = await Promise.all([sha256(catalogBytes), sha256(eventBytes)]);
  if (catalogHash !== manifest.catalogSha256 || eventsHash !== manifest.eventsSha256) {
    throw new InvalidPayloadError("artifact hash");
  }
  const decoder = new TextDecoder();
  const catalog: unknown = JSON.parse(decoder.decode(catalogBytes));
  if (!validateReplayCatalog(catalog)) throw new InvalidPayloadError("catalog");
  const events = parseEvents(decoder.decode(eventBytes));
  // The three files must be one artifact: a mix from two deploys would play as a frozen
  // screen, because the store only takes sequence 1, 2, 3... of one stream.
  const versions = new Set(catalog.versions.map((v) => v.catalogVersion));
  const consistent =
    events.length === manifest.eventCount &&
    events.every((e, i) => e.streamId === manifest.streamId && e.sequence === i + 1 && versions.has(e.catalogVersion));
  if (!consistent) throw new InvalidPayloadError("events");
  let diagnostics: FeedDiagnostics | null = null;
  if (diagnosticBytes !== null) {
    if ((await sha256(diagnosticBytes)) !== manifest.diagnosticsSha256) {
      throw new InvalidPayloadError("feed diagnostics");
    }
    const body: unknown = JSON.parse(decoder.decode(diagnosticBytes));
    if (!validateFeedDiagnostics(body)) {
      throw new InvalidPayloadError("feed diagnostics");
    }
    if (
      body.streamId !== manifest.streamId ||
      body.mode !== manifest.mode ||
      Date.parse(body.windowStart) !== Date.parse(manifest.periodStart) ||
      Date.parse(body.windowEnd) !== Date.parse(manifest.periodEnd)
    ) {
      throw new InvalidPayloadError("feed diagnostics period");
    }
    diagnostics = body;
  }
  return { manifest, catalog, events, diagnostics };
}
