import { execSync } from "node:child_process";
import { mkdirSync, writeFileSync } from "node:fs";
import { cpus, platform, release, totalmem } from "node:os";
import type { Browser } from "@playwright/test";

export const RESULTS = new URL("../../../../docs/results/data/", import.meta.url);

export function percentile(values: number[], p: number): number {
  const sorted = [...values].sort((a, b) => a - b);
  if (!sorted.length) return Number.NaN;
  const k = (sorted.length - 1) * (p / 100);
  const lo = Math.floor(k);
  const hi = Math.min(lo + 1, sorted.length - 1);
  return (sorted[lo] as number) + ((sorted[hi] as number) - (sorted[lo] as number)) * (k - lo);
}

export const round = (value: number, digits = 1) => Math.round(value * 10 ** digits) / 10 ** digits;

/** Where and on what the numbers were taken. `dirty`: the paths whose local changes count. */
export function environment(browser: Browser, dirty = "../../apps/web/src") {
  let commit = "unknown";
  try {
    commit = execSync("git rev-parse --short HEAD", { encoding: "utf8" }).trim();
    if (execSync(`git status --porcelain -- ${dirty}`, { encoding: "utf8" }).trim()) commit += " + local changes";
  } catch {
    // not a git checkout
  }
  return {
    cpu: cpus()[0]?.model.trim() ?? "unknown",
    cores: cpus().length,
    memoryGb: round(totalmem() / 1024 ** 3, 1),
    os: `${platform()} ${release()}`,
    browser: `chromium ${browser.version()} (Playwright, headless)`,
    commit,
    date: new Date().toISOString(),
  };
}

export function save(name: string, data: unknown) {
  mkdirSync(RESULTS, { recursive: true });
  writeFileSync(new URL(name, RESULTS), `${JSON.stringify(data, null, 2)}\n`);
}
