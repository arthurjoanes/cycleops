import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useDataSession } from "../../src/app/useDataSession";
import { useHashState } from "../../src/app/useHashState";
import { useStations } from "../../src/features/stations/store";
import { loadArtifact } from "../../src/streams/artifact";
import { catalog, catalogStation, event, iso, manifest, stationState, T0 } from "./factory";

vi.mock("../../src/streams/artifact", () => ({ artifactExists: vi.fn(async () => true), loadArtifact: vi.fn() }));

const identity = "a".repeat(64);
const urlHash = (at: number, digest = identity) => `#/?mode=recorded&station=A&artifact=${digest}&at=${at}`;

function change(hash: string) {
  window.history.replaceState(null, "", hash);
  window.dispatchEvent(new HashChangeEvent("hashchange"));
}

function session() {
  return renderHook(() => {
    const [hash] = useHashState();
    return useDataSession(hash.mode, 0, hash.source);
  });
}

beforeEach(() => {
  useStations.getState().clear();
  vi.mocked(loadArtifact).mockReset().mockResolvedValue({
    manifest: manifest({ mode: "recorded_replay", eventsSha256: identity, periodEnd: iso(90), eventCount: 3 }),
    catalog: catalog({ v1: [catalogStation("A")] }),
    events: [event(1, [stationState("A")], { at: 0 }), event(2, [], { at: 15.123 }), event(3, [], { at: 60 })],
  });
  window.history.replaceState(null, "", urlHash(15.123));
});

afterEach(() => {
  window.history.replaceState(null, "", "#/");
});

describe("replay link session", () => {
  it("restores once, keeps clock on station/zone edits and applies later hash navigation without refetching", async () => {
    const { result } = session();
    await waitFor(() => expect(result.current.player).not.toBeNull());
    const player = result.current.player;
    expect(player?.playing).toBe(false);
    expect(useStations.getState().now).toBe(T0 + 15_123);
    expect(useStations.getState().lastSequence).toBe(2);
    act(() => player?.seekOffset(35));
    act(() => change(`${urlHash(15.123)}&zone=local&view=all&q=another`));
    expect(result.current.player).toBe(player);
    expect(useStations.getState().now).toBe(T0 + 35_000);
    expect(loadArtifact).toHaveBeenCalledTimes(1);

    act(() => change(urlHash(60)));
    await waitFor(() => expect(useStations.getState().now).toBe(T0 + 60_000));
    expect(result.current.player).toBe(player);
    act(() => change(urlHash(15.123)));
    await waitFor(() => expect(useStations.getState().now).toBe(T0 + 15_123));
    expect(player?.playing).toBe(false);
    expect(loadArtifact).toHaveBeenCalledTimes(1);
  });

  it("refuses another artifact identity and opens from the start only after clearing the link", async () => {
    window.history.replaceState(null, "", urlHash(60, "b".repeat(64)));
    const { result } = session();
    await waitFor(() => expect(result.current.replayLinkError).toContain("outra versão"));
    expect(result.current.player?.playing).toBe(false);
    expect(useStations.getState().now).toBe(T0);
    expect(useStations.getState().lastSequence).toBe(1);
    act(() => change("#/?mode=recorded&station=A"));
    await waitFor(() => expect(result.current.replayLinkError).toBeNull());
    expect(useStations.getState().now).toBe(T0);
    expect(result.current.player?.playing).toBe(false);
    expect(loadArtifact).toHaveBeenCalledTimes(1);
  });
});
