import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { StoryCard } from "./components/StoryCard";
import { storiesQuery } from "./lib/api";
import { describeMatch, formatFigure, formatRelative, hostOf } from "./lib/format";
import { filtersToSearch, readFilters } from "./lib/urlState";
import { HomePage } from "./pages/HomePage";
import type { ScanStatus, SourceInfo, Story } from "./types/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState(null, "", "/");
});

const story: Story = {
  id: 1,
  title: "Fed cuts interest rates by 25 basis points",
  url: "https://demo-wire.example/fed",
  primary_source: { key: "demo-wire", name: "Demo Wire", publisher: "Demo Wire", is_demo: true },
  published_at: "2026-11-14T09:00:00Z",
  last_member_at: "2026-11-14T10:00:00Z",
  excerpt: "The Federal Reserve lowered its benchmark rate.",
  categories: [
    { slug: "fed", name: "FED", score: 4 },
    { slug: "fed-rates", name: "Rates", score: 4 },
  ],
  source_count: 2,
  member_count: 2,
  impact_score: 2,
  impact_level: "low",
  impact_reasons: [{ signal: "relevance", value: "matches a category", points: 10 }],
  sentiment: 0,
  tone: "neutral",
  others: [
    {
      item_id: 2,
      source: { key: "demo-markets", name: "Demo Markets Daily", publisher: "Demo Markets Daily", is_demo: true },
      title: "Federal Reserve lowers rates a quarter point",
      url: "https://demo-markets.example/fed",
      published_at: "2026-11-14T09:30:00Z",
      excerpt: null,
      is_primary: false,
      method: "cluster",
      match_score: 0.71,
      match_reason: { rule: "weighted_match", title_sim: 0.52, shared_entities: ["federal-reserve"], shared_numbers: ["bp:25"] },
      impact_score: 5,
      impact_level: "moderate",
      sentiment: -0.3,
      tone: "negative",
      impact_reasons: [{ signal: "event", value: "rate cut*", points: 25 }],
    },
  ],
};

describe("format helpers", () => {
  it("formats relative times", () => {
    const now = new Date("2026-11-14T12:00:00Z");
    expect(formatRelative("2026-11-14T11:59:50Z", now)).toBe("just now");
    expect(formatRelative("2026-11-14T11:50:00Z", now)).toBe("10 min ago");
    expect(formatRelative("2026-11-14T09:00:00Z", now)).toBe("3 h ago");
    expect(formatRelative(null, now)).toBe("never");
  });

  it("explains grouping decisions", () => {
    expect(describeMatch("identical_title", {})).toBe("Identical headline");
    const text = describeMatch("cluster", story.others[0].match_reason);
    expect(text).toContain("headline similarity 52%");
    expect(text).toContain("federal-reserve");
    expect(text).toContain("same figures: 25 bp");
    expect(formatFigure("money:$:4.1e+09")).toBe("$4.1bn");
    expect(formatFigure("num:142000")).toBe("142,000");
  });

  it("extracts hosts", () => {
    expect(hostOf("https://www.federalreserve.gov/x")).toBe("federalreserve.gov");
  });
});

describe("filters in the URL", () => {
  it("round-trips", () => {
    const f = readFilters("?q=opec&category=oil-gas&period=24h&source=demo-wire");
    expect(f).toEqual({ q: "opec", category: "oil-gas", source: "demo-wire", period: "24h", freeOnly: true, minImpact: null, sort: "latest" });
    const g = readFilters("?impact=6&sort=impact");
    expect(g.minImpact).toBe(6);
    expect(g.sort).toBe("impact");
    expect(readFilters(filtersToSearch(g))).toEqual(g);
    expect(readFilters("?impact=7").minImpact).toBeNull(); // only the 4 / 6 / 8 steps
    expect(readFilters("?paywalled=1").freeOnly).toBe(false);
    expect(readFilters(filtersToSearch(f))).toEqual(f);
    expect(readFilters("?period=bogus").period).toBe("7d");
  });

  it("builds API queries", () => {
    const q = storiesQuery({ q: " rare earth ", category: "geo-china", source: null, period: "7d", freeOnly: true, minImpact: null, sort: "latest" });
    expect(q).toContain("free_only=true");
    expect(q).toContain("q=rare+earth");
    expect(q).toContain("category=geo-china");
    expect(q).not.toContain("source=");
  });
});

describe("StoryCard", () => {
  it("shows attribution, categories, original link and collapsible sources", () => {
    const onCategory = vi.fn();
    render(<StoryCard story={story} now={new Date("2026-11-14T12:00:00Z")} onCategory={onCategory} />);

    expect(screen.getByRole("heading", { name: story.title })).toBeTruthy();
    expect(screen.getByText("Demo Wire")).toBeTruthy();
    expect(screen.getByText("2 sources")).toBeTruthy();
    const original = screen.getByRole("link", { name: /Open original/ });
    expect(original.getAttribute("href")).toBe(story.url);
    expect(original.getAttribute("rel")).toContain("noopener");

    expect(screen.queryByText("Federal Reserve lowers rates a quarter point")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Show 1 more report/ }));
    const other = screen.getByRole("link", { name: "Federal Reserve lowers rates a quarter point" });
    expect(other.getAttribute("href")).toBe("https://demo-markets.example/fed");
    expect(screen.getByText(/headline similarity 52%/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Rates" }));
    expect(onCategory).toHaveBeenCalledWith("fed-rates");
  });

  it("hides the sources section for single-source stories", () => {
    render(<StoryCard story={{ ...story, others: [], source_count: 1 }} now={new Date()} onCategory={() => {}} />);
    expect(screen.queryByRole("button", { name: /more report/ })).toBeNull();
  });
});

// ---------------------------------------------------------------------------- page

const sources: SourceInfo[] = [
  {
    key: "demo-wire", name: "Demo Wire", publisher: "Demo Wire", connector: "demo", enabled: true, is_demo: true,
    status: "ok", policy: "temporary_excerpt", retention_days: 30, disabled_reason: null, last_error: null,
    last_error_at: null, last_success_at: "2026-11-14T11:00:00Z", last_fetch_at: null, homepage: null, recent: [],
  },
  {
    key: "boe", name: "Bank of England", publisher: "Bank of England", connector: "rss", enabled: true, is_demo: false,
    status: "error", policy: "temporary_excerpt", retention_days: 30, disabled_reason: null,
    last_error: "ConnectorError: HTTP 503", last_error_at: null, last_success_at: null, last_fetch_at: null,
    homepage: null, recent: [],
  },
  {
    key: "ft-api", name: "Financial Times", publisher: "FT", connector: "ft", enabled: false, is_demo: false,
    status: "disabled", policy: "metadata_only", retention_days: null, disabled_reason: "missing credentials: FT_API_KEY",
    last_error: null, last_error_at: null, last_success_at: null, last_fetch_at: null, homepage: null, recent: [],
  },
];

function mockBackend(opts: { running: boolean; coalesced: boolean }) {
  let running = opts.running;
  const status = (): ScanStatus => ({
    running,
    current_run: null,
    last_run: null,
    last_refresh_at: "2026-11-14T11:00:00Z",
    next_scheduled_at: "2026-11-14T12:15:00Z",
    interval_minutes: 15,
  });
  const calls: string[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push(`${init?.method ?? "GET"} ${url}`);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200 });
    if (url.startsWith("/api/stories")) return json({ items: [story], total: 1, offset: 0, limit: 30 });
    if (url.startsWith("/api/categories"))
      return json([{ slug: "fed", name: "FED", count: 1, children: [{ slug: "fed-rates", name: "Rates", count: 1, children: [] }] }]);
    if (url.startsWith("/api/sources")) return json(sources);
    if (url.startsWith("/api/scan/status")) return json(status());
    if (url === "/api/scan") {
      running = true;
      return json({
        run_id: 7,
        coalesced: opts.coalesced,
        status: "running",
        message: opts.coalesced ? "A scan is already running (#7); your request joined it." : "Scan #7 started.",
      });
    }
    return new Response("not found", { status: 404 });
  });
  return { calls, finish: () => (running = false) };
}

describe("HomePage", () => {
  it("renders sidebar, source health and stories", async () => {
    mockBackend({ running: false, coalesced: false });
    render(<HomePage />);
    expect(await screen.findByRole("heading", { name: story.title })).toBeTruthy();
    const nav = within(screen.getByRole("navigation", { name: "Categories" }));
    expect(nav.getByRole("button", { name: /^All stories/ })).toBeTruthy();
    expect(nav.getByRole("button", { name: /^FED/ })).toBeTruthy();
    expect(await screen.findByText("ConnectorError: HTTP 503")).toBeTruthy(); // errors visible
    expect(screen.getByText("1 with errors")).toBeTruthy();
    expect(screen.getByText(/next at/)).toBeTruthy();
  });

  it("Fetch now reports a coalesced scan and shows progress", async () => {
    const backend = mockBackend({ running: false, coalesced: true });
    render(<HomePage />);
    await screen.findByRole("heading", { name: story.title });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Fetch now" }));
    });
    expect(await screen.findByText(/your request joined it/)).toBeTruthy();
    await waitFor(() => expect(screen.getByText("Fetching sources…")).toBeTruthy());
    expect(backend.calls.filter((c) => c === "POST /api/scan")).toHaveLength(1);
  });

  it("category selection filters the API request and updates the URL", async () => {
    const backend = mockBackend({ running: false, coalesced: false });
    render(<HomePage />);
    await screen.findByRole("heading", { name: story.title });
    const nav = within(screen.getByRole("navigation", { name: "Categories" }));
    fireEvent.click(nav.getByRole("button", { name: /^FED/ }));
    await waitFor(() => expect(backend.calls.some((c) => c.includes("category=fed"))).toBe(true));
    expect(window.location.search).toContain("category=fed");
  });

  it("search is debounced into the query", async () => {
    const backend = mockBackend({ running: false, coalesced: false });
    render(<HomePage />);
    await screen.findByRole("heading", { name: story.title });
    fireEvent.change(screen.getByRole("searchbox", { name: "Search stories" }), { target: { value: "opec" } });
    await waitFor(() => expect(backend.calls.some((c) => c.includes("q=opec"))).toBe(true));
  });
});

describe("filters never hide stories invisibly (regression)", () => {
  it("clicking a source in the sidebar shows details and does NOT filter", async () => {
    const backend = mockBackend({ running: false, coalesced: false });
    render(<HomePage />);
    await screen.findByRole("heading", { name: story.title });
    const before = backend.calls.length;
    fireEvent.click(screen.getByRole("button", { name: /Demo Wire: Healthy/ }));
    expect(await screen.findByText("Last success")).toBeTruthy();
    await new Promise((r) => setTimeout(r, 50));
    expect(backend.calls.slice(before).some((c) => c.includes("source="))).toBe(false);
    expect(window.location.search).not.toContain("source=");
  });

  it("an active source filter is visible, keeps sidebar counts in sync, and can be removed", async () => {
    const backend = mockBackend({ running: false, coalesced: false });
    window.history.replaceState(null, "", "/?source=demo-wire&category=fed");
    render(<HomePage />);
    await screen.findByRole("heading", { name: story.title });
    expect(screen.getByText("Source:")).toBeTruthy();
    expect(screen.getByText("Category:")).toBeTruthy();
    expect(backend.calls.some((c) => c.startsWith("GET /api/categories") && c.includes("source=demo-wire"))).toBe(true);

    fireEvent.click(screen.getByRole("button", { name: "Remove source filter" }));
    await waitFor(() => expect(window.location.search).not.toContain("source="));
    expect(window.location.search).toContain("category=fed");

    fireEvent.click(screen.getByRole("button", { name: "Remove category filter" }));
    await waitFor(() => expect(window.location.search).toBe(""));
    expect(screen.queryByLabelText("Active filters")).toBeNull();
  });

  it("empty results offer a one-click way out", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200 });
      if (url.startsWith("/api/stories")) {
        const filtered = url.includes("source=") || url.includes("category=");
        return json({ items: filtered ? [] : [story], total: filtered ? 0 : 1, offset: 0, limit: 30 });
      }
      if (url.startsWith("/api/categories")) return json([]);
      if (url.startsWith("/api/sources")) return json(sources);
      return json({ running: false, current_run: null, last_run: null, last_refresh_at: null,
                    next_scheduled_at: null, interval_minutes: 15 });
    });
    window.history.replaceState(null, "", "/?source=bls-cpi&category=tech");
    render(<HomePage />);
    expect(await screen.findByText("No stories match these filters.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByRole("heading", { name: story.title })).toBeTruthy();
    expect(window.location.search).toBe("");
  });
});

describe("paywall labelling", () => {
  it("flags paywalled sources on the card and in the other reports", () => {
    const paid = {
      ...story,
      primary_source: { ...story.primary_source, paywalled: false },
      others: [{ ...story.others[0], source: { ...story.others[0].source, name: "Bloomberg — Markets", paywalled: true } }],
    };
    render(<StoryCard story={paid} now={new Date()} onCategory={() => {}} />);
    expect(screen.queryByText("paywall")).toBeNull(); // primary is free
    fireEvent.click(screen.getByRole("button", { name: /Show 1 more report/ }));
    expect(screen.getByText("paywall")).toBeTruthy();
  });
});

describe("free to read toggle", () => {
  it("is on by default and can include paywalled stories", async () => {
    const backend = mockBackend({ running: false, coalesced: false });
    render(<HomePage />);
    await screen.findByRole("heading", { name: story.title });
    expect(backend.calls.some((c) => c.startsWith("GET /api/stories") && c.includes("free_only=true"))).toBe(true);
    const toggle = screen.getByRole("checkbox", { name: /Free to read only/ }) as HTMLInputElement;
    expect(toggle.checked).toBe(true);
    fireEvent.click(toggle);
    await waitFor(() => expect(window.location.search).toContain("paywalled=1"));
    const last = backend.calls.filter((c) => c.startsWith("GET /api/stories")).pop()!;
    expect(last).not.toContain("free_only");
  });
});

describe("impact score and colours", () => {
  const critical: Story = {
    ...story,
    impact_score: 9,
    impact_level: "critical",
    sentiment: -0.61,
    tone: "negative",
    impact_reasons: [
      { signal: "coverage", value: "3 publishers", points: 14 },
      { signal: "event", value: "rate cut*", points: 25 },
    ],
  };

  it("colours the card and explains the score", () => {
    const { container } = render(<StoryCard story={critical} now={new Date()} onCategory={() => {}} />);
    expect(container.querySelector("article")!.className).toBe("card lvl-critical");
    const badge = screen.getByText("Critical").closest(".impact-badge")!;
    expect(badge.className).toContain("impact-critical");
    expect(within(badge as HTMLElement).getByText("9")).toBeTruthy();
    const title = badge.getAttribute("title")!;
    expect(title).toContain("Story impact 9/10 — Critical");
    expect(title).toContain("Negative tone (−0.61)");
    expect(title).toContain("Coverage (3 publishers) +14");
  });

  it("maps every level to its colour class and label", () => {
    const cases: [Story["impact_level"], string][] = [
      ["low", "Normal"],
      ["moderate", "Moderate"],
      ["important", "Important"],
      ["critical", "Critical"],
    ];
    for (const [level, label] of cases) {
      const { container, unmount } = render(
        <StoryCard story={{ ...story, impact_level: level }} now={new Date()} onCategory={() => {}} />,
      );
      expect(container.querySelector("article")!.classList.contains(`lvl-${level}`)).toBe(true);
      expect(screen.getByText(label)).toBeTruthy();
      unmount();
    }
  });

  it("shows each report's own score in the other sources", () => {
    render(<StoryCard story={story} now={new Date()} onCategory={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: /Show 1 more report/ }));
    const report = screen.getByText("Demo Markets Daily").closest(".report")!;
    const badge = report.querySelector(".impact-badge")!;
    expect(badge.className).toContain("impact-moderate");
    expect(badge.getAttribute("title")).toContain("Report impact 5/10 — Moderate");
  });

  it("filters by minimum impact and sorts by importance", async () => {
    const backend = mockBackend({ running: false, coalesced: false });
    render(<HomePage />);
    await screen.findByRole("heading", { name: story.title });
    fireEvent.click(screen.getByRole("radio", { name: "Impact 6+" }));
    await waitFor(() => expect(backend.calls.some((c) => c.includes("min_impact=6"))).toBe(true));
    expect(backend.calls.some((c) => c.startsWith("GET /api/categories") && c.includes("min_impact=6"))).toBe(true);
    expect(window.location.search).toContain("impact=6");
    expect(screen.getByText("6+ of 10")).toBeTruthy(); // visible, removable filter chip

    fireEvent.change(screen.getByRole("combobox", { name: "Sort" }), { target: { value: "impact" } });
    await waitFor(() => expect(backend.calls.some((c) => c.includes("sort=impact"))).toBe(true));

    fireEvent.click(screen.getByRole("button", { name: "Remove impact filter" }));
    await waitFor(() => expect(window.location.search).not.toContain("impact=6"));
  });
});
