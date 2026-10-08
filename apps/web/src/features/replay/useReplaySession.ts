import { useEffect, useState } from "react";
import { InvalidPayloadError, type ReplayManifest } from "../../lib/contracts";
import { createEventBuffer, useStations } from "../stations/store";
import { type ArtifactKind, artifactExists, loadArtifact } from "../../streams/artifact";
import { ReplayPlayer, type ReplaySink } from "../../streams/replayPlayer";

export interface ReplaySession {
  kind: ArtifactKind | null;
  available: ArtifactKind[];
  player: ReplayPlayer | null;
  manifest: ReplayManifest | null;
}

const EMPTY: ReplaySession = { kind: null, available: [], player: null, manifest: null };

/**
 * Loads one artifact and plays it into the store. The page opens on the recorded replay
 * when one passed acceptance, otherwise on the simulation; switching is explicit.
 */
export function useReplaySession(
  requested: ArtifactKind | null,
  retry: number,
  source: string | null,
): ReplaySession {
  const [session, setSession] = useState<ReplaySession>(EMPTY);

  useEffect(() => {
    const abort = new AbortController();
    let player: ReplayPlayer | null = null;
    let clock: ReturnType<typeof setInterval> | null = null;
    const store = useStations.getState();
    store.setStatus("loading");
    let load: { stop(): void } | null = null;
    let available: ArtifactKind[] = [];
    let buffer: ReturnType<typeof createEventBuffer> | null = null;

    void (async () => {
      try {
        if (source === "load") {
          // Frontend load test (#/?source=load&n=1000&f=0.3): no artifact, no network.
          const { parseLoadOptions, startLoad } = await import("../../streams/loadSource");
          if (abort.signal.aborted) return;
          load = startLoad(parseLoadOptions(new URLSearchParams(window.location.hash.replace(/^#\/?\??/, ""))));
          setSession(EMPTY);
          return;
        }
        available = (await artifactExists("recorded", abort.signal))
          ? ["recorded", "synthetic"]
          : ["synthetic"];
        const kind = requested && available.includes(requested) ? requested : (available[0] as ArtifactKind);
        const artifact = await loadArtifact(kind, abort.signal);
        if (abort.signal.aborted) return;
        const { manifest } = artifact;
        const catalogs = new Map(artifact.catalog.versions.map((v) => [v.catalogVersion, v.stations]));
        const begin = () =>
          useStations.getState().begin(
            {
              mode: manifest.mode,
              streamId: manifest.streamId,
              seed: manifest.seed,
              periodStart: manifest.periodStart,
              recordedOn: manifest.mode === "recorded_replay" ? manifest.periodStart : null,
            },
            catalogs,
            "file",
            true,
          );
        const queue = createEventBuffer((batch) => useStations.getState().apply(batch));
        buffer = queue;
        const sink: ReplaySink = {
          reset() {
            queue.clear();
            begin();
          },
          apply: (batch) => batch.forEach((e) => queue.push(e)),
          applyNow: (batch) => {
            queue.flush();
            useStations.getState().apply(batch);
          },
          setNow: (now) => useStations.getState().setNow(now),
          setEnded: (ended) => useStations.getState().setEnded(ended),
        };
        player = new ReplayPlayer(artifact, sink);
        // Ages and durations follow the virtual clock while it plays.
        clock = setInterval(() => {
          if (player?.playing) useStations.getState().setNow(player.virtualNow());
        }, 1000);
        player.start(5);
        setSession({ kind, available, player, manifest });
      } catch (error) {
        if (abort.signal.aborted) return;
        useStations
          .getState()
          .setStatus("idle", error instanceof InvalidPayloadError ? "new_version" : "bootstrap");
        // Keeps the list of artifacts so the header can still switch to the other one.
        setSession({ ...EMPTY, available });
      }
    })();

    return () => {
      abort.abort();
      player?.stop();
      buffer?.clear();
      load?.stop();
      if (clock) clearInterval(clock);
    };
  }, [requested, retry, source]);

  return session;
}
