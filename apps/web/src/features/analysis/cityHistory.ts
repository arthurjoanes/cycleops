import type { FeedHealth, SourceState, StationState } from "../../lib/contracts";

export interface CityCounts {
  empty: number;
  full: number;
  disabled: number;
}

export interface CitySample extends CityCounts {
  at: number;
  sequence: number;
  source: SourceState;
  /** Unavailable source states are shown as gaps while their last counts stay intact. */
  available: boolean;
  valid: number;
  total: number;
}

export const CITY_SAMPLE_LIMIT = 120;

export function stationCityCounts(station: StationState): CityCounts {
  let empty = 0;
  let full = 0;
  for (const incident of station.decision.incidents) {
    if (incident.state !== "confirmed") continue;
    if (incident.kind === "pickup_unavailable") empty += 1;
    else full += 1;
  }
  const conditions = station.decision.conditions;
  const disabled = conditions.includes("pickup_disabled") || conditions.includes("return_disabled") ? 1 : 0;
  return { empty, full, disabled };
}

export function countCity(stations: Iterable<StationState>): CityCounts {
  const counts = { empty: 0, full: 0, disabled: 0 };
  for (const station of stations) {
    const next = stationCityCounts(station);
    counts.empty += next.empty;
    counts.full += next.full;
    counts.disabled += next.disabled;
  }
  return counts;
}

export function citySample(at: string, sequence: number, health: FeedHealth, counts: CityCounts): CitySample {
  return {
    ...counts,
    at: Date.parse(at),
    sequence,
    source: health.state,
    available: health.state === "fresh" || health.state === "late",
    valid: health.coverage.valid,
    total: health.coverage.total,
  };
}

/** Events at the same instant contribute one final state to the graph. */
export function appendCitySample(samples: readonly CitySample[], next: CitySample): readonly CitySample[] {
  const previous = samples.at(-1);
  if (previous && next.at < previous.at) return samples;
  const prefix = previous?.at === next.at ? samples.slice(0, -1) : samples;
  return [...prefix.slice(-(CITY_SAMPLE_LIMIT - 1)), next];
}
