import { useEffect, useRef } from "react";
import { indicators, liveOrder } from "../features/priorities/queue";
import { usePlayerState } from "../features/replay/ReplayControls";
import { useStations } from "../features/stations/store";
import type { ReplayPlayer } from "../streams/replayPlayer";

const MIN_INTERVAL_MS = 10_000;

function summary(): string {
  const { stations, feedHealth } = useStations.getState();
  const queue = liveOrder(stations).length;
  const values = indicators(stations, feedHealth?.coverage ?? null);
  return `${queue} estações em prioridade; ${values.empty} sem bicicletas e ${values.full} sem vagas confirmadas.`;
}

/**
 * One polite summary at most every 10 s, never a narration of each event. During a replay
 * faster than 1× it stays quiet and speaks when the replay pauses or ends.
 */
export function LiveAnnouncer(props: { player: ReplayPlayer | null }) {
  usePlayerState(props.player);
  const paused = props.player ? !props.player.playing : true;
  const quiet = props.player ? props.player.playing && props.player.speed > 1 : false;
  const region = useRef<HTMLDivElement>(null);
  const last = useRef(0);
  const stations = useStations((s) => s.stations);
  const ended = useStations((s) => s.ended);
  // Nothing to say before the first event: "0 estações" would be false.
  const started = useStations((s) => s.lastSequence > 0);

  // Throttled with a trailing call: a burst ends with its last state announced.
  useEffect(() => {
    if (quiet || !started) return;
    const announce = () => {
      last.current = performance.now();
      if (region.current) region.current.textContent = summary();
    };
    const wait = MIN_INTERVAL_MS - (performance.now() - last.current);
    if (wait <= 0) {
      announce();
      return;
    }
    const timer = setTimeout(announce, wait);
    return () => clearTimeout(timer);
  }, [stations, quiet, started]);

  useEffect(() => {
    if ((paused || ended) && started && region.current) {
      last.current = performance.now();
      region.current.textContent = summary();
    }
  }, [paused, ended, started]);

  return <div ref={region} className="visually-hidden" aria-live="polite" aria-atomic="true" />;
}
