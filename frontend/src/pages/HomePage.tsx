import { useCallback, useEffect, useRef, useState } from "react";
import { Sidebar } from "../components/Sidebar";
import { StoryCard } from "../components/StoryCard";
import { ActiveFilters } from "../components/ActiveFilters";
import { Toolbar } from "../components/Toolbar";
import { api } from "../lib/api";
import { useAsync, useDebounced, useNow } from "../lib/hooks";
import { readFilters, writeFilters } from "../lib/urlState";
import type { CategoryNode, Filters, Story } from "../types/api";

function findName(nodes: CategoryNode[], slug: string | null): string | null {
  for (const n of nodes) {
    if (n.slug === slug) return n.name;
    const child = n.children.find((c) => c.slug === slug);
    if (child) return `${n.name} / ${child.name}`;
  }
  return null;
}

export function HomePage() {
  const now = useNow();
  const [filters, setFilters] = useState<Filters>(() => readFilters(window.location.search));
  const [queryInput, setQueryInput] = useState(filters.q);
  const debouncedQuery = useDebounced(queryInput, 250);
  const [stories, setStories] = useState<Story[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [wasRunning, setWasRunning] = useState(false);
  const requestId = useRef(0);

  useEffect(() => {
    setFilters((f) => (f.q === debouncedQuery ? f : { ...f, q: debouncedQuery }));
  }, [debouncedQuery]);

  useEffect(() => {
    writeFilters(filters);
  }, [filters]);

  const status = useAsync(() => api.scanStatus(), [], wasRunning ? 2_000 : 15_000);
  const sources = useAsync(() => api.sources(), [], 30_000);
  const categories = useAsync(
    () => api.categories(filters.period, filters.source, filters.freeOnly, filters.minImpact),
    [filters.period, filters.source, filters.freeOnly, filters.minImpact],
    60_000,
  );

  const load = useCallback(
    async (offset: number) => {
      const id = ++requestId.current;
      setLoading(true);
      try {
        const page = await api.stories(filters, offset);
        if (id !== requestId.current) return;
        setStories((prev) => (offset === 0 ? page.items : [...prev, ...page.items]));
        setTotal(page.total);
        setError(null);
      } catch (err) {
        if (id === requestId.current) setError((err as Error).message);
      } finally {
        if (id === requestId.current) setLoading(false);
      }
    },
    [filters],
  );

  useEffect(() => {
    void load(0);
  }, [load]);

  // When a scan finishes, refresh everything once.
  const running = status.data?.running ?? false;
  useEffect(() => {
    if (running) setWasRunning(true);
    if (!running && wasRunning) {
      setWasRunning(false);
      const totals = status.data?.last_run?.stats?.totals;
      const errors = status.data?.last_run?.stats?.errors ?? 0;
      setNotice(
        totals
          ? `Scan finished: ${totals.new ?? 0} new items, ${totals.new_stories ?? 0} new stories` +
              (errors ? ` · ${errors} source${errors > 1 ? "s" : ""} failed` : "")
          : "Scan finished.",
      );
      void load(0);
      sources.reload();
      categories.reload();
    }
  }, [running]);

  const fetchNow = async () => {
    try {
      const ticket = await api.scan();
      setNotice(ticket.message);
      setWasRunning(true);
      status.reload();
    } catch (err) {
      setNotice(`Could not start a scan: ${(err as Error).message}`);
    }
  };

  const update = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }));
  const clearAll = () => {
    setQueryInput("");
    setFilters((f) => ({ ...f, q: "", category: null, source: null, minImpact: null }));
  };
  const heading = findName(categories.data ?? [], filters.category) ?? "All stories";
  const sourceName = sources.data?.find((s) => s.key === filters.source)?.name;

  return (
    <div className="layout">
      <Sidebar
        categories={categories.data ?? []}
        sources={sources.data ?? []}
        active={filters.category}
        onSelect={(category) => update({ category })}
      />
      <main className="main">
        <Toolbar
          query={queryInput}
          onQuery={setQueryInput}
          period={filters.period}
          onPeriod={(period) => update({ period })}
          freeOnly={filters.freeOnly}
          onFreeOnly={(freeOnly) => update({ freeOnly })}
          minImpact={filters.minImpact}
          onMinImpact={(minImpact) => update({ minImpact })}
          sort={filters.sort}
          onSort={(sort) => update({ sort })}
          source={filters.source}
          onSource={(source) => update({ source })}
          sources={sources.data ?? []}
          status={status.data}
          onFetchNow={fetchNow}
          notice={notice}
          now={now}
        />

        <div className="feed-head">
          <h1>{heading}</h1>
          <p className="muted" aria-live="polite">
            {loading && stories.length === 0 ? "Loading…" : `${total} ${total === 1 ? "story" : "stories"}`}
          </p>
        </div>

        <ActiveFilters
          category={filters.category ? heading : null}
          source={filters.source ? (sourceName ?? filters.source) : null}
          query={filters.q}
          minImpact={filters.minImpact}
          onClearImpact={() => update({ minImpact: null })}
          onClearCategory={() => update({ category: null })}
          onClearSource={() => update({ source: null })}
          onClearQuery={() => {
            setQueryInput("");
            update({ q: "" });
          }}
          onClearAll={clearAll}
        />

        {error && (
          <p className="error-box" role="alert">
            Could not load stories ({error}). Is the backend running on 127.0.0.1:8765?
          </p>
        )}

        {!loading && !error && stories.length === 0 && (
          <div className="empty">
            <p>No stories match these filters.</p>
            {filters.category || filters.source || filters.q || filters.minImpact ? (
              <>
                <p className="muted">
                  Showing only {[filters.category && heading, sourceName && `from ${sourceName}`,
                    filters.q && `matching “${filters.q}”`,
                    filters.minImpact && `with impact ${filters.minImpact}+`].filter(Boolean).join(", ")}{" "}
                  in the selected period.
                </p>
                <button type="button" className="btn-ghost" onClick={clearAll}>
                  Clear filters
                </button>
              </>
            ) : (
              <p className="muted">Try a longer period or press “Fetch now”.</p>
            )}
          </div>
        )}

        <section className="feed" aria-label="Stories">
          {stories.map((s) => (
            <StoryCard key={s.id} story={s} now={now} onCategory={(category) => update({ category })} />
          ))}
        </section>

        {stories.length < total && (
          <div className="more">
            <button type="button" className="btn-ghost" disabled={loading} onClick={() => void load(stories.length)}>
              {loading ? "Loading…" : `Load more (${total - stories.length} left)`}
            </button>
          </div>
        )}
      </main>
    </div>
  );
}
