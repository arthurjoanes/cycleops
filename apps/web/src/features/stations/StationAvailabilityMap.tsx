import "maplibre-gl/dist/maplibre-gl.css";
import * as maplibre from "maplibre-gl";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { useEffect, useRef } from "react";
import type { CatalogStation, Condition, StationState } from "../../lib/contracts";
import { useStations } from "./store";
import { CONDITION_STYLE, INCIDENT_LEGEND, mapColors, stationTone } from "./mapStyle";
import styles from "./StationAvailabilityMap.module.css";

type Feature = GeoJSON.Feature<GeoJSON.Point, { stationId: string; condition: Condition | "none" }>;

function features(catalog: ReadonlyMap<string, CatalogStation>, stations: ReadonlyMap<string, StationState>): GeoJSON.FeatureCollection<GeoJSON.Point> {
  const list: Feature[] = [...catalog.values()].map((s) => ({
    type: "Feature",
    geometry: { type: "Point", coordinates: [s.lon, s.lat] },
    properties: { stationId: s.stationId, condition: stations.get(s.stationId)?.decision.primaryCondition ?? "none" },
  }));
  return { type: "FeatureCollection", features: list };
}

/** Station symbol drawn on a canvas: the symbol carries the condition with the color. */
function symbol(glyph: string): ImageData | null {
  const size = 32;
  const canvas = document.createElement("canvas");
  canvas.width = canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = "#ffffff";
  ctx.font = `700 ${size * 0.6}px Inter, system-ui, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(glyph, size / 2, size / 2 + 1);
  return ctx.getImageData(0, 0, size, size);
}

export default function StationAvailabilityMap(props: {
  selected: string | null;
  onSelect(id: string): void;
  onUnavailable(): void;
  rightPadding: number;
}) {
  const container = useRef<HTMLDivElement>(null);
  const mapRef = useRef<maplibre.Map | null>(null);
  // Set on "load". isStyleLoaded() turns false again while a GeoJSON update is in flight,
  // so it cannot tell whether the style is there.
  const readyRef = useRef(false);
  const selectedRef = useRef<string | null>(null);
  const { onSelect, onUnavailable } = props;

  useEffect(() => {
    if (!container.current) return;
    maplibre.setWorkerUrl(workerUrl);
    const colors = mapColors(container.current);
    const { catalog, stations } = useStations.getState();
    let map: maplibre.Map;
    try {
      map = new maplibre.Map({
        container: container.current,
        attributionControl: false,
        style: {
          version: 8,
          sources: {
            base: { type: "geojson", data: `${import.meta.env.BASE_URL}basemap/basemap.geojson` },
            stations: { type: "geojson", data: features(catalog, stations), promoteId: "stationId" },
          },
          layers: [
            { id: "land", type: "background", paint: { "background-color": colors.land } },
            { id: "park", type: "fill", source: "base", filter: ["==", ["get", "kind"], "park"], paint: { "fill-color": colors.park } },
            { id: "water", type: "fill", source: "base", filter: ["==", ["get", "kind"], "water"], paint: { "fill-color": colors.water } },
            { id: "coast", type: "line", source: "base", filter: ["==", ["get", "kind"], "coast"], paint: { "line-color": colors.water, "line-width": 2 } },
            {
              id: "road",
              type: "line",
              source: "base",
              filter: ["==", ["get", "kind"], "road"],
              paint: {
                "line-color": colors.road,
                "line-width": ["match", ["get", "rank"], 1, 3, 2, 2, 1],
              },
            },
            {
              id: "stations",
              type: "circle",
              source: "stations",
              paint: {
                "circle-radius": ["case", ["boolean", ["feature-state", "selected"], false], 11, 8],
                "circle-color": ["coalesce", ["feature-state", "color"], colors.stale],
                "circle-opacity": ["case", ["boolean", ["feature-state", "dimmed"], false], 0.45, 1],
                "circle-stroke-color": ["case", ["boolean", ["feature-state", "selected"], false], colors.focus, "#ffffff"],
                "circle-stroke-width": ["case", ["boolean", ["feature-state", "selected"], false], 3, 1.5],
              },
            },
            {
              id: "station-symbols",
              type: "symbol",
              source: "stations",
              layout: { "icon-image": ["get", "condition"], "icon-allow-overlap": true, "icon-ignore-placement": true },
            },
          ],
        },
        bounds: bounds(catalog),
        fitBoundsOptions: { padding: 24 },
      });
    } catch (error) {
      if (error instanceof maplibre.GPUInitializationError) {
        onUnavailable();
        return;
      }
      throw error;
    }
    mapRef.current = map;
    // Bottom left: the details panel covers the right side of the map.
    map.addControl(
      new maplibre.AttributionControl({ compact: false, customAttribution: "© OpenStreetMap contributors" }),
      "bottom-left",
    );
    map.getCanvas().setAttribute("aria-label", "Mapa das estações. A lista Todas as estações tem o mesmo conteúdo.");
    // Symbols are added when the layout asks for them, so the first layout already has them.
    map.setMissingStyleImageResolver((id) => {
      const style = CONDITION_STYLE[id as Condition | "none"] as (typeof CONDITION_STYLE)["ok"] | undefined;
      const image = style && symbol(style.glyph);
      if (image) map.addImage(id, image, { pixelRatio: 2 });
    });
    map.on("load", () => {
      readyRef.current = true;
      paint(new Map(), useStations.getState().stations);
    });
    const popup = new maplibre.Popup({ closeButton: false, offset: 12 });
    map.on("click", "stations", (event) => {
      const id = event.features?.[0]?.properties?.stationId as string | undefined;
      if (!id) return;
      onSelect(id);
      // Station names are text, never HTML.
      popup.setLngLat(event.lngLat).setText(useStations.getState().catalog.get(id)?.name ?? id).addTo(map);
    });
    map.on("mouseenter", "stations", () => (map.getCanvas().style.cursor = "pointer"));
    map.on("mouseleave", "stations", () => (map.getCanvas().style.cursor = ""));

    // Updates go out once per frame, only for stations whose state object changed (the store
    // keeps unchanged ones by reference): feature-state for paint, updateData only when the
    // primary condition (the symbol) changes. A change in dimming repaints every station.
    let frame = 0;
    let shownDimmed = false;
    let shown = useStations.getState().stations;
    let shownCatalog = useStations.getState().catalogVersion;
    let pending = false;
    const paint = (before: ReadonlyMap<string, StationState>, after: ReadonlyMap<string, StationState>) => {
      if (!readyRef.current) return;
      const dimmed = ["stale", "quarantined"].includes(useStations.getState().feedHealth?.state ?? "");
      const all = dimmed !== shownDimmed;
      shownDimmed = dimmed;
      const update: maplibre.GeoJSONSourceDiff["update"] = [];
      for (const [id, state] of after) {
        const old = before.get(id);
        if (old === state && !all) continue;
        const condition = state.decision.primaryCondition;
        map.setFeatureState({ source: "stations", id }, { color: colors[stationTone(state)], dimmed });
        if (old?.decision.primaryCondition !== condition) {
          update.push({ id, addOrUpdateProperties: [{ key: "condition", value: condition }] });
        }
      }
      const source = map.getSource<maplibre.GeoJSONSource>("stations");
      if (update.length && source) void source.updateData({ update });
    };
    const unsubscribe = useStations.subscribe((state) => {
      if (pending) return;
      pending = true;
      frame = requestAnimationFrame(() => {
        pending = false;
        const current = useStations.getState();
        const source = map.getSource<maplibre.GeoJSONSource>("stations");
        if (current.catalogVersion !== shownCatalog && source) {
          shownCatalog = current.catalogVersion;
          source.setData(features(current.catalog, current.stations));
          paint(new Map(), current.stations);
        } else {
          paint(shown, current.stations);
        }
        shown = current.stations;
        void state;
      });
    });
    return () => {
      cancelAnimationFrame(frame);
      unsubscribe();
      popup.remove();
      map.remove();
      mapRef.current = null;
      readyRef.current = false;
    };
  }, [onSelect, onUnavailable]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const apply = () => {
      if (selectedRef.current) map.setFeatureState({ source: "stations", id: selectedRef.current }, { selected: false });
      if (props.selected) map.setFeatureState({ source: "stations", id: props.selected }, { selected: true });
      selectedRef.current = props.selected;
    };
    if (readyRef.current) apply();
    else map.once("load", apply);
  }, [props.selected]);

  useEffect(() => {
    mapRef.current?.setPadding({ top: 0, bottom: 0, left: 0, right: props.rightPadding });
  }, [props.rightPadding]);

  return (
    <div className={styles.wrap}>
      <div ref={container} className={styles.map} />
      {/* The details panel covers the right side of the map; the legend stays to its left. */}
      <ul
        className={styles.legend}
        aria-label="Legenda do mapa"
        style={props.rightPadding ? { paddingRight: `min(${props.rightPadding}px, 50%)` } : undefined}
      >
        {Object.entries(CONDITION_STYLE).map(([condition, style]) => (
          <li key={condition}>
            <span className={styles.symbol} data-tone={style.tone} aria-hidden="true">
              {style.glyph}
            </span>
            {style.label}
          </li>
        ))}
        {INCIDENT_LEGEND.map((entry) => (
          <li key={entry.tone}>
            <span className={styles.symbol} data-tone={entry.tone} aria-hidden="true" />
            {entry.label}
          </li>
        ))}
      </ul>
    </div>
  );
}

function bounds(catalog: ReadonlyMap<string, CatalogStation>): maplibre.LngLatBoundsLike | undefined {
  if (!catalog.size) return [10.65, 59.89, 10.82, 59.96];
  let west = Infinity;
  let south = Infinity;
  let east = -Infinity;
  let north = -Infinity;
  for (const s of catalog.values()) {
    west = Math.min(west, s.lon);
    east = Math.max(east, s.lon);
    south = Math.min(south, s.lat);
    north = Math.max(north, s.lat);
  }
  return [west, south, east, north];
}
