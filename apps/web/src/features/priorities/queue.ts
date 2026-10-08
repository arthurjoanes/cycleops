import type { IncidentItem, IncidentState, StationState } from "../../lib/contracts";

const STATION_REASONS = new Set(["missing_in_status", "invalid_record", "report_stale"]);

/** sortKey is compared as an ASCII string with <, never with localeCompare. */
export function compareSortKeys(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}

const TIER: Partial<Record<IncidentState, number>> = { confirmed: 0, recovering: 0, candidate: 1 };

/** The incident behind sortKey and reasonText: lowest tier, then the oldest. An unverifiable
 * incident keeps the tier it was suspended from; final states come last. */
export function leadIncident(incidents: readonly IncidentItem[]): IncidentItem | undefined {
  const tier = (i: IncidentItem) => TIER[i.state === "unverifiable" ? (i.suspendedFrom ?? i.state) : i.state] ?? 2;
  return [...incidents].sort((a, b) => tier(a) - tier(b) || compareSortKeys(a.firstObservedAt, b.firstObservedAt))[0];
}

/** Active queue: stations with a sortKey, oldest first inside each tier. */
export function liveOrder(stations: ReadonlyMap<string, StationState>): string[] {
  const rows: [string, string][] = [];
  for (const [id, state] of stations) {
    const key = state.decision.sortKey;
    if (key !== null) rows.push([key, id]);
  }
  rows.sort((a, b) => compareSortKeys(a[0], b[0]));
  return rows.map(([, id]) => id);
}

export interface FrozenRow {
  id: string;
  /** Left the queue while it was frozen (resolved, closed, discarded). */
  gone: boolean;
}

/** While frozen nothing moves or leaves; new entries are only counted. */
export function frozenView(frozen: readonly string[], live: readonly string[]): {
  rows: FrozenRow[];
  added: number;
} {
  const liveSet = new Set(live);
  const frozenSet = new Set(frozen);
  return {
    rows: frozen.map((id) => ({ id, gone: !liveSet.has(id) })),
    added: live.filter((id) => !frozenSet.has(id)).length,
  };
}

export function sameOrder(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i]);
}

/** "Revisão de dados": unverifiable for a reason of the station itself (sortKey null). */
export function dataReview(stations: ReadonlyMap<string, StationState>): string[] {
  const ids: string[] = [];
  for (const [id, { decision }] of stations) {
    const unverifiable = decision.incidents.some((i) => i.state === "unverifiable");
    if (unverifiable && decision.sortKey === null && STATION_REASONS.has(decision.unverifiableReason ?? "")) {
      ids.push(id);
    }
  }
  return ids.sort();
}

/** Collapsible section below the priorities: disabled operation or not installed. */
export function disabledStations(stations: ReadonlyMap<string, StationState>): string[] {
  const ids: string[] = [];
  for (const [id, { decision }] of stations) {
    const c = decision.conditions;
    if (c.includes("pickup_disabled") || c.includes("return_disabled") || c.includes("not_installed")) {
      ids.push(id);
    }
  }
  return ids.sort();
}

export interface Indicators {
  empty: number;
  full: number;
  disabled: number;
  coverage: { valid: number; total: number } | null;
}

/** Confirmed incidents only: recovering already has units, unverifiable is only dimmed. */
export function indicators(
  stations: ReadonlyMap<string, StationState>,
  coverage: { valid: number; total: number } | null,
): Indicators {
  let empty = 0;
  let full = 0;
  let disabled = 0;
  for (const { decision } of stations.values()) {
    for (const incident of decision.incidents) {
      if (incident.state !== "confirmed") continue;
      if (incident.kind === "pickup_unavailable") empty += 1;
      else full += 1;
    }
    if (decision.conditions.includes("pickup_disabled") || decision.conditions.includes("return_disabled")) {
      disabled += 1;
    }
  }
  return { empty, full, disabled, coverage };
}
