#!/usr/bin/env bash
# Builds apps/web/public/basemap/basemap.geojson and SOURCE.json from OpenStreetMap.
# Run by hand: scripts/basemap/generate.sh. Never in the build or CI.
# Needs bash, curl, gzip and Docker. On Windows, Git Bash or WSL2.
#
# If Overpass fails: download the Geofabrik Østlandet extract
# (https://download.geofabrik.de/europe/norway/ostlandet-latest.osm.pbf) to a folder
# outside OneDrive, cut it with `osmium extract -b W,S,E,N` (bbox below) and point $OSM at
# the result. Not implemented because Overpass worked.
set -euo pipefail
# Keeps Git Bash from rewriting container paths (/w) in the docker arguments.
export MSYS_NO_PATHCONV=1

cd "$(dirname "$0")/../.."
# pwd -W gives C:/... on Git Bash, which Docker Desktop can mount; plain pwd elsewhere.
ROOT=$(pwd -W 2>/dev/null || pwd)

# ubuntu-small-3.13.3 (GDAL 3.13.3), digest read with docker inspect on 2026-10-07.
IMAGE=ghcr.io/osgeo/gdal:ubuntu-small-3.13.3@sha256:64250faf833c06d4b21afce4c27190039ba7ab58d70f0eebc87cf77d929c0b40

# Stations of station_information in fixtures/recorded/inspect-20261007T032439Z.ndjson.gz
# (267 stations, lat 59.89844..59.95341, lon 10.65112..10.81431) plus 1 km on each side
# (0.00898 deg of lat, 0.01795 deg of lon at 59.96 N), rounded outward.
S=59.8894 W=10.6331 N=59.9624 E=10.8323

QUERY=$(cat <<EOF
[out:xml][timeout:180][bbox:$S,$W,$N,$E];
(
  way["natural"~"^(water|bay|wood|coastline)\$"];
  relation["natural"~"^(water|bay|wood)\$"];
  relation["waterway"="riverbank"];
  way["landuse"~"^(reservoir|grass|recreation_ground|forest)\$"];
  relation["landuse"~"^(reservoir|grass|recreation_ground|forest)\$"];
  way["leisure"~"^(park|garden)\$"];
  relation["leisure"~"^(park|garden)\$"];
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary)(_link)?\$"];
);
(._;>;);
out body;
EOF
)

CACHE=scripts/basemap/cache
OUT=apps/web/public/basemap
# Named after the query, so a changed query or bbox never reuses an old download.
OSM=$CACHE/overpass-$(printf '%s' "$QUERY" | sha256sum | cut -c1-12).osm
mkdir -p "$CACHE" "$OUT"

if [[ -s $OSM ]]; then
  echo "reusing $OSM"
else
  # overpass-api.de answers 406 to curl's default User-Agent.
  curl -sS --fail --max-time 600 -A 'CycleOps-basemap/1 (scripts/basemap, run by hand)' \
    --data-urlencode "data=$QUERY" -o "$OSM.part" https://overpass-api.de/api/interpreter
  # Overpass answers 200 with a <remark> when it runs out of time or memory.
  if grep -m1 '<remark>' "$OSM.part" >&2; then exit 1; fi
  date -u +%Y-%m-%dT%H:%M:%SZ >"$OSM.retrievedAt"
  mv "$OSM.part" "$OSM"
fi

gdal() { docker run --rm -i -v "$ROOT:/w" -w /w "$IMAGE" "$@"; }

WORK=$CACHE/work
rm -rf "$WORK"
mkdir -p "$WORK"

# piece <name> <SQL over the OSM driver layers, SQLite dialect> [more ogr2ogr options]
# Tags without their own column in GDAL's default osmconf.ini are only in other_tags.
piece() {
  local name=$1 sql=$2
  shift 2
  gdal ogr2ogr -f GeoJSONSeq "$WORK/$name.geojsonl" "$OSM" -dialect SQLite -sql "$sql" \
    -clipdst "$W" "$S" "$E" "$N" -simplify 0.00002 -lco COORDINATE_PRECISION=5 "$@"
}

piece water "SELECT 'water' AS kind, geometry FROM multipolygons
  WHERE \"natural\" IN ('water', 'bay') OR landuse = 'reservoir'
     OR other_tags LIKE '%\"waterway\"=>\"riverbank\"%'"
piece park "SELECT 'park' AS kind, geometry FROM multipolygons
  WHERE leisure IN ('park', 'garden') OR \"natural\" = 'wood'
     OR landuse IN ('grass', 'recreation_ground', 'forest')"
# The fjord has no water polygon, only natural=coastline ways. Open ones land in lines;
# closed ones (islands) land in multipolygons. ST_Boundary turns those into lines before
# the clip; clipping the polygon first would draw the bbox edge as shore.
piece coast "SELECT 'coast' AS kind, geometry FROM lines
  WHERE other_tags LIKE '%\"natural\"=>\"coastline\"%'"
piece coast-islands "SELECT 'coast' AS kind, ST_Boundary(geometry) AS geometry
  FROM multipolygons WHERE \"natural\" = 'coastline'"
# Tunnels are left out: drawn at street level they would cross the fjord and the parks.
piece road "SELECT 'road' AS kind,
    CASE WHEN highway LIKE 'motorway%' OR highway LIKE 'trunk%' THEN 1
         WHEN highway LIKE 'primary%' OR highway LIKE 'secondary%' THEN 2
         ELSE 3 END AS rank,
    geometry FROM lines
  WHERE highway IN ('motorway', 'motorway_link', 'trunk', 'trunk_link',
                    'primary', 'primary_link', 'secondary', 'secondary_link',
                    'tertiary', 'tertiary_link')
    AND COALESCE(other_tags, '') NOT LIKE '%\"tunnel\"=>\"yes\"%'"

gdal python3 - "$WORK" "$OUT" "$QUERY" "$(cat "$OSM.retrievedAt")" "$IMAGE" "$W" "$S" "$E" "$N" <<'PY'
import collections, glob, json, sys

work, out, query, retrieved_at, image = sys.argv[1:6]
bbox = [float(v) for v in sys.argv[6:10]]
features = []
for path in sorted(glob.glob(f"{work}/*.geojsonl")):
    with open(path, encoding="utf-8") as f:
        features += [json.loads(line) for line in f if line.strip()]
with open(f"{out}/basemap.geojson", "w", encoding="utf-8", newline="\n") as f:
    json.dump({"type": "FeatureCollection", "features": features}, f, separators=(",", ":"))
    f.write("\n")
source = {
    "source": "OpenStreetMap via Overpass API, https://overpass-api.de",
    "query": query,
    "retrievedAt": retrieved_at,
    "license": "ODbL-1.0",
    "attribution": "© OpenStreetMap contributors",
    "bbox": bbox,
    "image": image,
}
with open(f"{out}/SOURCE.json", "w", encoding="utf-8", newline="\n") as f:
    json.dump(source, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(dict(collections.Counter(feat["properties"]["kind"] for feat in features)))
PY

raw=$(wc -c <"$OUT/basemap.geojson")
gz=$(gzip -c "$OUT/basemap.geojson" | wc -c)
echo "basemap.geojson: $raw bytes, $gz bytes gzip"
# At most 2 MB with gzip.
if ((gz > 2 * 1024 * 1024)); then
  echo "over the 2 MB gzip cap" >&2
  exit 1
fi
