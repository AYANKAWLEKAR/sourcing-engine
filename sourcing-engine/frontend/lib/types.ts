// Mirrors the FastAPI response schemas (sourcing/api/schemas.py).

export interface Location {
  state?: string | null;
  postcode?: string | null;
  suburb?: string | null;
  lat?: number | null;
  lng?: number | null;
}

export interface CompanyRecord {
  entity_id: string;
  abn?: string | null;
  acn?: string | null;
  legal_name?: string | null;
  location?: Location;
  business_model?: string | null;
  sector?: { anzsic?: string[]; category_text?: string[]; keyword_hits?: string[] };
  age?: { years_operating?: number | null };
  size?: {
    employee_count?: number | null;
    revenue_est_aud?: number | null;
    ebitda_est_aud?: number | null;
    ebitda_confidence?: number | null;
  };
  moat_signals?: {
    gov_contracts?: boolean;
    gov_contract_value_aud?: number | null;
    award_finalist?: boolean | null;
    regulatory_accreditation?: boolean | null;
    ip?: boolean | null;
  };
  contacts_min?: Record<string, string>;
  [k: string]: unknown;
}

export interface RankedCompany {
  record: CompanyRecord;
  s_stat: number;
  s_evidence: number;
  s_final: number;
  judge_fit?: number | null;
  judge_rationale?: string;
  standout_signals?: string[];
  deferred_assessment?: string[];
  judge_unavailable?: boolean;
}

export interface RulesetState {
  confirmed?: boolean;
  sector_resolved?: boolean;
  geography_resolved?: boolean;
  sector_keywords?: string[];
  states?: string[];
  settings?: Record<string, Record<string, unknown>>;
  missing?: string[];
}

export interface BuyBoxReply {
  run_id: string;
  status: string;
  reply: string;
  agent_done: boolean;
  needs_review: boolean;
  ruleset_confirmed: boolean;
  ruleset_state: RulesetState;
}

export interface RunStatus {
  run_id: string;
  status: string;
  error?: string | null;
  ruleset_id?: string | null;
  label?: string | null;
  source_plan: Array<{ source_id: string; [k: string]: unknown }>;
  coverage: Record<string, number>;
  shortlist?: RankedCompany[] | null;
  conversation: Array<{ role: string; text: string; at?: string }>;
  stage_history: Array<{ status: string; at?: string }>;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface RunSummary {
  run_id: string;
  label?: string | null;
  status: string;
  thesis?: string | null;
  n_shortlist: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface QueryResponse {
  run_id: string;
  spec: Record<string, unknown>;
  results: RankedCompany[];
}
