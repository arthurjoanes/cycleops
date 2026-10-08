// Serves dist/ under /cycleops/ the way GitHub Pages does for this site: files as they
// are (a .gz goes out as application/gzip, without Content-Encoding), 404 for anything
// missing, no SPA fallback. Used by the E2E tests and by `pnpm preview:pages`.
import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import { extname, join, normalize, sep } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = fileURLToPath(new URL("../dist/", import.meta.url));
const BASE = "/cycleops/";
const PORT = Number(process.env.PORT ?? 4173);
const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".geojson": "application/geo+json",
  ".gz": "application/gzip",
  ".woff2": "font/woff2",
  ".png": "image/png",
  ".svg": "image/svg+xml",
  ".map": "application/json; charset=utf-8",
};

createServer(async (request, response) => {
  let path;
  try {
    path = decodeURIComponent(new URL(request.url ?? "/", "http://localhost").pathname);
  } catch {
    response.writeHead(400).end("bad request");
    return;
  }
  if (path === "/cycleops") {
    response.writeHead(301, { Location: BASE }).end();
    return;
  }
  if (!path.startsWith(BASE)) {
    response.writeHead(404).end("not found");
    return;
  }
  const relative = path.slice(BASE.length) || "index.html";
  const file = normalize(join(DIST, relative.endsWith("/") ? `${relative}index.html` : relative));
  if (!file.startsWith(DIST.endsWith(sep) ? DIST : DIST + sep)) {
    response.writeHead(404).end("not found");
    return;
  }
  try {
    const info = await stat(file);
    if (!info.isFile()) throw new Error("not a file");
    response.writeHead(200, {
      "Content-Type": TYPES[extname(file)] ?? "application/octet-stream",
      "Content-Length": info.size,
      "Cache-Control": "no-cache",
    });
    createReadStream(file).pipe(response);
  } catch {
    response.writeHead(404).end("not found");
  }
}).listen(PORT, "127.0.0.1", () => {
  console.log(`serving dist/ at http://127.0.0.1:${PORT}${BASE}`);
});
