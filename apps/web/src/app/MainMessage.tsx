import ui from "../components/ui/ui.module.css";
import { useStations } from "../features/stations/store";
import { formatTime, type Zone, zoneLabel } from "../lib/time";
import { mainMessage, stateSince } from "./screenState";

export function MainMessage(props: { zone: Zone; onRetry(): void }) {
  const error = useStations((s) => s.error);
  const resyncing = useStations((s) => s.resyncing);
  const source = useStations((s) => s.feedHealth?.state ?? null);
  const timeline = useStations((s) => s.sourceTimeline);
  const message = mainMessage({ error, resyncing, source, staleSince: stateSince(timeline) });
  if (!message) return null;
  const at = (iso: string | null) =>
    iso ? `${formatTime(iso, props.zone, true)} (${zoneLabel(iso, props.zone)})` : "";
  switch (message.kind) {
    case "new_version":
      return (
        <p className={`${ui.notice} ${ui.warning}`} role="alert">
          Nova versão disponível.{" "}
          <button type="button" className={ui.button} onClick={() => window.location.reload()}>
            Recarregar
          </button>
        </p>
      );
    case "bootstrap_error":
      return (
        <p className={`${ui.notice} ${ui.warning}`} role="alert">
          Não foi possível carregar os dados.{" "}
          <button type="button" className={ui.button} onClick={props.onRetry}>
            Tentar de novo
          </button>
        </p>
      );
    case "resync":
      return (
        <p className={ui.notice} role="status">
          Atualizando o estado completo.
        </p>
      );
    case "closed":
      return (
        <p className={ui.notice} role="status">
          Sistema fechado. Abre às 05:00 (horário de Oslo). As contagens continuam aparecendo.
        </p>
      );
    case "quarantined":
      return (
        <p className={`${ui.notice} ${ui.warning}`} role="status">
          O formato do feed mudou e os dados estão em quarentena desde {at(stateSince(timeline))}.
        </p>
      );
    case "stale":
      return (
        <p className={`${ui.notice} ${ui.warning}`} role="status">
          Fonte vencida desde {at(message.since)}.
        </p>
      );
  }
}
