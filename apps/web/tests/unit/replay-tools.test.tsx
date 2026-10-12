import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { parseHash, patchHash } from "../../src/app/useHashState";
import { ReplayControls } from "../../src/features/replay/ReplayControls";
import { replayMomentOffset, replayMomentUrl } from "../../src/features/replay/replayMoment";
import { replayMarkers } from "../../src/features/replay/ReplayTimeline";
import { useStations } from "../../src/features/stations/store";
import type { Artifact } from "../../src/streams/artifact";
import { ReplayPlayer, type ReplaySink } from "../../src/streams/replayPlayer";
import { catalog, catalogStation, event, iso, manifest, stationState, T0 } from "./factory";

function makeArtifact(): Artifact {
  return {
    manifest: manifest({
      periodEnd: iso(90),
      eventsSha256: "a".repeat(64),
      eventCount: 3,
      incidents: [
        { incidentId: "a", stationId: "A", kind: "pickup_unavailable", confirmedSequence: 2, confirmedOffsetSeconds: 10.123, resolvedSequence: 3, resolvedOffsetSeconds: 55 },
        { incidentId: "b", stationId: "B", kind: "return_unavailable", confirmedSequence: 2, confirmedOffsetSeconds: 10.123, resolvedSequence: null, resolvedOffsetSeconds: null },
      ],
    }),
    catalog: catalog({ v1: [catalogStation("A"), catalogStation("B")] }),
    events: [event(1, [stationState("A"), stationState("B")], { at: 5 }), event(2, [], { at: 15.123 }), event(3, [], { at: 60 })],
  };
}

function setup() {
  const artifact = makeArtifact();
  const catalogs = new Map(artifact.catalog.versions.map((version) => [version.catalogVersion, version.stations]));
  const sink: ReplaySink = {
    reset: () => useStations.getState().begin(
      { mode: "synthetic", streamId: artifact.manifest.streamId, seed: 42, periodStart: artifact.manifest.periodStart, recordedOn: null },
      catalogs, "file", true,
    ),
    apply: (batch) => { useStations.getState().apply(batch); },
    applyNow: (batch) => { useStations.getState().apply(batch); },
    setNow: (now) => useStations.getState().setNow(now),
    setEnded: (ended) => useStations.getState().setEnded(ended),
  };
  const player = new ReplayPlayer(artifact, sink);
  player.seekOffset(0);
  return { artifact, player };
}

afterEach(() => vi.restoreAllMocks());

describe("shared replay moment", () => {
  it("preserves view, selection, zone, search and unknown query parameters", () => {
    const hash = "#/?mode=recorded&view=all&tab=map&station=537&zone=local&q=St.+Olav&measure=1";
    const next = patchHash(hash, { station: "412", q: "" });
    expect(next).toContain("measure=1");
    expect(next).not.toContain("q=");
    const { artifact } = setup();
    const url = new URL(replayMomentUrl(`https://example.com/cycleops/${hash}`, artifact.manifest, 15.123));
    const shared = parseHash(url.hash);
    expect(shared).toMatchObject({ mode: "synthetic", view: "all", tab: "map", station: "537", zone: "local", q: "St. Olav", at: "15.123", artifact: "a".repeat(64) });
    expect(url.hash).toContain("measure=1");
    expect(replayMomentOffset(shared, artifact.manifest)).toBe(15.123);
  });

  it("rejects another artifact and invalid, partial or out-of-period offsets", () => {
    const { artifact } = setup();
    const identity = artifact.manifest.eventsSha256;
    expect(() => replayMomentOffset({ artifact: "b".repeat(64), at: "15" }, artifact.manifest)).toThrow("outra versão");
    for (const at of ["-1", "NaN", "Infinity", "1e1", " 1", "15.1234", "90.001", ""]) {
      expect(() => replayMomentOffset({ artifact: identity, at }, artifact.manifest)).toThrow();
    }
    expect(() => replayMomentOffset({ artifact: identity, at: null }, artifact.manifest)).toThrow();
    expect(() => replayMomentOffset({ artifact: null, at: "15" }, artifact.manifest)).toThrow();
    expect(replayMomentOffset({ artifact: identity, at: "0" }, artifact.manifest)).toBe(0);
    expect(replayMomentOffset({ artifact: identity, at: "90" }, artifact.manifest)).toBe(90);
  });

  it("seeks on real elapsed time without advancing to a future commit", () => {
    const { player } = setup();
    expect(player.seekOffset(15.122)).toBe(true);
    expect(useStations.getState().lastSequence).toBe(1);
    expect(useStations.getState().now).toBe(T0 + 15_122);
    expect(player.seekOffset(15.123)).toBe(true);
    expect(useStations.getState().lastSequence).toBe(2);
    player.play();
    expect(player.seekOffset(40)).toBe(true);
    expect(player.playing).toBe(false);
    expect(useStations.getState().now).toBe(T0 + 40_000);
    expect(player.position).toBe(2);
    expect(player.seekOffset(0)).toBe(true);
    expect(player.position).toBe(0);
    expect(useStations.getState().now).toBe(T0);
    expect(player.seekOffset(90)).toBe(true);
    expect(player.position).toBe(3);
    expect(player.virtualNow()).toBe(T0 + 90_000);
    expect(useStations.getState().ended).toBe(true);
    for (const offset of [-1, 90.001, Infinity, NaN]) expect(player.seekOffset(offset)).toBe(false);
    expect(player.offsetSeconds).toBe(90);
    player.stop();
  });
});

describe("incident timeline", () => {
  it("keeps unresolved confirmations, exact offsets and station filtering", () => {
    const { artifact } = setup();
    expect(replayMarkers(artifact.manifest, artifact.events).map((marker) => [marker.incident.stationId, marker.state, marker.offset])).toEqual([
      ["A", "confirmado", 15.123], ["B", "confirmado", 15.123], ["A", "resolvido", 60],
    ]);
    expect(replayMarkers(artifact.manifest, artifact.events, "B")).toHaveLength(1);
  });

  it("offers a keyboard range, station filter and exact resolution jump", () => {
    const { player, artifact } = setup();
    const onIncident = vi.fn();
    render(<ReplayControls player={player} manifest={artifact.manifest} zone="oslo" selectedStation="A" onIncident={onIncident} />);
    const slider = screen.getByRole("slider", { name: "Linha do tempo" });
    fireEvent.keyDown(slider, { key: "ArrowRight" });
    expect(player.offsetSeconds).toBe(1);
    expect(player.playing).toBe(false);
    fireEvent.keyDown(slider, { key: "End" });
    expect(player.offsetSeconds).toBe(90);
    fireEvent.click(screen.getByText(/Marcos dos incidentes/));
    expect(screen.queryByRole("button", { name: /Station B, sem vagas/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("checkbox", { name: "Só Station A" }));
    expect(screen.getByRole("button", { name: /Station B, sem vagas/ })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: /resolvido, Station A, sem bicicletas/ }));
    expect(player.offsetSeconds).toBe(60);
    expect(onIncident).toHaveBeenCalledWith(artifact.manifest.incidents[0]);
    expect(slider).toHaveAttribute("aria-valuetext", expect.stringContaining("horário de Oslo"));
    player.stop();
  });

  it("copies a paused precise moment, with a selectable fallback if clipboard is denied", async () => {
    const { player, artifact } = setup();
    window.history.replaceState(null, "", "#/?station=A&zone=local&view=all&custom=kept");
    const write = vi.fn().mockRejectedValue(new Error("denied"));
    Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: write } });
    render(<ReplayControls player={player} manifest={artifact.manifest} zone="local" onIncident={() => undefined} />);
    act(() => player.seekOffset(15.123));
    fireEvent.click(screen.getByRole("button", { name: "Copiar este momento" }));
    await waitFor(() => expect(screen.getByLabelText("Link deste momento")).toBeVisible());
    const fallback = screen.getByLabelText<HTMLInputElement>("Link deste momento");
    expect(fallback.value).toContain("at=15.123");
    expect(fallback.value).toContain("custom=kept");
    expect(fallback.value).toContain("station=A");
    expect(player.playing).toBe(false);
    window.history.replaceState(null, "", "#/");
    player.stop();
  });
});
