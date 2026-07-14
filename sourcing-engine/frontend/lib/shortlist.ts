// Client-side filter/sort presets for the shortlist "filter buttons". These run
// instantly on the already-loaded ranked list (no round-trip); the natural-
// language query box (POST /runs/{id}/query) covers everything these presets don't.

import type { RankedCompany } from "./types";

export interface FilterPreset {
  id: string;
  label: string;
  hint: string;
  // Optional predicate to filter; optional key to sort by (desc, nulls last).
  predicate?: (rc: RankedCompany) => boolean;
  sortKey: (rc: RankedCompany) => number | null;
}

const ebitda = (rc: RankedCompany) => rc.record.size?.ebitda_est_aud ?? null;
const govValue = (rc: RankedCompany) => rc.record.moat_signals?.gov_contract_value_aud ?? null;

export const FILTER_PRESETS: FilterPreset[] = [
  {
    id: "best_fit",
    label: "Best fit",
    hint: "Blended final score",
    sortKey: (rc) => rc.s_final,
  },
  {
    id: "evidence",
    label: "Strongest evidence",
    hint: "Gov contracts, awards, IP, EBITDA fit",
    sortKey: (rc) => rc.s_evidence,
  },
  {
    id: "gov",
    label: "Gov contracts",
    hint: "Has AusTender / government revenue",
    predicate: (rc) => Boolean(rc.record.moat_signals?.gov_contracts),
    sortKey: (rc) => govValue(rc) ?? 0,
  },
  {
    id: "awards",
    label: "Award finalists",
    hint: "Trades Champion / Telstra finalist or winner",
    predicate: (rc) =>
      Boolean(rc.record.moat_signals?.award_finalist) ||
      (rc.standout_signals || []).some((s) => /award|finalist|winner/i.test(s)),
    sortKey: (rc) => rc.s_evidence,
  },
  {
    id: "accredited",
    label: "Accredited",
    hint: "Regulatory accreditation on file",
    predicate: (rc) => Boolean(rc.record.moat_signals?.regulatory_accreditation),
    sortKey: (rc) => rc.s_final,
  },
  {
    id: "ebitda",
    label: "Highest EBITDA",
    hint: "By estimated EBITDA (unknowns last)",
    predicate: (rc) => ebitda(rc) != null,
    sortKey: (rc) => ebitda(rc),
  },
  {
    id: "judge",
    label: "Judge's pick",
    hint: "By the LLM judge's qualitative fit",
    sortKey: (rc) => rc.judge_fit ?? null,
  },
];

export function applyPreset(list: RankedCompany[], preset: FilterPreset): RankedCompany[] {
  const filtered = preset.predicate ? list.filter(preset.predicate) : [...list];
  filtered.sort((a, b) => {
    const av = preset.sortKey(a);
    const bv = preset.sortKey(b);
    // Nulls always sort last regardless of direction.
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return bv - av; // descending
  });
  return filtered;
}
