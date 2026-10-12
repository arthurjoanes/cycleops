import ui from "../components/ui/ui.module.css";
import { ActionIcon } from "../components/ui/ActionIcon";
import { useStations } from "../features/stations/store";
import type { ReplayManifest } from "../lib/contracts";
import { MODE_LABEL, SOURCE_LABEL } from "../lib/labels";
import { formatDate, formatTime, type Zone } from "../lib/time";
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

function closeSessionData(details: HTMLDetailsElement | null) {
  if (!details) return;
  details.open = false;
  details.querySelector("summary")?.focus();
}

/** Mode and source health stay visible; provenance is available in the session panel.
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
  const loading = useStations((s) => s.status === "loading");
  const { zone } = props;

  const mode = session ? MODE_LABEL[session.mode] : "Carregando";
  const modeDetail =
    session?.mode === "synthetic"
      ? `seed ${session.seed ?? "?"}`
      : session?.recordedOn
        ? formatDate(session.recordedOn, zone)
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
  // The feed's last publication; a clock event has none of its own, and the server stream
  // keeps it current through the heartbeat.
  const published = feed?.lastSourceUpdatedAt ?? last?.sourceUpdatedAt ?? null;
  const publicationNote =
    session?.mode === "synthetic"
      ? "publicação simulada"
      : session?.mode === "recorded_replay"
        ? "publicação gravada"
        : "última publicação";

  const quality = feed ? (
    <>
      <span className={`${ui.badge} ${feed.state === "fresh" ? ui.ok : feed.state === "late" ? ui.warning : ui.stale}`}>fonte {SOURCE_LABEL[feed.state]}</span>
      <span className={styles.block}>cobertura {feed.coverage.valid}/{feed.coverage.total}</span>
    </>
  ) : "sem dado ainda";

  return (
    <header className={styles.header}>
      <div className={styles.brandBar}>
        <div className={styles.identity}>
          <img
            className={styles.mark}
            src={`${import.meta.env.BASE_URL}brand/cycleops-mark-light.svg`}
            width="48"
            height="32"
            alt=""
          />
          <div>
            <h1 className={styles.title}>CycleOps</h1>
            <p className={styles.context}>Oslo, Noruega</p>
          </div>
        </div>
        <div className={styles.switches}>
          {props.available.length > 1 && (
            <div className={`${ui.group} ${styles.sourceSwitch}`} role="group" aria-label="Fonte dos dados">
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
          {loading && props.available.length === 0 && (
            <div className={`${ui.group} ${styles.sourceSwitch} ${styles.loadingSources}`} aria-hidden="true">
              {(import.meta.env.MODE === "pages" ? ["recorded", "synthetic"] : ["stream", "recorded", "synthetic"]).map((kind) => (
                <button key={kind} type="button" className={ui.button} disabled tabIndex={-1}>
                  {kindLabel(kind as DataKind, import.meta.env.MODE !== "pages")}
                </button>
              ))}
            </div>
          )}
          <label className={styles.zoneSwitch}>
            <span>Horários</span>
            <select name="zone" title="Fuso dos horários" aria-label="Fuso dos horários" value={zone} onChange={(event) => props.onZone(event.target.value as Zone)}>
              <option value="oslo">Oslo</option>
              <option value="local">Local</option>
            </select>
          </label>
        </div>
      </div>
      <div className={styles.statusBar}>
        <p className={session?.mode === "synthetic" ? `${styles.mode} ${styles.simulated}` : styles.mode} data-testid="mode">
          <strong>{mode}</strong>
          {modeDetail && <span>{modeDetail}</span>}
        </p>
        <span className={`${styles.health} ${ui.badge} ${feed?.state === "fresh" ? ui.ok : feed?.state === "late" ? ui.warning : ui.stale}`}>
          Fonte {SOURCE_LABEL[feed?.state ?? "unknown"]}
        </span>
        <details className={styles.sessionData} onKeyDown={(event) => {
          if (event.key === "Escape") { event.preventDefault(); closeSessionData(event.currentTarget); }
        }}>
          <summary aria-label="Dados da sessão" title="Dados da sessão"><ActionIcon name="info" /><span>Dados</span></summary>
          <div className={styles.sessionPanel}>
            <div className={styles.panelHead}>
              <h2>Dados da sessão</h2>
              <button type="button" className={ui.button} aria-label="Fechar dados da sessão" title="Fechar dados da sessão" onClick={(event) => closeSessionData(event.currentTarget.closest("details"))}><ActionIcon name="close" /></button>
            </div>
            <dl className={styles.items}>
        <div className={styles.item}>
          <dt>{connection === "file" ? "Origem" : "Conexão"}</dt>
          <dd data-testid="connection">{connectionText}</dd>
        </div>
        <div className={styles.item}>
          <dt>Publicação</dt>
          <dd data-testid="publication">
            {published ? (
              <>
                {formatTime(published, zone, true)}{" "}
                <span className={styles.publicationNote}>
                  {publicationNote}
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
            {quality}
            {props.manifest && <p className={styles.block}>{howMade(props.manifest, zone)}</p>}
          </dd>
        </div>
            </dl>
          </div>
        </details>
      </div>
    </header>
  );
}
