import { describe, expect, it } from "vitest";
import { applyEvents, emptyData, type StreamData } from "../../src/features/stations/store";
import type { StationBatchEvent } from "../../src/lib/contracts";
import type { Artifact } from "../../src/streams/artifact";
import { ReplayPlayer, type ReplaySink, type Timers } from "../../src/streams/replayPlayer";
import { catalog, catalogStation, event, manifest, stationState, T0 } from "./factory";

/** Manual clock: nothing moves until the test advances it. */
class FakeTimers implements Timers {
  current = 0;
  private queue: { at: number; callback: () => void; id: number }[] = [];
  private next = 0;
  now() {
    return this.current;
  }
  setTimeout(callback: () => void, ms: number) {
    const id = (this.next += 1);
    this.queue.push({ at: this.current + ms, callback, id });
    return id;
  }
  clearTimeout(handle: unknown) {
    this.queue = this.queue.filter((t) => t.id !== handle);
  }
  advance(ms: number, delayed = false) {
    const target = this.current + ms;
    for (;;) {
      this.queue.sort((a, b) => a.at - b.at);
      const first = this.queue[0];
      if (!first || first.at > target) break;
      this.queue.shift();
      this.current = delayed ? target : first.at;
      first.callback();
    }
    this.current = target;
  }
}

function setup(coalesce = false) {
  const events: StationBatchEvent[] = [
    event(1, [stationState("A"), stationState("B")], { at: 0 }),
    event(2, [stationState("A", { bikes: 0 })], { at: 15 }),
    event(3, [], { at: 30, catalogVersion: "v2", removed: ["B"] }),
    event(4, [stationState("A", { bikes: 3 })], { at: 45 }),
  ];
  const artifact: Artifact = {
    manifest: manifest({
      incidents: [
        {
          incidentId: "x",
          kind: "pickup_unavailable",
          stationId: "A",
          confirmedSequence: 3,
          resolvedSequence: 4,
          confirmedOffsetSeconds: 30,
          resolvedOffsetSeconds: 45,
        },
      ],
      eventCount: events.length,
    }),
    catalog: catalog({ v1: [catalogStation("A"), catalogStation("B")], v2: [catalogStation("A")] }),
    events,
  };
  const catalogs = new Map(artifact.catalog.versions.map((v) => [v.catalogVersion, v.stations]));
  const state: { data: StreamData; now: number; ended: boolean; endedSequence: number | null } = {
    data: emptyData("test:00000000", catalogs),
    now: 0,
    ended: false,
    endedSequence: null,
  };
  let pending: StationBatchEvent[] = [];
  const sink: ReplaySink = {
    reset: () => {
      pending = [];
      state.data = emptyData("test:00000000", catalogs);
    },
    apply: (batch) => {
      if (coalesce) pending.push(...batch);
      else state.data = applyEvents(state.data, batch).data;
    },
    applyNow: (batch) => {
      state.data = applyEvents(state.data, [...pending, ...batch]).data;
      pending = [];
    },
    setNow: (now) => (state.now = now),
    setEnded: (ended) => {
      state.ended = ended;
      state.endedSequence = ended ? state.data.lastSequence : null;
    },
  };
  const timers = new FakeTimers();
  return { player: new ReplayPlayer(artifact, sink, timers), state, timers, artifact };
}

describe("ReplayPlayer", () => {
  it("plays on the virtual clock and switches the catalog version", () => {
    const { player, state, timers } = setup();
    player.start(1);
    timers.advance(0);
    expect(state.data.lastSequence).toBe(1);
    expect([...state.data.catalog.keys()]).toEqual(["A", "B"]);
    timers.advance(15_000);
    expect(state.data.lastSequence).toBe(2);
    timers.advance(15_000);
    expect([...state.data.catalog.keys()]).toEqual(["A"]);
  });

  it("goes faster at 20x and freezes business time on pause", () => {
    const { player, state, timers } = setup();
    player.start(20);
    timers.advance(750); // 15 s of virtual time
    expect(state.data.lastSequence).toBe(2);
    player.pause();
    const frozen = player.virtualNow();
    timers.advance(60_000);
    expect(player.virtualNow()).toBe(frozen);
    expect(state.data.lastSequence).toBe(2);
  });

  it("steps one event and stops there", () => {
    const { player, state } = setup();
    player.start(1);
    player.pause();
    player.step();
    player.step();
    expect(state.data.lastSequence).toBe(2);
    expect(player.playing).toBe(false);
    expect(state.now).toBe(T0 + 15_000);
  });

  it("commits the buffered events before announcing the paused point", () => {
    const { player, state, timers } = setup(true);
    player.start(1);
    timers.advance(0);
    expect(state.data.lastSequence).toBe(0);
    player.pause();
    expect(state.data.lastSequence).toBe(1);
    expect(state.now).toBe(T0);
  });

  it("flushes the final batch and bounds the clock when a browser timer wakes late", () => {
    const { player, state, timers } = setup(true);
    player.start(5);
    // A busy or background page can deliver its timer after every remaining event.
    timers.advance(60_000, true);
    expect(state.endedSequence).toBe(4);
    expect(state.now).toBe(T0 + 45_000);
    expect(player.virtualNow()).toBe(state.now);
    expect(player.playing).toBe(false);
  });

  it("goes to the incident with the manifest offset, replaying from the start", () => {
    const { player, state, artifact } = setup();
    player.start(1);
    const incident = artifact.manifest.incidents[0];
    if (!incident) throw new Error("fixture");
    player.goToIncident(incident);
    expect(state.data.lastSequence).toBe(3);
    expect(player.position).toBe(3);
    expect(state.now).toBe(T0 + 30_000);
  });

  it("ignores every control after stop, so an old player cannot write into a new session", () => {
    const { player, state, timers } = setup();
    player.start(1);
    timers.advance(0);
    player.stop();
    player.play();
    player.step();
    player.seek(3);
    player.start(5);
    timers.advance(60_000);
    expect(state.data.lastSequence).toBe(1);
    expect(player.playing).toBe(false);
  });

  it("ends after the last event", () => {
    const { player, state, timers } = setup();
    player.start(5);
    timers.advance(60_000);
    expect(state.data.lastSequence).toBe(4);
    expect(state.ended).toBe(true);
    expect(player.playing).toBe(false);
  });
});
