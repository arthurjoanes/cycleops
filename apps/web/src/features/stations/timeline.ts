import type { SourceState } from "../../lib/contracts";
import type { HistoryPoint, SourcePoint } from "./store";

export type SpanKind = "data" | "gap" | "closed";

export interface Span {
  start: number;
  end: number;
  kind: SpanKind;
  bikes: number | null;
  docks: number | null;
}

const GAP_STATES: ReadonlySet<SourceState> = new Set(["unknown", "stale", "quarantined"]);

/**
 * Splits [from, to] into spans with one value each. The rule is the same in every mode: a
 * gap is source unknown, stale or quarantined, or the station unverifiable; closed is the
 * source closed. A step line is drawn only over "data" spans.
 */
export function spans(
  points: readonly HistoryPoint[],
  sources: readonly SourcePoint[],
  from: number,
  to: number,
): Span[] {
  const cuts = new Set<number>([from, to]);
  for (const p of points) cuts.add(Date.parse(p.at));
  for (const s of sources) cuts.add(Date.parse(s.at));
  const times = [...cuts].filter((t) => t >= from && t <= to).sort((a, b) => a - b);
  const result: Span[] = [];
  for (let i = 0; i < times.length - 1; i += 1) {
    const start = times[i] as number;
    const end = times[i + 1] as number;
    const point = latest(points, start);
    const source = latest(sources, start)?.state ?? "unknown";
    const kind: SpanKind =
      source === "closed"
        ? "closed"
        : GAP_STATES.has(source) || !point || point.unverifiable
          ? "gap"
          : "data";
    const span = { start, end, kind, bikes: point?.bikes ?? null, docks: point?.docks ?? null };
    const previous = result.at(-1);
    if (previous && previous.kind === kind && previous.bikes === span.bikes && previous.docks === span.docks) {
      previous.end = end;
    } else {
      result.push(span);
    }
  }
  return result;
}

function latest<T extends { at: string }>(items: readonly T[], at: number): T | undefined {
  let found: T | undefined;
  for (const item of items) {
    if (Date.parse(item.at) <= at) found = item;
    else break;
  }
  return found;
}

export function summary(list: readonly Span[]): {
  minBikes: number | null;
  maxBikes: number | null;
  emptyMs: number;
  gapMs: number;
  closedMs: number;
  changes: number;
} {
  const data = list.filter((s) => s.kind === "data");
  const bikes = data.map((s) => s.bikes).filter((b): b is number => b !== null);
  return {
    minBikes: bikes.length ? Math.min(...bikes) : null,
    maxBikes: bikes.length ? Math.max(...bikes) : null,
    emptyMs: data.filter((s) => s.bikes === 0).reduce((t, s) => t + s.end - s.start, 0),
    gapMs: list.filter((s) => s.kind === "gap").reduce((t, s) => t + s.end - s.start, 0),
    closedMs: list.filter((s) => s.kind === "closed").reduce((t, s) => t + s.end - s.start, 0),
    changes: Math.max(0, data.length - 1),
  };
}
