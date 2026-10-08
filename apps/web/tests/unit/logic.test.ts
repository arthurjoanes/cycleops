import fixture from "@contracts/fixtures/sort-keys.json";
import { describe, expect, it } from "vitest";
import { mainMessage } from "../../src/app/screenState";
import { compareSortKeys, frozenView, indicators, leadIncident, liveOrder } from "../../src/features/priorities/queue";
import { stationTone } from "../../src/features/stations/mapStyle";
import { spans } from "../../src/features/stations/timeline";
import { InvalidPayloadError, validateStationBatchEvent } from "../../src/lib/contracts";
import { fold } from "../../src/lib/search";
import { formatTime, utcOffset, zoneLabel } from "../../src/lib/time";
import { parseEvents } from "../../src/streams/artifact";
import { event, incident, iso, stationState } from "./factory";

describe("sortKey order", () => {
  it("matches the order the backend generated", () => {
    expect([...fixture.input].sort(compareSortKeys)).toEqual(fixture.sorted);
  });

  it("puts stations with a sortKey in order and leaves the others out", () => {
    const stations = new Map([
      ["B", stationState("B", { sortKey: "1|1791360000|B" })],
      ["A", stationState("A", { sortKey: "0|1791360015|A" })],
      ["C", stationState("C")],
    ]);
    expect(liveOrder(stations)).toEqual(["A", "B"]);
  });
});

describe("frozen queue", () => {
  it("keeps rows in place, marks the ones that left and counts the new ones", () => {
    const view = frozenView(["A", "B", "C"], ["C", "D", "A"]);
    expect(view.rows).toEqual([
      { id: "A", gone: false },
      { id: "B", gone: true },
      { id: "C", gone: false },
    ]);
    expect(view.added).toBe(1);
  });
});

describe("indicators", () => {
  it("counts confirmed incidents only", () => {
    const stations = new Map([
      ["A", stationState("A", { incidents: [incident()] })],
      ["B", stationState("B", { incidents: [incident({ state: "recovering" })] })],
      ["C", stationState("C", { incidents: [incident({ kind: "return_unavailable", id: "r" })] })],
    ]);
    expect(indicators(stations, null)).toMatchObject({ empty: 1, full: 1, disabled: 0 });
  });
});

describe("search", () => {
  it("finds Bjørvika when typing Bjorvika", () => {
    expect(fold("Bjørvika").includes(fold("Bjorvika"))).toBe(true);
    expect(fold("Ærtebakken Å")).toBe("aertebakken a");
    expect(fold("ØKERN")).toBe("okern");
  });
});

describe("time in Oslo", () => {
  it("follows the end of daylight saving time on 25/10/2026", () => {
    const before = "2026-10-25T00:30:00Z"; // 02:30 CEST
    const after = "2026-10-25T01:30:00Z"; // 02:30 CET
    expect(formatTime(before, "oslo")).toBe("02:30");
    expect(formatTime(after, "oslo")).toBe("02:30");
    expect(utcOffset(before, "oslo")).toBe("UTC+2");
    expect(utcOffset(after, "oslo")).toBe("UTC+1");
    expect(zoneLabel(after, "oslo")).toBe("horário de Oslo, UTC+1");
  });
});

describe("main message", () => {
  const base = { error: null, resyncing: false, source: "fresh" as const, staleSince: null };
  it("shows one message by precedence", () => {
    expect(mainMessage(base)).toBeNull();
    expect(mainMessage({ ...base, source: "late" })).toBeNull();
    expect(mainMessage({ ...base, source: "stale", staleSince: iso(0) })).toEqual({ kind: "stale", since: iso(0) });
    expect(mainMessage({ ...base, source: "quarantined" })?.kind).toBe("quarantined");
    expect(mainMessage({ ...base, source: "closed" })?.kind).toBe("closed");
    expect(mainMessage({ ...base, source: "closed", resyncing: true })?.kind).toBe("resync");
    expect(mainMessage({ ...base, source: "closed", resyncing: true, error: "bootstrap" })?.kind).toBe(
      "bootstrap_error",
    );
    expect(mainMessage({ ...base, error: "new_version" })?.kind).toBe("new_version");
  });
});

describe("timeline spans", () => {
  it("never draws across a gap and marks the closed period", () => {
    const points = [
      { at: iso(0), bikes: 3, docks: 7, condition: "ok" as const, unverifiable: false },
      { at: iso(60), bikes: 0, docks: 10, condition: "pickup_unavailable" as const, unverifiable: false },
      { at: iso(120), bikes: 0, docks: 10, condition: "unverifiable" as const, unverifiable: true },
    ];
    const sources = [
      { at: iso(0), state: "fresh" as const },
      { at: iso(180), state: "closed" as const },
    ];
    const list = spans(points, sources, Date.parse(iso(0)), Date.parse(iso(240)));
    expect(list.map((s) => [s.kind, s.bikes])).toEqual([
      ["data", 3],
      ["data", 0],
      ["gap", 0],
      ["closed", 0],
    ]);
  });
});

describe("contract validation", () => {
  it("accepts a valid event and rejects a broken one", () => {
    const good = event(1, [stationState("A")]);
    expect(validateStationBatchEvent(good)).toBe(true);
    expect(validateStationBatchEvent({ ...good, schemaVersion: 2 })).toBe(false);
    const missing: Record<string, unknown> = { ...good };
    delete missing.feedHealth;
    expect(validateStationBatchEvent(missing)).toBe(false);
  });

  it("rejects an artifact with one bad line", () => {
    const good = JSON.stringify(event(1, [stationState("A")]));
    expect(parseEvents(`${good}\n`)).toHaveLength(1);
    expect(() => parseEvents(`${good}\n{"type":"station_batch"}\n`)).toThrow(InvalidPayloadError);
  });
});

describe("leadIncident", () => {
  // The row shows the incident behind sortKey: lowest tier, then the oldest.
  it("picks the confirmed one over a newer candidate, and the tier an unverifiable came from", () => {
    const full = incident({ id: "full", kind: "return_unavailable", firstObservedAt: iso(0) });
    const empty = incident({ id: "empty", state: "candidate", firstObservedAt: iso(-60) });
    expect(leadIncident([empty, full])?.id).toBe("full");
    const suspended = incident({ id: "s", state: "unverifiable", suspendedFrom: "confirmed", firstObservedAt: iso(30) });
    expect(leadIncident([empty, suspended])?.id).toBe("s");
    const older = incident({ id: "older", kind: "return_unavailable", firstObservedAt: iso(-120) });
    expect(leadIncident([full, older])?.id).toBe("older");
  });
});

describe("stationTone", () => {
  // primaryCondition colors the map only outside the queue.
  it("takes the queue incident's state, and the condition otherwise", () => {
    const recovering = incident({ state: "recovering" });
    expect(stationTone(stationState("A", { incidents: [recovering], primary: "ok", sortKey: "0|x|A" }))).toBe("recovering");
    expect(stationTone(stationState("A", { incidents: [incident({ state: "candidate" })], sortKey: "1|x|A" }))).toBe("candidate");
    expect(stationTone(stationState("A", { incidents: [incident()], sortKey: "0|x|A" }))).toBe("empty");
    expect(stationTone(stationState("A", { primary: "ok" }))).toBe("ok");
  });
});
