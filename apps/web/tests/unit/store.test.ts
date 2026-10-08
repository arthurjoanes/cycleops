import { describe, expect, it, vi } from "vitest";
import { applyEvents, createEventBuffer, emptyData } from "../../src/features/stations/store";
import { catalogStation, event, feedHealth, stationState } from "./factory";

const catalogs = new Map([
  ["v1", [catalogStation("A"), catalogStation("B")]],
  ["v2", [catalogStation("A")]],
]);
const start = () => emptyData("test:00000000", catalogs);

describe("applyEvents", () => {
  it("applies only contiguous sequences and is idempotent", () => {
    const first = event(1, [stationState("A"), stationState("B")]);
    const { data, results } = applyEvents(start(), [first]);
    expect(results).toEqual(["applied"]);
    expect(data.lastSequence).toBe(1);
    const again = applyEvents(data, [first]);
    expect(again.results).toEqual(["duplicate"]);
    expect(again.data).toBe(data);
  });

  it("reports a gap and keeps the state", () => {
    const { data } = applyEvents(start(), [event(1, [stationState("A")])]);
    const gap = applyEvents(data, [event(3, [stationState("A", { bikes: 0 })])]);
    expect(gap.results).toEqual(["gap"]);
    expect(gap.data.stations.get("A")?.availability.bikesAvailable).toBe(5);
  });

  it("rejects an event from another stream", () => {
    const foreign = applyEvents(start(), [event(1, [stationState("A")], { streamId: "other:1" })]);
    expect(foreign.results).toEqual(["foreign"]);
    expect(foreign.data.stations.size).toBe(0);
  });

  it("keeps the object of stations that did not change", () => {
    const { data } = applyEvents(start(), [event(1, [stationState("A"), stationState("B")])]);
    const before = data.stations.get("B");
    const next = applyEvents(data, [event(2, [stationState("A", { bikes: 1 })])]);
    expect(next.data.stations.get("B")).toBe(before);
    expect(next.data.stations.get("A")?.availability.bikesAvailable).toBe(1);
  });

  it("switches the catalog version and drops removed stations", () => {
    const { data } = applyEvents(start(), [event(1, [stationState("A"), stationState("B")])]);
    expect([...data.catalog.keys()]).toEqual(["A", "B"]);
    const next = applyEvents(data, [event(2, [], { catalogVersion: "v2", removed: ["B"] })]);
    expect([...next.data.catalog.keys()]).toEqual(["A"]);
    expect(next.data.stations.has("B")).toBe(false);
  });

  it("keeps a per-station history and the source timeline", () => {
    let { data } = applyEvents(start(), [event(1, [stationState("A")])]);
    ({ data } = applyEvents(data, [event(2, [stationState("A", { bikes: 0 })], { feed: feedHealth("stale" ) })]));
    expect(data.history.get("A")?.map((p) => p.bikes)).toEqual([5, 0]);
    expect(data.sourceTimeline.map((p) => p.state)).toEqual(["fresh", "stale"]);
    expect(data.monitoringStart).toBe(event(1, []).sourceUpdatedAt);
  });
});

describe("createEventBuffer", () => {
  // A step applies its event at once; the ones still in the window must go first.
  it("flush commits what waits, in order, and cancels the scheduled commit", () => {
    vi.useFakeTimers();
    try {
      const committed: number[][] = [];
      const buffer = createEventBuffer((batch) => committed.push(batch.map((e) => e.sequence)));
      buffer.push(event(1, [stationState("A")]));
      buffer.push(event(2, [stationState("A")]));
      buffer.flush();
      expect(committed).toEqual([[1, 2]]);
      vi.runAllTimers();
      expect(committed).toEqual([[1, 2]]);
      buffer.push(event(3, [stationState("A")]));
      vi.runAllTimers();
      expect(committed).toEqual([[1, 2], [3]]);
    } finally {
      vi.useRealTimers();
    }
  });
});
