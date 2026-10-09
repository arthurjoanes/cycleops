import { useEffect, useMemo, useState } from "react";
import ui from "../../components/ui/ui.module.css";
import { InvalidPayloadError, type StationHistory, type StationState } from "../../lib/contracts";
import { CONDITION_LABEL, incidentLine, openingLabels, QUALITY_LABEL, REASON_LABEL } from "../../lib/labels";
import { formatDuration, formatTime, type Zone, zoneLabel } from "../../lib/time";
import { AvailabilityStepChart } from "./AvailabilityStepChart";
import { useStations } from "./store";
import { historySpans, spans } from "./timeline";
import styles from "./StationDetails.module.css";

const DAY_MS = 24 * 60 * 60 * 1000;
const EMPTY: never[] = [];
// Compile-time constant: the Pages build has no API to ask.
const SERVER = import.meta.env.MODE !== "pages";

/**
 * Worker modes: the timeline comes from GET /history (last 6 h), asked again when the
 * station changes. undefined while loading, null when it failed. A response for another
 * stream or station is dropped, and a switch aborts the request.
 */
function useServerHistory(id: string, streamId: string | null, state: StationState | undefined) {
  const enabled = useStations((s) => s.connection !== "file");
  const sourceState = useStations((s) => s.feedHealth?.state);
  const [result, setResult] = useState<{ key: string; history: StationHistory | null }>();
  const key = `${streamId}/${id}`;
  useEffect(() => {
    if (!SERVER || !enabled || !streamId) return;
    const abort = new AbortController();
    import("../../streams/api")
      .then(({ fetchHistory }) => fetchHistory(id, streamId, abort.signal))
      .then((history) => {
        if (!abort.signal.aborted && history) setResult({ key: `${streamId}/${id}`, history });
      })
      .catch((error: unknown) => {
        if (abort.signal.aborted) return;
        if (error instanceof InvalidPayloadError) useStations.getState().setStatus("ready", "new_version");
        setResult({ key: `${streamId}/${id}`, history: null });
      });
    return () => abort.abort();
  }, [enabled, id, streamId, state, sourceState]);
  return { enabled, history: result?.key === key ? result.history : undefined };
}

/** Details of one station. The decision is shown as the backend sent it, even when it
 * looks odd next to the counts: the rules live in the engine, not here. */
export function StationDetails(props: { id: string; zone: Zone }) {
  const { id, zone } = props;
  const state = useStations((s) => s.stations.get(id));
  const info = useStations((s) => s.catalog.get(id));
  const points = useStations((s) => s.history.get(id) ?? EMPTY);
  const sources = useStations((s) => s.sourceTimeline);
  const now = useStations((s) => s.now);
  const periodStart = useStations((s) => s.session?.periodStart ?? null);
  const lastEvent = useStations((s) => s.lastEvent);
  const published = useStations((s) => s.feedHealth?.lastSourceUpdatedAt ?? null);
  const streamId = useStations((s) => s.streamId);
  const server = useServerHistory(id, streamId, state);
  const remote = server.history;

  const to = remote ? Math.max(now, Date.parse(remote.windowEnd)) : now;
  const from = remote
    ? Date.parse(remote.windowStart)
    : Math.max(periodStart ? Date.parse(periodStart) : now - DAY_MS, now - DAY_MS);
  const list = useMemo(
    () => (remote ? historySpans(remote, to, sources) : spans(points, sources, from, to)),
    [remote, points, sources, from, to],
  );

  if (!state) return <p className={ui.notice}>Sem dado desta estação ainda.</p>;
  const { availability: a, decision: d } = state;
  const capacity = info?.capacity ?? null;
  const sum = a.bikesAvailable !== null && a.docksAvailable !== null ? a.bikesAvailable + a.docksAvailable : null;
  const missing = capacity !== null && sum !== null ? capacity - sum : null;
  const at = (iso: string | null) => (iso ? `${formatTime(iso, zone, true)} (${zoneLabel(iso, zone)})` : "não informado");

  return (
    <div className={styles.details}>
      <ul className={styles.badges} aria-label="Condições">
        {d.conditions.map((c) => (
          <li key={c} className={`${ui.badge} ${c === "ok" ? ui.ok : ui.stale}`}>
            {CONDITION_LABEL[c]}
          </li>
        ))}
        {d.unverifiableReason && (
          <li className={`${ui.badge} ${ui.stale}`}>{REASON_LABEL[d.unverifiableReason]}</li>
        )}
        {openingLabels(d.reasonCodes).map((label) => (
          <li key={label} className={`${ui.badge} ${ui.warning}`}>
            {label}
          </li>
        ))}
      </ul>
      {d.incidents.length > 0 && (
        <ul className={styles.incidents}>
          {d.incidents.map((i) => (
            <li key={i.id}>
              {incidentLine(i.kind, i.state, null)}
              {i.confirmedAt && ` (primeira confirmação às ${formatTime(i.confirmedAt, zone)})`}
              {", observado desde "}
              {formatTime(i.observedSince, zone)}
              {` (${formatDuration(now - Date.parse(i.observedSince))})`}
            </li>
          ))}
        </ul>
      )}
      <section aria-labelledby={`reason-${id}`}>
        <h3 id={`reason-${id}`} className={styles.subtitle}>
          Motivo da prioridade
        </h3>
        <p>{d.reasonText ?? "Nenhum incidente nesta estação."}</p>
        {d.facts && (
          <dl className={styles.facts}>
            <dt>Bicicletas e vagas na transição</dt>
            <dd>
              {d.facts.bikesAvailable ?? "?"} e {d.facts.docksAvailable ?? "?"}
            </dd>
            <dt>Capacidade usada</dt>
            <dd>{d.facts.capacityUsed ?? "não informada"}</dd>
            <dt>Mínimo para recuperar</dt>
            <dd>{d.facts.recoveryMinUnits ?? "não se aplica"}</dd>
            <dt>Publicações usadas</dt>
            <dd>
              {d.facts.publicationsUsed} em {d.facts.spanSeconds} s
            </dd>
            <dt>Publicação da transição</dt>
            <dd>{at(d.facts.sourceUpdatedAt)}</dd>
          </dl>
        )}
      </section>
      <section aria-labelledby={`availability-${id}`}>
        <h3 id={`availability-${id}`} className={styles.subtitle}>
          Disponibilidade
        </h3>
        <dl className={styles.facts}>
          <dt>Bicicletas</dt>
          <dd>{a.bikesAvailable ?? "não informado"}</dd>
          <dt>Vagas</dt>
          <dd>{a.docksAvailable ?? "não informado"}</dd>
          <dt>Capacidade</dt>
          <dd>
            {capacity ?? "não informada"}
            {missing !== null && missing > 0 && `; ${missing} não informado`}
          </dd>
          {a.vehicleTypes.length > 0 && (
            <>
              <dt>Por tipo</dt>
              <dd>{a.vehicleTypes.map((t) => `${t.vehicleTypeId} ${t.count}`).join(", ")}</dd>
            </>
          )}
          <dt>Retirada</dt>
          <dd>{a.isRenting === null ? "não informado" : a.isRenting ? "habilitada" : "desabilitada"}</dd>
          <dt>Devolução</dt>
          <dd>{a.isReturning === null ? "não informado" : a.isReturning ? "habilitada" : "desabilitada"}</dd>
          <dt>Última publicação</dt>
          <dd>{at(published ?? a.sourceUpdatedAt)}</dd>
          <dt>Última mudança da estação</dt>
          <dd>{at(a.sourceUpdatedAt)}</dd>
          <dt>Recebimento</dt>
          <dd>{at(lastEvent?.receivedAt ?? null)}</dd>
          <dt>Gerado pelo provedor</dt>
          <dd>{at(a.stationReportedAt)}</dd>
          <dt>Qualidade</dt>
          <dd>
            {QUALITY_LABEL[a.quality] ?? a.quality}
            {a.qualityFlags.length > 0 && ` (${a.qualityFlags.map((f) => QUALITY_LABEL[f] ?? f).join(", ")})`}
          </dd>
        </dl>
      </section>
      <section aria-labelledby={`timeline-${id}`}>
        <h3 id={`timeline-${id}`} className={styles.subtitle}>
          Linha do tempo
        </h3>
        {server.enabled && !remote ? (
          <p className={ui.notice}>
            {server.history === null ? "Linha do tempo indisponível agora." : "Carregando a linha do tempo."}
          </p>
        ) : (
          <AvailabilityStepChart spans={list} from={from} to={to} capacity={capacity} zone={zone} />
        )}
      </section>
    </div>
  );
}
