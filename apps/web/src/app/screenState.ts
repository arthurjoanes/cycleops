import type { SourceState } from "../lib/contracts";
import type { Connection, StationsStore } from "../features/stations/store";

export type MainMessage =
  | { kind: "new_version" }
  | { kind: "bootstrap_error" }
  | { kind: "resync" }
  | { kind: "closed" }
  | { kind: "quarantined" }
  | { kind: "stale"; since: string | null };

/**
 * One message at a time in the main area: bootstrap error, then resync in progress, then
 * the source state (closed, quarantine or stale), which comes from a single SourceState.
 * Late is only a header warning; reconnecting and suspect show in the header and dim the
 * queue without replacing this message.
 */
export function mainMessage(input: {
  error: StationsStore["error"];
  resyncing: boolean;
  source: SourceState | null;
  staleSince: string | null;
}): MainMessage | null {
  if (input.error === "new_version") return { kind: "new_version" };
  if (input.error === "bootstrap") return { kind: "bootstrap_error" };
  if (input.resyncing) return { kind: "resync" };
  if (input.source === "closed") return { kind: "closed" };
  if (input.source === "quarantined") return { kind: "quarantined" };
  if (input.source === "stale") return { kind: "stale", since: input.staleSince };
  return null;
}

/** The queue is dimmed when nothing in it can be taken as current. */
export function queueDimmed(source: SourceState | null, connection: Connection): boolean {
  return source === "stale" || source === "quarantined" || connection === "suspect" || connection === "reconnecting";
}

/** Instant the source went into its current state, from the timeline of the events. */
export function stateSince(timeline: readonly { at: string; state: SourceState }[]): string | null {
  return timeline.at(-1)?.at ?? null;
}
