"use client";

import { motion } from "framer-motion";
import type { RunStatus } from "@/lib/types";

const STAGES = ["planning", "acquiring", "resolving", "enriching", "ranking"] as const;
const LABELS: Record<string, string> = {
  planning: "Planning sources",
  acquiring: "Acquiring candidates",
  resolving: "Resolving to ABNs",
  enriching: "Enriching (web + signals)",
  ranking: "Screening, scoring & judging",
};

function stageState(status: string, stage: string): "done" | "active" | "pending" {
  if (status === "complete") return "done";
  const ci = STAGES.indexOf(status as (typeof STAGES)[number]);
  const si = STAGES.indexOf(stage as (typeof STAGES)[number]);
  if (ci < 0) return "pending";
  if (si < ci) return "done";
  return si === ci ? "active" : "pending";
}

function detailFor(stage: string, run: RunStatus): string {
  const cov = run.coverage || {};
  if (stage === "planning" && run.source_plan?.length) {
    return `${run.source_plan.length} sources planned`;
  }
  if (stage === "acquiring" && cov.n_raw != null) {
    return `${cov.n_raw} raw → ${cov.n_pool ?? "?"} after dedup`;
  }
  if (stage === "resolving" && cov.n_resolved != null) return `${cov.n_resolved} matched to an ABN`;
  if (stage === "enriching") return "fetching websites & extracting signals";
  if (stage === "ranking" && cov.n_shortlist != null) return `${cov.n_shortlist} shortlisted`;
  return "";
}

export function RunTrace({ run }: { run: RunStatus }) {
  const status = run.status;
  const fraction =
    status === "complete" ? 1 : (STAGES.indexOf(status as (typeof STAGES)[number]) + 1) / (STAGES.length + 1);

  return (
    <div className="card space-y-4 p-5">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-slate-100">Sourcing run</span>
        <span className="text-xs capitalize text-slate-500">{status}</span>
      </div>

      <div className="h-1.5 w-full overflow-hidden rounded-full bg-ink-700">
        <motion.div
          className="h-full rounded-full bg-accent"
          animate={{ width: `${Math.max(0, Math.min(1, fraction)) * 100}%` }}
          transition={{ duration: 0.5 }}
        />
      </div>

      <ul className="space-y-2.5">
        {STAGES.map((stage) => {
          const st = stageState(status, stage);
          const detail = detailFor(stage, run);
          return (
            <li key={stage} className="flex items-start gap-3">
              <span className="mt-0.5">
                {st === "done" ? (
                  <span className="text-good">✓</span>
                ) : st === "active" ? (
                  <motion.span
                    className="inline-block text-warn"
                    animate={{ rotate: 360 }}
                    transition={{ repeat: Infinity, duration: 1.2, ease: "linear" }}
                  >
                    ◍
                  </motion.span>
                ) : (
                  <span className="text-slate-600">○</span>
                )}
              </span>
              <div>
                <div className={st === "pending" ? "text-sm text-slate-500" : "text-sm text-slate-200"}>
                  {LABELS[stage]}
                </div>
                {detail && <div className="text-xs text-slate-500">{detail}</div>}
              </div>
            </li>
          );
        })}
      </ul>

      {run.error && (
        <div className="rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-xs text-accent-soft">
          Run failed: {run.error}
        </div>
      )}
    </div>
  );
}
