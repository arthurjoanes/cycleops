// Bundle budget: gzip of the entry chunk, the chunks it imports statically (in chain) and
// their CSS. Dynamic imports (the map) stay out. Fails above apps/web/bundle-budget.json,
// which holds the first build plus 10%.
import { readFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const dist = new URL("../dist/", import.meta.url);
const manifest = JSON.parse(readFileSync(new URL(".vite/manifest.json", dist), "utf8"));
const budget = JSON.parse(readFileSync(new URL("../bundle-budget.json", import.meta.url), "utf8"));

const entry = Object.values(manifest).find((chunk) => chunk.isEntry);
if (!entry) throw new Error("no entry chunk in dist/.vite/manifest.json");

const files = new Set();
const visit = (chunk) => {
  if (files.has(chunk.file)) return;
  files.add(chunk.file);
  for (const css of chunk.css ?? []) files.add(css);
  for (const key of chunk.imports ?? []) visit(manifest[key]);
};
visit(entry);

let total = 0;
for (const file of files) {
  const size = gzipSync(readFileSync(new URL(file, dist))).length;
  total += size;
  console.log(`${String(size).padStart(8)}  ${file}`);
}
console.log(`${String(total).padStart(8)}  total gzip (budget ${budget.maxGzipBytes})`);
if (total > budget.maxGzipBytes) {
  console.error(`over budget by ${total - budget.maxGzipBytes} bytes`);
  process.exit(1);
}
