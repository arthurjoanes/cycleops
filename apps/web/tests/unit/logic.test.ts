import fixture from "@contracts/fixtures/sort-keys.json";
import { gzipSync } from "node:zlib";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mainMessage, queueDimmed } from "../../src/app/screenState";
import { compareSortKeys, frozenView, indicators, leadIncident, liveOrder } from "../../src/features/priorities/queue";
import { stationTone } from "../../src/features/stations/mapStyle";
import { historySpans, spans } from "../../src/features/stations/timeline";
import { InvalidPayloadError, type StationHistory, validateStationBatchEvent } from "../../src/lib/contracts";
import { fold } from "../../src/lib/search";
import { formatTime, utcOffset, zoneLabel } from "../../src/lib/time";
import { loadArtifact, parseEvents } from "../../src/streams/artifact";
import { catalog, catalogStation, event, incident, iso, manifest, stationState } from "./factory";

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
  const base = { error: null, connection: "connected" as const, resyncing: false, source: "fresh" as const, staleSince: null };
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
    expect(mainMessage({ ...base, connection: "superseded", resyncing: true })?.kind).toBe("superseded");
    expect(mainMessage({ ...base, connection: "superseded", error: "bootstrap" })?.kind).toBe("bootstrap_error");
    // Reconnecting, suspect and offline only dim the queue; the message stays the source's.
    expect(mainMessage({ ...base, connection: "suspect", source: "stale", staleSince: iso(0) })?.kind).toBe("stale");
    expect(mainMessage({ ...base, connection: "offline" })).toBeNull();
  });

  it("dims the queue when the connection is lost or the source is not current", () => {
    expect(queueDimmed("fresh", "connected")).toBe(false);
    expect(queueDimmed("late", "file")).toBe(false);
    for (const connection of ["reconnecting", "suspect", "offline", "superseded"] as const) {
      expect(queueDimmed("fresh", connection)).toBe(true);
    }
    expect(queueDimmed("quarantined", "connected")).toBe(true);
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

describe("server history spans", () => {
  it("cuts at the server's gap and closed intervals, an open one running to the end", () => {
    const point = (at: number, bikes: number) => ({
      sourceUpdatedAt: iso(at),
      bikesAvailable: bikes,
      docksAvailable: 10 - bikes,
      quality: "valid" as const,
      primaryCondition: bikes ? ("ok" as const) : ("pickup_unavailable" as const),
    });
    const list = historySpans(
      {
        streamId: "test:00000000",
        stationId: "A",
        windowStart: iso(0),
        windowEnd: iso(300),
        points: [point(0, 3), point(60, 0)],
        incidents: [],
        gaps: [{ start: iso(120), end: iso(180) }],
        closed: [{ start: iso(240), end: null }],
      },
      Date.parse(iso(300)),
    );
    expect(list.map((s) => [s.kind, s.bikes, (s.start - Date.parse(iso(0))) / 1000])).toEqual([
      ["data", 3, 0],
      ["data", 0, 60],
      ["gap", 0, 120],
      ["data", 0, 180],
      ["closed", 0, 240],
    ]);
  });

  it("puts each point at its commit and takes the station's gaps only from the server", () => {
    // The station empties at 60 s (committed at 63 s), then drops out of the status: that
    // change commits at 125 s with the sourceUpdatedAt of 60 s.
    type Condition = StationHistory["points"][number]["primaryCondition"];
    const point = (updated: number, at: number, bikes: number, condition: Condition) => ({
      sourceUpdatedAt: iso(updated),
      at: iso(at),
      bikesAvailable: bikes,
      docksAvailable: 10 - bikes,
      quality: "valid" as const,
      primaryCondition: condition,
    });
    const points = [
      point(-8, -5, 3, "ok"),
      point(60, 63, 0, "pickup_unavailable"),
      point(60, 125, 0, "unverifiable"),
    ];
    const shape = (list: StationHistory["points"]) =>
      historySpans(
        {
          streamId: "test:00000000",
          stationId: "A",
          windowStart: iso(0),
          windowEnd: iso(180),
          points: list,
          incidents: [],
          gaps: [{ start: iso(125), end: null }],
          closed: [],
        },
        Date.parse(iso(180)),
      ).map((s) => [s.kind, s.bikes, (s.start - Date.parse(iso(0))) / 1000]);
    expect(shape(points)).toEqual([
      ["data", 3, 0],
      ["data", 0, 63],
      ["gap", 0, 125],
    ]);
    // A payload without `at` falls back to sourceUpdatedAt; the gap still starts at 125 s.
    expect(shape(points.map((p) => ({ ...p, at: undefined })))).toEqual([
      ["data", 3, 0],
      ["data", 0, 60],
      ["gap", 0, 125],
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

  // The three files must be one artifact: a mix from two deploys would freeze the screen.
  describe("loadArtifact", () => {
    afterEach(() => vi.unstubAllGlobals());
    const serve = (events: ReturnType<typeof event>[], eventCount = events.length) => {
      const files: Record<string, BodyInit> = {
        "manifest.json": JSON.stringify(manifest({ eventCount })),
        "catalog.json": JSON.stringify(catalog({ v1: [catalogStation("A")] })),
        "events.ndjson.gz": gzipSync(events.map((e) => JSON.stringify(e)).join("\n")),
      };
      vi.stubGlobal("fetch", async (url: string) => new Response(files[url.split("/").pop() ?? ""] ?? null));
    };

    it("loads three files that match", async () => {
      serve([event(1, [stationState("A")]), event(2, [])]);
      expect((await loadArtifact("synthetic")).events).toHaveLength(2);
    });

    it("refuses another stream, a hole in the sequence, an unknown catalog or a wrong count", async () => {
      for (const [events, count] of [
        [[event(1, []), event(2, [], { streamId: "other:00000000" })], 2],
        [[event(1, []), event(3, [])], 2],
        [[event(1, [], { catalogVersion: "v9" })], 1],
        [[event(1, [])], 2],
      ] as const) {
        serve([...events], count);
        await expect(loadArtifact("synthetic")).rejects.toThrow(InvalidPayloadError);
      }
    });
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
