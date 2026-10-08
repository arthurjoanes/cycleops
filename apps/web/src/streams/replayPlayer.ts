import type { ManifestIncident, StationBatchEvent } from "../lib/contracts";
import type { Artifact } from "./artifact";
import type { StationEventSource } from "./source";

export type Speed = 1 | 5 | 20;

/** Where the player sends events. The store is one; the load test injects another. */
export interface ReplaySink {
  reset(): void;
  /** Events played in time: coalesced before they reach the screen. */
  apply(events: StationBatchEvent[]): void;
  /** Steps and jumps: on screen at once. */
  applyNow(events: StationBatchEvent[]): void;
  setNow(now: number): void;
  setEnded(ended: boolean): void;
}

export interface Timers {
  now(): number;
  setTimeout(callback: () => void, ms: number): unknown;
  clearTimeout(handle: unknown): void;
}

const browserTimers: Timers = {
  now: () => performance.now(),
  setTimeout: (callback, ms) => setTimeout(callback, ms),
  clearTimeout: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
};

/**
 * Plays the artifact on a virtual clock that moves by the difference between the events'
 * virtual committedAt. Pausing freezes business time; original instants are never
 * replaced by "now". Jumps reapply from the start (no checkpoints).
 */
export class ReplayPlayer implements StationEventSource {
  readonly events: readonly StationBatchEvent[];
  private readonly times: number[];
  private index = 0;
  private virtualAnchor: number;
  private realAnchor = 0;
  private timer: unknown = null;
  /** After stop() the player ignores every control: an old one may still be on screen
   * for a moment while the next session loads, and must not write into it. */
  private stopped = false;
  playing = false;
  speed: Speed = 1;
  private listeners = new Set<() => void>();

  constructor(
    readonly artifact: Artifact,
    private readonly sink: ReplaySink,
    private readonly timers: Timers = browserTimers,
  ) {
    this.events = artifact.events;
    this.times = artifact.events.map((e) => Date.parse(e.committedAt));
    this.virtualAnchor = this.times[0] ?? 0;
  }

  /** Index of the next event to apply. */
  get position(): number {
    return this.index;
  }

  get ended(): boolean {
    return this.index >= this.events.length;
  }

  virtualNow(): number {
    if (!this.playing) return this.virtualAnchor;
    return this.virtualAnchor + (this.timers.now() - this.realAnchor) * this.speed;
  }

  subscribe(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  start(speed: Speed = 1): void {
    if (this.stopped) return;
    this.sink.reset();
    this.index = 0;
    this.virtualAnchor = this.times[0] ?? 0;
    this.sink.setEnded(false);
    this.speed = speed;
    this.play();
  }

  play(): void {
    if (this.stopped || this.ended) return;
    this.realAnchor = this.timers.now();
    this.playing = true;
    this.schedule();
    this.notify();
  }

  pause(): void {
    if (this.stopped) return;
    this.virtualAnchor = this.virtualNow();
    this.playing = false;
    this.cancel();
    this.sink.setNow(this.virtualAnchor);
    this.notify();
  }

  setSpeed(speed: Speed): void {
    if (this.stopped) return;
    this.virtualAnchor = this.virtualNow();
    this.realAnchor = this.timers.now();
    this.speed = speed;
    if (this.playing) this.schedule();
    this.notify();
  }

  /** Applies the next event and pauses there. */
  step(): void {
    if (this.stopped) return;
    if (this.playing) this.pause();
    const event = this.events[this.index];
    if (!event) return;
    this.index += 1;
    this.virtualAnchor = this.times[this.index - 1] ?? this.virtualAnchor;
    this.sink.applyNow([event]);
    this.sink.setNow(this.virtualAnchor);
    this.finishIfDone();
    this.notify();
  }

  /** Goes to the event of the incident's first confirmation, found by the manifest offset. */
  goToIncident(incident: ManifestIncident): void {
    const origin = this.times[0] ?? 0;
    // Offsets are seconds with millisecond precision; rounding avoids landing one event early.
    const target = origin + Math.round(incident.confirmedOffsetSeconds * 1000);
    let last = 0;
    for (let i = 0; i < this.times.length; i += 1) {
      if ((this.times[i] ?? Infinity) <= target) last = i;
      else break;
    }
    this.seek(last);
  }

  /** Reapplies events 0..index from the start. */
  seek(index: number): void {
    if (this.stopped) return;
    const wasPlaying = this.playing;
    this.cancel();
    this.sink.reset();
    this.sink.setEnded(false);
    this.index = Math.min(index + 1, this.events.length);
    this.sink.applyNow(this.events.slice(0, this.index));
    this.virtualAnchor = this.times[this.index - 1] ?? this.virtualAnchor;
    this.realAnchor = this.timers.now();
    this.sink.setNow(this.virtualAnchor);
    this.playing = wasPlaying && !this.ended;
    if (this.playing) this.schedule();
    this.finishIfDone();
    this.notify();
  }

  stop(): void {
    this.stopped = true;
    this.cancel();
    this.playing = false;
    this.listeners.clear();
  }

  private schedule(): void {
    this.cancel();
    const next = this.times[this.index];
    if (next === undefined) {
      this.finishIfDone();
      return;
    }
    const delay = Math.max(0, (next - this.virtualNow()) / this.speed);
    this.timer = this.timers.setTimeout(() => this.fire(), delay);
  }

  private fire(): void {
    this.timer = null;
    const now = this.virtualNow();
    const batch: StationBatchEvent[] = [];
    while (this.index < this.events.length && (this.times[this.index] ?? Infinity) <= now) {
      batch.push(this.events[this.index] as StationBatchEvent);
      this.index += 1;
    }
    if (batch.length) this.sink.apply(batch);
    this.sink.setNow(now);
    if (this.ended) this.finishIfDone();
    else this.schedule();
    this.notify();
  }

  private finishIfDone(): void {
    if (!this.ended) return;
    this.virtualAnchor = this.times.at(-1) ?? this.virtualAnchor;
    this.playing = false;
    this.cancel();
    this.sink.setEnded(true);
  }

  private cancel(): void {
    if (this.timer !== null) this.timers.clearTimeout(this.timer);
    this.timer = null;
  }

  private notify(): void {
    for (const listener of this.listeners) listener();
  }
}
