// Thin typed client over the FastAPI run surface. All calls go through the
// same-origin "/api" prefix, which next.config rewrites to the backend.

import type {
  BuyBoxReply,
  CompanyRecord,
  QueryResponse,
  RunStatus,
  RunSummary,
} from "./types";

const BASE = "/api";

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {
      /* ignore */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  startRun: (message: string) =>
    jsonFetch<BuyBoxReply>("/runs", { method: "POST", body: JSON.stringify({ message }) }),

  continueBuybox: (runId: string, message: string) =>
    jsonFetch<BuyBoxReply>(`/runs/${runId}/buybox`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),

  getRun: (runId: string) => jsonFetch<RunStatus>(`/runs/${runId}`),

  listRuns: () => jsonFetch<{ runs: RunSummary[] }>("/runs").then((r) => r.runs),

  labelRun: (runId: string, label: string) =>
    jsonFetch<RunStatus>(`/runs/${runId}`, { method: "PATCH", body: JSON.stringify({ label }) }),

  queryShortlist: (runId: string, message: string) =>
    jsonFetch<QueryResponse>(`/runs/${runId}/query`, {
      method: "POST",
      body: JSON.stringify({ message }),
    }),

  getCompany: (runId: string, abn: string) =>
    jsonFetch<{ record: CompanyRecord; selected: boolean }>(`/runs/${runId}/companies/${abn}`),

  getCompanySources: (runId: string, abn: string) =>
    jsonFetch<{ provenance: Array<{ field: string; source: string; confidence: number }> }>(
      `/runs/${runId}/companies/${abn}/sources`,
    ),

  select: (runId: string, abn: string) =>
    jsonFetch<{ selected: boolean }>(`/runs/${runId}/select`, {
      method: "POST",
      body: JSON.stringify({ abn }),
    }),
};
