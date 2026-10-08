// Frontend load test (no network): a seeded generator sends batches straight into the
// store through the same coalescing path as the replay. Loaded on demand with
// #/?source=load, never part of the main bundle.
import { createEventBuffer, useStations } from "../features/stations/store";
import type { CatalogStation, StationBatchEvent, StationState } from "../lib/contracts";
import { renderCount } from "../lib/renderCount";
import type { StationEventSource } from "./source";

export interface LoadOptions {
  stations: number;
  fraction: number;
  intervalMs: number;
  batches: number;
  seed: number;
}

export interface BatchSample {
  sequence: number;
  changed: number;
  applyMs: number;
  /** Store update plus the React render and commit it causes. External store updates render
   * in a microtask queued during the update, so a microtask queued after it runs after them. */
  renderMs: number;
  /** From the batch leaving the generator to the next paint after the commit. */
  paintMs: number;
  rowRenders: number;
}

declare global {
  interface Window {
    __cycleopsLoad?: { samples: BatchSample[]; done: boolean; options: LoadOptions };
  }
}

function mulberry32(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function parseLoadOptions(params: URLSearchParams): LoadOptions {
  // The values come from the URL: clamped so a shared link cannot freeze the tab.
  const number = (key: string, fallback: number, min: number, max: number) =>
    Math.min(max, Math.max(min, Number(params.get(key) ?? fallback) || fallback));
  return {
    stations: Math.round(number("n", 267, 1, 10000)),
    fraction: number("f", 1, 0, 1),
    intervalMs: number("interval", 15000, 100, 60000),
    batches: Math.round(number("batches", 30, 1, 1000)),
    seed: Math.round(number("seed", 1, 1, 2 ** 31)),
  };
}

export function startLoad(options: LoadOptions): StationEventSource {
  const random = mulberry32(options.seed);
  const streamId = `load:${options.seed.toString(16).padStart(8, "0")}`;
  const t0 = Date.parse("2026-10-06T22:15:00Z");
  const catalog: CatalogStation[] = Array.from({ length: options.stations }, (_, i) => ({
    stationId: `L-${String(i + 1).padStart(4, "0")}`,
    name: `Carga ${String(i + 1).padStart(4, "0")}`,
    lat: 59.9 + random() * 0.05,
    lon: 10.66 + random() * 0.15,
    capacity: 20,
    isVirtual: false,
  }));
  const samples: BatchSample[] = [];
  window.__cycleopsLoad = { samples, done: false, options };
  useStations.getState().begin(
    { mode: "synthetic", streamId, seed: options.seed, periodStart: new Date(t0).toISOString(), recordedOn: null },
    new Map([["load", catalog]]),
    "file",
    false,
  );

  const pendingAt = new Map<number, number>();
  const buffer = createEventBuffer((events) => {
    const before = renderCount.rows;
    const start = performance.now();
    useStations.getState().apply(events);
    const applyMs = performance.now() - start;
    let renderMs = Number.NaN;
    queueMicrotask(() => {
      renderMs = performance.now() - start;
    });
    // Paint: next frame, then a task after it.
    requestAnimationFrame(() => {
      const channel = new MessageChannel();
      channel.port1.onmessage = () => {
        for (const e of events) {
          samples.push({
            sequence: e.sequence,
            changed: e.stations.length,
            applyMs,
            renderMs,
            paintMs: performance.now() - (pendingAt.get(e.sequence) ?? start),
            rowRenders: renderCount.rows - before,
          });
        }
        if (samples.length >= options.batches && window.__cycleopsLoad) window.__cycleopsLoad.done = true;
      };
      channel.port2.postMessage(null);
    });
  });

  let sequence = 0;
  const station = (s: CatalogStation, at: string): StationState => {
    const bikes = Math.floor(random() * 21);
    const empty = bikes === 0;
    const firstSeen = Math.floor(t0 / 1000) + Math.floor(random() * 600);
    return {
      availability: {
        stationId: s.stationId,
        isInstalled: true,
        isRenting: true,
        isReturning: true,
        bikesAvailable: bikes,
        docksAvailable: 20 - bikes,
        vehicleTypes: [{ vehicleTypeId: "bike", count: bikes }],
        sourceUpdatedAt: at,
        stationReportedAt: at,
        quality: "valid",
        qualityFlags: [],
      },
      decision: {
        stationId: s.stationId,
        conditions: [empty ? "pickup_unavailable" : "ok"],
        primaryCondition: empty ? "pickup_unavailable" : "ok",
        unverifiableReason: null,
        reasonCodes: empty ? ["CONFIRMED_EMPTY"] : [],
        reasonText: empty ? "sem bicicletas, confirmado com 3 publicações em 30 s" : null,
        facts: null,
        quality: "valid",
        incidents: empty
          ? [
              {
                id: `${s.stationId}-x`,
                kind: "pickup_unavailable",
                state: "confirmed",
                suspendedFrom: null,
                firstObservedAt: new Date(firstSeen * 1000).toISOString(),
                observedSince: new Date(firstSeen * 1000).toISOString(),
                confirmedAt: new Date(firstSeen * 1000 + 30000).toISOString(),
                closeReason: null,
              },
            ]
          : [],
        policyVersion: "load",
        sortKey: empty ? `0|${String(firstSeen).padStart(10, "0")}|${s.stationId}` : null,
      },
    };
  };
  const emit = () => {
    sequence += 1;
    const at = new Date(t0 + sequence * 15000).toISOString();
    const chosen = sequence === 1 ? catalog : catalog.filter(() => random() < options.fraction);
    const event: StationBatchEvent = {
      type: "station_batch",
      schemaVersion: 1,
      eventId: `${streamId}@${sequence}`,
      streamId,
      sequence,
      mode: "synthetic",
      cause: "publication",
      catalogVersion: "load",
      sourceUpdatedAt: at,
      receivedAt: at,
      committedAt: at,
      stations: chosen.map((s) => station(s, at)),
      removedStationIds: [],
      feedHealth: {
        state: "fresh",
        lastSourceUpdatedAt: at,
        lastAttemptAt: at,
        lastOutcome: "ok",
        coverage: { valid: options.stations, total: options.stations },
        breakerOpenUntil: null,
        quarantinedFeeds: [],
      },
    };
    pendingAt.set(sequence, performance.now());
    useStations.getState().setNow(Date.parse(at));
    buffer.push(event);
  };
  emit();
  const timer = setInterval(() => {
    if (sequence > options.batches) {
      clearInterval(timer);
      return;
    }
    emit();
  }, options.intervalMs);
  return {
    stop() {
      clearInterval(timer);
      buffer.clear();
    },
  };
}
