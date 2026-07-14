"use client";

import { motion } from "framer-motion";
import type { RunSummary } from "@/lib/types";

interface SidebarProps {
  runs: RunSummary[];
  activeRunId: string | null;
  onNew: () => void;
  onOpen: (runId: string) => void;
}

export function Sidebar({ runs, activeRunId, onNew, onOpen }: SidebarProps) {
  return (
    <aside className="flex h-full w-72 shrink-0 flex-col border-r border-ink-800 bg-ink-900/60 p-4">
      <div className="flex items-center gap-2 px-1">
        <div className="grid h-7 w-7 place-items-center rounded-lg bg-accent/90 text-sm font-bold text-ink-950">
          O
        </div>
        <span className="text-sm font-semibold tracking-wide text-slate-100">Origo</span>
      </div>

      <button
        onClick={onNew}
        className="mt-5 w-full rounded-xl border border-ink-600/70 bg-ink-800/70 px-3 py-2 text-sm font-medium text-slate-200 transition hover:border-accent/60 hover:text-white"
      >
        + New search
      </button>

      <div className="mt-6 px-1 text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        Saved searches
      </div>

      <div className="mt-2 flex-1 space-y-1 overflow-y-auto pr-1">
        {runs.length === 0 && (
          <p className="px-1 py-3 text-xs text-slate-500">No searches yet.</p>
        )}
        {runs.map((r) => {
          const active = r.run_id === activeRunId;
          return (
            <motion.button
              key={r.run_id}
              onClick={() => onOpen(r.run_id)}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              className={`w-full rounded-lg px-3 py-2 text-left transition ${
                active ? "bg-ink-700/80 ring-1 ring-accent/40" : "hover:bg-ink-800/70"
              }`}
            >
              <div className="truncate text-sm text-slate-200">
                {r.label || r.thesis || "Untitled search"}
              </div>
              <div className="mt-0.5 flex items-center gap-2 text-[11px] text-slate-500">
                <StatusDot status={r.status} />
                <span className="capitalize">{r.status}</span>
                {r.n_shortlist > 0 && <span>· {r.n_shortlist} companies</span>}
              </div>
            </motion.button>
          );
        })}
      </div>
    </aside>
  );
}

function StatusDot({ status }: { status: string }) {
  const tone =
    status === "complete"
      ? "bg-good"
      : status === "failed"
        ? "bg-accent"
        : "bg-warn animate-pulse";
  return <span className={`h-1.5 w-1.5 rounded-full ${tone}`} />;
}
