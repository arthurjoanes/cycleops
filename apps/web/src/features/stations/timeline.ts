import type { Interval, SourceState, StationHistory } from "../../lib/contracts";
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

/**
 * Worker modes: the series comes from GET /history, where the server already applied the
 * gap rule. Its intervals become a source timeline (a gap as "stale") so the spans are cut
 * the same way as in the artifact modes. Points sit at their commit instant, like the
 * events in the store; the station-unverifiable gaps come only from the server's
 * intervals, since an unverifiable point keeps an older sourceUpdatedAt.
 */
export function historySpans(history: StationHistory, to: number, streamedSources: readonly SourcePoint[] = []): Span[] {
  const points: HistoryPoint[] = history.points.map((p) => ({
    // Payloads from before `at` existed only have sourceUpdatedAt.
    at: p.at ?? p.sourceUpdatedAt,
    bikes: p.bikesAvailable,
    docks: p.docksAvailable,
    condition: p.primaryCondition,
    unverifiable: false,
  }));
  const from = Date.parse(history.windowStart);
  const inside = (list: readonly Interval[], t: number) =>
    list.some((i) => Date.parse(i.start) <= t && (i.end === null || t < Date.parse(i.end)));
  const cuts = new Set([from]);
  for (const i of [...history.gaps, ...history.closed]) {
    cuts.add(Date.parse(i.start));
    if (i.end !== null) cuts.add(Date.parse(i.end));
  }
  for (const source of streamedSources) cuts.add(Date.parse(source.at));
  const sources: SourcePoint[] = [...cuts]
    .sort((a, b) => a - b)
    .map((t) => {
      // Heartbeats can report a source outage before another station observation exists.
      // Keep the server's station gaps and add the source states received by this page.
      const streamed = latest(streamedSources, t)?.state;
      return {
        at: new Date(t).toISOString(),
        state: inside(history.closed, t) || streamed === "closed"
          ? "closed"
          : inside(history.gaps, t) || (streamed !== undefined && GAP_STATES.has(streamed))
            ? "stale"
            : "fresh",
      };
    });
  return spans(points, sources, from, to);
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
