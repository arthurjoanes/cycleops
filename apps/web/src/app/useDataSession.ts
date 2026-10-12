import { useEffect, useRef, useState } from "react";
import { replayMomentOffset, ReplayMomentError } from "../features/replay/replayMoment";
import { createEventBuffer, useStations } from "../features/stations/store";
import { InvalidPayloadError, type ReplayManifest } from "../lib/contracts";
import { type ArtifactKind, artifactExists, loadArtifact } from "../streams/artifact";
import { ReplayPlayer, type ReplaySink } from "../streams/replayPlayer";
import type { StationEventSource } from "../streams/source";
import { parseHash, useHashState } from "./useHashState";

/** "stream" is the server's SSE (worker live or synthetic); the others are artifacts. */
export type DataKind = "stream" | ArtifactKind;

export interface DataSession {
  kind: DataKind | null;
  available: DataKind[];
  player: ReplayPlayer | null;
  manifest: ReplayManifest | null;
  replayLinkError: string | null;
}

// Compile-time constant: the Pages build drops the stream code and never calls the API.
const SERVER = import.meta.env.MODE !== "pages";

const EMPTY: DataSession = { kind: null, available: [], player: null, manifest: null, replayLinkError: null };

const hashParams = () => new URLSearchParams(window.location.hash.replace(/^#\/?\??/, ""));

/**
 * One data session at a time. With a server the page opens on its stream, with the
 * artifacts next to it; on Pages it opens on the recorded replay when one passed
 * acceptance, otherwise on the simulation. Switching is always explicit, and a switch
 * aborts what is in flight and clears cursor, store, history and subscriptions.
 */
export function useDataSession(requested: DataKind | null, retry: number, source: string | null): DataSession {
  const [session, setSession] = useState<DataSession>(EMPTY);
  const [route] = useHashState();
  const restored = useRef<string | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    let running: StationEventSource | null = null;
    let clock: ReturnType<typeof setInterval> | null = null;
    let available: DataKind[] = [];
    let buffer: ReturnType<typeof createEventBuffer> | null = null;
    useStations.getState().clear();

    void (async () => {
      try {
        if (source === "load") {
          // Frontend load test (#/?source=load&n=1000&f=0.3): no artifact, no network.
          const { parseLoadOptions, startLoad } = await import("../streams/loadSource");
          if (abort.signal.aborted) return;
          running = startLoad(parseLoadOptions(hashParams()));
          setSession(EMPTY);
          return;
        }
        const artifacts: ArtifactKind[] = (await artifactExists("recorded", abort.signal))
          ? ["recorded", "synthetic"]
          : ["synthetic"];
        available = SERVER ? ["stream", ...artifacts] : artifacts;
        const kind = requested && available.includes(requested) ? requested : (available[0] as DataKind);
        if (SERVER && kind === "stream") {
          const route = parseHash(window.location.hash);
          if (route.at !== null || route.artifact !== null) {
            useStations.getState().setStatus("idle");
            setSession({ ...EMPTY, kind, available, replayLinkError: "Este link de um momento precisa de um replay gravado ou de uma simulação. Escolha o arquivo para abri-lo." });
            return;
          }
          const { LiveSseSource } = await import("../streams/liveSse");
          if (abort.signal.aborted) return;
          const live = new LiveSseSource(hashParams().get("measure") === "1");
          running = live;
          live.start();
          setSession({ kind, available, player: null, manifest: null, replayLinkError: null });
          return;
        }
        const artifact = await loadArtifact(kind as ArtifactKind, abort.signal);
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
        const player = new ReplayPlayer(artifact, sink);
        running = player;
        // Ages and durations follow the virtual clock while it plays.
        clock = setInterval(() => {
          if (player.playing) useStations.getState().setNow(player.virtualNow());
        }, 1000);
        const link = parseHash(window.location.hash);
        let replayLinkError: string | null = null;
        try {
          const offset = replayMomentOffset(link, manifest);
          if (offset === null) player.start(5);
          else player.seekOffset(offset);
        } catch (error) {
          if (!(error instanceof ReplayMomentError)) throw error;
          player.seekOffset(0);
          replayLinkError = error.message;
        }
        restored.current = `${link.artifact ?? ""}|${link.at ?? ""}`;
        setSession({ kind, available, player, manifest, replayLinkError });
      } catch (error) {
        if (abort.signal.aborted) return;
        useStations
          .getState()
          .setStatus("idle", error instanceof InvalidPayloadError ? "new_version" : "bootstrap");
        // Keeps the list of sources so the header can still switch to another one.
        setSession({ ...EMPTY, available });
      }
    })();

    return () => {
      abort.abort();
      running?.stop();
      buffer?.clear();
      if (clock) clearInterval(clock);
    };
  }, [requested, retry, source]);

  // Changing a station, zone or search does not reload the artifact or move its clock.
  // History navigation to another shared moment applies once to the existing player.
  useEffect(() => {
    const player = session.player;
    if (!player || (requested !== null && requested !== session.kind)) return;
    const key = `${route.artifact ?? ""}|${route.at ?? ""}`;
    if (restored.current === key) return;
    restored.current = key;
    let replayLinkError: string | null = null;
    try {
      player.seekOffset(replayMomentOffset({ artifact: route.artifact, at: route.at }, player.artifact.manifest) ?? 0);
    } catch (error) {
      if (!(error instanceof ReplayMomentError)) throw error;
      player.seekOffset(0);
      replayLinkError = error.message;
    }
    setSession((current) => current.player === player ? { ...current, replayLinkError } : current);
  }, [session.player, session.kind, requested, route.artifact, route.at]);

  return session;
}
