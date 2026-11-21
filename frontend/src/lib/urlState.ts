import type { Filters, Period } from "../types/api";

const PERIODS: Period[] = ["24h", "3d", "7d", "30d", "all"];
export const IMPACT_STEPS = [4, 6, 8];

/** Filters live in the URL so a reload or bookmark keeps the current view. */
export function readFilters(search: string): Filters {
  const p = new URLSearchParams(search);
  const period = p.get("period") as Period | null;
  return {
    q: p.get("q") ?? "",
    category: p.get("category"),
    source: p.get("source"),
    period: period && PERIODS.includes(period) ? period : "7d",
    freeOnly: p.get("paywalled") !== "1", // free-to-read only unless explicitly included
    minImpact: IMPACT_STEPS.includes(Number(p.get("impact"))) ? Number(p.get("impact")) : null,
    sort: p.get("sort") === "impact" ? "impact" : "latest",
  };
}

export function filtersToSearch(f: Filters): string {
  const p = new URLSearchParams();
  if (f.q) p.set("q", f.q);
  if (f.category) p.set("category", f.category);
  if (f.source) p.set("source", f.source);
  if (f.period !== "7d") p.set("period", f.period);
  if (!f.freeOnly) p.set("paywalled", "1");
  if (f.minImpact) p.set("impact", String(f.minImpact));
  if (f.sort !== "latest") p.set("sort", f.sort);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function writeFilters(f: Filters): void {
  const next = filtersToSearch(f);
  if (next !== window.location.search) {
    window.history.replaceState(null, "", `${window.location.pathname}${next}`);
  }
}
