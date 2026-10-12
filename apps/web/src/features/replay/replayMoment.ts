import { patchHash, type HashState } from "../../app/useHashState";
import type { ReplayManifest } from "../../lib/contracts";

export class ReplayMomentError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ReplayMomentError";
  }
}

/** Seconds from the manifest's periodStart, including intervals without events. */
export function replayMomentOffset(
  route: Pick<HashState, "artifact" | "at">,
  manifest: ReplayManifest,
): number | null {
  if (route.artifact === null && route.at === null) return null;
  if (!route.artifact || !route.at || !/^[a-f0-9]{64}$/i.test(route.artifact)) {
    throw new ReplayMomentError("O link do replay está incompleto ou inválido. Abra a gravação do início.");
  }
  if (route.artifact.toLowerCase() !== manifest.eventsSha256.toLowerCase()) {
    throw new ReplayMomentError("Este link pertence a outra versão do replay. A gravação disponível mudou; abra do início.");
  }
  const duration = (Date.parse(manifest.periodEnd) - Date.parse(manifest.periodStart)) / 1000;
  const offset = Number(route.at);
  if (!/^(?:0|[1-9]\d*)(?:\.\d{1,3})?$/.test(route.at) || !Number.isFinite(offset) || offset > duration) {
    throw new ReplayMomentError("O horário deste link está fora do período gravado ou é inválido. Abra a gravação do início.");
  }
  return offset;
}

export function replayMomentUrl(url: string, manifest: ReplayManifest, offsetSeconds: number): string {
  const link = new URL(url);
  link.hash = patchHash(link.hash, {
    mode: manifest.mode === "recorded_replay" ? "recorded" : "synthetic",
    source: null,
    artifact: manifest.eventsSha256,
    at: String(Math.round(offsetSeconds * 1000) / 1000),
  });
  return link.href;
}
