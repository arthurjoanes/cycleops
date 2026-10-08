import ui from "../components/ui/ui.module.css";
import { useStations } from "../features/stations/store";
import { formatTime, type Zone, zoneLabel } from "../lib/time";
import { mainMessage } from "./screenState";

/** onReplay: the server stream failed and an artifact exists; the switch is the visitor's. */
export function MainMessage(props: { zone: Zone; onRetry(): void; onReplay?: () => void }) {
  const error = useStations((s) => s.error);
  const connection = useStations((s) => s.connection);
  const resyncing = useStations((s) => s.resyncing);
  const source = useStations((s) => s.feedHealth?.state ?? null);
  // From the server (events, snapshot, heartbeat), never from when this page saw it.
  const since = useStations((s) => s.feedHealth?.stateSince ?? null);
  const message = mainMessage({ error, connection, resyncing, source, staleSince: since });
  if (!message) return null;
  // Left out when the payload has no start (older server or artifact).
  const sinceText = (iso: string | null) =>
    iso ? ` desde ${formatTime(iso, props.zone, true)} (${zoneLabel(iso, props.zone)})` : "";
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
          {props.onReplay && (
            <>
              {" "}
              <button type="button" className={ui.button} onClick={props.onReplay}>
                Abrir o replay em arquivo
              </button>
            </>
          )}
        </p>
      );
    case "superseded":
      return (
        <p className={`${ui.notice} ${ui.warning}`} role="alert">
          O servidor encerrou esta conexão porque outra aba ou aparelho da mesma rede abriu o stream.{" "}
          <button type="button" className={ui.button} onClick={props.onRetry}>
            Reconectar
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
          O formato do feed mudou e os dados estão em quarentena{sinceText(since)}.
        </p>
      );
    case "stale":
      return (
        <p className={`${ui.notice} ${ui.warning}`} role="status">
          Fonte vencida{sinceText(message.since)}.
        </p>
      );
  }
}
