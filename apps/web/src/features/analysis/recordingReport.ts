import type { IncidentKind, ManifestIncident, StationState } from "../../lib/contracts";
import type { Artifact } from "../../streams/artifact";
import { applyEvents, emptyData, type StreamData } from "../stations/store";

export interface StationObservation {
  stationId: string;
  name: string;
  catalogMs: number;
  validMs: number;
  bikesObservedMs: number;
  docksObservedMs: number;
  emptyMs: number;
  fullMs: number;
  gapMs: number;
  closedMs: number;
}

export interface ResolvedObservation {
  incidentId: string;
  stationId: string;
  name: string;
  kind: IncidentKind;
  confirmedAt: number;
  resolvedAt: number | null;
  elapsedMs: number | null;
}

export interface ReplayReport {
  from: number;
  to: number;
  durationMs: number;
  sourceValidMs: number;
  sourceGapMs: number;
  sourceClosedMs: number;
  stationValidMs: number;
  stationGapMs: number;
  stationClosedMs: number;
  confirmed: number;
  resolved: number;
  stations: readonly StationObservation[];
  incidents: readonly ResolvedObservation[];
}

function stationValid(state: StationState | undefined): state is StationState {
  return !!state && state.availability.quality === "valid" && state.decision.quality === "valid" &&
    !state.decision.conditions.includes("unverifiable");
}

/** Integrates the states between commits. Only the event engine defines gaps and closure. */
export function replayReport(artifact: Artifact): ReplayReport {
  const from = Date.parse(artifact.manifest.periodStart);
  const to = Date.parse(artifact.manifest.periodEnd);
  const report: ReplayReport = {
    from,
    to,
    durationMs: Math.max(0, to - from),
    sourceValidMs: 0,
    sourceGapMs: 0,
    sourceClosedMs: 0,
    stationValidMs: 0,
    stationGapMs: 0,
    stationClosedMs: 0,
    confirmed: artifact.manifest.incidents.length,
    resolved: artifact.manifest.incidents.filter((incident) => incident.resolvedSequence !== null).length,
    stations: [],
    incidents: [],
  };
  const catalogs = new Map(artifact.catalog.versions.map((version) => [version.catalogVersion, version.stations]));
  let data = emptyData(artifact.manifest.streamId, catalogs);
  const rows = new Map<string, StationObservation>();
  const names = new Map<string, string>();
  for (const version of artifact.catalog.versions) {
    for (const station of version.stations) names.set(station.stationId, station.name);
  }
  const accumulate = (state: StreamData, duration: number) => {
    if (duration <= 0) return;
    const source = state.feedHealth?.state ?? "unknown";
    const sourceValid = source === "fresh" || source === "late";
    if (source === "closed") report.sourceClosedMs += duration;
    else if (sourceValid) report.sourceValidMs += duration;
    else report.sourceGapMs += duration;
    for (const station of state.catalog.values()) {
      let row = rows.get(station.stationId);
      if (!row) {
        row = {
          stationId: station.stationId,
          name: station.name,
          catalogMs: 0,
          validMs: 0,
          bikesObservedMs: 0,
          docksObservedMs: 0,
          emptyMs: 0,
          fullMs: 0,
          gapMs: 0,
          closedMs: 0,
        };
        rows.set(station.stationId, row);
      }
      row.name = station.name;
      row.catalogMs += duration;
      const observation = state.stations.get(station.stationId);
      if (source === "closed" || observation?.decision.conditions.includes("outside_service_hours")) {
        row.closedMs += duration;
        report.stationClosedMs += duration;
      } else if (!sourceValid || !stationValid(observation)) {
        row.gapMs += duration;
        report.stationGapMs += duration;
      } else {
        row.validMs += duration;
        report.stationValidMs += duration;
        if (observation.availability.bikesAvailable !== null) {
          row.bikesObservedMs += duration;
          if (observation.availability.bikesAvailable === 0) row.emptyMs += duration;
        }
        if (!station.isVirtual && !observation.decision.conditions.includes("return_capacity_unknown") && observation.availability.docksAvailable !== null) {
          row.docksObservedMs += duration;
          if (observation.availability.docksAvailable === 0) row.fullMs += duration;
        }
      }
    }
  };
  let previous = from;
  let lastAt = -Infinity;
  for (const event of artifact.events) {
    const at = Date.parse(event.committedAt);
    if (at < lastAt) throw new Error("Replay com eventos fora de ordem.");
    lastAt = at;
    const cut = Math.max(from, Math.min(to, at));
    accumulate(data, cut - previous);
    previous = cut;
    const applied = applyEvents(data, [event], false);
    if (applied.results[0] !== "applied") throw new Error("Replay com sequência incompleta.");
    data = applied.data;
  }
  accumulate(data, to - previous);
  report.stations = [...rows.values()];
  const eventTimes = new Map(artifact.events.map((event) => [event.sequence, Date.parse(event.committedAt)]));
  // Manifest offsets start at the first commit, which can follow the recording start.
  const incidentOrigin = artifact.events[0] ? Date.parse(artifact.events[0].committedAt) : from;
  report.incidents = artifact.manifest.incidents.map((incident: ManifestIncident) => {
    const confirmedAt = eventTimes.get(incident.confirmedSequence) ?? incidentOrigin + incident.confirmedOffsetSeconds * 1000;
    const resolvedAt = incident.resolvedOffsetSeconds === null || incident.resolvedSequence === null
      ? null
      : eventTimes.get(incident.resolvedSequence) ?? incidentOrigin + incident.resolvedOffsetSeconds * 1000;
    return {
      incidentId: incident.incidentId,
      stationId: incident.stationId,
      name: names.get(incident.stationId) ?? incident.stationId,
      kind: incident.kind,
      confirmedAt,
      resolvedAt,
      elapsedMs: resolvedAt === null ? null : Math.max(0, resolvedAt - confirmedAt),
    };
  });
  return report;
}

export function observationRanking(
  stations: readonly StationObservation[],
  kind: "empty" | "full",
): StationObservation[] {
  const field = kind === "empty" ? "emptyMs" : "fullMs";
  return stations.filter((station) => station[field] > 0).sort((a, b) =>
    b[field] - a[field] || (a.stationId < b.stationId ? -1 : a.stationId > b.stationId ? 1 : 0));
}

export function formatObservedDuration(ms: number): string {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) return `${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} min${seconds % 60 ? ` ${seconds % 60} s` : ""}`;
  return `${Math.floor(minutes / 60)} h${minutes % 60 ? ` ${minutes % 60} min` : ""}`;
}
