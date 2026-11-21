interface Props {
  category: string | null;
  source: string | null;
  query: string;
  minImpact?: number | null;
  onClearImpact?: () => void;
  onClearCategory: () => void;
  onClearSource: () => void;
  onClearQuery: () => void;
  onClearAll: () => void;
}

/** Every active filter is visible and removable, so the list is never filtered "invisibly". */
export function ActiveFilters(p: Props) {
  const chips = [
    p.category && { label: "Category", value: p.category, clear: p.onClearCategory },
    p.source && { label: "Source", value: p.source, clear: p.onClearSource },
    p.query && { label: "Search", value: `“${p.query}”`, clear: p.onClearQuery },
    p.minImpact && p.onClearImpact && { label: "Impact", value: `${p.minImpact}+ of 10`, clear: p.onClearImpact },
  ].filter(Boolean) as { label: string; value: string; clear: () => void }[];
  if (!chips.length) return null;

  return (
    <div className="active-filters" aria-label="Active filters">
      {chips.map((c) => (
        <span key={c.label} className="filter-chip">
          <span className="muted">{c.label}:</span> {c.value}
          <button type="button" onClick={c.clear} aria-label={`Remove ${c.label.toLowerCase()} filter`}>
            ×
          </button>
        </span>
      ))}
      {chips.length > 1 && (
        <button type="button" className="link-btn" onClick={p.onClearAll}>
          Clear all
        </button>
      )}
    </div>
  );
}
