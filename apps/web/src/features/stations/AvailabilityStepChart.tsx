import { useId } from "react";
import { formatDuration, formatTime, type Zone } from "../../lib/time";
import type { Span } from "./timeline";
import { summary } from "./timeline";
import styles from "./StationDetails.module.css";

const WIDTH = 600;
const HEIGHT = 140;
const PAD = 4;

/** Step lines for bikes and docks. A line never crosses a gap or a closed period. */
export function AvailabilityStepChart(props: {
  spans: Span[];
  from: number;
  to: number;
  capacity: number | null;
  zone: Zone;
}) {
  const { spans: list, from, to, zone } = props;
  const max = Math.max(
    1,
    props.capacity ?? 0,
    ...list.flatMap((s) => [s.bikes ?? 0, s.docks ?? 0]),
  );
  const x = (t: number) => PAD + ((t - from) / Math.max(1, to - from)) * (WIDTH - 2 * PAD);
  const y = (v: number) => HEIGHT - PAD - (v / max) * (HEIGHT - 2 * PAD);
  const path = (value: (s: Span) => number | null) => {
    let d = "";
    let open = false;
    for (const span of list) {
      const v = span.kind === "data" ? value(span) : null;
      if (v === null) {
        open = false;
        continue;
      }
      d += open ? ` V${y(v).toFixed(1)}` : ` M${x(span.start).toFixed(1)} ${y(v).toFixed(1)}`;
      d += ` H${x(span.end).toFixed(1)}`;
      open = true;
    }
    return d.trim();
  };
  const facts = summary(list);
  const hatch = useId();
  const label = `De ${formatTime(from, zone)} a ${formatTime(to, zone)}`;

  return (
    <figure className={styles.chart}>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img" aria-label={`Linha do tempo. ${label}`}>
        {/* Gaps are hatched and closed periods solid, so they differ by more than color. */}
        <defs>
          <pattern id={hatch} width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
            <line x1="0" y1="0" x2="0" y2="6" className={styles.hatch} />
          </pattern>
        </defs>
        <rect x="0" y="0" width={WIDTH} height={HEIGHT} className={styles.chartBg} />
        {list
          .filter((s) => s.kind !== "data")
          .map((s) => (
            <rect
              key={`${s.kind}-${s.start}`}
              x={x(s.start)}
              y={PAD}
              width={Math.max(1, x(s.end) - x(s.start))}
              height={HEIGHT - 2 * PAD}
              className={s.kind === "closed" ? styles.closed : styles.gap}
              fill={s.kind === "closed" ? undefined : `url(#${hatch})`}
            />
          ))}
        <path d={path((s) => s.docks)} className={styles.docksLine} />
        <path d={path((s) => s.bikes)} className={styles.bikesLine} />
      </svg>
      <figcaption className={styles.legend}>
        <span className={styles.keyBikes}>bicicletas</span>
        <span className={styles.keyDocks}>vagas</span>
        <span className={styles.keyGap}>sem dado</span>
        <span className={styles.keyClosed}>sistema fechado</span>
      </figcaption>
      <p className={styles.summary}>
        {label}:{" "}
        {facts.minBikes === null
          ? "sem dado válido."
          : `bicicletas entre ${facts.minBikes} e ${facts.maxBikes}, ${facts.changes} mudanças` +
            (facts.emptyMs ? `, sem bicicletas por ${formatDuration(facts.emptyMs)}` : "") +
            (facts.gapMs ? `, sem dado por ${formatDuration(facts.gapMs)}` : "") +
            (facts.closedMs ? `, fechado por ${formatDuration(facts.closedMs)}` : "") +
            "."}
      </p>
      <details>
        <summary>Tabela da linha do tempo</summary>
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Início</th>
              <th scope="col">Bicicletas</th>
              <th scope="col">Vagas</th>
              <th scope="col">Situação</th>
            </tr>
          </thead>
          <tbody>
            {list.map((s) => (
              <tr key={`${s.start}-${s.kind}`}>
                <td>{formatTime(s.start, zone, true)}</td>
                <td>{s.kind === "data" ? (s.bikes ?? "?") : ""}</td>
                <td>{s.kind === "data" ? (s.docks ?? "?") : ""}</td>
                <td>{s.kind === "data" ? "com dado" : s.kind === "gap" ? "sem dado" : "sistema fechado"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}
