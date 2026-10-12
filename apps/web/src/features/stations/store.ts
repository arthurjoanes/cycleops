import { create } from "zustand";
import {
  appendCitySample,
  citySample,
  countCity,
  stationCityCounts,
  type CityCounts,
  type CitySample,
} from "../analysis/cityHistory";
import type {
  CatalogStation,
  Condition,
  DataMode,
  FeedHealth,
  Heartbeat,
  Snapshot,
  SourceState,
  StationBatchEvent,
  StationState,
} from "../../lib/contracts";

export interface Session {
  mode: DataMode;
  streamId: string;
  seed: number | null;
  /** Start of the recording or of the simulated period (ISO). */
  periodStart: string;
  /** "Replay gravado": the day it was recorded (ISO). */
  recordedOn: string | null;
}

export interface HistoryPoint {
  at: string;
  bikes: number | null;
  docks: number | null;
  condition: Condition;
  unverifiable: boolean;
}

export interface SourcePoint {
  at: string;
  state: SourceState;
}

/** "file" for the artifacts; the others for the server stream. superseded and ended
 * (invalid payload or failed bootstrap) are terminal. */
export type Connection =
  | "file"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "suspect"
  | "offline"
  | "superseded"
  | "ended";
export type ApplyResult = "applied" | "duplicate" | "gap" | "foreign";

/** Everything that comes from the event stream. */
export interface StreamData {
  streamId: string | null;
  lastSequence: number;
  catalogs: ReadonlyMap<string, readonly CatalogStation[]>;
  catalogVersion: string | null;
  catalog: ReadonlyMap<string, CatalogStation>;
  stations: ReadonlyMap<string, StationState>;
  feedHealth: FeedHealth | null;
  lastEvent: Pick<
    StationBatchEvent,
    "sequence" | "committedAt" | "sourceUpdatedAt" | "receivedAt" | "cause"
  > | null;
  /** sourceUpdatedAt of the first publication of the stream. */
  monitoringStart: string | null;
  history: ReadonlyMap<string, readonly HistoryPoint[]>;
  sourceTimeline: readonly SourcePoint[];
  cityCounts: CityCounts;
  citySamples: readonly CitySample[];
}

const DAY_MS = 24 * 60 * 60 * 1000;

export function emptyData(
  streamId: string | null,
  catalogs: ReadonlyMap<string, readonly CatalogStation[]> = new Map(),
): StreamData {
  return {
    streamId,
    lastSequence: 0,
    catalogs,
    catalogVersion: null,
    catalog: new Map(),
    stations: new Map(),
    feedHealth: null,
    lastEvent: null,
    monitoringStart: null,
    history: new Map(),
    sourceTimeline: [],
    cityCounts: { empty: 0, full: 0, disabled: 0 },
    citySamples: [],
  };
}

/**
 * Applies events in order. Only sequence == last + 1 goes in; an older or repeated one is
 * a duplicate, a jump is a gap, another streamId is foreign. Maps are copied once per call,
 * so stations that did not change keep their object and their selectors do not re-render.
 */
export function applyEvents(
  data: StreamData,
  events: readonly StationBatchEvent[],
  keepHistory = true,
): { data: StreamData; results: ApplyResult[] } {
  let stations: Map<string, StationState> | null = null;
  let history: Map<string, readonly HistoryPoint[]> | null = null;
  let next = data;
  const results: ApplyResult[] = [];
  for (const event of events) {
    if (event.streamId !== next.streamId) {
      results.push("foreign");
      continue;
    }
    if (event.sequence <= next.lastSequence) {
      results.push("duplicate");
      continue;
    }
    if (event.sequence !== next.lastSequence + 1) {
      results.push("gap");
      continue;
    }
    stations ??= new Map(data.stations);
    if (keepHistory) history ??= new Map(data.history);
    const cityCounts = { ...next.cityCounts };
    const updateCounts = (state: StationState | undefined, sign: 1 | -1) => {
      if (!state) return;
      const delta = stationCityCounts(state);
      cityCounts.empty += sign * delta.empty;
      cityCounts.full += sign * delta.full;
      cityCounts.disabled += sign * delta.disabled;
    };
    let catalog = next.catalog;
    if (event.catalogVersion !== next.catalogVersion) {
      const version = next.catalogs.get(event.catalogVersion) ?? [];
      catalog = new Map(version.map((s) => [s.stationId, s]));
    }
    for (const id of event.removedStationIds) {
      updateCounts(stations.get(id), -1);
      stations.delete(id);
      history?.delete(id);
    }
    for (const state of event.stations) {
      updateCounts(stations.get(state.decision.stationId), -1);
      updateCounts(state, 1);
      stations.set(state.decision.stationId, state);
      if (history) {
        const point: HistoryPoint = {
          at: event.committedAt,
          bikes: state.availability.bikesAvailable,
          docks: state.availability.docksAvailable,
          condition: state.decision.primaryCondition,
          unverifiable: state.decision.conditions.includes("unverifiable"),
        };
        history.set(state.decision.stationId, trim([...(history.get(state.decision.stationId) ?? []), point]));
      }
    }
    const timeline =
      next.sourceTimeline.at(-1)?.state === event.feedHealth.state
        ? next.sourceTimeline
        : [...next.sourceTimeline, { at: event.committedAt, state: event.feedHealth.state }];
    next = {
      ...next,
      lastSequence: event.sequence,
      catalogVersion: event.catalogVersion,
      catalog,
      stations,
      history: history ?? next.history,
      feedHealth: event.feedHealth,
      lastEvent: {
        sequence: event.sequence,
        committedAt: event.committedAt,
        sourceUpdatedAt: event.sourceUpdatedAt,
        receivedAt: event.receivedAt,
        cause: event.cause,
      },
      monitoringStart: next.monitoringStart ?? event.sourceUpdatedAt,
      sourceTimeline: timeline,
      cityCounts,
      citySamples: appendCitySample(next.citySamples, citySample(event.committedAt, event.sequence, event.feedHealth, cityCounts)),
    };
    results.push("applied");
  }
  return { data: next, results };
}

/** Keeps at most 24 h of history per station. */
function trim(points: HistoryPoint[]): HistoryPoint[] {
  const last = points.at(-1);
  if (!last) return points;
  const cutoff = Date.parse(last.at) - DAY_MS;
  const first = points.findIndex((p) => Date.parse(p.at) >= cutoff);
  return first > 0 ? points.slice(first) : points;
}

export interface StationsStore extends StreamData {
  session: Session | null;
  status: "idle" | "loading" | "ready";
  /** bootstrap: the data could not be loaded; new_version: a payload failed its schema. */
  error: "bootstrap" | "new_version" | null;
  connection: Connection;
  resyncing: boolean;
  /** Clock for ages and durations; in the replay modes it is the player's virtual clock. */
  now: number;
  ended: boolean;
  keepHistory: boolean;
  begin(
    session: Session,
    catalogs: ReadonlyMap<string, readonly CatalogStation[]>,
    connection: Connection,
    keepHistory: boolean,
  ): void;
  /**
   * Server stream: the snapshot replaces everything, cursor included. basis is the streamId
   * the store had when the snapshot was requested; if the store moved on meanwhile (another
   * stream or mode), the snapshot is late and is dropped. Returns whether it went in.
   */
  beginSnapshot(snapshot: Snapshot, basis: string | null): boolean;
  /** Heartbeat of the same stream: source state, its start and last publication. */
  applyHeartbeat(heartbeat: Heartbeat): boolean;
  /** Mode or stream switch: nothing of the previous session stays. */
  clear(): void;
  apply(events: readonly StationBatchEvent[]): ApplyResult[];
  setNow(now: number): void;
  setEnded(ended: boolean): void;
  setStatus(status: StationsStore["status"], error?: StationsStore["error"]): void;
  setConnection(connection: Connection): void;
  setResyncing(resyncing: boolean): void;
}

export const useStations = create<StationsStore>()((set, get) => ({
  ...emptyData(null),
  session: null,
  // The session starts loading on mount; starting here keeps the first paint from shifting.
  status: "loading",
  error: null,
  connection: "file",
  resyncing: false,
  now: 0,
  ended: false,
  keepHistory: true,
  begin(session, catalogs, connection, keepHistory) {
    set({
      ...emptyData(session.streamId, catalogs),
      session,
      connection,
      keepHistory,
      status: "ready",
      error: null,
      ended: false,
      now: Date.parse(session.periodStart),
    });
  },
  beginSnapshot(snapshot, basis) {
    if (get().streamId !== basis) return false;
    const now = Date.now();
    const at = new Date(now).toISOString();
    // The cursor was checked against streamId when the snapshot was received.
    const lastSequence = Number(snapshot.cursor.slice(snapshot.cursor.lastIndexOf("@") + 1));
    set({
      ...emptyData(snapshot.streamId, new Map([[snapshot.catalogVersion, snapshot.catalog]])),
      lastSequence,
      // Joined mid-stream: the first publication is unknown, and the next event is not it.
      // "" never equals an observedSince and is not replaced by applyEvents (only null is).
      monitoringStart: lastSequence > 0 ? "" : null,
      catalogVersion: snapshot.catalogVersion,
      catalog: new Map(snapshot.catalog.map((s) => [s.stationId, s])),
      stations: new Map(snapshot.stations.map((s) => [s.decision.stationId, s])),
      cityCounts: countCity(snapshot.stations),
      feedHealth: snapshot.feedHealth,
      // Without stateSince the cut is this page's start; the messages leave it out.
      sourceTimeline: [{ at: snapshot.feedHealth.stateSince ?? at, state: snapshot.feedHealth.state }],
      // periodStart: start of what this page holds. The timeline comes from /history.
      session: { mode: snapshot.mode, streamId: snapshot.streamId, seed: snapshot.seed, periodStart: at, recordedOn: null },
      keepHistory: false,
      status: "ready",
      // A payload that failed its schema still asks for a reload after a resync.
      error: get().error === "new_version" ? "new_version" : null,
      resyncing: false,
      ended: false,
      now,
    });
    return true;
  },
  applyHeartbeat(heartbeat) {
    const { streamId, feedHealth, sourceTimeline } = get();
    if (heartbeat.streamId !== streamId || !feedHealth) return false;
    const same = feedHealth.state === heartbeat.feedState;
    // A heartbeat without stateSince (older server) keeps the start only while the state holds.
    const stateSince = heartbeat.stateSince ?? (same ? (feedHealth.stateSince ?? null) : null);
    if (
      !same ||
      feedHealth.lastSourceUpdatedAt !== heartbeat.lastSourceUpdatedAt ||
      (feedHealth.stateSince ?? null) !== stateSince
    ) {
      set({
        feedHealth: { ...feedHealth, state: heartbeat.feedState, lastSourceUpdatedAt: heartbeat.lastSourceUpdatedAt, stateSince },
        sourceTimeline: same
          ? sourceTimeline
          : [...sourceTimeline, { at: stateSince ?? heartbeat.serverTime, state: heartbeat.feedState }],
      });
    }
    return true;
  },
  clear() {
    set({
      ...emptyData(null),
      session: null,
      status: "loading",
      error: null,
      connection: "file",
      resyncing: false,
      ended: false,
      now: 0,
    });
  },
  apply(events) {
    const { data, results } = applyEvents(get(), events, get().keepHistory);
    if (results.includes("applied")) set(data);
    return results;
  },
  setNow: (now) => set({ now }),
  setEnded: (ended) => set({ ended }),
  setStatus: (status, error = null) => set({ status, error }),
  setConnection: (connection) => set({ connection }),
  setResyncing: (resyncing) => set({ resyncing }),
}));

/**
 * Events are applied together: collected for 100 ms and committed in the next animation
 * frame. The 100 ms do not change the source cadence.
 */
export function createEventBuffer(commit: (events: StationBatchEvent[]) => void, windowMs = 100) {
  let pending: StationBatchEvent[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;
  let frame = 0;
  const cancel = () => {
    if (timer) clearTimeout(timer);
    cancelAnimationFrame(frame);
    timer = null;
  };
  /** Commits what is waiting, in order. Steps and jumps call it before applying, so an
   * event never reaches the store ahead of the ones still in the window. */
  const flush = () => {
    cancel();
    const batch = pending;
    pending = [];
    if (batch.length) commit(batch);
  };
  return {
    push(event: StationBatchEvent) {
      pending.push(event);
      timer ??= setTimeout(() => {
        frame = requestAnimationFrame(flush);
      }, windowMs);
    },
    flush,
    clear() {
      cancel();
      pending = [];
    },
  };
}
