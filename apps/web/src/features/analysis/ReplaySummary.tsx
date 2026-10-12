import { useMemo, useState } from "react";
import { formatDate, formatTime, type Zone } from "../../lib/time";
import type { Artifact } from "../../streams/artifact";
import { formatObservedDuration, observationRanking, replayReport, type StationObservation } from "./recordingReport";
import styles from "./ReplaySummary.module.css";

const VISIBLE_ROWS = 8;

function percent(value: number, denominator: number): string {
  return denominator > 0 ? `${((value / denominator) * 100).toFixed(1).replace(".", ",")}%` : "—";
}

function Ranking(props: {
  rows: readonly StationObservation[];
  kind: "empty" | "full";
  onSelectStation: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { rows, kind, onSelectStation } = props;
  const field = kind === "empty" ? "emptyMs" : "fullMs";
  const denominator = kind === "empty" ? "bikesObservedMs" : "docksObservedMs";
  const label = kind === "empty" ? "Tempo sem bicicletas" : "Tempo sem vagas";
  return (
    <section className={styles.section}>
      <h4>{label}</h4>
      {rows.length ? (
        <>
          <div className={styles.tableWrap} role="region" aria-label={label} tabIndex={0}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">Estação</th>
                  <th scope="col">Tempo</th>
                  <th scope="col">% observado</th>
                  <th scope="col">Observado</th>
                </tr>
              </thead>
              <tbody>
                {(expanded ? rows : rows.slice(0, VISIBLE_ROWS)).map((station) => (
                  <tr key={station.stationId}>
                    <th scope="row">
                      <button type="button" className={styles.station} onClick={() => onSelectStation(station.stationId)}>
                        {station.name}
                      </button>
                    </th>
                    <td>{formatObservedDuration(station[field])}</td>
                    <td>{percent(station[field], station[denominator])}</td>
                    <td>{formatObservedDuration(station[denominator])}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > VISIBLE_ROWS ? (
            <button type="button" className={styles.expand} aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>
              {expanded ? "Mostrar primeiras 8" : `Mostrar todas (${rows.length})`}
            </button>
          ) : null}
        </>
      ) : <p>Nenhum intervalo observado com esse valor em zero.</p>}
    </section>
  );
}

export function ReplaySummary(props: {
  artifact: Artifact;
  zone: Zone;
  onSelectStation: (id: string) => void;
}) {
  const { artifact, zone, onSelectStation } = props;
  const report = useMemo(() => replayReport(artifact), [artifact]);
  const empty = useMemo(() => observationRanking(report.stations, "empty"), [report]);
  const full = useMemo(() => observationRanking(report.stations, "full"), [report]);
  const [allIncidents, setAllIncidents] = useState(false);
  const [allCoverage, setAllCoverage] = useState(false);
  const openStationMs = report.stationValidMs + report.stationGapMs;
  const coverage = percent(report.stationValidMs, openStationMs);
  const resolved = useMemo(() => report.incidents.filter((incident) => incident.resolvedAt !== null), [report]);
  const incidents = allIncidents ? report.incidents : resolved;
  return (
    <div className={styles.summary}>
      <div className={styles.intro}>
        <h3>{artifact.manifest.mode === "recorded_replay" ? "Resumo da gravação" : "Resumo da simulação"}</h3>
        <p>
          {formatDate(report.from, zone)}, {formatTime(report.from, zone, true)} até {formatDate(report.to, zone)}, {formatTime(report.to, zone, true)}.
          {" "}Os números cobrem todo esse período, independente da posição do replay.
        </p>
      </div>
      <dl className={styles.metrics}>
        <div><dt>Duração</dt><dd>{formatObservedDuration(report.durationMs)}</dd></div>
        <div><dt>Incidentes confirmados</dt><dd>{report.confirmed}</dd></div>
        <div><dt>Incidentes resolvidos</dt><dd>{report.resolved}</dd></div>
        <div><dt>Cobertura do período aberto</dt><dd>{coverage}</dd></div>
      </dl>
      <section className={styles.section}>
        <h4>Tempo da fonte</h4>
        <dl className={styles.sourceTimes}>
          <div><dt>Com dado válido</dt><dd>{formatObservedDuration(report.sourceValidMs)}</dd></div>
          <div><dt>Sem dado verificável</dt><dd>{formatObservedDuration(report.sourceGapMs)}</dd></div>
          <div><dt>Sistema fechado</dt><dd>{formatObservedDuration(report.sourceClosedMs)}</dd></div>
        </dl>
        <p>
          A cobertura soma o tempo com dado válido de cada estação e divide pelo tempo em que ela consta no catálogo com o sistema aberto.
          Intervalos sem dado entram nessa base; o período fechado e estações fora do catálogo ficam separados.
        </p>
      </section>
      <p className={styles.note}>
        Os rankings usam bicicletas e vagas em zero nas observações válidas.
        O percentual de cada linha usa apenas o tempo observado daquele campo; valores desconhecidos ficam fora.
        Estações virtuais e capacidade de devolução desconhecida ficam fora do ranking de vagas.
        A fila de prioridades mantém as confirmações e regras do motor.
      </p>
      <Ranking rows={empty} kind="empty" onSelectStation={onSelectStation} />
      <Ranking rows={full} kind="full" onSelectStation={onSelectStation} />
      <section className={styles.section}>
        <h4>Incidentes resolvidos</h4>
        <p>Tempo entre a confirmação e a resolução registradas pelo motor.</p>
        {incidents.length ? (
          <div className={styles.tableWrap} role="region" aria-label="Incidentes da gravação" tabIndex={0}>
            <table className={styles.table}>
              <thead><tr>
                <th scope="col">Estação</th><th scope="col">Tipo</th><th scope="col">Confirmação</th>
                <th scope="col">Resolução</th><th scope="col">Tempo</th>
              </tr></thead>
              <tbody>{incidents.map((incident) => (
                <tr key={incident.incidentId}>
                  <th scope="row"><button type="button" className={styles.station} onClick={() => onSelectStation(incident.stationId)}>{incident.name}</button></th>
                  <td>{incident.kind === "pickup_unavailable" ? "Sem bicicletas" : "Sem vagas"}</td>
                  <td>{formatTime(incident.confirmedAt, zone, true)}</td>
                  <td>{incident.resolvedAt === null ? "Sem resolução registrada" : formatTime(incident.resolvedAt, zone, true)}</td>
                  <td>{incident.elapsedMs === null ? "—" : formatObservedDuration(incident.elapsedMs)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <p>Nenhum incidente teve resolução registrada neste período.</p>}
        {report.incidents.length > resolved.length ? (
          <button type="button" className={styles.expand} aria-expanded={allIncidents} onClick={() => setAllIncidents((value) => !value)}>
            {allIncidents ? "Mostrar apenas resolvidos" : `Ver todos os confirmados (${report.confirmed})`}
          </button>
        ) : null}
      </section>
      <section className={styles.section}>
        <h4>Cobertura por estação</h4>
        <p>Tempo acumulado enquanto cada estação consta no catálogo.</p>
        <div className={styles.tableWrap} role="region" aria-label="Cobertura por estação" tabIndex={0}>
          <table className={styles.table}>
            <thead><tr>
              <th scope="col">Estação</th><th scope="col">Com dado válido</th><th scope="col">Sem dado</th><th scope="col">Fechado</th>
            </tr></thead>
            <tbody>{(allCoverage ? report.stations : report.stations.slice(0, VISIBLE_ROWS)).map((station) => (
              <tr key={station.stationId}>
                <th scope="row"><button type="button" className={styles.station} onClick={() => onSelectStation(station.stationId)}>{station.name}</button></th>
                <td>{formatObservedDuration(station.validMs)}</td>
                <td>{formatObservedDuration(station.gapMs)}</td>
                <td>{formatObservedDuration(station.closedMs)}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
        {report.stations.length > VISIBLE_ROWS ? (
          <button type="button" className={styles.expand} aria-expanded={allCoverage} onClick={() => setAllCoverage((value) => !value)}>
            {allCoverage ? "Mostrar primeiras 8" : `Ver cobertura de todas (${report.stations.length})`}
          </button>
        ) : null}
      </section>
    </div>
  );
}
