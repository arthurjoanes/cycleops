import { type ReactNode, useMemo, useState } from "react";
import ui from "../../components/ui/ui.module.css";
import type { ManifestIncident, ReplayManifest, StationBatchEvent } from "../../lib/contracts";
import { KIND_LABEL } from "../../lib/labels";
import { formatTime, zoneLabel, type Zone } from "../../lib/time";
import type { ReplayPlayer } from "../../streams/replayPlayer";
import styles from "./ReplayControls.module.css";

interface Marker {
  incident: ManifestIncident;
  offset: number;
  state: "confirmado" | "resolvido";
}

export function replayMarkers(manifest: ReplayManifest, events: readonly StationBatchEvent[], stationId: string | null = null): Marker[] {
  const list: Marker[] = [];
  const start = Date.parse(manifest.periodStart);
  // Manifest incident offsets begin at the first commit, which can follow the recording start.
  const first = Date.parse(events[0]?.committedAt ?? manifest.periodStart);
  const offset = (sequence: number, fallback: number) => {
    const event = events[sequence - 1];
    return event ? (Date.parse(event.committedAt) - start) / 1000 : (first - start) / 1000 + fallback;
  };
  for (const incident of manifest.incidents) {
    if (stationId && incident.stationId !== stationId) continue;
    list.push({ incident, offset: offset(incident.confirmedSequence, incident.confirmedOffsetSeconds), state: "confirmado" });
    if (incident.resolvedOffsetSeconds !== null && incident.resolvedSequence !== null) {
      list.push({ incident, offset: offset(incident.resolvedSequence, incident.resolvedOffsetSeconds), state: "resolvido" });
    }
  }
  return list.sort((a, b) => a.offset - b.offset || a.incident.stationId.localeCompare(b.incident.stationId));
}

export function ReplayTimeline(props: {
  player: ReplayPlayer;
  manifest: ReplayManifest;
  now: number;
  zone: Zone;
  selectedStation?: string | null;
  stationName(id: string): string;
  onIncident(incident: ManifestIncident): void;
  children?: ReactNode;
  shortcut?: ReactNode;
  replayDetails?: ReactNode;
}) {
  const { player, manifest, now, zone, selectedStation, stationName, onIncident } = props;
  const [onlySelected, setOnlySelected] = useState(true);
  const station = selectedStation && onlySelected ? selectedStation : null;
  const markers = useMemo(() => replayMarkers(manifest, player.events, station), [manifest, player.events, station]);
  const start = Date.parse(manifest.periodStart);
  const duration = player.durationSeconds;
  const current = Math.min(duration, Math.max(0, (now - start) / 1000));
  const point = (offset: number) => duration > 0 ? offset / duration * 1000 : 0;

  return (
    <div className={styles.timeline}>
      <label htmlFor="replay-time" className="visually-hidden">Linha do tempo</label>
      <div className={styles.scrubber}>
        <svg className={styles.markers} viewBox="0 0 1000 24" preserveAspectRatio="none" aria-hidden="true">
          {markers.map((marker) => (
            <line
              key={`${marker.incident.incidentId}-${marker.state}`}
              x1={point(marker.offset)} x2={point(marker.offset)}
              y1={marker.state === "confirmado" ? 1 : 13} y2={marker.state === "confirmado" ? 10 : 22}
              className={marker.state === "confirmado" ? styles.confirmedMarker : styles.resolvedMarker}
            />
          ))}
        </svg>
        <input
          id="replay-time"
          className={styles.range}
          type="range" min={0} max={duration} step={0.001} value={current}
          disabled={duration <= 0}
          aria-valuetext={`${formatTime(now, zone, true)}, ${zoneLabel(now, zone)}`}
          aria-describedby="replay-time-bounds"
          onChange={(event) => player.seekOffset(Number(event.target.value))}
          onKeyDown={(event) => {
            const movement: Record<string, number> = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -60, PageUp: 60 };
            let target: number;
            if (event.key === "Home") target = 0;
            else if (event.key === "End") target = duration;
            else if (event.key in movement) target = current + (movement[event.key] ?? 0);
            else return;
            event.preventDefault();
            player.seekOffset(Math.max(0, Math.min(duration, target)));
          }}
        />
      </div>
      <p id="replay-time-bounds" className={styles.bounds}>
        <span>{formatTime(start, zone, true)}</span>
        <span>{formatTime(manifest.periodEnd, zone, true)}</span>
      </p>
      <div className={styles.timelineTools}>
        {props.shortcut}
        <details className={styles.milestones}>
          <summary title="Explorar incidentes" aria-label="Explorar incidentes">Incidentes</summary>
          {props.children}
          {selectedStation && (
            <label className={styles.filter}>
              <input type="checkbox" checked={onlySelected} onChange={(event) => setOnlySelected(event.target.checked)} />
              Só {stationName(selectedStation)}
            </label>
          )}
          <p className={styles.legend}>Marcos dos incidentes ({markers.length})</p>
          <p className={styles.legend}>Superior: confirmação. Inferior: resolução. Mover na linha pausa o replay.</p>
          {markers.length ? (
            <ol className={styles.markerList} aria-label="Marcos dos incidentes">
              {markers.map((marker) => (
                <li key={`${marker.incident.incidentId}-${marker.state}`}>
                  <button
                    type="button" className={ui.button}
                    aria-label={`${marker.state}, ${stationName(marker.incident.stationId)}, ${KIND_LABEL[marker.incident.kind]}, ${formatTime(start + marker.offset * 1000, zone, true)}, ${zoneLabel(start + marker.offset * 1000, zone)}`}
                    onClick={() => {
                      player.seekOffset(marker.offset);
                      onIncident(marker.incident);
                    }}
                  >
                    <span className={marker.state === "confirmado" ? styles.confirmedText : styles.resolvedText}>{marker.state}</span>
                    <span className={styles.markerStation}>{stationName(marker.incident.stationId)}, {KIND_LABEL[marker.incident.kind]}</span>
                    <time dateTime={new Date(start + Math.round(marker.offset * 1000)).toISOString()}>
                      {formatTime(start + marker.offset * 1000, zone, true)}
                    </time>
                  </button>
                </li>
              ))}
            </ol>
          ) : <p className={styles.legend}>Esta estação não tem incidentes confirmados no período.</p>}
        </details>
        {props.replayDetails}
      </div>
    </div>
  );
}
