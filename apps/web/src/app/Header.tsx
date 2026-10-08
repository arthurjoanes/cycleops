import ui from "../components/ui/ui.module.css";
import { useStations } from "../features/stations/store";
import type { ReplayManifest } from "../lib/contracts";
import { MODE_LABEL, SOURCE_LABEL } from "../lib/labels";
import { formatDate, formatTime, type Zone, zoneLabel } from "../lib/time";
import styles from "./Header.module.css";
import type { DataKind } from "./useDataSession";

const CONNECTION_LABEL = {
  connecting: "conectando",
  connected: "conectada",
  reconnecting: "reconectando",
  suspect: "suspeita",
  offline: "offline",
  superseded: "encerrada",
  ended: "encerrada",
} as const;

/** With a server the artifacts are named as files, so "Simulação" is not ambiguous. */
function kindLabel(kind: DataKind, server: boolean): string {
  if (kind === "stream") return "Servidor";
  const label = kind === "recorded" ? MODE_LABEL.recorded_replay : MODE_LABEL.synthetic;
  return server ? `${label} (arquivo)` : label;
}

function howMade(manifest: ReplayManifest, zone: Zone): string {
  if (manifest.mode === "recorded_replay") {
    return `gravado do feed de ${formatTime(manifest.periodStart, zone)} a ${formatTime(manifest.periodEnd, zone)}`;
  }
  const p = manifest.parameters ?? {};
  const stations = typeof p.stations === "number" ? ` ${p.stations} estações` : "";
  const cadence = typeof p.cadenceSeconds === "number" ? ` a cada ${p.cadenceSeconds} s` : "";
  return `gerado pelo motor:${stations}${cadence}`;
}

/** Four separate items, never a single status dot: mode, connection, publication, quality.
 * The mode label comes from the data (manifest or event), never from build config. */
export function Header(props: {
  zone: Zone;
  onZone(zone: Zone): void;
  available: DataKind[];
  kind: DataKind | null;
  onKind(kind: DataKind): void;
  manifest: ReplayManifest | null;
}) {
  const session = useStations((s) => s.session);
  const connection = useStations((s) => s.connection);
  const feed = useStations((s) => s.feedHealth);
  const last = useStations((s) => s.lastEvent);
  const { zone } = props;

  const mode = session ? MODE_LABEL[session.mode] : "Carregando";
  const modeDetail =
    session?.mode === "synthetic"
      ? `seed ${session.seed ?? "?"}`
      : session?.recordedOn
        ? `gravado em ${formatDate(session.recordedOn, zone)}`
        : null;
  const connectionText =
    connection !== "file"
      ? CONNECTION_LABEL[connection]
      : !session
        ? "carregando"
        : session.mode === "synthetic"
          ? `arquivo local, seed ${session.seed ?? "?"}`
          : session.recordedOn
            ? `arquivo gravado em ${formatDate(session.recordedOn, zone)}`
            : "arquivo local";
  // Artifact modes say how the artifact was made; the source state follows.
  const made = props.manifest ? howMade(props.manifest, zone) : null;
  // The feed's last publication; a clock event has none of its own, and the server stream
  // keeps it current through the heartbeat.
  const published = feed?.lastSourceUpdatedAt ?? last?.sourceUpdatedAt ?? null;
  const publicationNote =
    session?.mode === "synthetic"
      ? "horário simulado"
      : session?.mode === "recorded_replay"
        ? "horário original da publicação"
        : "última publicação";

  return (
    <header className={styles.header}>
      <h1 className={styles.title}>CycleOps</h1>
      <dl className={styles.items}>
        <div className={styles.item}>
          <dt>Modo</dt>
          <dd data-testid="mode">
            <strong>{mode}</strong>
            {modeDetail && <span className={ui.muted}> {modeDetail}</span>}
          </dd>
        </div>
        <div className={styles.item}>
          <dt>Conexão</dt>
          <dd data-testid="connection">{connectionText}</dd>
        </div>
        <div className={styles.item}>
          <dt>Publicação</dt>
          <dd data-testid="publication">
            {published ? (
              <>
                {formatTime(published, zone, true)}{" "}
                <span className={ui.muted}>
                  ({zoneLabel(published, zone)}; {publicationNote})
                </span>
              </>
            ) : (
              "nenhuma ainda"
            )}
          </dd>
        </div>
        <div className={styles.item}>
          <dt>Qualidade</dt>
          <dd data-testid="quality">
            {made && <span className={styles.block}>{made}</span>}
            {feed ? (
              <>
                <span className={feed.state === "late" ? `${ui.badge} ${ui.warning}` : undefined}>
                  fonte {SOURCE_LABEL[feed.state]}
                </span>{" "}
                <span className={ui.muted}>
                  cobertura {feed.coverage.valid}/{feed.coverage.total}
                </span>
              </>
            ) : (
              "sem dado ainda"
            )}
          </dd>
        </div>
      </dl>
      <div className={styles.switches}>
        {props.available.length > 1 && (
          <div className={ui.group} role="group" aria-label="Fonte dos dados">
            {props.available.map((kind) => (
              <button
                key={kind}
                type="button"
                className={ui.button}
                aria-pressed={props.kind === kind}
                onClick={() => props.onKind(kind)}
              >
                {kindLabel(kind, props.available.includes("stream"))}
              </button>
            ))}
          </div>
        )}
        <div className={ui.group} role="group" aria-label="Fuso dos horários">
          <button
            type="button"
            className={ui.button}
            aria-pressed={zone === "oslo"}
            onClick={() => props.onZone("oslo")}
          >
            Horário de Oslo
          </button>
          <button
            type="button"
            className={ui.button}
            aria-pressed={zone === "local"}
            onClick={() => props.onZone("local")}
          >
            Horário local
          </button>
        </div>
      </div>
    </header>
  );
}
