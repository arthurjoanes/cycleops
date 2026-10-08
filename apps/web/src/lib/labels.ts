import type { Condition, DataMode, IncidentKind, IncidentState, SourceState } from "./contracts";

export const MODE_LABEL: Record<DataMode, string> = {
  live: "Ao vivo",
  recorded_replay: "Replay gravado",
  synthetic: "Simulação",
};

export const KIND_LABEL: Record<IncidentKind, string> = {
  pickup_unavailable: "sem bicicletas",
  return_unavailable: "sem vagas",
};

export const STATE_LABEL: Record<IncidentState, string> = {
  candidate: "em confirmação",
  confirmed: "confirmado",
  recovering: "recuperando",
  unverifiable: "dados vencidos",
  resolved: "resolvido",
  closed_without_recovery: "encerrado sem recuperação",
  discarded: "descartado",
};

export const CONDITION_LABEL: Record<Condition, string> = {
  outside_service_hours: "sistema fechado",
  not_installed: "não instalada",
  unverifiable: "sem dado confiável",
  pickup_disabled: "retirada desabilitada",
  return_disabled: "devolução desabilitada",
  pickup_unavailable: "sem bicicletas",
  return_unavailable: "sem vagas",
  return_capacity_unknown: "vagas não informadas",
  ok: "normal",
};

export const SOURCE_LABEL: Record<SourceState, string> = {
  unknown: "sem dado ainda",
  fresh: "em dia",
  late: "atrasada",
  stale: "vencida",
  quarantined: "em quarentena",
  closed: "sistema fechado",
};

export const REASON_LABEL: Record<string, string> = {
  source_unknown: "fonte sem dado",
  source_stale: "fonte vencida",
  source_quarantined: "fonte em quarentena",
  missing_in_status: "estação ausente do status",
  invalid_record: "registro inválido",
  report_stale: "relato da estação vencido",
};

export const QUALITY_LABEL: Record<string, string> = {
  valid: "válido",
  suspect: "suspeito",
  invalid: "inválido",
  unknown: "não informado",
  last_reported_missing: "sem last_reported",
  last_reported_in_future: "last_reported no futuro",
  vehicle_types_mismatch: "tipos não somam o total",
  capacity_missing: "sem capacidade",
  capacity_invalid: "capacidade inválida",
  ...REASON_LABEL,
};

/** "sem bicicletas, confirmado, agora 1" */
export function incidentLine(kind: IncidentKind, state: IncidentState, now: number | null): string {
  const value = now === null ? "" : `, agora ${now}`;
  return `${KIND_LABEL[kind]}, ${STATE_LABEL[state]}${value}`;
}

/** Codes the engine keeps until the incident closes when the station opened at 05:00 already
 * in that condition. */
const AT_OPENING: Record<string, string> = {
  EMPTY_AT_OPENING: "vazia na abertura",
  FULL_AT_OPENING: "cheia na abertura",
};

export function openingLabels(reasonCodes: readonly string[]): string[] {
  return reasonCodes.flatMap((code) => AT_OPENING[code] ?? []);
}
