"use client";

import { AnimatePresence, motion } from "framer-motion";
import type { RulesetState } from "@/lib/types";

export interface ChatMessage {
  role: "user" | "assistant";
  text: string;
}

export function ChatFeed({ messages }: { messages: ChatMessage[] }) {
  return (
    <div className="space-y-4">
      <AnimatePresence initial={false}>
        {messages.map((m, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[80%] whitespace-pre-wrap rounded-2xl px-4 py-2.5 text-sm leading-relaxed ${
                m.role === "user"
                  ? "bg-accent/90 text-ink-950"
                  : "border border-ink-700/70 bg-ink-850/80 text-slate-200"
              }`}
            >
              {m.text}
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

export function RulesetSummary({ state }: { state: RulesetState | null }) {
  if (!state || Object.keys(state).length === 0) {
    return (
      <div className="card p-4 text-xs text-slate-500">
        Describe your buy box to begin. The agent will resolve the sector and geography, then
        confirm the ruleset before searching.
      </div>
    );
  }
  const settings = state.settings || {};
  return (
    <div className="card space-y-3 p-4">
      <div className="text-[11px] font-semibold uppercase tracking-wider text-slate-500">
        Buy box
      </div>
      <Row label="Sector" ok={state.sector_resolved} />
      <Row label="Geography" ok={state.geography_resolved} />
      {state.sector_keywords && state.sector_keywords.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {state.sector_keywords.slice(0, 8).map((k) => (
            <span key={k} className="chip">
              {k}
            </span>
          ))}
        </div>
      )}
      {state.states && state.states.length > 0 && (
        <div className="text-xs text-slate-400">States: {state.states.join(", ")}</div>
      )}
      {Object.entries(settings).map(([field, logic]) => (
        <div key={field} className="text-xs text-slate-400">
          <span className="text-slate-500">{field}:</span>{" "}
          {Object.entries(logic || {})
            .map(([k, v]) => `${k}=${v}`)
            .join(", ")}
        </div>
      ))}
      {state.missing && state.missing.length > 0 ? (
        <div className="text-xs">
          <div className="mb-1 font-medium text-warn">Still required</div>
          <ul className="space-y-0.5 text-slate-400">
            {state.missing.map((m) => (
              <li key={m}>– {m}</li>
            ))}
          </ul>
        </div>
      ) : (
        state.confirmed && <div className="text-xs font-medium text-good">Confirmed — searching.</div>
      )}
    </div>
  );
}

function Row({ label, ok }: { label: string; ok?: boolean }) {
  return (
    <div className="flex items-center justify-between text-xs">
      <span className="text-slate-400">{label}</span>
      <span className={ok ? "text-good" : "text-slate-500"}>{ok ? "✓ resolved" : "pending"}</span>
    </div>
  );
}
