import type { ImpactLevel, ImpactReason, Tone } from "../types/api";

const TIMEZONE = "Europe/Bucharest";

const absolute = new Intl.DateTimeFormat("en-GB", {
  timeZone: TIMEZONE,
  day: "2-digit",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const shortTime = new Intl.DateTimeFormat("en-GB", {
  timeZone: TIMEZONE,
  hour: "2-digit",
  minute: "2-digit",
});

/** "12 Nov 2026, 14:05" in the user's timezone (Europe/Bucharest). */
export function formatAbsolute(iso: string | null | undefined): string {
  if (!iso) return "—";
  return absolute.format(new Date(iso));
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  return shortTime.format(new Date(iso));
}

/** "just now", "5 min ago", "3 h ago", "2 d ago", then absolute date. */
export function formatRelative(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return "never";
  const seconds = Math.round((now.getTime() - new Date(iso).getTime()) / 1000);
  if (seconds < 0) return "in " + formatDuration(-seconds);
  if (seconds < 45) return "just now";
  if (seconds < 7 * 86400) return formatDuration(seconds) + " ago";
  return formatAbsolute(iso);
}

function formatDuration(seconds: number): string {
  if (seconds < 3600) return `${Math.max(1, Math.round(seconds / 60))} min`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h`;
  return `${Math.round(seconds / 86400)} d`;
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Human-readable explanation of why a report was grouped into a story. */
export function describeMatch(method: string, reason: Record<string, unknown>): string {
  if (method === "identical_title") return "Identical headline";
  if (method === "seed") return "First report";
  const parts: string[] = [];
  const rule = String(reason.rule ?? "");
  if (rule === "near_identical_title") parts.push("near-identical headline");
  if (typeof reason.title_sim === "number") parts.push(`headline similarity ${Math.round(reason.title_sim * 100)}%`);
  const ents = reason.shared_entities as string[] | undefined;
  if (ents?.length) parts.push(`shared: ${ents.join(", ")}`);
  const nums = reason.shared_numbers as string[] | undefined;
  if (nums?.length) parts.push(`same figures: ${nums.map(formatFigure).join(", ")}`);
  return parts.join(" · ") || rule;
}

/** "pct:3.1" -> "3.1%", "bp:25" -> "25 bp", "money:$:4.1e+09" -> "$4.1bn", "num:142000" -> "142,000". */
export function formatFigure(token: string): string {
  const [kind, a, b] = token.split(":");
  if (kind === "pct") return `${a}%`;
  if (kind === "bp") return `${a} bp`;
  if (kind === "num") return Number(a).toLocaleString("en-US");
  if (kind === "money") {
    const v = Number(b);
    if (v >= 1e9) return `${a}${+(v / 1e9).toFixed(2)}bn`;
    if (v >= 1e6) return `${a}${+(v / 1e6).toFixed(2)}m`;
    return `${a}${v.toLocaleString("en-US")}`;
  }
  return token;
}

const SIGNAL_LABEL: Record<string, string> = {
  coverage: "Coverage",
  relevance: "Relevance",
  tone: "Tone",
  source: "Market-moving source",
  event: "Event",
  magnitude: "Magnitude",
  actors: "Key actors",
  followup: "Follow-up format",
  off_topic: "Off-topic",
};

export const IMPACT_LABEL: Record<ImpactLevel, string> = {
  low: "Normal",
  moderate: "Moderate",
  important: "Important",
  critical: "Critical",
};

/** "Negative tone (−0.61)" / "Neutral tone" / "Positive tone (+0.46)". */
export function describeTone(tone: Tone, sentiment: number): string {
  const label = tone[0].toUpperCase() + tone.slice(1);
  if (tone === "neutral") return `${label} tone`;
  const sign = sentiment > 0 ? "+" : "−";
  return `${label} tone (${sign}${Math.abs(sentiment).toFixed(2)})`;
}

/** One line per scoring signal, e.g. "Coverage (3 publishers) +14". */
export function describeImpact(reasons: ImpactReason[] = []): string[] {
  return reasons.map((r) => {
    const sign = r.points >= 0 ? "+" : "";
    return `${SIGNAL_LABEL[r.signal] ?? r.signal}${r.value ? ` (${r.value})` : ""} ${sign}${r.points}`;
  });
}
