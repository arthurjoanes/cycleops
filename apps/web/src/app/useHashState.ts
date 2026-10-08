import { useCallback, useSyncExternalStore } from "react";

/** View state kept in the URL with hash routing: #/?view=all&station=... */
export interface HashState {
  view: "priorities" | "all";
  tab: "list" | "map";
  station: string | null;
  zone: "oslo" | "local";
  mode: "recorded" | "synthetic" | null;
  q: string;
  source: string | null;
}

export function parseHash(hash: string): HashState {
  const params = new URLSearchParams(hash.replace(/^#\/?\??/, ""));
  return {
    view: params.get("view") === "all" ? "all" : "priorities",
    tab: params.get("tab") === "map" ? "map" : "list",
    station: params.get("station"),
    zone: params.get("zone") === "local" ? "local" : "oslo",
    mode: params.get("mode") === "recorded" ? "recorded" : params.get("mode") === "synthetic" ? "synthetic" : null,
    q: params.get("q") ?? "",
    source: params.get("source"),
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

function subscribe(callback: () => void): () => void {
  window.addEventListener("hashchange", callback);
  return () => window.removeEventListener("hashchange", callback);
}

export function useHashState(): [HashState, (patch: Partial<HashState>) => void] {
  const hash = useSyncExternalStore(subscribe, () => window.location.hash);
  const state = parseHash(hash);
  const update = useCallback((patch: Partial<HashState>) => {
    const next = { ...parseHash(window.location.hash), ...patch };
    const target = formatHash(next);
    if (target !== window.location.hash) window.history.replaceState(null, "", target);
    window.dispatchEvent(new HashChangeEvent("hashchange"));
  }, []);
  return [state, update];
}
