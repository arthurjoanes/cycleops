// Types and validators generated from the backend's Pydantic models (packages/contracts/).
export type {
  CatalogStation,
  FeedHealth,
  Heartbeat,
  IncidentItem,
  Interval,
  ManifestIncident,
  PriorityDecision,
  ReplayCatalog,
  ReplayManifest,
  ResyncRequired,
  Snapshot,
  StationAvailability,
  StationBatchEvent,
  StationHistory,
  StationState,
  StreamClosed,
} from "@contracts/types";
export {
  validateHeartbeat,
  validateReplayCatalog,
  validateReplayManifest,
  validateResyncRequired,
  validateSnapshot,
  validateStationBatchEvent,
  validateStationHistory,
  validateStreamClosed,
} from "@contracts/validators.js";

import type { PriorityDecision, StationBatchEvent } from "@contracts/types";

export type DataMode = StationBatchEvent["mode"];
export type SourceState = StationBatchEvent["feedHealth"]["state"];
export type Condition = PriorityDecision["primaryCondition"];
export type IncidentKind = PriorityDecision["incidents"][number]["kind"];
export type IncidentState = PriorityDecision["incidents"][number]["state"];

/** A payload that fails its schema: the page is older or newer than the data. */
export class InvalidPayloadError extends Error {
  constructor(what: string) {
    super(`invalid ${what}`);
    this.name = "InvalidPayloadError";
  }
}
