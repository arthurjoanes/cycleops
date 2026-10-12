import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { CityTrend } from "../../src/features/analysis/CityTrend";
import { ReplaySummary } from "../../src/features/analysis/ReplaySummary";
import { CITY_SAMPLE_LIMIT } from "../../src/features/analysis/cityHistory";
import { formatObservedDuration, observationRanking, replayReport } from "../../src/features/analysis/recordingReport";
import { indicators } from "../../src/features/priorities/queue";
import { applyEvents, emptyData, useStations } from "../../src/features/stations/store";
import type { StationBatchEvent, StationState } from "../../src/lib/contracts";
import type { Artifact } from "../../src/streams/artifact";
import { catalog, catalogStation, event, feedHealth, incident, iso, manifest, snapshot, stationState } from "./factory";

const catalogs = new Map([
  ["v1", [catalogStation("A"), catalogStation("B")]],
  ["v2", [catalogStation("A")]],
]);

function artifact(events: StationBatchEvent[], end = 60): Artifact {
  return {
    manifest: manifest({ periodEnd: iso(end), eventCount: events.length }),
    catalog: catalog({ v1: [catalogStation("A"), catalogStation("B")], v2: [catalogStation("A")] }),
    events,
  };
}

function unavailable(station: StationState): StationState {
  return {
    ...station,
    decision: { ...station.decision, primaryCondition: "unverifiable", conditions: ["unverifiable"], unverifiableReason: "report_stale" },
  };
}

function timedRecording() {
  const withoutDocks = stationState("A", { bikes: 2 });
  withoutDocks.availability.docksAvailable = null;
  const events = [
    event(1, [stationState("A", { bikes: 0 }), stationState("B", { bikes: 5, docks: 0 })], { at: 10 }),
    { ...event(2, [], { at: 20, feed: feedHealth("stale") }), cause: "clock" as const },
    event(3, [unavailable(stationState("A", { bikes: 0 }))], { at: 30 }),
    event(4, [withoutDocks], { at: 40, feed: feedHealth("late") }),
    { ...event(5, [], { at: 50, feed: feedHealth("closed") }), cause: "clock" as const },
  ];
  return artifact(events);
}

describe("replay observation report", () => {
  it("cuts source gaps, station gaps and closed time, including the end of the manifest", () => {
    const report = replayReport(timedRecording());
    expect(report.durationMs).toBe(60_000);
    expect([report.sourceValidMs, report.sourceGapMs, report.sourceClosedMs]).toEqual([30_000, 20_000, 10_000]);
    expect([report.stationValidMs, report.stationGapMs, report.stationClosedMs]).toEqual([50_000, 30_000, 20_000]);
    const a = report.stations.find((row) => row.stationId === "A");
    const b = report.stations.find((row) => row.stationId === "B");
    expect(a).toMatchObject({ catalogMs: 50_000, validMs: 20_000, bikesObservedMs: 20_000, docksObservedMs: 10_000, emptyMs: 10_000, gapMs: 20_000, closedMs: 10_000 });
    expect(b).toMatchObject({ validMs: 30_000, fullMs: 30_000, gapMs: 10_000, closedMs: 10_000 });
  });

  it("excludes catalog absence and requires another observation after a station returns", () => {
    const report = replayReport(artifact([
      event(1, [stationState("A"), stationState("B", { bikes: 0, docks: 0 })], { at: 0 }),
      event(2, [], { at: 10, catalogVersion: "v2", removed: ["B"] }),
      event(3, [], { at: 20, catalogVersion: "v1" }),
      event(4, [stationState("B", { bikes: 1, docks: 19 })], { at: 25 }),
    ], 30));
    expect(report.stations.find((row) => row.stationId === "B")).toMatchObject({
      catalogMs: 20_000, validMs: 15_000, gapMs: 5_000, emptyMs: 10_000, fullMs: 10_000,
    });
  });

  it("uses the final state at one instant, counts physical zeros and never infers incidents", () => {
    const recording = artifact([
      event(1, [stationState("A", { bikes: 0, primary: "ok" })], { at: 0 }),
      event(2, [stationState("A", { bikes: 5, docks: 0, primary: "ok" })], { at: 0 }),
    ], 10);
    const report = replayReport(recording);
    expect(report.stations.find((row) => row.stationId === "A")).toMatchObject({ emptyMs: 0, fullMs: 10_000 });
    expect([report.confirmed, report.resolved]).toEqual([0, 0]);
    expect(observationRanking(report.stations, "empty")).toEqual([]);
    expect(observationRanking(report.stations, "full").map((row) => row.stationId)).toEqual(["A"]);
  });

  it("uses clock events for station unverifiability and rejects invalid quality", () => {
    const suspect = stationState("B", { bikes: 0 });
    suspect.availability.quality = "suspect";
    const report = replayReport(artifact([
      event(1, [stationState("A", { bikes: 0 }), suspect], { at: 0 }),
      { ...event(2, [unavailable(stationState("A", { bikes: 0 }))], { at: 10 }), cause: "clock" },
    ], 20));
    expect(report.stations.find((row) => row.stationId === "A")).toMatchObject({ emptyMs: 10_000, gapMs: 10_000 });
    expect(report.stations.find((row) => row.stationId === "B")).toMatchObject({ emptyMs: 0, gapMs: 20_000 });
  });

  it("excludes virtual stations and unknown return capacity from the dock denominator", () => {
    const recording = artifact([
      event(1, [stationState("A", { bikes: 0, docks: 0 }), stationState("B", { bikes: 2, docks: 0, primary: "return_capacity_unknown" })], { at: 0 }),
    ], 10);
    recording.catalog.versions[0]?.stations.forEach((station) => {
      if (station.stationId === "A") station.isVirtual = true;
    });
    const report = replayReport(recording);
    expect(report.stations.find((row) => row.stationId === "A")).toMatchObject({ emptyMs: 10_000, fullMs: 0, docksObservedMs: 0 });
    expect(report.stations.find((row) => row.stationId === "B")).toMatchObject({ validMs: 10_000, fullMs: 0, docksObservedMs: 0 });
    expect(observationRanking(report.stations, "full")).toEqual([]);
  });

  it("takes confirmation, resolution and elapsed time from the engine manifest", () => {
    const recording = artifact([event(1, [stationState("A")], { at: 0 })]);
    recording.manifest.incidents = [
      { incidentId: "x", stationId: "A", kind: "pickup_unavailable", confirmedOffsetSeconds: 10, confirmedSequence: 2, resolvedOffsetSeconds: 30, resolvedSequence: 4 },
      { incidentId: "y", stationId: "B", kind: "return_unavailable", confirmedOffsetSeconds: 15, confirmedSequence: 3, resolvedOffsetSeconds: null, resolvedSequence: null },
    ];
    const report = replayReport(recording);
    expect([report.confirmed, report.resolved]).toEqual([2, 1]);
    expect(report.incidents.map((row) => row.elapsedMs)).toEqual([20_000, null]);
    expect(formatObservedDuration(20_000)).toBe("20 s");
    expect(formatObservedDuration(75_000)).toBe("1 min 15 s");
  });

  it("uses incident commit instants when the first commit follows the manifest start", () => {
    const recording = artifact([
      event(1, [stationState("A")], { at: 10 }),
      event(2, [stationState("A", { incidents: [incident()] })], { at: 20 }),
      event(3, [stationState("A", { incidents: [incident({ state: "resolved" })] })], { at: 30 }),
    ]);
    recording.manifest.incidents = [
      { incidentId: "x", stationId: "A", kind: "pickup_unavailable", confirmedOffsetSeconds: 10, confirmedSequence: 2, resolvedOffsetSeconds: 20, resolvedSequence: 3 },
    ];
    const report = replayReport(recording);
    expect(report.incidents[0]).toMatchObject({ confirmedAt: Date.parse(iso(20)), resolvedAt: Date.parse(iso(30)), elapsedMs: 10_000 });
    expect(report.sourceGapMs).toBe(10_000);
  });

  it("rejects incomplete sequences instead of extrapolating the last valid observation", () => {
    expect(() => replayReport(artifact([event(1, [], { at: 0 }), event(3, [], { at: 10 })]))).toThrow("sequência incompleta");
  });
});

describe("bounded city history", () => {
  it("keeps the same counts as the priority indicators across additions, changes and removal", () => {
    const initial = emptyData("test:00000000", catalogs);
    const confirmed = stationState("A", { bikes: 0, incidents: [incident()] });
    const disabled = stationState("B", { primary: "pickup_disabled" });
    const first = applyEvents(initial, [event(1, [confirmed, disabled])]).data;
    expect(first.cityCounts).toEqual({ empty: 1, full: 0, disabled: 1 });
    const recovered = stationState("A", { incidents: [incident({ state: "recovering" })] });
    const next = applyEvents(first, [event(2, [recovered]), event(3, [], { removed: ["B"], catalogVersion: "v2" })]).data;
    const expected = indicators(next.stations, next.feedHealth?.coverage ?? null);
    expect(next.cityCounts).toEqual({ empty: expected.empty, full: expected.full, disabled: expected.disabled });
    expect(next.citySamples.map((point) => [point.empty, point.disabled])).toEqual([[1, 1], [0, 1], [0, 0]]);
  });

  it("retains gaps without plotting fake zeros and admits only accepted contiguous events", () => {
    const first = event(1, [stationState("A", { incidents: [incident()] })], { at: 10 });
    const next = applyEvents(emptyData("test:00000000", catalogs), [
      first, first,
      event(2, [], { at: 20, feed: feedHealth("stale") }),
      event(3, [], { at: 30, feed: feedHealth("late") }),
      event(4, [], { at: 40, feed: feedHealth("closed") }),
      event(6, [], { at: 60 }),
      event(5, [], { at: 50, streamId: "foreign:0" }),
    ]).data;
    expect(next.citySamples.map((point) => [point.sequence, point.available, point.empty])).toEqual([
      [1, true, 1], [2, false, 1], [3, true, 1], [4, false, 1],
    ]);
  });

  it("caps history, collapses the same instant and preserves unchanged station objects", () => {
    const station = stationState("A");
    const events = Array.from({ length: CITY_SAMPLE_LIMIT + 10 }, (_, index) => event(index + 1, index ? [] : [station], { at: index }));
    const data = applyEvents(emptyData("test:00000000", catalogs), events).data;
    expect(data.citySamples).toHaveLength(CITY_SAMPLE_LIMIT);
    expect(data.stations.get("A")).toBe(station);
    const final = applyEvents(data, [event(events.length + 1, [stationState("A", { incidents: [incident()] })], { at: events.length - 1 })]).data;
    expect(final.citySamples).toHaveLength(CITY_SAMPLE_LIMIT);
    expect(final.citySamples.at(-1)?.empty).toBe(1);
    expect(final.citySamples.at(-1)?.sequence).toBe(events.length + 1);
  });

  it("clears the series at mode changes, replay resets and snapshot resync", () => {
    const session = { mode: "synthetic" as const, streamId: "test:00000000", seed: 42, periodStart: iso(0), recordedOn: null };
    const store = useStations.getState();
    store.clear();
    store.begin(session, catalogs, "file", false);
    store.apply([event(1, [stationState("A")])]);
    expect(useStations.getState().citySamples).toHaveLength(1);
    store.begin(session, catalogs, "file", false);
    expect(useStations.getState().citySamples).toEqual([]);
    store.apply([event(1, [stationState("A")])]);
    store.beginSnapshot(snapshot(2, { stations: [stationState("A", { incidents: [incident()] })] }), session.streamId);
    expect(useStations.getState().citySamples).toEqual([]);
    expect(useStations.getState().cityCounts.empty).toBe(1);
    store.apply([event(3, [])]);
    expect(useStations.getState().citySamples[0]?.empty).toBe(1);
    store.clear();
    expect(useStations.getState().citySamples).toEqual([]);
  });
});

describe("analysis views", () => {
  beforeEach(() => useStations.getState().clear());

  it("shows coverage and observed denominators, and selects stations by id", () => {
    const select = vi.fn();
    render(<ReplaySummary artifact={timedRecording()} zone="oslo" onSelectStation={select} />);
    expect(screen.getByText("62,5%")).toBeInTheDocument();
    const empty = screen.getByRole("region", { name: "Tempo sem bicicletas" });
    expect(within(empty).getByText("50,0%")).toBeInTheDocument();
    fireEvent.click(within(empty).getByRole("button", { name: "Station A" }));
    expect(select).toHaveBeenCalledWith("A");
    expect(screen.getByText(/Os números cobrem todo esse período/)).toBeInTheDocument();
  });

  it("provides a table alternative and uses dashes in source gaps", () => {
    act(() => {
      useStations.getState().begin({ mode: "synthetic", streamId: "test:00000000", seed: 42, periodStart: iso(0), recordedOn: null }, catalogs, "file", false);
      useStations.getState().apply([
        event(1, [stationState("A", { incidents: [incident()] })], { at: 0 }),
        event(2, [], { at: 15, feed: feedHealth("stale") }),
      ]);
    });
    render(<CityTrend zone="oslo" />);
    expect(screen.getByRole("img", { name: /Valores na tabela/ })).toBeInTheDocument();
    const table = screen.getByRole("region", { name: "Valores dos indicadores" });
    const rows = within(table).getAllByRole("row");
    expect(within(rows[2] as HTMLElement).getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["vencida", "—", "—", "—", "—"]);
    fireEvent.click(screen.getByRole("button", { name: "Cobertura válida" }));
    expect(screen.getByRole("img", { name: /Cobertura válida em percentual/ })).toBeInTheDocument();
  });
});
