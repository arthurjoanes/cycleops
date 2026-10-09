import type { Condition, StationState } from "../../lib/contracts";
import { leadIncident } from "../priorities/queue";

export type Tone = "ok" | "empty" | "full" | "stale" | "candidate" | "recovering";

/** Symbol and color per primary condition; the color is never the only signal. */
export const CONDITION_STYLE: Record<Condition | "none", { glyph: string; tone: Tone; label: string }> = {
  ok: { glyph: "✓", tone: "ok", label: "normal" },
  pickup_unavailable: { glyph: "0", tone: "empty", label: "sem bicicletas" },
  return_unavailable: { glyph: "▲", tone: "full", label: "sem vagas" },
  return_capacity_unknown: { glyph: "∞", tone: "recovering", label: "vagas não informadas" },
  pickup_disabled: { glyph: "R", tone: "candidate", label: "retirada desabilitada" },
  return_disabled: { glyph: "D", tone: "candidate", label: "devolução desabilitada" },
  not_installed: { glyph: "×", tone: "stale", label: "não instalada" },
  unverifiable: { glyph: "?", tone: "stale", label: "sem dado confiável" },
  outside_service_hours: { glyph: "z", tone: "stale", label: "sistema fechado" },
  none: { glyph: "·", tone: "stale", label: "sem dado ainda" },
};

/** Colors that come from the queue incident, not from the condition. */
export const INCIDENT_LEGEND: { tone: Tone; label: string }[] = [
  { tone: "candidate", label: "na fila, em confirmação" },
  { tone: "recovering", label: "na fila, recuperando" },
];

/** Station color on the map. primaryCondition is used only outside the queue: a queued
 * station takes the color of the incident behind its sortKey. The symbol stays the condition. */
export function stationTone(state: StationState): Tone {
  const lead = state.decision.sortKey ? leadIncident(state.decision.incidents) : undefined;
  if (!lead) return CONDITION_STYLE[state.decision.primaryCondition].tone;
  if (lead.state === "candidate") return "candidate";
  if (lead.state === "recovering") return "recovering";
  if (lead.state === "unverifiable") return "stale";
  return lead.kind === "pickup_unavailable" ? "empty" : "full";
}

/** MapLibre needs concrete colors; they come from the CSS tokens. */
export function mapColors(element: Element) {
  const css = getComputedStyle(element);
  const v = (name: string, fallback: string) => css.getPropertyValue(name).trim() || fallback;
  return {
    land: v("--map-land", "#e7eef0"),
    water: v("--map-water", "#c5dde2"),
    park: v("--map-park", "#dce7de"),
    road: v("--map-road", "#ffffff"),
    focus: v("--focus", "#086978"),
    ok: v("--ok", "#1a7f37"),
    empty: v("--empty", "#b42318"),
    full: v("--full", "#8a4b00"),
    stale: v("--stale", "#57606a"),
    candidate: v("--candidate", "#6b5300"),
    recovering: v("--recovering", "#0b5cad"),
  };
}
