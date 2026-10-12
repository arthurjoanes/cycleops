import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { gunzipSync } from "node:zlib";
import { describe, expect, it } from "vitest";
import { replayReport } from "../../src/features/analysis/recordingReport";
import { validateReplayCatalog, validateReplayManifest } from "../../src/lib/contracts";
import { parseEvents } from "../../src/streams/artifact";

describe("published replay observation reports", () => {
  for (const kind of ["recorded", "synthetic"]) {
    it(`partitions the ${kind} window and station exposure without counting gaps as availability`, () => {
      const base = resolve("public", "replay", kind);
      const manifest: unknown = JSON.parse(readFileSync(resolve(base, "manifest.json"), "utf8"));
      const catalog: unknown = JSON.parse(readFileSync(resolve(base, "catalog.json"), "utf8"));
      if (!validateReplayManifest(manifest) || !validateReplayCatalog(catalog)) throw new Error("Invalid replay fixture");
      const events = parseEvents(gunzipSync(readFileSync(resolve(base, "events.ndjson.gz"))).toString("utf8"));
      const report = replayReport({ manifest, catalog, events });
      expect(report.sourceValidMs + report.sourceGapMs + report.sourceClosedMs).toBeCloseTo(report.durationMs, 3);
      expect(report.durationMs).toBeGreaterThan(0);
      expect(report.confirmed).toBe(manifest.incidents.length);
      expect(report.resolved).toBe(manifest.incidents.filter((incident) => incident.resolvedSequence !== null).length);
      for (const station of report.stations) {
        expect(station.validMs + station.gapMs + station.closedMs).toBeCloseTo(station.catalogMs, 3);
        expect(station.emptyMs).toBeLessThanOrEqual(station.bikesObservedMs);
        expect(station.fullMs).toBeLessThanOrEqual(station.docksObservedMs);
        expect(station.bikesObservedMs).toBeLessThanOrEqual(station.validMs);
        expect(station.docksObservedMs).toBeLessThanOrEqual(station.validMs);
      }
    });
  }
});
