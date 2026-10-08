import { useMemo } from "react";
import { useStations } from "../stations/store";
import { indicators } from "./queue";
import styles from "./Indicators.module.css";

export function Indicators() {
  const stations = useStations((s) => s.stations);
  const coverage = useStations((s) => s.feedHealth?.coverage ?? null);
  const values = useMemo(() => indicators(stations, coverage), [stations, coverage]);
  const percent = values.coverage?.total
    ? Math.round((values.coverage.valid / values.coverage.total) * 100)
    : null;
  return (
    <ul className={styles.indicators} aria-label="Indicadores">
      <li>
        <span className={styles.value}>{values.empty}</span>
        <span className={styles.label}>sem bicicletas (confirmado)</span>
      </li>
      <li>
        <span className={styles.value}>{values.full}</span>
        <span className={styles.label}>sem vagas (confirmado)</span>
      </li>
      <li>
        <span className={styles.value}>{values.disabled}</span>
        <span className={styles.label}>com operação desabilitada</span>
      </li>
      <li>
        <span className={styles.value}>{percent === null ? "?" : `${percent}%`}</span>
        <span className={styles.label}>
          cobertura válida
          {values.coverage ? ` (${values.coverage.valid} de ${values.coverage.total})` : ""}
        </span>
      </li>
    </ul>
  );
}
