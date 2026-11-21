import { formatClock, formatRelative } from "../lib/format";
import type { ImpactLevel, Period, ScanStatus, SortOrder, SourceInfo } from "../types/api";

const PERIODS: { value: Period; label: string }[] = [
  { value: "24h", label: "24h" },
  { value: "3d", label: "3 days" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "all", label: "All" },
];

/** Colour scale doubles as the legend: green < 4, yellow 4–5, orange 6–7, red 8–10. */
const IMPACT_FILTERS: { value: number | null; label: string; level?: ImpactLevel; hint: string }[] = [
  { value: null, label: "All", hint: "All stories, every colour" },
  { value: 4, label: "4+", level: "moderate", hint: "Yellow, orange and red (score 4–10)" },
  { value: 6, label: "6+", level: "important", hint: "Orange and red (score 6–10)" },
  { value: 8, label: "8+", level: "critical", hint: "Red only: critical (score 8–10)" },
];

interface Props {
  query: string;
  onQuery: (q: string) => void;
  period: Period;
  onPeriod: (p: Period) => void;
  freeOnly: boolean;
  onFreeOnly: (v: boolean) => void;
  minImpact: number | null;
  onMinImpact: (v: number | null) => void;
  sort: SortOrder;
  onSort: (v: SortOrder) => void;
  source: string | null;
  onSource: (key: string | null) => void;
  sources: SourceInfo[];
  status: ScanStatus | null;
  onFetchNow: () => void;
  notice: string | null;
  now: Date;
}

export function Toolbar(props: Props) {
  const { query, onQuery, period, onPeriod, source, onSource, sources, status, onFetchNow, notice, now } = props;
  const { freeOnly, onFreeOnly, minImpact, onMinImpact, sort, onSort } = props;
  const running = status?.running ?? false;

  return (
    <header className="toolbar">
      <div className="toolbar-row">
        <label className="search">
          <span className="sr-only">Search stories</span>
          <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
            <circle cx="7" cy="7" r="4.5" fill="none" stroke="currentColor" strokeWidth="1.5" />
            <path d="M10.5 10.5L14 14" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
          <input
            type="search"
            placeholder="Search headlines, companies, sources…"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
          />
        </label>

        <div className="refresh">
          <div className="refresh-text" aria-live="polite">
            {running ? (
              <span className="running">
                <span className="spinner" aria-hidden="true" /> Fetching sources…
              </span>
            ) : (
              <span>
                Updated <strong>{formatRelative(status?.last_refresh_at, now)}</strong>
              </span>
            )}
            {status?.next_scheduled_at && !running && (
              <span className="muted"> · next at {formatClock(status.next_scheduled_at)}</span>
            )}
          </div>
          <button type="button" className="btn-primary" onClick={onFetchNow} aria-busy={running}>
            {running ? "Fetching…" : "Fetch now"}
          </button>
        </div>
      </div>

      <div className="toolbar-row filters">
        <div className="segmented" role="radiogroup" aria-label="Period">
          {PERIODS.map((p) => (
            <button
              key={p.value}
              type="button"
              role="radio"
              aria-checked={period === p.value}
              className={period === p.value ? "is-active" : ""}
              onClick={() => onPeriod(p.value)}
            >
              {p.label}
            </button>
          ))}
        </div>

        <label className="toggle" title="Hide stories that are only available behind a paywall (FT, Bloomberg, WaPo)">
          <input type="checkbox" checked={freeOnly} onChange={(e) => onFreeOnly(e.target.checked)} />
          <span className="toggle-track" aria-hidden="true" />
          Free to read only
        </label>

        <div className="segmented impact-filter" role="radiogroup" aria-label="Minimum impact">
          <span className="segmented-label" aria-hidden="true">
            Impact
          </span>
          {IMPACT_FILTERS.map((o) => (
            <button
              key={o.label}
              type="button"
              role="radio"
              aria-checked={minImpact === o.value}
              aria-label={o.value ? `Impact ${o.label}` : "Any impact"}
              title={o.hint}
              className={minImpact === o.value ? "is-active" : ""}
              onClick={() => onMinImpact(o.value)}
            >
              {o.level && <span className={`dot dot-${o.level}`} aria-hidden="true" />}
              {o.label}
            </button>
          ))}
        </div>

        <label className="select">
          <span className="sr-only">Sort</span>
          <select value={sort} onChange={(e) => onSort(e.target.value as SortOrder)}>
            <option value="latest">Newest first</option>
            <option value="impact">Most important first</option>
          </select>
        </label>

        <label className="select">
          <span className="sr-only">Source</span>
          <select value={source ?? ""} onChange={(e) => onSource(e.target.value || null)}>
            <option value="">All sources</option>
            {sources
              .filter((s) => s.enabled)
              .map((s) => (
                <option key={s.key} value={s.key}>
                  {s.name}
                </option>
              ))}
          </select>
        </label>
      </div>

      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
    </header>
  );
}
