const paths = {
  play: "M8 5l11 7-11 7Z",
  pause: "M8 5v14M16 5v14",
  step: "M5 5l10 7-10 7ZM19 5v14",
  share: "M10 13a5 5 0 0 0 7 0l3-3a5 5 0 0 0-7-7l-2 2M14 11a5 5 0 0 0-7 0l-3 3a5 5 0 0 0 7 7l2-2",
  restart: "M4 10a8 8 0 1 1 1 8M4 4v6h6",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20ZM12 11v6M12 7v.5",
  chart: "M4 4v16h16M8 16v-4M12 16V8M16 16V5",
  close: "M6 6l12 12M18 6 6 18",
  list: "M8 6h13M8 12h13M8 18h13M3 6h.5M3 12h.5M3 18h.5",
  map: "M3 5l6-2 6 2 6-2v16l-6 2-6-2-6 2ZM9 3v16M15 5v16",
  bike: "M8 17a3 3 0 1 0-6 0 3 3 0 0 0 6 0ZM22 17a3 3 0 1 0-6 0 3 3 0 0 0 6 0ZM5 17l5-9 5 9H5M8 8h5M15 5h3l1 12",
  dock: "M4 3h16v18H4ZM9 17V7h4a3 3 0 0 1 0 6H9",
  coverage: "M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6ZM8 12l3 3 5-6",
  disabled: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20ZM5 5l14 14",
} as const;

export function ActionIcon({ name, size = 18 }: { name: keyof typeof paths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={paths[name]} /></svg>;
}
