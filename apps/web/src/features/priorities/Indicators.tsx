import { useMemo, type ReactNode } from "react";
import { ActionIcon } from "../../components/ui/ActionIcon";
import { useStations } from "../stations/store";
import { indicators } from "./queue";
import styles from "./Indicators.module.css";

export function Indicators({ action }: { action?: ReactNode }) {
  const stations = useStations((s) => s.stations);
  const coverage = useStations((s) => s.feedHealth?.coverage ?? null);
  const values = useMemo(() => indicators(stations, coverage), [stations, coverage]);
  const percent = values.coverage?.total
    ? Math.round((values.coverage.valid / values.coverage.total) * 100)
    : null;
  return (
    <ul className={styles.indicators} aria-label="Indicadores">
      <li className={styles.confirmed}>
        <span className={styles.context}>Incidentes confirmados</span>
        <div className={styles.pair}>
          <div>
            <span className={styles.metric}><ActionIcon name="bike" /><span className={styles.value}>{values.empty}</span></span>
            <span className={styles.label}>Sem bicicletas</span>
          </div>
          <div>
            <span className={styles.metric}><ActionIcon name="dock" /><span className={styles.value}>{values.full}</span></span>
            <span className={styles.label}>Sem vagas</span>
          </div>
        </div>
      </li>
      <li>
        <span className={styles.metric}><ActionIcon name="disabled" /><span className={styles.value}>{values.disabled}</span></span>
        <span className={styles.label}>Operação desabilitada</span>
      </li>
      <li>
        <span className={styles.metric}><ActionIcon name="coverage" /><span className={styles.value}>{percent === null ? "?" : `${percent}%`}</span></span>
        <span className={styles.label} title={values.coverage ? `${values.coverage.valid} de ${values.coverage.total} estações com dados válidos` : undefined}>Cobertura válida</span>
        {values.coverage && <span className="visually-hidden">{values.coverage.valid} de {values.coverage.total} estações</span>}
      </li>
      {action && <li className={styles.action}>{action}</li>}
    </ul>
  );
}
