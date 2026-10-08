import { create } from "zustand";
import type {
  CatalogStation,
  Condition,
  DataMode,
  FeedHealth,
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

export type Connection = "file" | "connected" | "reconnecting" | "suspect" | "offline";
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
    let catalog = next.catalog;
    if (event.catalogVersion !== next.catalogVersion) {
      const version = next.catalogs.get(event.catalogVersion) ?? [];
      catalog = new Map(version.map((s) => [s.stationId, s]));
    }
    for (const id of event.removedStationIds) {
      stations.delete(id);
      history?.delete(id);
    }
    for (const state of event.stations) {
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
  apply(events: readonly StationBatchEvent[]): ApplyResult[];
  setNow(now: number): void;
  setEnded(ended: boolean): void;
  setStatus(status: StationsStore["status"], error?: StationsStore["error"]): void;
  setConnection(connection: Connection): void;
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
  apply(events) {
    const { data, results } = applyEvents(get(), events, get().keepHistory);
    if (results.includes("applied")) set(data);
    return results;
  },
  setNow: (now) => set({ now }),
  setEnded: (ended) => set({ ended }),
  setStatus: (status, error = null) => set({ status, error }),
  setConnection: (connection) => set({ connection }),
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
