import { memo, type RefObject, useEffect, useMemo, useRef, useState } from "react";
import ui from "../../components/ui/ui.module.css";
import type { IncidentItem } from "../../lib/contracts";
import { incidentLine, openingLabels, REASON_LABEL } from "../../lib/labels";
import { formatAge, formatDuration, formatTime, type Zone, zoneLabel } from "../../lib/time";
import { queueDimmed, stateSince } from "../../app/screenState";
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
  const now = useStations((s) => s.now);
  const monitoringStart = useStations((s) => s.monitoringStart);
  // With feed_generation (Oslo and the simulation) the age is the feed's last publication.
  const published = useStations((s) => s.feedHealth?.lastSourceUpdatedAt ?? null);
  if (!state) return null;
  const { availability, decision } = state;
  const lead = leadIncident(decision.incidents);
  const since = lead?.observedSince ?? null;
  const fromStart = since !== null && since === monitoringStart;
  return (
    <li className={styles.row} data-station={props.id}>
      <button
        type="button"
        className={styles.rowButton}
        aria-current={props.selected ? "true" : undefined}
        onClick={() => props.onSelect(props.id)}
      >
        <span className={styles.line}>
          <span className={styles.name}>{name}</span>
          {props.gone && <span className={`${ui.badge} ${ui.ok}`}>saiu da fila</span>}
          {decision.incidents.map((incident) => (
            <span key={incident.id} className={badgeClass(incident)}>
              {incidentLine(
                incident.kind,
                incident.state,
                incident.kind === "pickup_unavailable"
                  ? availability.bikesAvailable
                  : availability.docksAvailable,
              )}
            </span>
          ))}
          {openingLabels(decision.reasonCodes).map((label) => (
            <span key={label} className={`${ui.badge} ${ui.warning}`}>
              {label}
            </span>
          ))}
        </span>
        <span className={`${styles.line} ${styles.detail}`}>
          {since && (
            <span>
              {fromStart
                ? "desde o início do monitoramento"
                : `observado desde ${formatTime(since, props.zone)}`}
            </span>
          )}
          {(published ?? availability.sourceUpdatedAt) && (
            <span>publicação {formatAge(published ?? availability.sourceUpdatedAt ?? "", now)}</span>
          )}
          {decision.reasonText && (
            <span>
              {decision.reasonText}
              {since ? `, há ${formatDuration(now - Date.parse(since))}` : ""}
            </span>
          )}
          {decision.unverifiableReason && !decision.sortKey && (
            <span>{REASON_LABEL[decision.unverifiableReason]}</span>
          )}
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
  const timeline = useStations((s) => s.sourceTimeline);
  const status = useStations((s) => s.status);
  const now = useStations((s) => s.now);
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
    if (wasInQueue.current === props.selected && !selectedInQueue && document.activeElement === document.body) {
      headingRef.current?.focus();
    }
    wasInQueue.current = selectedInQueue ? props.selected : null;
  }, [props.selected, selectedInQueue, headingRef]);

  const source = feed?.state ?? null;
  const dimmed = queueDimmed(source, connection);

  return (
    <div className={styles.panel}>
      {dimmed && source && (source === "stale" || source === "quarantined") && (
        <p className={`${ui.notice} ${ui.warning}`} role="status">
          Dados vencidos desde {formatTime(stateSince(timeline) ?? now, props.zone, true)} (
          {zoneLabel(stateSince(timeline) ?? now, props.zone)}). A fila é do último dado válido.
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
        ) : status === "ready" && started ? (
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
