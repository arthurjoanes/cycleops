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
      <p className={styles.tableNote}>Percentual calculado sobre o tempo com contagem verificável.</p>
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
  const [period, setPeriod] = useState("all");
  const report = useMemo(() => {
    const from = Date.parse(artifact.manifest.periodStart);
    const to = Date.parse(artifact.manifest.periodEnd);
    const window = period === "all" ? undefined
      : period === "last15" ? { from: to - 900_000, to }
      : { from, to: from + Number(period) * 60_000 };
    return replayReport(artifact, window);
  }, [artifact, period]);
  const empty = useMemo(() => observationRanking(report.stations, "empty"), [report]);
  const full = useMemo(() => observationRanking(report.stations, "full"), [report]);
  const [allIncidents, setAllIncidents] = useState(false);
  const [allCoverage, setAllCoverage] = useState(false);
  const openStationMs = report.stationValidMs + report.stationGapMs;
  const coverage = percent(report.stationValidMs, openStationMs);
  const resolved = useMemo(() => report.incidents.filter((incident) => incident.resolvedAt !== null && incident.resolvedAt <= report.to), [report]);
  const incidents = allIncidents ? report.incidents : resolved;
  return (
    <div className={styles.summary}>
      <div className={styles.intro}>
        <h3>{artifact.manifest.mode === "recorded_replay" ? "Resumo da gravação" : "Resumo da simulação"}</h3>
        <p className={styles.period}>
          {formatDate(report.from, zone)}, {formatTime(report.from, zone, true)} até {formatDate(report.to, zone)}, {formatTime(report.to, zone, true)}.
        </p>
        <label className={styles.periodControl}>Período observado
          <select value={period} onChange={event => setPeriod(event.target.value)}>
            <option value="all">{artifact.manifest.mode === "synthetic" ? "Simulação inteira" : "Gravação inteira"}</option>
            <option value="15">Primeiros 15 minutos</option>
            <option value="30">Primeiros 30 minutos</option>
            <option value="last15">15 minutos finais</option>
          </select>
        </label>
      </div>
      <dl className={styles.metrics}>
        <div><dt>Duração</dt><dd>{formatObservedDuration(report.durationMs)}</dd></div>
        <div><dt>{period === "all" ? "Incidentes confirmados" : "Confirmações no recorte"}</dt><dd>{report.confirmed}</dd></div>
        <div><dt>{period === "all" ? "Incidentes resolvidos" : "Resoluções no recorte"}</dt><dd>{report.resolved}</dd></div>
        <div><dt>Cobertura do período aberto</dt><dd>{coverage}</dd></div>
      </dl>
      <section className={styles.section}>
        <h4>Tempo da fonte</h4>
        <div key={period} className={styles.sourceBar} role="img" aria-label={`Tempo da fonte: ${formatObservedDuration(report.sourceValidMs)} válido, ${formatObservedDuration(report.sourceGapMs)} sem dado verificável, ${formatObservedDuration(report.sourceClosedMs)} fechado. Valores abaixo.`}>
          {[
            { duration: report.sourceValidMs, className: styles.valid },
            { duration: report.sourceGapMs, className: styles.gap },
            { duration: report.sourceClosedMs, className: styles.closed },
          ].map(({ duration, className }) => (
            <span key={className} className={className} style={{ width: `${report.durationMs ? duration / report.durationMs * 100 : 0}%` }} />
          ))}
        </div>
        <dl className={styles.sourceTimes}>
          <div><dt>Com dado válido</dt><dd>{formatObservedDuration(report.sourceValidMs)}</dd></div>
          <div><dt>Sem dado verificável</dt><dd>{formatObservedDuration(report.sourceGapMs)}</dd></div>
          <div><dt>Sistema fechado</dt><dd>{formatObservedDuration(report.sourceClosedMs)}</dd></div>
        </dl>
      </section>
      <details className={styles.method}>
        <summary>Como ler a cobertura e os rankings</summary>
        <div>
          <p>
            A cobertura soma o tempo com dado válido de cada estação e divide pelo tempo em que ela consta no catálogo com o sistema aberto.
            Intervalos sem dado entram nessa base; o período fechado e estações fora do catálogo ficam separados.
          </p>
          <p>
            Os rankings usam bicicletas e vagas em zero nas observações válidas.
            O percentual de cada linha usa apenas o tempo observado daquele campo; valores desconhecidos ficam fora.
            Estações virtuais e capacidade de devolução desconhecida ficam fora do ranking de vagas.
            A fila de prioridades mantém as confirmações e regras do motor.
          </p>
          <p>O período selecionado não move o replay. Incidentes iniciados antes do recorte permanecem na lista; resoluções posteriores aparecem como “Após o recorte”.</p>
        </div>
      </details>
      <div className={styles.rankings}>
        <Ranking rows={empty} kind="empty" onSelectStation={onSelectStation} />
        <Ranking rows={full} kind="full" onSelectStation={onSelectStation} />
      </div>
      <section className={styles.section}>
        <h4>{allIncidents ? "Incidentes no período" : "Incidentes resolvidos"}</h4>
        <p>Tempo entre a confirmação e a resolução registradas pelo motor.</p>
        {incidents.length ? (
          <div className={styles.tableWrap} role="region" aria-label="Incidentes no período" tabIndex={0}>
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
                  <td>{incident.resolvedAt === null ? "Sem resolução registrada" : incident.resolvedAt > report.to ? "Após o recorte" : formatTime(incident.resolvedAt, zone, true)}</td>
                  <td>{incident.elapsedMs === null || (incident.resolvedAt !== null && incident.resolvedAt > report.to) ? "—" : formatObservedDuration(incident.elapsedMs)}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        ) : <p>Nenhum incidente teve resolução registrada neste período.</p>}
        {report.incidents.length > resolved.length ? (
          <button type="button" className={styles.expand} aria-expanded={allIncidents} onClick={() => setAllIncidents((value) => !value)}>
            {allIncidents ? "Mostrar apenas resolvidos" : `Ver todos no período (${report.incidents.length})`}
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
