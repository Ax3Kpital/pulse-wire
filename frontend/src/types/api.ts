// Mirrors backend/app/api/schemas.py

export interface SourceRef {
  key: string;
  name: string;
  publisher: string;
  is_demo: boolean;
  paywalled?: boolean;
}

export interface CategoryChip {
  slug: string;
  name: string;
  score: number;
}

export interface StoryReport {
  item_id: number;
  source: SourceRef;
  title: string;
  url: string;
  published_at: string;
  excerpt: string | null;
  is_primary: boolean;
  method: string;
  match_score: number | null;
  match_reason: Record<string, unknown>;
  impact_score?: number;
  impact_level?: ImpactLevel;
  sentiment?: number;
  tone?: Tone;
  impact_reasons?: ImpactReason[];
}

/** low = green, moderate = yellow, important = orange, critical = red. */
export type ImpactLevel = "low" | "moderate" | "important" | "critical";
export type Tone = "negative" | "neutral" | "positive";

export interface ImpactReason {
  signal: string;
  value: string;
  points: number;
}

export interface Story {
  id: number;
  title: string;
  url: string;
  primary_source: SourceRef;
  published_at: string;
  last_member_at: string;
  excerpt: string | null;
  categories: CategoryChip[];
  source_count: number;
  member_count: number;
  impact_score: number; // 1 (routine) .. 10 (critical)
  impact_level: ImpactLevel;
  impact_reasons: ImpactReason[];
  sentiment: number; // -1 .. +1
  tone: Tone;
  primary_impact_score?: number;
  others: StoryReport[];
}

export interface StoryPage {
  items: Story[];
  total: number;
  offset: number;
  limit: number;
}

export interface CategoryNode {
  slug: string;
  name: string;
  count: number;
  children: CategoryNode[];
}

export interface HealthCheck {
  checked_at: string;
  ok: boolean;
  http_status: number | null;
  latency_ms: number | null;
  items_fetched: number;
  items_new: number;
  not_modified: boolean;
  error: string | null;
}

export interface SourceInfo {
  key: string;
  name: string;
  publisher: string;
  connector: string;
  enabled: boolean;
  is_demo: boolean;
  status: "ok" | "error" | "disabled" | "never" | "running";
  policy: string;
  retention_days: number | null;
  disabled_reason: string | null;
  last_error: string | null;
  last_error_at: string | null;
  last_success_at: string | null;
  last_fetch_at: string | null;
  homepage: string | null;
  recent: HealthCheck[];
}

export interface Run {
  id: number;
  kind: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  coalesced_requests: number;
  stats: {
    totals?: Record<string, number>;
    errors?: number;
  };
  error: string | null;
}

export interface ScanStatus {
  running: boolean;
  current_run: Run | null;
  last_run: Run | null;
  last_refresh_at: string | null;
  next_scheduled_at: string | null;
  interval_minutes: number;
}

export interface ScanTicket {
  run_id: number;
  coalesced: boolean;
  status: string;
  message: string;
}

export type Period = "24h" | "3d" | "7d" | "30d" | "all";

export interface Filters {
  q: string;
  category: string | null;
  source: string | null;
  period: Period;
  freeOnly: boolean;
  minImpact: number | null; // only stories scoring at least this (4, 6 or 8)
  sort: SortOrder;
}

export type SortOrder = "latest" | "impact";
