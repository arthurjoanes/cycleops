import { memo, type RefObject, useEffect, useMemo, useRef, useState } from "react";
import ui from "../../components/ui/ui.module.css";
import { ActionIcon } from "../../components/ui/ActionIcon";
import type { IncidentItem } from "../../lib/contracts";
import { CONDITION_LABEL, incidentLine, openingLabels, REASON_LABEL } from "../../lib/labels";
import { formatAge, formatTime, type Zone, zoneLabel } from "../../lib/time";
import { queueDimmed } from "../../app/screenState";
import { countRowRender } from "../../lib/renderCount";
import { useStations } from "../stations/store";
import { dataReview, disabledStations, frozenView, leadIncident, liveOrder, sameOrder } from "./queue";
import styles from "./StationPriorityList.module.css";

const RELEASE_MS = 1000;
const STATE_CLASS: Record<string, string | undefined> = {
  candidate: ui.candidate,
  confirmed: undefined,
  recovering: ui.recovering,
  unverifiable: ui.stale,
};

function badgeClass(incident: IncidentItem): string {
  const byState = STATE_CLASS[incident.state];
  if (byState) return `${ui.badge} ${byState}`;
  return `${ui.badge} ${incident.kind === "pickup_unavailable" ? ui.empty : ui.full}`;
}

export const StationRow = memo(function StationRow(props: {
  id: string;
  selected: boolean;
  gone?: boolean;
  zone: Zone;
  onSelect(id: string): void;
}) {
  countRowRender();
  const state = useStations((s) => s.stations.get(props.id));
  const name = useStations((s) => s.catalog.get(props.id)?.name ?? props.id);
  const monitoringStart = useStations((s) => s.monitoringStart);
  const lead = state ? leadIncident(state.decision.incidents) : undefined;
  const since = lead?.observedSince ?? null;
  const age = useStations((s) => since === null ? null : formatAge(since, s.now));
  if (!state) return null;
  const { availability, decision } = state;
  const fromStart = since !== null && since === monitoringStart;
  return (
    <li className={styles.row} data-station={props.id}>
      <button
        type="button"
        className={styles.rowButton}
        aria-current={props.selected ? "true" : undefined}
        onClick={() => props.onSelect(props.id)}
      >
        <span className={styles.identity}>
          <span className={styles.name}>{name}</span>
          <span className={styles.line}>
            {props.gone && <span className={`${ui.badge} ${ui.ok}`}>saiu da fila</span>}
            {decision.incidents.length === 0 && (
              <span className={`${ui.badge} ${ui.stale}`}>{CONDITION_LABEL[decision.primaryCondition]}</span>
            )}
            {decision.incidents.map((incident) => (
              <span key={incident.id} className={badgeClass(incident)}>
                {incidentLine(incident.kind, incident.state, null)}
              </span>
            ))}
            {openingLabels(decision.reasonCodes).map((label) => (
              <span key={label} className={`${ui.badge} ${ui.warning}`}>
                {label}
              </span>
            ))}
          </span>
          <span className={styles.detail}>
            {since && (
              <span title={fromStart ? "Desde o início do monitoramento" : `Observado desde ${formatTime(since, props.zone)}`}>
                {fromStart ? "monitorado " : "observado "}{age}
                {fromStart && <span className="visually-hidden"> desde o início do monitoramento</span>}
              </span>
            )}
            {props.selected && decision.reasonText && (
              <span className={styles.reason}>
                {decision.reasonText}
              </span>
            )}
            {decision.unverifiableReason && !decision.sortKey && (
              <span>{REASON_LABEL[decision.unverifiableReason]}</span>
            )}
          </span>
        </span>
        <span className={styles.counts}>
          <span title="Bicicletas">
            <ActionIcon name="bike" />
            <strong aria-hidden="true">{availability.bikesAvailable ?? "?"}</strong>
            <span className="visually-hidden">Bicicletas: {availability.bikesAvailable ?? "não informado"}</span>
          </span>
          <span title="Vagas">
            <ActionIcon name="dock" />
            <strong aria-hidden="true">{availability.docksAvailable ?? "?"}</strong>
            <span className="visually-hidden">Vagas: {availability.docksAvailable ?? "não informado"}</span>
          </span>
        </span>
      </button>
    </li>
  );
});

export function StationPriorityList(props: {
  selected: string | null;
  onSelect(id: string): void;
  zone: Zone;
  headingRef: RefObject<HTMLHeadingElement | null>;
}) {
  const stations = useStations((s) => s.stations);
  const feed = useStations((s) => s.feedHealth);
  const connection = useStations((s) => s.connection);
  const status = useStations((s) => s.status);
  // Before the first event the queue is unknown, not empty.
  const started = useStations((s) => s.lastSequence > 0);
  const live = useMemo(() => liveOrder(stations), [stations]);
  const review = useMemo(() => dataReview(stations), [stations]);
  const disabled = useMemo(() => disabledStations(stations), [stations]);

  // Frozen order: while the pointer is over the list or focus is inside it.
  const [frozen, setFrozen] = useState<string[] | null>(null);
  const pointerInside = useRef(false);
  const focusInside = useRef(false);
  const release = useRef<ReturnType<typeof setTimeout> | null>(null);
  const liveRef = useRef(live);
  useEffect(() => {
    liveRef.current = live;
  }, [live]);

  const freeze = () => {
    if (release.current) clearTimeout(release.current);
    release.current = null;
    setFrozen((current) => current ?? liveRef.current);
  };
  const unfreezeLater = () => {
    if (focusInside.current) return;
    release.current = setTimeout(() => {
      if (!pointerInside.current && !focusInside.current) setFrozen(null);
    }, RELEASE_MS);
  };
  useEffect(() => () => {
    if (release.current) clearTimeout(release.current);
  }, []);

  const view = frozen
    ? frozenView(frozen, live)
    : { rows: live.map((id) => ({ id, gone: false })), added: 0 };
  const changed = frozen !== null && (view.added > 0 || !sameOrder(frozen, live));

  // If the selected row leaves the queue and focus fell to the body, focus goes to the list
  // title. Only on that change: a selected station that was never in the queue is left alone.
  const listRef = useRef<HTMLDivElement>(null);
  const selectedInQueue = props.selected !== null && view.rows.some((r) => r.id === props.selected);
  const wasInQueue = useRef<string | null>(null);
  const { headingRef } = props;
  useEffect(() => {
    if (wasInQueue.current !== null && wasInQueue.current === props.selected && !selectedInQueue && document.activeElement === document.body) {
      headingRef.current?.focus();
    }
    wasInQueue.current = selectedInQueue ? props.selected : null;
  }, [props.selected, selectedInQueue, headingRef]);

  const source = feed?.state ?? null;
  const dimmed = queueDimmed(source, connection);
  // null when the payload has no start: it is left out.
  const since = feed?.stateSince ?? null;

  return (
    <div className={styles.panel}>
      {dimmed && source && (source === "stale" || source === "quarantined") && (
        <p className={`${ui.notice} ${ui.warning}`} role="status">
          Dados vencidos
          {since && ` desde ${formatTime(since, props.zone, true)} (${zoneLabel(since, props.zone)})`}. A fila é do
          último dado válido.
        </p>
      )}
      <div
        ref={listRef}
        className={styles.list}
        onPointerEnter={() => {
          pointerInside.current = true;
          freeze();
        }}
        onPointerLeave={() => {
          pointerInside.current = false;
          unfreezeLater();
        }}
        onFocus={() => {
          focusInside.current = true;
          freeze();
        }}
        onBlur={(event) => {
          if (listRef.current?.contains(event.relatedTarget as Node | null)) return;
          focusInside.current = false;
          if (!pointerInside.current) setFrozen(null);
        }}
      >
        {view.rows.length > 0 ? (
          <ol className={dimmed ? `${styles.rows} ${styles.dimmed}` : styles.rows} aria-label="Estações em prioridade">
            {view.rows.map((row) => (
              <StationRow
                key={row.id}
                id={row.id}
                gone={row.gone}
                selected={row.id === props.selected}
                zone={props.zone}
                onSelect={props.onSelect}
              />
            ))}
          </ol>
        ) : status === "ready" && started && source === "closed" ? (
          <p className={ui.notice}>Sistema fechado. Abre às 05:00 (horário de Oslo).</p>
        ) : status === "ready" && started && (source === "fresh" || source === "late") ? (
          <p className={ui.notice}>Nenhuma estação em prioridade agora.</p>
        ) : null}
        {/* Inside the frozen area (focus on the button keeps the order frozen) and after the rows,
            sticky at the bottom, so it never pushes a row from under the pointer. */}
        {changed && (
          <p className={`${ui.notice} ${styles.changed}`}>
            <span>
              {view.added > 0 ? `${view.added} nova${view.added > 1 ? "s" : ""} na fila. ` : ""}
            </span>
            <button
              type="button"
              className={ui.button}
              onClick={() => {
                setFrozen(liveRef.current);
                props.headingRef.current?.focus();
              }}
            >
              Prioridades mudaram. Reordenar
            </button>
          </p>
        )}
      </div>
      {review.length > 0 && (
        <section className={styles.section} aria-labelledby="review-title">
          <h3 id="review-title" className={styles.sectionTitle}>
            Revisão de dados ({review.length})
          </h3>
          <ol className={styles.rows}>
            {review.map((id) => (
              <StationRow key={id} id={id} selected={id === props.selected} zone={props.zone} onSelect={props.onSelect} />
            ))}
          </ol>
        </section>
      )}
      {disabled.length > 0 && (
        <details className={styles.section}>
          <summary className={styles.sectionTitle}>
            Operação desabilitada ou não instaladas ({disabled.length})
          </summary>
          <ol className={styles.rows}>
            {disabled.map((id) => (
              <StationRow key={id} id={id} selected={id === props.selected} zone={props.zone} onSelect={props.onSelect} />
            ))}
          </ol>
        </details>
      )}
    </div>
  );
}
