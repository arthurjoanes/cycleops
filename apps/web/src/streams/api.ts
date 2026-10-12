// REST side of the server stream. Loaded only in the builds that have a server (never on
// Pages). Every payload is validated with the generated schema before it reaches the store.
import {
  InvalidPayloadError,
  type FeedDiagnostics,
  type Snapshot,
  type StationHistory,
  validateSnapshot,
  validateFeedDiagnostics,
  validateStationHistory,
} from "../lib/contracts";

/** Same origin: Caddy in Compose, the Vite proxy in dev. */
export const API = `${import.meta.env.BASE_URL}api/v1/`;

// A request that hangs (proxy up, API stuck) must end as a failure, not as a page that
// stays in "loading" forever.
const TIMEOUT_MS = 10_000;

/**
 * A non-JSON answer (an HTML fallback, a proxy error page) means the API is not reachable;
 * it is not a payload to validate.
 */
async function getJson(path: string, signal: AbortSignal): Promise<unknown> {
  const response = await fetch(`${API}${path}`, {
    signal: AbortSignal.any([signal, AbortSignal.timeout(TIMEOUT_MS)]),
    headers: { Accept: "application/json" },
  });
  if (!response.ok || !(response.headers.get("content-type") ?? "").includes("json")) {
    throw new Error(`${path}: ${response.status}`);
  }
  return response.json();
}

/** The snapshot, with its cursor checked: "<streamId>@<sequence>". */
export async function fetchSnapshot(signal: AbortSignal): Promise<Snapshot> {
  const body = await getJson("stations/snapshot", signal);
  if (!validateSnapshot(body)) throw new InvalidPayloadError("snapshot");
  const sequence = body.cursor.slice(body.cursor.lastIndexOf("@") + 1);
  if (body.cursor !== `${body.streamId}@${sequence}` || !/^\d{1,15}$/.test(sequence)) {
    throw new InvalidPayloadError("snapshot cursor");
  }
  return body;
}

/** Last 6 h (the server's default window). null when the answer is for another stream or
 * station: a late response after a switch never reaches the screen. */
export async function fetchHistory(
  stationId: string,
  streamId: string,
  signal: AbortSignal,
): Promise<StationHistory | null> {
  const body = await getJson(`stations/${encodeURIComponent(stationId)}/history`, signal);
  if (!validateStationHistory(body)) throw new InvalidPayloadError("history");
  return body.streamId === streamId && body.stationId === stationId ? body : null;
}

/** The last 6 h of stored attempts. A late answer for a superseded stream is ignored. */
export async function fetchDiagnostics(
  streamId: string,
  signal: AbortSignal,
): Promise<FeedDiagnostics | null> {
  const body = await getJson("feed-diagnostics", signal);
  if (!validateFeedDiagnostics(body)) throw new InvalidPayloadError("feed diagnostics");
  return body.streamId === streamId ? body : null;
}

/** Whether the API answers. Checked before reopening a closed stream. */
export async function apiReachable(signal: AbortSignal): Promise<boolean> {
  try {
    await getJson("feed-health", signal);
    return true;
  } catch {
    return false;
  }
}
