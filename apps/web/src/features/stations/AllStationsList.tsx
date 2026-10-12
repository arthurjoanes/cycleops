import { memo, useMemo, useState } from "react";
import ui from "../../components/ui/ui.module.css";
import { ActionIcon } from "../../components/ui/ActionIcon";
import { CONDITION_LABEL } from "../../lib/labels";
import { countRowRender } from "../../lib/renderCount";
import { byName, fold } from "../../lib/search";
import { useStations } from "./store";
import styles from "./AllStationsList.module.css";

const PAGE_SIZE = 50;

const AllRow = memo(function AllRow(props: {
  id: string;
  name: string;
  selected: boolean;
  onSelect(id: string): void;
}) {
  countRowRender();
  const state = useStations((s) => s.stations.get(props.id));
  const availability = state?.availability;
  const condition = state?.decision.primaryCondition;
  return (
    <li>
      <button
        type="button"
        className={styles.row}
        aria-current={props.selected ? "true" : undefined}
        onClick={() => props.onSelect(props.id)}
      >
        <span className={styles.identity}>
          <span className={styles.name}>{props.name}</span>
          <span className={styles.meta}>{condition ? CONDITION_LABEL[condition] : "sem dado"}</span>
        </span>
        <span className={styles.counts}>
          <span title="Bicicletas">
            <ActionIcon name="bike" />
            <strong aria-hidden="true">{availability?.bikesAvailable ?? "?"}</strong>
            <span className="visually-hidden">Bicicletas: {availability?.bikesAvailable ?? "não informado"}</span>
          </span>
          <span title="Vagas">
            <ActionIcon name="dock" />
            <strong aria-hidden="true">{availability?.docksAvailable ?? "?"}</strong>
            <span className="visually-hidden">Vagas: {availability?.docksAvailable ?? "não informado"}</span>
          </span>
        </span>
      </button>
    </li>
  );
});

/** Full list with search. Also the fallback without WebGL and the text alternative to the map. */
export function AllStationsList(props: {
  query: string;
  onQuery(query: string): void;
  selected: string | null;
  onSelect(id: string): void;
  notice?: string;
}) {
  const catalog = useStations((s) => s.catalog);
  // The folded name is computed once per station, not on every keystroke.
  const entries = useMemo(
    () =>
      [...catalog.values()]
        .map((s) => ({ id: s.stationId, name: s.name, key: fold(s.name) }))
        .sort((a, b) => byName.compare(a.name, b.name)),
    [catalog],
  );
  const needle = fold(props.query);
  const shown = needle ? entries.filter((e) => e.key.includes(needle)) : entries;
  const [page, setPage] = useState({ query: needle, count: PAGE_SIZE });
  const requested = page.query === needle ? page.count : PAGE_SIZE;
  // A shared link or a map selection can point past the first page. Keep that row
  // mounted in name order, including the prefix, without filtering the catalog.
  const selectedIndex = shown.findIndex((e) => e.id === props.selected);
  const visible = shown.slice(0, Math.max(requested, selectedIndex + 1));

  return (
    <div className={styles.panel}>
      {props.notice && <p className={`${ui.notice} ${ui.warning}`}>{props.notice}</p>}
      <label className={styles.search}>
        <span>Buscar estação</span>
        <input
          type="search"
          value={props.query}
          onChange={(e) => {
            setPage({ query: fold(e.target.value), count: PAGE_SIZE });
            props.onQuery(e.target.value);
          }}
          autoComplete="off"
        />
      </label>
      <p className={ui.muted} aria-live="polite">
        Exibindo {visible.length} de {shown.length} {needle ? (shown.length === 1 ? "resultado da busca" : "resultados da busca") : (shown.length === 1 ? "estação" : "estações")}
      </p>
      {shown.length > 0 ? (
        <ul className={styles.rows} aria-label="Todas as estações">
          {visible.map((e) => (
            <AllRow
              key={e.id}
              id={e.id}
              name={e.name}
              selected={e.id === props.selected}
              onSelect={props.onSelect}
            />
          ))}
        </ul>
      ) : entries.length > 0 ? (
        <p className={ui.notice}>Nenhuma estação com esse nome.</p>
      ) : null}
      {visible.length < shown.length && (
        <button
          type="button"
          className={`${ui.button} ${styles.more}`}
          onClick={() => setPage({ query: needle, count: visible.length + PAGE_SIZE })}
        >
          Mostrar mais estações
        </button>
      )}
    </div>
  );
}
