import { useState, useSyncExternalStore } from "react";
import ui from "../../components/ui/ui.module.css";
import { ActionIcon } from "../../components/ui/ActionIcon";
import type { ManifestIncident, ReplayManifest } from "../../lib/contracts";
import { KIND_LABEL } from "../../lib/labels";
import { formatTime, type Zone } from "../../lib/time";
import { useStations } from "../stations/store";
import type { ReplayPlayer, Speed } from "../../streams/replayPlayer";
import styles from "./ReplayControls.module.css";
import { replayMomentUrl } from "./replayMoment";
import { ReplayTimeline } from "./ReplayTimeline";

const SPEEDS: Speed[] = [1, 5, 20];

/** Re-renders the caller when the player plays, pauses, steps or changes speed. */
export function usePlayerState(player: ReplayPlayer | null) {
  return useSyncExternalStore(
    (listener) => player?.subscribe(listener) ?? (() => undefined),
    () => (player ? `${player.playing}|${player.speed}|${player.position}|${player.ended}` : ""),
  );
}

export function ReplayControls(props: {
  player: ReplayPlayer;
  manifest: ReplayManifest;
  zone: Zone;
  selectedStation?: string | null;
  onIncident(incident: ManifestIncident): void;
}) {
  const { player, manifest, zone } = props;
  usePlayerState(player);
  const catalog = useStations((s) => s.catalogs);
  const now = useStations((s) => s.now);
  // Incidents that resolve: the visitor sees both the confirmation and the resolution.
  const incidents = manifest.incidents.filter((i) => i.resolvedSequence !== null);
  const [chosen, setChosen] = useState(incidents[0]?.incidentId ?? "");
  const [share, setShare] = useState<{ url: string; copied: boolean } | null>(null);
  const name = (stationId: string) => {
    for (const stations of catalog.values()) {
      const found = stations.find((s) => s.stationId === stationId);
      if (found) return found.name;
    }
    return stationId;
  };
  const origin = Date.parse(player.events[0]?.committedAt ?? manifest.periodStart);

  return (
    <section className={styles.controls} aria-label="Controles do replay">
      <div className={styles.toolbar}>
        <div className={`${ui.group} ${styles.transport}`}>
          {player.ended ? (
            <button type="button" className={`${ui.button} ${ui.primary}`} onClick={() => player.start(player.speed)}>
              <ActionIcon name="restart" />
              Recomeçar
            </button>
          ) : (
            <button
              type="button"
              className={`${ui.button} ${ui.primary}`}
              onClick={() => (player.playing ? player.pause() : player.play())}
            >
              <ActionIcon name={player.playing ? "pause" : "play"} />
              {player.playing ? "Pausar" : "Continuar"}
            </button>
          )}
          <button type="button" className={`${ui.button} ${styles.iconButton}`} title="Passo" aria-label="Passo" onClick={() => player.step()} disabled={player.ended}>
            <ActionIcon name="step" />
          </button>
        </div>
        <div className={`${ui.group} ${styles.speed}`} role="group" aria-label="Velocidade">
          {SPEEDS.map((speed) => (
            <button
              key={speed}
              type="button"
              className={ui.button}
              aria-pressed={player.speed === speed}
              onClick={() => player.setSpeed(speed)}
            >
              {speed}×
            </button>
          ))}
        </div>
        <p className={styles.position}>
          <time className={styles.currentTime} dateTime={new Date(now).toISOString()}>{formatTime(now, zone, true)}</time>
        </p>
        <div className={styles.share}>
          <button type="button" className={`${ui.button} ${styles.iconButton}`} title="Copiar este momento" aria-label="Copiar este momento" onClick={() => {
            // Reapply through this instant so a delayed timer cannot leave its state behind the clock.
            player.seekOffset(player.offsetSeconds);
            const url = replayMomentUrl(window.location.href, manifest, player.offsetSeconds);
            void (async () => {
              try {
                await navigator.clipboard.writeText(url);
                setShare({ url, copied: true });
              } catch {
                setShare({ url, copied: false });
              }
            })();
          }}>
            <ActionIcon name="share" />
          </button>
        </div>
      </div>
      {share && <span role="status" className={styles.shareStatus}>{share.copied ? "Link copiado. O replay abre pausado neste horário." : "Copie o link abaixo para abrir este momento."}</span>}
      {share && !share.copied && (
        <label className={styles.shareFallback}>Link deste momento
          <input readOnly value={share.url} onFocus={(event) => event.target.select()} />
        </label>
      )}
      <ReplayTimeline
        player={player} manifest={manifest} now={now} zone={zone}
        selectedStation={props.selectedStation} stationName={name} onIncident={props.onIncident}
        shortcut={incidents.length > 0 && (
          <button
            type="button" className={`${ui.button} ${styles.quickJump}`}
            onClick={() => {
              const incident = incidents.find((item) => item.incidentId === chosen);
              if (incident) {
                player.goToIncident(incident);
                props.onIncident(incident);
              }
            }}
          >
            <ActionIcon name="step" /> Ir para o incidente
          </button>
        )}
        replayDetails={(
          <details className={styles.replayDetails}>
            <summary title="Detalhes do replay" aria-label="Detalhes do replay"><ActionIcon name="info" /></summary>
            <p>Evento {Math.min(player.position, player.events.length)} de {player.events.length}</p>
            {player.ended && <span className={ui.badge}>Replay terminado</span>}
          </details>
        )}
      >
        {incidents.length > 0 && (
          <div className={styles.incident}>
            <label htmlFor="incident-select" className="visually-hidden">
              Incidente
            </label>
            <select
              id="incident-select"
              className={styles.select}
              value={chosen}
              onChange={(e) => setChosen(e.target.value)}
            >
              {incidents.map((i) => (
                <option key={i.incidentId} value={i.incidentId}>
                  {name(i.stationId)}, {KIND_LABEL[i.kind]},{" "}
                  {formatTime(origin + i.confirmedOffsetSeconds * 1000, zone)}
                </option>
              ))}
            </select>
          </div>
        )}
      </ReplayTimeline>
    </section>
  );
}
