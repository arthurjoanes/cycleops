import { useId, useState } from "react";
import { SOURCE_LABEL } from "../../lib/labels";
import { formatTime, type Zone } from "../../lib/time";
import { useStations } from "../stations/store";
import { CITY_SAMPLE_LIMIT, type CitySample } from "./cityHistory";
import styles from "./CityTrend.module.css";

const WIDTH = 680;
const HEIGHT = 190;
const LEFT = 88;
const TOP = 20;
const BOTTOM = HEIGHT - 26;

function coverage(sample: CitySample): number | null {
  return sample.available && sample.total > 0 ? (sample.valid / sample.total) * 100 : null;
}

export function CityTrend({ zone }: { zone: Zone }) {
  const samples = useStations((state) => state.citySamples);
  const feedState = useStations((state) => state.feedHealth?.state ?? "unknown");
  const [metric, setMetric] = useState<"incidents" | "coverage">("incidents");
  const hatch = useId();
  const from = samples[0]?.at ?? 0;
  const to = samples.at(-1)?.at ?? from;
  const max = metric === "coverage" ? 100 : Math.ceil(Math.max(2, ...samples.filter((point) => point.available).flatMap((point) => [point.empty, point.full, point.disabled])) / 2) * 2;
  const x = (at: number) => LEFT + ((at - from) / Math.max(1, to - from)) * (WIDTH - LEFT - 8);
  const y = (value: number) => BOTTOM - (value / max) * (BOTTOM - TOP);
  const path = (read: (sample: CitySample) => number | null) => {
    let line = "";
    let open = false;
    for (let index = 0; index < samples.length; index += 1) {
      const sample = samples[index] as CitySample;
      const value = sample.available ? read(sample) : null;
      if (value === null) {
        open = false;
        continue;
      }
      const end = samples[index + 1]?.at ?? sample.at;
      line += open ? ` V${y(value).toFixed(1)}` : ` M${x(sample.at).toFixed(1)} ${y(value).toFixed(1)}`;
      line += ` H${x(end).toFixed(1)}`;
      open = true;
    }
    return line.trim();
  };
  const latest = samples.at(-1);
  return (
    <section className={styles.trend}>
      <h3>Evolução dos indicadores</h3>
      <p>Incidentes confirmados, operação desabilitada e cobertura dos dados recebidos.</p>
      {!samples.length ? <p className={styles.empty}>Aguardando o próximo evento para iniciar o histórico.</p> : (
        <>
          <div className={styles.choices} role="group" aria-label="Série do gráfico">
            <button type="button" aria-pressed={metric === "incidents"} onClick={() => setMetric("incidents")}>Incidentes e operação</button>
            <button type="button" aria-pressed={metric === "coverage"} onClick={() => setMetric("coverage")}>Cobertura válida</button>
          </div>
          <figure className={styles.chart}>
            <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`${metric === "incidents" ? "Incidentes confirmados e operação desabilitada" : "Cobertura válida em percentual"}, de ${formatTime(from, zone, true)} a ${formatTime(to, zone, true)}. Valores na tabela abaixo.`}>
              <defs><pattern id={hatch} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <line x1="0" y1="0" x2="0" y2="6" className={styles.hatch} />
              </pattern></defs>
              <rect x={LEFT} y={TOP} width={WIDTH - LEFT - 8} height={BOTTOM - TOP} className={styles.background} />
              {[0, max / 2, max].map((value) => <g key={value}>
                <line x1={LEFT} x2={WIDTH - 8} y1={y(value)} y2={y(value)} className={styles.grid} />
                <text x={LEFT - 5} y={y(value) + 4} textAnchor="end" className={styles.axis}>{Math.round(value)}{metric === "coverage" ? "%" : ""}</text>
              </g>)}
              {samples.filter((point) => !point.available).map((point) => {
                const index = samples.indexOf(point);
                const end = samples[index + 1]?.at ?? point.at;
                return <rect key={point.sequence} x={Math.min(WIDTH - 12, x(point.at))} y={TOP} width={Math.max(4, x(end) - x(point.at))} height={BOTTOM - TOP} fill={`url(#${hatch})`} />;
              })}
              {metric === "incidents" ? <>
                <path d={path((point) => point.empty)} className={styles.emptyLine} />
                <path d={path((point) => point.full)} className={styles.fullLine} />
                <path d={path((point) => point.disabled)} className={styles.disabledLine} />
                {latest?.available ? <>
                  <circle cx={x(latest.at)} cy={y(latest.empty)} r="3" className={styles.emptyPoint} />
                  <circle cx={x(latest.at)} cy={y(latest.full)} r="3" className={styles.fullPoint} />
                  <circle cx={x(latest.at)} cy={y(latest.disabled)} r="3" className={styles.disabledPoint} />
                </> : null}
              </> : <>
                <path d={path(coverage)} className={styles.coverageLine} />
                {latest && coverage(latest) !== null ? <circle cx={x(latest.at)} cy={y(coverage(latest) as number)} r="3" className={styles.coveragePoint} /> : null}
              </>}
              <text x={LEFT} y={HEIGHT - 5} className={styles.axis}>{formatTime(from, zone, true)}</text>
              <text x={WIDTH - 8} y={HEIGHT - 5} textAnchor="end" className={styles.axis}>{formatTime(to, zone, true)}</text>
            </svg>
            <figcaption className={styles.legend}>
              {metric === "incidents" ? <>
                <span className={styles.emptyKey}>sem bicicletas confirmado</span>
                <span className={styles.fullKey}>sem vagas confirmado</span>
                <span className={styles.disabledKey}>operação desabilitada</span>
              </> : <span className={styles.coverageKey}>cobertura válida</span>}
              <span className={styles.gapKey}>fonte indisponível ou sistema fechado</span>
            </figcaption>
          </figure>
          <p className={styles.source}>Fonte agora: {SOURCE_LABEL[feedState]}. Intervalos sem dado verificável aparecem como lacunas.</p>
          <details className={styles.values}>
            <summary>Tabela dos indicadores ({samples.length} pontos)</summary>
            <div className={styles.tableWrap} role="region" aria-label="Valores dos indicadores" tabIndex={0}>
              <table className={styles.table}>
                <thead><tr>
                  <th scope="col">Horário</th><th scope="col">Fonte</th><th scope="col">Sem bicicletas</th><th scope="col">Sem vagas</th>
                  <th scope="col">Desabilitada</th><th scope="col">Cobertura válida</th>
                </tr></thead>
                <tbody>{samples.map((point) => <tr key={point.sequence}>
                  <th scope="row">{formatTime(point.at, zone, true)}</th><td>{SOURCE_LABEL[point.source]}</td>
                  <td>{point.available ? point.empty : "—"}</td><td>{point.available ? point.full : "—"}</td>
                  <td>{point.available ? point.disabled : "—"}</td>
                  <td>{point.available ? `${point.valid} de ${point.total}` : "—"}</td>
                </tr>)}</tbody>
              </table>
            </div>
          </details>
        </>
      )}
      <details className={styles.method}>
        <summary>Sobre este histórico</summary>
        <p>
          Até {CITY_SAMPLE_LIMIT} pontos de eventos aceitos. O histórico reinicia ao mudar a fonte ou sincronizar o stream; no replay, é reconstruído até a posição escolhida.
          Os números usam as mesmas condições dos indicadores acima da lista.
        </p>
      </details>
    </section>
  );
}
