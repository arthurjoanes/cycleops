// Hand-built payloads for unit tests. Real events come from the backend's artifacts.
import type {
  CatalogStation,
  FeedHealth,
  IncidentItem,
  ReplayCatalog,
  ReplayManifest,
  Snapshot,
  StationBatchEvent,
  StationState,
} from "../../src/lib/contracts";

export const T0 = Date.parse("2026-10-06T22:15:00Z");
export const iso = (offsetSeconds: number) => new Date(T0 + offsetSeconds * 1000).toISOString();

export function catalogStation(id: string, name = `Station ${id}`): CatalogStation {
  return { stationId: id, name, lat: 59.91, lon: 10.75, capacity: 20, isVirtual: false };
}

export function incident(partial: Partial<IncidentItem> = {}): IncidentItem {
  return {
    id: "inc-1",
    kind: "pickup_unavailable",
    state: "confirmed",
    suspendedFrom: null,
    firstObservedAt: iso(0),
    observedSince: iso(0),
    confirmedAt: iso(30),
    closeReason: null,
    ...partial,
  };
}

export function stationState(
  id: string,
  options: {
    bikes?: number;
    docks?: number;
    incidents?: IncidentItem[];
    sortKey?: string | null;
    primary?: StationState["decision"]["primaryCondition"];
    reasonText?: string | null;
  } = {},
): StationState {
  const bikes = options.bikes ?? 5;
  const incidents = options.incidents ?? [];
  const primary = options.primary ?? (incidents.length ? "pickup_unavailable" : "ok");
  return {
    availability: {
      stationId: id,
      isInstalled: true,
      isRenting: true,
      isReturning: true,
      bikesAvailable: bikes,
      docksAvailable: options.docks ?? 20 - bikes,
      vehicleTypes: [{ vehicleTypeId: "bike", count: bikes }],
      sourceUpdatedAt: iso(0),
      stationReportedAt: iso(3),
      quality: "valid",
      qualityFlags: [],
    },
    decision: {
      stationId: id,
      conditions: [primary],
      primaryCondition: primary,
      unverifiableReason: null,
      reasonCodes: [],
      reasonText: options.reasonText ?? null,
      facts: null,
      quality: "valid",
      incidents,
      policyVersion: "1.0.0+test",
      sortKey: options.sortKey ?? null,
    },
  };
}

export function feedHealth(state: FeedHealth["state"] = "fresh"): FeedHealth {
  return {
    state,
    lastSourceUpdatedAt: iso(0),
    lastAttemptAt: iso(1),
    lastOutcome: "ok",
    coverage: { valid: 3, total: 3 },
    breakerOpenUntil: null,
    quarantinedFeeds: [],
  };
}

export function event(
  sequence: number,
  stations: StationState[],
  options: { streamId?: string; catalogVersion?: string; feed?: FeedHealth; at?: number; removed?: string[] } = {},
): StationBatchEvent {
  const streamId = options.streamId ?? "test:00000000";
  const at = options.at ?? sequence * 15;
  return {
    type: "station_batch",
    schemaVersion: 1,
    eventId: `${streamId}@${sequence}`,
    streamId,
    sequence,
    mode: "synthetic",
    cause: "publication",
    catalogVersion: options.catalogVersion ?? "v1",
    sourceUpdatedAt: iso(at - 10),
    receivedAt: iso(at - 1),
    committedAt: iso(at),
    stations,
    removedStationIds: options.removed ?? [],
    feedHealth: options.feed ?? feedHealth(),
  };
}

export function catalog(versions: Record<string, CatalogStation[]>): ReplayCatalog {
  return {
    schemaVersion: 1,
    systemId: "test",
    versions: Object.entries(versions).map(([catalogVersion, stations]) => ({ catalogVersion, stations })),
  };
}

export function manifest(partial: Partial<ReplayManifest> = {}): ReplayManifest {
  return {
    schemaVersion: 1,
    mode: "synthetic",
    streamId: "test:00000000",
    systemId: "test",
    timezone: "Europe/Oslo",
    periodStart: iso(0),
    periodEnd: iso(3600),
    periodStartLocal: "2026-10-07T00:15:00+02:00",
    periodEndLocal: "2026-10-07T01:15:00+02:00",
    recordingHash: "0".repeat(64),
    eventsSha256: "0".repeat(64),
    catalogSha256: "0".repeat(64),
    gbfsVersion: "2.3",
    adapterVersion: "gbfs-2.3/1",
    engineVersion: "0.1.0",
    policyVersion: "1.0.0+test",
    eventSchemaVersion: 1,
    hashVersion: 1,
    seed: 42,
    parameters: null,
    incidents: [],
    eventCount: 0,
    ...partial,
  };
}

export function snapshot(
  sequence: number,
  options: { streamId?: string; stations?: StationState[]; catalogVersion?: string; feed?: FeedHealth } = {},
): Snapshot {
  const streamId = options.streamId ?? "test:00000000";
  return {
    schemaVersion: 1,
    streamId,
    mode: "synthetic",
    systemId: "test",
    seed: 42,
    cursor: `${streamId}@${sequence}`,
    eventSchemaVersion: 1,
    catalogVersion: options.catalogVersion ?? "v1",
    feedHealth: options.feed ?? feedHealth(),
    catalog: [catalogStation("A"), catalogStation("B")],
    stations: options.stations ?? [stationState("A")],
  };
}
