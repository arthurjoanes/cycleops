import { useCallback, useSyncExternalStore } from "react";
import type { DataKind } from "./useDataSession";

/** View state kept in the URL with hash routing: #/?view=all&station=... */
export interface HashState {
  view: "priorities" | "all";
  tab: "list" | "map";
  station: string | null;
  zone: "oslo" | "local";
  mode: DataKind | null;
  q: string;
  source: string | null;
  artifact: string | null;
  at: string | null;
}

const MODES: readonly DataKind[] = ["stream", "recorded", "synthetic"];

export function parseHash(hash: string): HashState {
  const params = new URLSearchParams(hash.replace(/^#\/?\??/, ""));
  return {
    view: params.get("view") === "all" ? "all" : "priorities",
    tab: params.get("tab") === "map" ? "map" : "list",
    station: params.get("station"),
    zone: params.get("zone") === "local" ? "local" : "oslo",
    mode: MODES.find((m) => m === params.get("mode")) ?? null,
    q: params.get("q") ?? "",
    source: params.get("source"),
    artifact: params.get("artifact"),
    at: params.get("at"),
  };
}

const DEFAULTS: Record<string, string> = { view: "priorities", tab: "list", zone: "oslo" };

export function formatHash(state: Partial<Record<keyof HashState, string | null>>): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(state)) {
    if (value && DEFAULTS[key] !== value) params.set(key, value);
  }
  const query = params.toString();
  return query ? `#/?${query}` : "#/";
}

/** Keeps query parameters belonging to diagnostics and other optional views. */
export function patchHash(hash: string, patch: Partial<HashState>): string {
  const params = new URLSearchParams(hash.replace(/^#\/?\??/, ""));
  for (const [key, value] of Object.entries(patch)) {
    if (value && DEFAULTS[key] !== value) params.set(key, value);
    else params.delete(key);
  }
  const query = params.toString();
  return query ? `#/?${query}` : "#/";
}

function subscribe(callback: () => void): () => void {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

export function useHashState(): [HashState, (patch: Partial<HashState>) => void] {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash);
  const state = parseHash(hash);
  const update = useCallback((patch: Partial<HashState>) => {
    const target = patchHash(window.location.hash, patch);
    if (target !== window.location.hash) window.history.replaceState(null, "", target);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  }, []);
  return [state, update];
}
