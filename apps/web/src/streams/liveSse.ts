// Server stream (SSE) into the store. Loaded by dynamic import, only in the builds that
// have a server.
import { type Connection, createEventBuffer, useStations } from "../features/stations/store";
import {
  type Heartbeat,
  InvalidPayloadError,
  type StationBatchEvent,
  type StreamClosed,
  validateHeartbeat,
  validateResyncRequired,
  validateStationBatchEvent,
  validateStreamClosed,
} from "../lib/contracts";
import { API, apiReachable, fetchSnapshot } from "./api";
import type { StationEventSource } from "./source";

const WATCHDOG_MS = 40_000;
const BACKOFF_BASE_MS = 1_000;
const BACKOFF_CAP_MS = 30_000;
const HIDDEN_RESYNC_MS = 5 * 60_000;

declare global {
  interface Window {
    /** Receive-to-paint samples in ms, only with #/?measure=1. */
    __cycleopsLatency?: { samples: number[] };
  }
}

/**
 * One EventSource per tab; the old one closes before another opens. The browser's own
 * reconnection is kept for network errors only: when the stream ends up CLOSED, or the
 * server asks for it, this class reopens with ?after= the last applied cursor.
 *
 * Only sequence == last + 1 goes in. Older is dropped. A jump reconnects from the last
 * applied cursor, and a second jump in a row takes a new snapshot.
 */
export class LiveSseSource implements StationEventSource {
  private es: EventSource | null = null;
  /** In-flight snapshot or feed-health request. */
  private abort: AbortController | null = null;
  /** Backoff while closed, watchdog while open: never both. */
  private timer: ReturnType<typeof setTimeout> | null = null;
  private clock: ReturnType<typeof setInterval> | null = null;
  private streamId: string | null = null;
  private catalogVersion: string | null = null;
  private last = 0;
  private needSnapshot = true;
  private gapOnce = false;
  private attempt = 0;
  private lastSeen = 0;
  private hiddenAt: number | null = null;
  private suspended = false;
  private stopped = false;
  private unsubscribe: (() => void) | null = null;
  /** Server clock minus ours, from the heartbeat (uncertainty ±RTT/2). */
  private clockOffset = 0;
  private readonly buffer = createEventBuffer((batch) => this.commit(batch));

  constructor(private readonly measure = false) {}

  start(): void {
    if (this.measure) window.__cycleopsLatency = { samples: [] };
    window.addEventListener("pagehide", this.hide);
    window.addEventListener("pageshow", this.show);
    document.addEventListener("freeze", this.hide);
    document.addEventListener("resume", this.show);
    document.addEventListener("visibilitychange", this.visibility);
    // A page opened in a background tab is already away.
    if (document.visibilityState === "hidden") this.hiddenAt = performance.now();
    // A payload that failed its schema outside this class (the station history) ends the
    // stream the same way.
    this.unsubscribe = useStations.subscribe((s) => {
      if (s.error === "new_version") this.invalid();
    });
    // Ages on screen follow the server clock.
    this.clock = setInterval(() => useStations.getState().setNow(Date.now() + this.clockOffset), 1000);
    this.set("connecting");
    void this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.unsubscribe?.();
    this.unsubscribe = null;
    this.close();
    this.buffer.clear();
    if (this.clock) clearInterval(this.clock);
    window.removeEventListener("pagehide", this.hide);
    window.removeEventListener("pageshow", this.show);
    document.removeEventListener("freeze", this.hide);
    document.removeEventListener("resume", this.show);
    document.removeEventListener("visibilitychange", this.visibility);
  }

  private close(): void {
    this.es?.close();
    this.es = null;
    this.abort?.abort();
    this.abort = null;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private set(connection: Connection): void {
    useStations.getState().setConnection(connection);
  }

  /** Snapshot first when one is due, then the stream from the cursor. */
  private async connect(): Promise<void> {
    this.close();
    if (this.stopped || this.suspended) return;
    const abort = (this.abort = new AbortController());
    if (this.needSnapshot) {
      try {
        const snapshot = await fetchSnapshot(abort.signal);
        if (abort.signal.aborted) return;
        this.buffer.clear();
        if (!useStations.getState().beginSnapshot(snapshot, this.streamId)) {
          // The store belongs to another session now.
          this.stop();
          return;
        }
        this.streamId = snapshot.streamId;
        this.catalogVersion = snapshot.catalogVersion;
        this.last = useStations.getState().lastSequence;
        this.needSnapshot = false;
        this.gapOnce = false;
      } catch (error) {
        if (abort.signal.aborted) return;
        if (error instanceof InvalidPayloadError) return this.invalid();
        if (this.streamId === null) {
          // The first snapshot is the bootstrap: the page shows the error with a retry
          // button and the artifacts next to it, and never switches to them by itself.
          this.stop();
          this.set("ended");
          useStations.getState().setStatus("idle", "bootstrap");
          return;
        }
        return this.recover();
      }
    }
    this.open();
  }

  private open(): void {
    const cursor = `${this.streamId}@${this.last}`;
    const es = new EventSource(`${API}stream/stations?after=${encodeURIComponent(cursor)}`);
    this.es = es;
    this.lastSeen = performance.now();
    this.watch();
    es.addEventListener("open", () => {
      if (es === this.es) this.set("connected");
    });
    es.addEventListener("error", () => {
      if (es !== this.es) return;
      if (es.readyState === EventSource.CLOSED) void this.recover();
      else this.set("reconnecting");
    });
    this.on(es, "station_batch", validateStationBatchEvent, (event) => this.event(event));
    this.on(es, "heartbeat", validateHeartbeat, (heartbeat) => this.heartbeat(heartbeat));
    this.on(es, "resync_required", validateResyncRequired, () => this.resync());
    this.on(es, "stream_closed", validateStreamClosed, (message) => this.closed(message));
  }

  /** The validator is chosen by the event name. */
  private on<T>(es: EventSource, name: string, valid: (data: unknown) => data is T, handle: (data: T) => void) {
    es.addEventListener(name, (message) => {
      if (es !== this.es) return;
      let data: unknown;
      try {
        data = JSON.parse((message as MessageEvent<string>).data);
      } catch {
        data = undefined;
      }
      if (!valid(data)) return this.invalid();
      this.lastSeen = performance.now();
      handle(data);
    });
  }

  private event(event: StationBatchEvent): void {
    if (event.streamId !== this.streamId || event.sequence <= this.last) return;
    if (event.sequence !== this.last + 1) return this.gap();
    // A new catalog comes with a new snapshot (the live client has no catalog.json).
    if (event.catalogVersion !== this.catalogVersion) return this.resync();
    this.last = event.sequence;
    this.gapOnce = false;
    this.attempt = 0;
    if (this.measure) performance.mark(`cycleops:receive:${event.sequence}`);
    this.buffer.push(event);
    // No animation frame runs in a hidden page, so the window would hold every event until
    // the tab came back. Nothing is painted there: apply them as they come.
    if (document.visibilityState === "hidden") this.buffer.flush();
  }

  private heartbeat(heartbeat: Heartbeat): void {
    // The server moved to another stream (reset or reseed) without telling this socket.
    if (heartbeat.streamId !== this.streamId) return this.resync();
    this.clockOffset = Date.parse(heartbeat.serverTime) - Date.now();
    this.attempt = 0;
    // Events still in the window are older than this heartbeat.
    this.buffer.flush();
    useStations.getState().applyHeartbeat(heartbeat);
  }

  private gap(): void {
    if (this.gapOnce) return this.resync();
    this.gapOnce = true;
    void this.recover();
  }

  private resync(): void {
    this.needSnapshot = true;
    useStations.getState().setResyncing(true);
    void this.recover();
  }

  private closed(message: StreamClosed): void {
    if (message.reason !== "superseded") return void this.recover();
    // Terminal: a newer connection from the same address took the slot. Reopening by
    // itself would take it back and start a loop between tabs; the page offers a button.
    this.stop();
    this.set("superseded");
  }

  /** The page is older or newer than the data: stop and ask for a reload. */
  private invalid(): void {
    this.stop();
    this.set("ended");
    useStations.getState().setStatus(this.streamId === null ? "idle" : "ready", "new_version");
  }

  /** Closed stream: check the API, wait with backoff and jitter, then reconnect. */
  private async recover(): Promise<void> {
    this.close();
    if (this.stopped || this.suspended) return;
    const shown = useStations.getState().connection;
    // Suspect stays on screen until a new stream opens; offline until the API answers.
    if (shown !== "suspect" && shown !== "offline") this.set("reconnecting");
    const abort = (this.abort = new AbortController());
    const reachable = await apiReachable(abort.signal);
    if (abort.signal.aborted) return;
    if (!reachable) this.set("offline");
    else if (useStations.getState().connection === "offline") this.set("reconnecting");
    const ceiling = Math.min(BACKOFF_CAP_MS, BACKOFF_BASE_MS * 2 ** this.attempt);
    this.attempt += 1;
    // Jitter, so tabs that lost the same server do not come back at the same instant.
    const delay = ceiling / 2 + (Math.random() * ceiling) / 2;
    this.timer = setTimeout(() => void (reachable ? this.connect() : this.recover()), delay);
  }

  /** 40 s without a heartbeat or an event, measured on the monotonic clock. */
  private watch(): void {
    const left = WATCHDOG_MS - (performance.now() - this.lastSeen);
    if (left > 0) {
      this.timer = setTimeout(() => this.watch(), left);
      return;
    }
    this.set("suspect");
    void this.recover();
  }

  /** pagehide and freeze: the page may go to the back/forward cache or be frozen. */
  private readonly hide = (): void => {
    this.hiddenAt ??= performance.now();
    this.suspended = true;
    this.close();
  };

  /** pageshow and resume: reopen what pagehide or freeze closed. */
  private readonly show = (): void => {
    if (!this.suspended) return;
    this.suspended = false;
    this.wake(true);
  };

  private readonly visibility = (): void => {
    if (document.visibilityState === "hidden") this.hiddenAt ??= performance.now();
    else if (!this.suspended) this.wake(false);
  };

  /** Back after more than 5 min hidden: what came in meanwhile is replaced by a snapshot.
   * pageshow and resume can fire while the page is still hidden; the absence goes on. */
  private wake(reopen: boolean): void {
    const visible = document.visibilityState !== "hidden";
    const long = visible && this.hiddenAt !== null && performance.now() - this.hiddenAt > HIDDEN_RESYNC_MS;
    if (visible) this.hiddenAt = null;
    if (long) {
      this.needSnapshot = true;
      useStations.getState().setResyncing(true);
    }
    if (long || reopen) void this.connect();
  }

  private commit(batch: StationBatchEvent[]): void {
    useStations.getState().apply(batch);
    if (!this.measure) return;
    // Paint: the next frame, then a task after it.
    requestAnimationFrame(() => {
      const channel = new MessageChannel();
      channel.port1.onmessage = () => {
        for (const event of batch) {
          const mark = `cycleops:receive:${event.sequence}`;
          try {
            const measure = performance.measure(`cycleops:paint:${event.sequence}`, mark);
            window.__cycleopsLatency?.samples.push(measure.duration);
            performance.clearMeasures(measure.name);
          } catch {
            // The mark is gone (a snapshot replaced the stream meanwhile).
          }
          performance.clearMarks(mark);
        }
      };
      channel.port2.postMessage(null);
    });
  }
}
