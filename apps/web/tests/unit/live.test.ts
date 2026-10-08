import { act, render, renderHook } from "@testing-library/react";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { queueDimmed } from "../../src/app/screenState";
import { type DataKind, useDataSession } from "../../src/app/useDataSession";
import { StationDetails } from "../../src/features/stations/StationDetails";
import { useStations } from "../../src/features/stations/store";
import type { Heartbeat, Snapshot } from "../../src/lib/contracts";
import { fetchHistory } from "../../src/streams/api";
import { LiveSseSource } from "../../src/streams/liveSse";
import { event, feedHealth, iso, snapshot, stationState } from "./factory";

/** EventSource the test drives by hand: open, messages, errors. */
class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static all: FakeEventSource[] = [];
  readyState = FakeEventSource.CONNECTING;
  private listeners = new Map<string, ((event: Event) => void)[]>();

  constructor(readonly url: string) {
    FakeEventSource.all.push(this);
  }

  get after(): string | null {
    return new URL(this.url, "http://localhost").searchParams.get("after");
  }

  addEventListener(type: string, listener: (event: Event) => void) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  close() {
    this.readyState = FakeEventSource.CLOSED;
  }

  open() {
    this.readyState = FakeEventSource.OPEN;
    this.dispatch("open", new Event("open"));
  }

  send(type: string, data: unknown) {
    const text = typeof data === "string" ? data : JSON.stringify(data);
    this.dispatch(type, new MessageEvent(type, { data: text }));
  }

  /** closed: the browser gave up (readyState CLOSED); otherwise it retries by itself. */
  fail(closed: boolean) {
    this.readyState = closed ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING;
    this.dispatch("error", new Event("error"));
  }

  private dispatch(type: string, event: Event) {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

const latest = (): FakeEventSource => {
  const es = FakeEventSource.all.at(-1);
  if (!es) throw new Error("no EventSource opened");
  return es;
};

// The API, by path. Snapshots are served in order; the last one repeats.
let snapshots: Snapshot[] = [];
let history: unknown = null;
let apiUp = true;
let snapshotHangs = false;
const requests: { url: string; signal: AbortSignal | null | undefined }[] = [];

const answer = (status: number, body: unknown) =>
  ({
    ok: status < 400,
    status,
    headers: new Headers({ "content-type": status < 400 ? "application/json" : "text/html" }),
    json: async () => body,
  }) as Response;

const fetchMock = vi.fn(async (input: string | URL | Request, init?: RequestInit): Promise<Response> => {
  const url = String(input);
  requests.push({ url, signal: init?.signal });
  if (url.endsWith("stations/snapshot")) {
    if (snapshotHangs) return new Promise(() => {});
    if (!apiUp) return answer(502, null);
    const next = snapshots.length > 1 ? snapshots.shift() : snapshots[0];
    return answer(200, next);
  }
  if (url.endsWith("feed-health")) return answer(apiUp ? 200 : 502, {});
  if (url.includes("/history")) return answer(200, history);
  return answer(404, null);
});

const calls = (path: string) => requests.filter((r) => r.url.endsWith(path)).length;
const store = () => useStations.getState();
const heartbeat = (partial: Partial<Heartbeat> = {}): Heartbeat => ({
  serverTime: new Date(Date.now()).toISOString(),
  streamId: "test:00000000",
  headSequence: 5,
  lastSourceUpdatedAt: iso(0),
  feedState: "fresh",
  ...partial,
});

const sources: LiveSseSource[] = [];

/** A started source with its first snapshot (cursor @5) and an open stream. */
async function started(): Promise<FakeEventSource> {
  const source = new LiveSseSource();
  sources.push(source);
  source.start();
  await vi.advanceTimersByTimeAsync(0);
  const es = latest();
  es.open();
  return es;
}

const flush = () => vi.advanceTimersByTimeAsync(200);

beforeEach(() => {
  FakeEventSource.all = [];
  snapshots = [snapshot(5)];
  history = null;
  apiUp = true;
  snapshotHangs = false;
  requests.length = 0;
  vi.stubGlobal("EventSource", FakeEventSource);
  vi.stubGlobal("fetch", fetchMock);
  // Jitter at its top: the delay is the backoff ceiling (1 s, 2 s, 4 s...).
  vi.spyOn(Math, "random").mockReturnValue(1);
  store().clear();
});

afterEach(() => {
  for (const source of sources.splice(0)) source.stop();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("LiveSseSource", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it("applies only last + 1 and reconnects from the last applied cursor on a gap", async () => {
    const es = await started();
    expect(es.after).toBe("test:00000000@5");
    expect(store().connection).toBe("connected");
    es.send("station_batch", event(6, [stationState("A", { bikes: 1 })]));
    es.send("station_batch", event(6, [stationState("A", { bikes: 9 })]));
    es.send("station_batch", event(4, [stationState("A", { bikes: 9 })]));
    await flush();
    expect(store().lastSequence).toBe(6);
    expect(store().stations.get("A")?.availability.bikesAvailable).toBe(1);

    es.send("station_batch", event(8, []));
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    expect(store().connection).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeEventSource.all).toHaveLength(2);
    expect(latest().after).toBe("test:00000000@6");
    expect(calls("stations/snapshot")).toBe(1);
    expect(store().lastSequence).toBe(6);
  });

  it("takes a new snapshot when the gap repeats", async () => {
    snapshots = [snapshot(5), snapshot(10, { stations: [stationState("A", { bikes: 7 })] })];
    const es = await started();
    es.send("station_batch", event(7, []));
    await vi.advanceTimersByTimeAsync(1000);
    const second = latest();
    second.open();
    second.send("station_batch", event(7, []));
    expect(store().resyncing).toBe(true);
    await vi.advanceTimersByTimeAsync(2000);
    expect(calls("stations/snapshot")).toBe(2);
    expect(store().resyncing).toBe(false);
    expect(store().lastSequence).toBe(10);
    expect(store().stations.get("A")?.availability.bikesAvailable).toBe(7);
    expect(latest().after).toBe("test:00000000@10");
  });

  it("rejects an event, a snapshot and a history of another stream", async () => {
    const es = await started();
    es.send("station_batch", event(6, [stationState("B")], { streamId: "other:11111111" }));
    await flush();
    expect(store().lastSequence).toBe(5);
    expect(store().stations.has("B")).toBe(false);
    expect(es.readyState).toBe(FakeEventSource.OPEN);

    // A snapshot asked while the store held another stream arrives late.
    expect(store().beginSnapshot(snapshot(9, { streamId: "other:11111111" }), "other:11111111")).toBe(false);
    expect(store().beginSnapshot(snapshot(9, { streamId: "other:11111111" }), null)).toBe(false);
    expect(store().streamId).toBe("test:00000000");

    history = {
      streamId: "other:11111111",
      stationId: "A",
      windowStart: iso(0),
      windowEnd: iso(3600),
      points: [],
      incidents: [],
      gaps: [],
      closed: [],
    };
    expect(await fetchHistory("A", "test:00000000", new AbortController().signal)).toBeNull();
    expect(await fetchHistory("A", "other:11111111", new AbortController().signal)).toEqual(history);
  });

  it("on resync_required closes and takes a new snapshot, even of a new generation", async () => {
    snapshots = [snapshot(5), snapshot(3, { streamId: "test:11111111", stations: [stationState("B")] })];
    const es = await started();
    es.send("resync_required", { reason: "unknown_stream", lastSequence: null });
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    expect(store().resyncing).toBe(true);
    await vi.advanceTimersByTimeAsync(1000);
    expect(store().streamId).toBe("test:11111111");
    expect([...store().stations.keys()]).toEqual(["B"]);
    expect(store().resyncing).toBe(false);
    expect(latest().after).toBe("test:11111111@3");
  });

  it("takes a new snapshot when the catalog version changes", async () => {
    snapshots = [snapshot(5), snapshot(6, { catalogVersion: "v2" })];
    const es = await started();
    es.send("station_batch", event(6, [], { catalogVersion: "v2" }));
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    await vi.advanceTimersByTimeAsync(1000);
    expect(store().catalogVersion).toBe("v2");
    expect(latest().after).toBe("test:00000000@6");
  });

  it("reopens after shutdown and capacity with growing backoff; superseded is terminal", async () => {
    const es = await started();
    es.send("stream_closed", { reason: "shutdown" });
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    expect(store().connection).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(999);
    expect(FakeEventSource.all).toHaveLength(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeEventSource.all).toHaveLength(2);
    expect(latest().after).toBe("test:00000000@5");
    expect(calls("feed-health")).toBe(1);

    // Nothing healthy came in between, so the next wait doubles.
    latest().open();
    latest().send("stream_closed", { reason: "capacity" });
    await vi.advanceTimersByTimeAsync(1999);
    expect(FakeEventSource.all).toHaveLength(2);
    await vi.advanceTimersByTimeAsync(1);
    expect(FakeEventSource.all).toHaveLength(3);

    const third = latest();
    third.open();
    third.send("stream_closed", { reason: "superseded" });
    expect(third.readyState).toBe(FakeEventSource.CLOSED);
    expect(store().connection).toBe("superseded");
    await vi.advanceTimersByTimeAsync(120_000);
    window.dispatchEvent(new Event("pageshow"));
    expect(FakeEventSource.all).toHaveLength(3);
  });

  it("marks the connection suspect after 40 s without heartbeat or event, then reconnects", async () => {
    const es = await started();
    await vi.advanceTimersByTimeAsync(30_000);
    es.send("heartbeat", heartbeat());
    await vi.advanceTimersByTimeAsync(39_999);
    expect(store().connection).toBe("connected");
    expect(es.readyState).toBe(FakeEventSource.OPEN);
    await vi.advanceTimersByTimeAsync(1);
    expect(store().connection).toBe("suspect");
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    await vi.advanceTimersByTimeAsync(1000);
    expect(FakeEventSource.all).toHaveLength(2);
    // Stays suspect on screen until the new stream opens.
    expect(store().connection).toBe("suspect");
    latest().open();
    expect(store().connection).toBe("connected");
  });

  it.each([
    ["station_batch", { ...event(6, []), schemaVersion: 2 }],
    ["station_batch", "{not json"],
    ["heartbeat", { serverTime: "yesterday" }],
    ["stream_closed", { reason: "bored" }],
  ])("an invalid %s closes the stream and does not reconnect", async (name, payload) => {
    const es = await started();
    es.send(name, payload);
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    expect(store().error).toBe("new_version");
    expect(store().connection).toBe("ended");
    expect(queueDimmed(store().feedHealth?.state ?? null, store().connection)).toBe(true);
    await vi.advanceTimersByTimeAsync(120_000);
    expect(FakeEventSource.all).toHaveLength(1);
    expect(calls("stations/snapshot")).toBe(1);
  });

  it("an invalid history closes the stream and does not reconnect", async () => {
    history = { streamId: "test:00000000", stationId: "A" };
    const es = await started();
    render(createElement(StationDetails, { id: "A", zone: "oslo" }));
    await act(() => vi.waitFor(() => expect(store().error).toBe("new_version")));
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    expect(store().connection).toBe("ended");
    await vi.advanceTimersByTimeAsync(120_000);
    expect(FakeEventSource.all).toHaveLength(1);
    expect(calls("stations/snapshot")).toBe(1);
    expect(store().error).toBe("new_version");
  });

  it("applies the events still in the window before a heartbeat", async () => {
    const es = await started();
    es.send("station_batch", event(6, [], { feed: feedHealth("late") }));
    es.send("heartbeat", heartbeat({ feedState: "stale" }));
    await flush();
    expect(store().lastSequence).toBe(6);
    expect(store().feedHealth?.state).toBe("stale");
    expect(store().sourceTimeline.map((p) => p.state)).toEqual(["fresh", "late", "stale"]);
  });

  it("applies feedState from the heartbeat and keeps ages on the server clock", async () => {
    const es = await started();
    const serverTime = new Date(Date.now() + 60_000).toISOString();
    es.send("heartbeat", heartbeat({ feedState: "stale", serverTime, lastSourceUpdatedAt: iso(-30) }));
    expect(store().feedHealth?.state).toBe("stale");
    expect(store().feedHealth?.lastSourceUpdatedAt).toBe(iso(-30));
    expect(store().sourceTimeline.at(-1)).toEqual({ at: serverTime, state: "stale" });
    await vi.advanceTimersByTimeAsync(1000);
    expect(store().now).toBe(Date.now() + 60_000);

    // A heartbeat of another stream: the server moved on, so a new snapshot.
    es.send("heartbeat", heartbeat({ streamId: "test:22222222" }));
    expect(store().resyncing).toBe(true);
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
  });

  it("goes through feed-health when closed, shows offline while the API is down", async () => {
    const es = await started();
    es.fail(false);
    expect(store().connection).toBe("reconnecting");
    expect(FakeEventSource.all).toHaveLength(1);
    es.open();

    apiUp = false;
    es.fail(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(store().connection).toBe("offline");
    await vi.advanceTimersByTimeAsync(1000);
    expect(calls("feed-health")).toBe(2);
    expect(FakeEventSource.all).toHaveLength(1);

    apiUp = true;
    await vi.advanceTimersByTimeAsync(2000);
    expect(store().connection).toBe("reconnecting");
    await vi.advanceTimersByTimeAsync(4000);
    expect(FakeEventSource.all).toHaveLength(2);
    expect(latest().after).toBe("test:00000000@5");
  });

  it("closes on pagehide and freeze, reopens on pageshow and resume", async () => {
    const es = await started();
    window.dispatchEvent(new Event("pagehide"));
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    await vi.advanceTimersByTimeAsync(60_000);
    expect(FakeEventSource.all).toHaveLength(1);
    window.dispatchEvent(new Event("pageshow"));
    expect(FakeEventSource.all).toHaveLength(2);
    expect(latest().after).toBe("test:00000000@5");

    document.dispatchEvent(new Event("freeze"));
    expect(latest().readyState).toBe(FakeEventSource.CLOSED);
    document.dispatchEvent(new Event("resume"));
    expect(FakeEventSource.all).toHaveLength(3);
    expect(calls("stations/snapshot")).toBe(1);
  });

  it("takes a new snapshot after more than 5 min hidden", async () => {
    snapshots = [snapshot(5), snapshot(40)];
    let visibility: DocumentVisibilityState = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
    const es = await started();

    visibility = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
    for (let i = 0; i < 10; i += 1) {
      await vi.advanceTimersByTimeAsync(30_000);
      es.send("heartbeat", heartbeat());
    }
    visibility = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
    expect(calls("stations/snapshot")).toBe(1); // 5 min exactly is not more than 5 min

    visibility = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
    for (let i = 0; i < 11; i += 1) {
      await vi.advanceTimersByTimeAsync(30_000);
      es.send("heartbeat", heartbeat());
    }
    visibility = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
    expect(store().resyncing).toBe(true);
    await vi.advanceTimersByTimeAsync(0);
    expect(calls("stations/snapshot")).toBe(2);
    expect(latest().after).toBe("test:00000000@40");
  });

  it("does not hold events while hidden, and a page that starts hidden counts as hidden", async () => {
    snapshots = [snapshot(5), snapshot(40)];
    let visibility: DocumentVisibilityState = "hidden";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
    const es = await started();
    // No animation frame runs in a hidden page: nothing may wait for one.
    for (let sequence = 6; sequence <= 9; sequence += 1) es.send("station_batch", event(sequence, []));
    expect(store().lastSequence).toBe(9);

    for (let i = 0; i < 11; i += 1) {
      await vi.advanceTimersByTimeAsync(30_000);
      es.send("heartbeat", heartbeat());
    }
    visibility = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);
    expect(calls("stations/snapshot")).toBe(2);
    expect(latest().after).toBe("test:00000000@40");
  });

  it("a resume while still hidden does not end the absence", async () => {
    snapshots = [snapshot(5), snapshot(40)];
    let visibility: DocumentVisibilityState = "visible";
    vi.spyOn(document, "visibilityState", "get").mockImplementation(() => visibility);
    await started();
    visibility = "hidden";
    document.dispatchEvent(new Event("visibilitychange"));
    document.dispatchEvent(new Event("freeze"));
    await vi.advanceTimersByTimeAsync(60_000);
    document.dispatchEvent(new Event("resume"));
    expect(FakeEventSource.all).toHaveLength(2);
    expect(calls("stations/snapshot")).toBe(1);

    const es = latest();
    es.open();
    for (let i = 0; i < 9; i += 1) {
      await vi.advanceTimersByTimeAsync(30_000);
      es.send("heartbeat", heartbeat());
    }
    visibility = "visible";
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(0);
    expect(calls("stations/snapshot")).toBe(2);
  });

  it("shows the bootstrap error when the first snapshot fails, and stops", async () => {
    apiUp = false;
    const source = new LiveSseSource();
    sources.push(source);
    source.start();
    await vi.advanceTimersByTimeAsync(60_000);
    expect(store().error).toBe("bootstrap");
    expect(store().connection).toBe("ended");
    expect(calls("stations/snapshot")).toBe(1);
    expect(FakeEventSource.all).toHaveLength(0);
  });
});

describe("data session", () => {
  const session = (kind: DataKind | null) =>
    renderHook(({ requested }) => useDataSession(requested, 0, null), { initialProps: { requested: kind } });

  it("opens the server stream first and never switches to a replay by itself", async () => {
    apiUp = false;
    const { result } = session(null);
    await act(() => vi.waitFor(() => expect(store().error).toBe("bootstrap")));
    expect(result.current.kind).toBe("stream");
    expect(result.current.available).toEqual(["stream", "synthetic"]);
    expect(requests.some((r) => r.url.includes("replay/synthetic"))).toBe(false);
  });

  it("a switch aborts requests, closes the stream and clears the store", async () => {
    const { rerender } = session("stream");
    await act(() => vi.waitFor(() => expect(FakeEventSource.all).toHaveLength(1)));
    const es = latest();
    act(() => {
      es.open();
      es.send("station_batch", event(6, [stationState("A", { bikes: 2 })]));
    });
    await act(() => vi.waitFor(() => expect(store().lastSequence).toBe(6)));

    rerender({ requested: "synthetic" });
    expect(es.readyState).toBe(FakeEventSource.CLOSED);
    const cleared = store();
    expect(cleared.streamId).toBeNull();
    expect(cleared.lastSequence).toBe(0);
    expect(cleared.stations.size).toBe(0);
    expect(cleared.history.size).toBe(0);
    expect(cleared.sourceTimeline).toEqual([]);
    expect(cleared.session).toBeNull();
    // The old stream cannot write into the new session.
    es.send("station_batch", event(7, [stationState("A")]));
    await new Promise((resolve) => setTimeout(resolve, 200));
    expect(store().lastSequence).toBe(0);

    // Back to the stream while its snapshot hangs, then away again: the request is aborted.
    snapshotHangs = true;
    rerender({ requested: "stream" });
    await act(() => vi.waitFor(() => expect(calls("stations/snapshot")).toBe(2)));
    rerender({ requested: "synthetic" });
    expect(requests.filter((r) => r.url.endsWith("stations/snapshot")).at(-1)?.signal?.aborted).toBe(true);
  });
});
