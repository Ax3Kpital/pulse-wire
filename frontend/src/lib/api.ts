import type {
  CategoryNode,
  Filters,
  ScanStatus,
  ScanTicket,
  SourceInfo,
  StoryPage,
} from "../types/api";

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { headers: { Accept: "application/json" }, ...init });
  if (!res.ok) {
    throw new ApiError(`${res.status} ${res.statusText}`, res.status);
  }
  return (await res.json()) as T;
}

export function storiesQuery(filters: Filters, offset = 0, limit = 30): string {
  const params = new URLSearchParams({ period: filters.period, offset: String(offset), limit: String(limit) });
  if (filters.q.trim()) params.set("q", filters.q.trim());
  if (filters.category) params.set("category", filters.category);
  if (filters.source) params.set("source", filters.source);
  if (filters.freeOnly) params.set("free_only", "true");
  if (filters.minImpact) params.set("min_impact", String(filters.minImpact));
  if (filters.sort !== "latest") params.set("sort", filters.sort);
  return `/api/stories?${params.toString()}`;
}

export const api = {
  stories: (filters: Filters, offset = 0) => request<StoryPage>(storiesQuery(filters, offset)),
  categories: (period: string, source: string | null, freeOnly: boolean, minImpact: number | null = null) =>
    request<CategoryNode[]>(
      `/api/categories?period=${period}${source ? `&source=${encodeURIComponent(source)}` : ""}` +
        (freeOnly ? "&free_only=true" : "") +
        (minImpact ? `&min_impact=${minImpact}` : ""),
    ),
  sources: () => request<SourceInfo[]>("/api/sources"),
  scanStatus: () => request<ScanStatus>("/api/scan/status"),
  scan: () => request<ScanTicket>("/api/scan", { method: "POST" }),
};
