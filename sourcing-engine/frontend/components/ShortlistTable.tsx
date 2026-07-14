"use client";

import { AnimatePresence, motion } from "framer-motion";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { applyPreset, FILTER_PRESETS } from "@/lib/shortlist";
import type { RankedCompany } from "@/lib/types";
import { Gauge } from "./Gauge";

function websiteOf(rc: RankedCompany): string | null {
  const w = rc.record.contacts_min?.website;
  if (!w) return null;
  return w.startsWith("http") ? w : `https://${w}`;
}

function fmtMoney(n?: number | null): string {
  if (n == null) return "—";
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}k`;
  return `$${n}`;
}

export function ShortlistTable({ runId, shortlist }: { runId: string; shortlist: RankedCompany[] }) {
  const [presetId, setPresetId] = useState("best_fit");
  const [nlResults, setNlResults] = useState<RankedCompany[] | null>(null);
  const [nlText, setNlText] = useState("");
  const [nlBusy, setNlBusy] = useState(false);
  const [nlError, setNlError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const preset = FILTER_PRESETS.find((p) => p.id === presetId) || FILTER_PRESETS[0];

  // The base list is either the NL-query result or the full shortlist; the preset
  // buttons always re-sort whatever base is active.
  const rows = useMemo(() => {
    const base = nlResults ?? shortlist;
    return applyPreset(base, preset);
  }, [nlResults, shortlist, preset]);

  async function runQuery() {
    const msg = nlText.trim();
    if (!msg) return;
    setNlBusy(true);
    setNlError(null);
    try {
      const res = await api.queryShortlist(runId, msg);
      setNlResults(res.results);
    } catch (e) {
      setNlError(e instanceof Error ? e.message : "Query failed");
    } finally {
      setNlBusy(false);
    }
  }

  function clearQuery() {
    setNlResults(null);
    setNlText("");
    setNlError(null);
  }

  return (
    <div className="card p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-100">
          Ranked shortlist
          <span className="ml-2 text-slate-500">{rows.length} companies</span>
        </h2>
      </div>

      {/* Natural-language re-rank */}
      <div className="mt-3 flex gap-2">
        <input
          value={nlText}
          onChange={(e) => setNlText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && runQuery()}
          placeholder="Refine in words — e.g. 'only ones with government contracts over $1M'"
          className="flex-1 rounded-xl border border-ink-700 bg-ink-900/70 px-3 py-2 text-sm text-slate-200 placeholder:text-slate-600 focus:border-accent/60 focus:outline-none"
        />
        <button
          onClick={runQuery}
          disabled={nlBusy}
          className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-ink-950 transition hover:bg-accent-soft disabled:opacity-60"
        >
          {nlBusy ? "…" : "Refine"}
        </button>
        {nlResults && (
          <button
            onClick={clearQuery}
            className="rounded-xl border border-ink-600 px-3 py-2 text-sm text-slate-300 hover:border-slate-400"
          >
            Reset
          </button>
        )}
      </div>
      {nlError && <div className="mt-2 text-xs text-accent-soft">{nlError}</div>}

      {/* Filter buttons — instant client-side sort/filter */}
      <div className="mt-4 flex flex-wrap gap-2">
        {FILTER_PRESETS.map((p) => (
          <button
            key={p.id}
            onClick={() => setPresetId(p.id)}
            title={p.hint}
            className={`rounded-full px-3 py-1.5 text-xs font-medium transition ${
              p.id === presetId
                ? "bg-slate-100 text-ink-950"
                : "border border-ink-600/70 bg-ink-800/60 text-slate-300 hover:border-slate-400"
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* Table */}
      <div className="mt-4 overflow-x-auto">
        <table className="w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-ink-700 text-left text-[11px] uppercase tracking-wider text-slate-500">
              <th className="py-2 pr-3 font-medium">#</th>
              <th className="py-2 pr-3 font-medium">Company</th>
              <th className="py-2 pr-3 font-medium">Location</th>
              <th className="py-2 pr-3 font-medium">EBITDA</th>
              <th className="py-2 pr-3 text-center font-medium">Fit</th>
              <th className="py-2 pr-3 text-center font-medium">Sector</th>
              <th className="py-2 pr-3 text-center font-medium">Evidence</th>
              <th className="py-2 pr-3 text-center font-medium">Judge</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((rc, i) => {
              const abn = rc.record.abn || rc.record.entity_id;
              const site = websiteOf(rc);
              const loc = rc.record.location || {};
              const isOpen = expanded === abn;
              return (
                <motion.tr
                  key={abn}
                  layout="position"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.2 }}
                  className={`group cursor-pointer border-b border-ink-800/70 hover:bg-ink-800/40 ${
                    isOpen ? "bg-ink-800/40" : ""
                  }`}
                  onClick={() => setExpanded(isOpen ? null : abn)}
                >
                  <td className="py-3 pr-3 align-top text-slate-500">{i + 1}</td>
                  <td className="py-3 pr-3">
                    <div className="font-medium text-slate-100">
                      {rc.record.legal_name || "(unnamed)"}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      {site && (
                        <a
                          href={site}
                          target="_blank"
                          rel="noreferrer"
                          onClick={(e) => e.stopPropagation()}
                          className="text-xs text-accent-soft underline-offset-2 hover:underline"
                        >
                          {site.replace(/^https?:\/\/(www\.)?/, "").replace(/\/$/, "")}
                        </a>
                      )}
                      {(rc.standout_signals || []).slice(0, 2).map((s) => (
                        <span key={s} className="chip">
                          {s}
                        </span>
                      ))}
                    </div>
                    <AnimatePresence>
                      {isOpen && (
                        <DetailPanel
                          runId={runId}
                          rc={rc}
                          onClose={() => setExpanded(null)}
                        />
                      )}
                    </AnimatePresence>
                  </td>
                  <td className="py-3 pr-3 align-top text-slate-400">
                    {[loc.suburb, loc.state, loc.postcode].filter(Boolean).join(" ") || "—"}
                  </td>
                  <td className="py-3 pr-3 align-top text-slate-300">
                    {fmtMoney(rc.record.size?.ebitda_est_aud)}
                  </td>
                  <td className="py-3 pr-3 align-top">
                    <Gauge value={rc.s_final} label="final" tone="accent" size={48} />
                  </td>
                  <td className="py-3 pr-3 align-top">
                    <Gauge value={rc.s_stat / 100} label="stat" tone="muted" size={48} />
                  </td>
                  <td className="py-3 pr-3 align-top">
                    <Gauge value={rc.s_evidence} label="evid" tone="good" size={48} />
                  </td>
                  <td className="py-3 pr-3 align-top">
                    <Gauge
                      value={rc.judge_fit ?? 0}
                      display={rc.judge_fit == null ? "—" : undefined}
                      label="judge"
                      tone="warn"
                      size={48}
                    />
                  </td>
                </motion.tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {rows.length === 0 && (
        <div className="mt-6 text-center text-sm text-slate-500">No companies match this filter.</div>
      )}
    </div>
  );
}

function DetailPanel({
  runId,
  rc,
  onClose,
}: {
  runId: string;
  rc: RankedCompany;
  onClose: () => void;
}) {
  const [provenance, setProvenance] = useState<
    Array<{ field: string; source: string; confidence: number }> | null
  >(null);
  const [selected, setSelected] = useState(false);
  const abn = rc.record.abn;

  useEffect(() => {
    let cancelled = false;
    if (!abn) {
      setProvenance([]);
      return;
    }
    api
      .getCompanySources(runId, abn)
      .then((res) => !cancelled && setProvenance(res.provenance))
      .catch(() => !cancelled && setProvenance([]));
    return () => {
      cancelled = true;
    };
  }, [runId, abn]);

  async function shortlistIt() {
    if (!abn) return;
    await api.select(runId, abn);
    setSelected(true);
  }

  return (
    <motion.div
      initial={{ opacity: 0, height: 0 }}
      animate={{ opacity: 1, height: "auto" }}
      exit={{ opacity: 0, height: 0 }}
      className="overflow-hidden"
      onClick={(e) => e.stopPropagation()}
    >
      <div className="mt-3 space-y-3 rounded-xl border border-ink-700 bg-ink-900/60 p-4">
        <div className="flex items-start justify-between">
          <div className="text-xs text-slate-500">
            ABN {abn || "—"} · {rc.record.business_model || "model unknown"}
          </div>
          <button onClick={onClose} className="text-xs text-slate-500 hover:text-slate-300">
            Close
          </button>
        </div>

        {rc.judge_rationale && (
          <p className="text-sm italic text-slate-400">“{rc.judge_rationale}”</p>
        )}

        {rc.standout_signals && rc.standout_signals.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {rc.standout_signals.map((s) => (
              <span key={s} className="chip border-good/40 text-good">
                {s}
              </span>
            ))}
          </div>
        )}

        {rc.deferred_assessment && rc.deferred_assessment.length > 0 && (
          <div className="text-xs">
            <div className="mb-1 font-medium text-warn">Open diligence</div>
            <ul className="space-y-0.5 text-slate-400">
              {rc.deferred_assessment.map((d) => (
                <li key={d}>– {d}</li>
              ))}
            </ul>
          </div>
        )}

        {provenance && provenance.length > 0 && (
          <div className="text-xs">
            <div className="mb-1 font-medium text-slate-400">Provenance</div>
            <div className="overflow-hidden rounded-lg border border-ink-700">
              <table className="w-full text-left">
                <tbody>
                  {provenance.map((p, i) => (
                    <tr key={i} className="border-b border-ink-800/70 last:border-0">
                      <td className="px-2 py-1 text-slate-300">{p.field}</td>
                      <td className="px-2 py-1 text-slate-500">{p.source}</td>
                      <td className="px-2 py-1 text-right text-slate-500">
                        {(p.confidence * 100).toFixed(0)}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        <button
          onClick={shortlistIt}
          disabled={selected || !abn}
          className="rounded-lg border border-ink-600 px-3 py-1.5 text-xs font-medium text-slate-200 transition hover:border-good/60 disabled:opacity-60"
        >
          {selected ? "✓ Saved to list" : "Save to list"}
        </button>
      </div>
    </motion.div>
  );
}
