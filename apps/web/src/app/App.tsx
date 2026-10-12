import * as Dialog from "@radix-ui/react-dialog";
import { lazy, Suspense, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import ui from "../components/ui/ui.module.css";
import { Indicators } from "../features/priorities/Indicators";
import { StationPriorityList } from "../features/priorities/StationPriorityList";
import { ReplayControls } from "../features/replay/ReplayControls";
import { AllStationsList } from "../features/stations/AllStationsList";
import { StationDetails } from "../features/stations/StationDetails";
import { useStations } from "../features/stations/store";
import { MODE_LABEL } from "../lib/labels";
import { formatDate } from "../lib/time";
import { Footer } from "./Footer";
import { Header } from "./Header";
import { LiveAnnouncer } from "./LiveAnnouncer";
import { MainMessage } from "./MainMessage";
import { useDataSession } from "./useDataSession";
import { useHashState } from "./useHashState";
import styles from "./App.module.css";

// If the map chunk cannot load (a new deploy replaced it, or the network dropped), the page
// falls back to the list instead of losing the whole tree.
function MapLoadFailed(props: { onUnavailable(): void }) {
  const { onUnavailable } = props;
  useEffect(() => onUnavailable(), [onUnavailable]);
  return <></>;
}
const StationAvailabilityMap = lazy(() =>
  import("../features/stations/StationAvailabilityMap").catch(() => ({ default: MapLoadFailed })),
);
const AnalysisPanel = lazy(() => import("../features/analysis/AnalysisPanel").catch(() => ({
  default: function AnalysisLoadFailed() {
    return <p role="alert" className={ui.notice}>Não foi possível carregar a análise. Recarregue a página para tentar novamente.</p>;
  },
})));

const NARROW = "(max-width: 767px)";

function useNarrow(): boolean {
  return useSyncExternalStore(
    (callback) => {
      const query = window.matchMedia(NARROW);
      query.addEventListener("change", callback);
      return () => query.removeEventListener("change", callback);
    },
    () => window.matchMedia(NARROW).matches,
  );
}

export function App() {
  const [hash, setHash] = useHashState();
  const [retry, setRetry] = useState(0);
  const session = useDataSession(hash.mode, retry, hash.source);
  const narrow = useNarrow();
  const [mapUnavailable, setMapUnavailable] = useState(false);
  const [analysisOpen, setAnalysisOpen] = useState(false);
  const analysisStation = useRef<string | null>(null);
  const status = useStations((s) => s.status);
  const data = useStations((s) => s.session);
  const selectedName = useStations((s) => (hash.station ? (s.catalog.get(hash.station)?.name ?? hash.station) : null));
  const queueHeading = useRef<HTMLHeadingElement>(null);
  const detailsHeading = useRef<HTMLHeadingElement>(null);
  const opener = useRef<HTMLElement | null>(null);

  const select = useCallback(
    (id: string) => {
      opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setHash({ station: id });
    },
    [setHash],
  );
  const closeDetails = useCallback(() => {
    setHash({ station: null });
    // Focus goes back to whatever opened the details, or to the queue title if that row is gone.
    requestAnimationFrame(() => (opener.current?.isConnected ? opener.current : queueHeading.current)?.focus());
  }, [setHash]);
  const unavailable = useCallback(() => setMapUnavailable(true), []);

  useEffect(() => {
    if (hash.station && !narrow) detailsHeading.current?.focus();
  }, [hash.station, narrow]);

  const player = session.player;
  // The stream failed: the artifacts are offered, never opened by themselves.
  const replay = session.kind === "stream" ? session.available.find((k) => k !== "stream") : undefined;
  const showList = !narrow || hash.tab === "list";
  const showMap = !narrow || hash.tab === "map";

  const details = hash.station ? <StationDetails id={hash.station} zone={hash.zone} /> : null;
  // The mobile drawer covers the page header, so it carries the source identity too.
  const detailsContext = data
    ? `${MODE_LABEL[data.mode]}${data.mode === "synthetic"
      ? `, seed ${data.seed ?? "?"}`
      : data.recordedOn ? `, ${formatDate(data.recordedOn, hash.zone)}` : ""}`
    : null;

  return (
    <div className={styles.app}>
      {/* The hash holds the app state, so the link moves focus without changing it. */}
      <a
        className={styles.skip}
        href="#queue-title"
        onClick={(event) => {
          event.preventDefault();
          if (narrow) setHash({ tab: "list" });
          requestAnimationFrame(() => queueHeading.current?.focus());
        }}
      >
        Ir para a fila
      </a>
      <Header
        zone={hash.zone}
        onZone={(zone) => setHash({ zone })}
        available={session.available}
        kind={session.kind}
        onKind={(mode) => setHash({ mode, station: null, artifact: null, at: null })}
        manifest={session.manifest}
      />
      {data?.mode === "synthetic" && (
        <p className={styles.simulation}>
          Simulação: estações e contagens geradas com seed {data.seed}. Não é a operação real do Oslo
          Bysykkel.
        </p>
      )}
      {player && session.manifest && (
        <ReplayControls
          key={session.manifest.streamId}
          player={player}
          manifest={session.manifest}
          zone={hash.zone}
          selectedStation={hash.station}
          onIncident={(incident) => select(incident.stationId)}
        />
      )}
      {status === "loading" && (
        <p className={styles.loading} role="status">
          Carregando os dados.
        </p>
      )}
      <Indicators />
      {session.replayLinkError && (
        <div className={styles.linkError} role="alert">
          <p>{session.replayLinkError}</p>
          <button type="button" className={ui.button} onClick={() => setHash({ at: null, artifact: null })}>Abrir do início</button>
        </div>
      )}
      <Dialog.Root open={analysisOpen} onOpenChange={setAnalysisOpen}>
        <div className={styles.analysisActions}>
          <Dialog.Trigger asChild>
            <button type="button" className={ui.button} disabled={status !== "ready"}>Analisar dados</button>
          </Dialog.Trigger>
        </div>
        <Dialog.Portal>
          <Dialog.Overlay className={styles.overlay} />
          <Dialog.Content className={styles.analysisDialog} aria-describedby="analysis-description" onCloseAutoFocus={(event) => {
            if (analysisStation.current) {
              event.preventDefault();
              select(analysisStation.current);
              analysisStation.current = null;
              if (!narrow) requestAnimationFrame(() => detailsHeading.current?.focus());
            }
          }}>
            <div className={styles.analysisHead}>
              <div>
                <Dialog.Title>Análise dos dados</Dialog.Title>
                <Dialog.Description id="analysis-description">
                  {player ? "Resumo da gravação, evolução dos indicadores e qualidade da coleta." : "Indicadores recebidos nesta sessão e qualidade da coleta."}
                </Dialog.Description>
              </div>
              <Dialog.Close className={ui.button}>Fechar análise</Dialog.Close>
            </div>
            {detailsContext && <p className={styles.analysisContext}>{detailsContext}</p>}
            <Suspense fallback={<p role="status">Carregando a análise.</p>}>
              <AnalysisPanel key={data?.streamId ?? "loading"} player={player} zone={hash.zone} onSelectStation={(id) => {
                analysisStation.current = id;
                setAnalysisOpen(false);
              }} />
            </Suspense>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
      <div className={styles.message}>
        <MainMessage
          zone={hash.zone}
          onRetry={() => setRetry((n) => n + 1)}
          onReplay={replay && (() => setHash({ mode: replay, station: null, artifact: null, at: null }))}
        />
      </div>
      {narrow && (
        <div className={styles.tabs} role="tablist" aria-label="Visão">
          {(["list", "map"] as const).map((tab) => (
            <button
              key={tab}
              type="button"
              role="tab"
              aria-selected={hash.tab === tab}
              className={ui.button}
              onClick={() => setHash({ tab })}
            >
              {tab === "list" ? "Lista" : "Mapa"}
            </button>
          ))}
        </div>
      )}
      <main className={styles.main}>
        {showList && (
          <section className={styles.queue} aria-labelledby="queue-title">
            <div className={styles.queueHead}>
              <h2 id="queue-title" ref={queueHeading} tabIndex={-1}>
                {hash.view === "all" ? "Todas as estações" : "Prioridades"}
              </h2>
              <div className={ui.group} role="group" aria-label="Lista">
                <button
                  type="button"
                  className={ui.button}
                  aria-pressed={hash.view === "priorities"}
                  onClick={() => setHash({ view: "priorities" })}
                >
                  Prioridades
                </button>
                <button
                  type="button"
                  className={ui.button}
                  aria-pressed={hash.view === "all"}
                  onClick={() => setHash({ view: "all" })}
                >
                  Todas as estações
                </button>
              </div>
            </div>
            {hash.view === "all" || (mapUnavailable && narrow && hash.tab === "map") ? (
              <AllStationsList
                query={hash.q}
                onQuery={(q) => setHash({ q })}
                selected={hash.station}
                onSelect={select}
              />
            ) : (
              <StationPriorityList
                selected={hash.station}
                onSelect={select}
                zone={hash.zone}
                headingRef={queueHeading}
              />
            )}
          </section>
        )}
        {showMap && (
          <section className={styles.mapArea} aria-label="Mapa">
            {mapUnavailable ? (
              <AllStationsList
                query={hash.q}
                onQuery={(q) => setHash({ q })}
                selected={hash.station}
                onSelect={select}
                notice="Mapa indisponível neste navegador (sem WebGL2). A lista tem as mesmas estações."
              />
            ) : (
              <>
                <a className={styles.mapLink} href="#/?view=all" onClick={(e) => {
                  e.preventDefault();
                  setHash({ view: "all", tab: "list" });
                }}>
                  Ver as estações em lista
                </a>
                <Suspense fallback={<p className={ui.notice}>Carregando o mapa.</p>}>
                  <StationAvailabilityMap
                    selected={hash.station}
                    onSelect={select}
                    onUnavailable={unavailable}
                    rightPadding={hash.station && !narrow ? 400 : 0}
                  />
                </Suspense>
              </>
            )}
            {details && !narrow && (
              <aside className={styles.details} aria-labelledby="details-title">
                <div className={styles.detailsHead}>
                  <h2 id="details-title" ref={detailsHeading} tabIndex={-1}>
                    {selectedName}
                  </h2>
                  <button type="button" className={ui.button} onClick={closeDetails}>
                    Fechar
                  </button>
                </div>
                {details}
              </aside>
            )}
          </section>
        )}
      </main>
      {narrow && (
        <Dialog.Root open={Boolean(details)} onOpenChange={(open) => !open && closeDetails()}>
          <Dialog.Portal>
            <Dialog.Overlay className={styles.overlay} />
            <Dialog.Content className={styles.drawer} aria-describedby={undefined}>
              <div className={styles.detailsHead}>
                <div className={styles.detailsIdentity}>
                  <Dialog.Title>{selectedName}</Dialog.Title>
                  {detailsContext && <p className={styles.detailsContext}>{detailsContext}</p>}
                </div>
                <Dialog.Close className={ui.button}>Fechar</Dialog.Close>
              </div>
              {details}
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      )}
      <LiveAnnouncer player={player} />
      <Footer />
    </div>
  );
}
