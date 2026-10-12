import { useEffect, useState } from "react";
import ui from "../../components/ui/ui.module.css";
import type { FeedDiagnostics } from "../../lib/contracts";
import { SOURCE_LABEL } from "../../lib/labels";
import { formatDate, formatTime, type Zone } from "../../lib/time";
import type { Artifact } from "../../streams/artifact";
import { useStations } from "../stations/store";
import styles from "./FeedQualityPanel.module.css";

const FEED_NAMES: Record<string, string> = {
  station_status: "Disponibilidade",
  station_information: "Cadastro das estações",
  system_information: "Informações do sistema",
  vehicle_types: "Tipos de bicicleta",
  gbfs: "Descoberta dos feeds",
};

const OUTCOME_NAMES: Record<string, string> = {
  ok: "Publicação válida",
  not_modified: "Sem alteração (HTTP 304)",
  duplicate: "Publicação repetida",
  out_of_order: "Publicação fora de ordem",
  correction: "Correção de publicação anterior",
  future_timestamp: "Horário da publicação no futuro",
  invalid: "Conteúdo fora do contrato",
  too_large: "Resposta acima do limite",
  http_error: "Erro HTTP",
  timeout: "Prazo de resposta excedido",
  network_error: "Falha de transporte",
  truncated: "Resposta interrompida",
};

export function FeedQualityPanel({ artifact, zone }: { artifact: Artifact | null; zone: Zone }) {
  const streamId = useStations((s) => s.streamId);
  const health = useStations((s) => s.feedHealth);
  const [remote, setRemote] = useState<FeedDiagnostics | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [refresh, setRefresh] = useState(0);
  const diagnostic = artifact?.diagnostics ?? remote;

  useEffect(() => {
    if (artifact || !streamId) return;
    const abort = new AbortController();
    void import("../../streams/api")
      .then(({ fetchDiagnostics }) => fetchDiagnostics(streamId, abort.signal))
      .then((found) => {
        if (abort.signal.aborted) return;
        setRemote(found);
        setStatus(found ? "ready" : "error");
      })
      .catch(() => {
        if (!abort.signal.aborted) setStatus("error");
      });
    return () => abort.abort();
  }, [artifact, streamId, refresh]);

  const period = (value: string) => `${formatDate(value, zone)}, ${formatTime(value, zone)}`;
  return (
    <section aria-labelledby="quality-title" className={styles.panel}>
      <div className={styles.heading}>
        <div>
          <h3 id="quality-title">Qualidade do feed</h3>
          <p>Respostas recebidas, validação e publicações usadas pelo motor.</p>
        </div>
        {!artifact && (
          <button type="button" className={ui.button} onClick={() => { setRemote(null); setStatus("loading"); setRefresh((n) => n + 1); }} disabled={status === "loading"}>
            Atualizar relatório
          </button>
        )}
      </div>
      {health && (
        <p className={styles.coverage}>
          Registros válidos na publicação: {health.coverage.valid} de {health.coverage.total} estações. Fonte no instante exibido: {SOURCE_LABEL[health.state]}.
          {(health.quarantinedFeeds?.length ?? 0) > 0 && ` Em quarentena: ${(health.quarantinedFeeds ?? []).map((feed) => FEED_NAMES[feed] ?? feed).join(", ")}.`}
        </p>
      )}
      {!diagnostic ? (
        <p className={ui.notice} role="status">
          {artifact
            ? "Esta gravação não inclui o relatório de qualidade."
            : status === "loading" ? "Consultando as tentativas de coleta." : "Não foi possível consultar o relatório. Tente atualizar."}
        </p>
      ) : (
        <>
          <p className={styles.period}>
            {artifact ? "Gravação inteira" : "Janela consultada"}: {period(diagnostic.windowStart)} a {period(diagnostic.windowEnd)}.
            {diagnostic.mode === "synthetic" && " Dados de simulação."}
          </p>
          <dl className={styles.counts}>
            <div><dt>Tentativas</dt><dd>{diagnostic.totals.attempts}</dd></div>
            <div><dt>HTTP 200 ou 304</dt><dd>{diagnostic.totals.successfulResponses} de {diagnostic.totals.httpResponses} respostas</dd></div>
            <div><dt>Publicações válidas</dt><dd>{diagnostic.totals.acceptedPublications}</dd></div>
            <div><dt>Publicações rejeitadas</dt><dd>{diagnostic.totals.rejectedPublications}</dd></div>
          </dl>
          <p className={styles.explanation}>
            Uma resposta HTTP 200 pode conter dados inválidos. Tentativas sem resposta HTTP contam nas falhas de transporte.
            Publicações repetidas, corrigidas, fora de ordem ou com horário futuro são separadas das falhas de contrato.
          </p>
          <div className={styles.tableWrap} role="region" aria-label="Coleta por feed" tabIndex={0}>
            <table>
              <caption>Coleta por feed no período</caption>
              <thead><tr><th scope="col">Feed</th><th scope="col">Tentativas</th><th scope="col">Falhas HTTP</th><th scope="col">Transporte</th><th scope="col">Contrato</th><th scope="col">Registros inválidos</th></tr></thead>
              <tbody>
                {diagnostic.feeds.map((feed) => (
                  <tr key={feed.feed}>
                    <th scope="row">{FEED_NAMES[feed.feed] ?? feed.feed}</th>
                    <td>{feed.counts.attempts}</td><td>{feed.counts.httpFailures}</td><td>{feed.counts.transportFailures}</td><td>{feed.counts.contractFailures}</td>
                    <td>{feed.counts.invalidStationRecords === null ? (["station_status", "station_information"].includes(feed.feed) ? "Não apurado" : "Não se aplica") : `${feed.counts.invalidStationRecords} de ${feed.counts.stationRecords}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className={styles.explanation}>As colunas HTTP, transporte e contrato contam falhas. Registros são somados por resposta validada e podem incluir a mesma estação em publicações diferentes.</p>
          {diagnostic.feeds.map((feed) => (
            <details key={feed.feed} className={styles.feed}>
              <summary>{FEED_NAMES[feed.feed] ?? feed.feed}: resultados e últimas tentativas</summary>
              <dl className={styles.outcomes}>
                {feed.outcomes.map((outcome) => <div key={outcome.outcome}><dt>{OUTCOME_NAMES[outcome.outcome] ?? outcome.outcome}</dt><dd>{outcome.count}</dd></div>)}
              </dl>
              <ul className={styles.attempts}>
                {feed.recentAttempts.map((attempt, i) => (
                  <li key={`${attempt.attemptAt}-${i}`}>
                    <time dateTime={attempt.attemptAt}>{period(attempt.attemptAt)}</time>
                    <span>{OUTCOME_NAMES[attempt.outcome] ?? attempt.outcome}{attempt.status === null ? ", sem resposta HTTP" : `, HTTP ${attempt.status}`}</span>
                    {attempt.errorCode && <span className={styles.errorCode}>{attempt.errorCode}</span>}
                  </li>
                ))}
              </ul>
            </details>
          ))}
          {diagnostic.totals.attempts === 0 && <p className={ui.notice}>Nenhuma tentativa registrada nessa janela.</p>}
        </>
      )}
    </section>
  );
}
