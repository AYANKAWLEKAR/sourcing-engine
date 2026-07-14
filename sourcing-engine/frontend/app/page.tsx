"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChatFeed, ChatMessage, RulesetSummary } from "@/components/Chat";
import { RunTrace } from "@/components/RunTrace";
import { ShortlistTable } from "@/components/ShortlistTable";
import { Sidebar } from "@/components/Sidebar";
import { api } from "@/lib/api";
import type { RulesetState, RunStatus, RunSummary } from "@/lib/types";

type Phase = "chat" | "running" | "results";

export default function Home() {
  const [phase, setPhase] = useState<Phase>("chat");
  const [runId, setRunId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [ruleset, setRuleset] = useState<RulesetState | null>(null);
  const [run, setRun] = useState<RunStatus | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [banner, setBanner] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const refreshRuns = useCallback(() => {
    api.listRuns().then(setRuns).catch(() => {});
  }, []);

  useEffect(() => {
    refreshRuns();
  }, [refreshRuns]);

  // Poll the run while it's in flight.
  useEffect(() => {
    if (phase !== "running" || !runId) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const r = await api.getRun(runId);
        if (cancelled) return;
        setRun(r);
        if (r.status === "complete") {
          setPhase("results");
          refreshRuns();
          return;
        }
        if (r.status === "failed") {
          refreshRuns();
          return;
        }
      } catch {
        /* keep polling */
      }
      pollRef.current = setTimeout(tick, 1200);
    };
    tick();
    return () => {
      cancelled = true;
      if (pollRef.current) clearTimeout(pollRef.current);
    };
  }, [phase, runId, refreshRuns]);

  async function send() {
    const msg = input.trim();
    if (!msg || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: msg }]);
    setBusy(true);
    setBanner(null);
    try {
      const reply = runId
        ? await api.continueBuybox(runId, msg)
        : await api.startRun(msg);
      setRunId(reply.run_id);
      setMessages((m) => [...m, { role: "assistant", text: reply.reply }]);
      setRuleset(reply.ruleset_state);
      refreshRuns();
      if (reply.ruleset_confirmed) {
        setPhase("running");
      } else if (reply.needs_review) {
        setBanner("Question cap reached without confirmation. Refine and try again.");
      }
    } catch (e) {
      setBanner(e instanceof Error ? e.message : "Something went wrong");
      setMessages((m) => [
        ...m,
        { role: "assistant", text: "⚠️ I couldn't reach the engine. Is the API running on :8000?" },
      ]);
    } finally {
      setBusy(false);
    }
  }

  function reset() {
    if (pollRef.current) clearTimeout(pollRef.current);
    setPhase("chat");
    setRunId(null);
    setMessages([]);
    setRuleset(null);
    setRun(null);
    setInput("");
    setBanner(null);
  }

  async function openRun(id: string) {
    if (pollRef.current) clearTimeout(pollRef.current);
    try {
      const r = await api.getRun(id);
      setRunId(id);
      setRun(r);
      setRuleset(null);
      setMessages(
        (r.conversation || []).map((c) => ({
          role: c.role === "user" ? "user" : "assistant",
          text: c.text,
        })),
      );
      if (r.status === "complete") setPhase("results");
      else if (r.status === "failed") setPhase("results");
      else if (["planning", "acquiring", "resolving", "enriching", "ranking"].includes(r.status))
        setPhase("running");
      else setPhase("chat");
    } catch {
      setBanner("Could not open that run.");
    }
  }

  const showComposer = phase === "chat";

  return (
    <div className="flex h-screen">
      <Sidebar runs={runs} activeRunId={runId} onNew={reset} onOpen={openRun} />

      <main className="flex flex-1 flex-col overflow-hidden">
        <header className="border-b border-ink-800 px-8 py-4">
          <h1 className="text-lg font-semibold tracking-tight text-slate-100">Off-Market Sourcing</h1>
          <p className="text-xs text-slate-500">
            Describe a buy box → the agent confirms a ruleset → an evidence-weighted shortlist.
          </p>
        </header>

        <div className="flex flex-1 gap-6 overflow-y-auto px-8 py-6">
          <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
            {banner && (
              <div className="rounded-xl border border-warn/40 bg-warn/10 px-4 py-2 text-sm text-warn">
                {banner}
              </div>
            )}

            {messages.length > 0 && <ChatFeed messages={messages} />}

            {phase === "chat" && messages.length === 0 && (
              <div className="grid gap-3 pt-10">
                <h2 className="text-2xl font-semibold text-slate-100">What are you looking to buy?</h2>
                <p className="max-w-lg text-sm text-slate-500">
                  e.g. “Founder-owned HVAC installers in Sydney, $1–5M EBITDA.” The agent will resolve
                  the sector and geography, then confirm before searching.
                </p>
                <div className="mt-2 flex flex-wrap gap-2">
                  {[
                    "Founder-owned HVAC installers in Sydney, $1–5M EBITDA",
                    "B2B testing & certification firms in QLD",
                    "Independent electrical contractors in NSW",
                  ].map((s) => (
                    <button
                      key={s}
                      onClick={() => setInput(s)}
                      className="rounded-full border border-ink-600/70 bg-ink-800/60 px-3 py-1.5 text-xs text-slate-300 hover:border-accent/60"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {(phase === "running" || (phase === "results" && run)) && run && <RunTrace run={run} />}

            {phase === "results" && run?.shortlist && run.shortlist.length > 0 && (
              <ShortlistTable runId={run.run_id} shortlist={run.shortlist} />
            )}
            {phase === "results" && run && (!run.shortlist || run.shortlist.length === 0) && (
              <div className="card p-6 text-sm text-slate-400">
                No companies matched the buy box.
              </div>
            )}

            {showComposer && (
              <div className="sticky bottom-0 mt-auto pt-4">
                <div className="flex gap-2 rounded-2xl border border-ink-700 bg-ink-850/90 p-2 backdrop-blur">
                  <input
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && send()}
                    placeholder="Describe your buy box…"
                    className="flex-1 bg-transparent px-3 py-2 text-sm text-slate-100 placeholder:text-slate-600 focus:outline-none"
                  />
                  <button
                    onClick={send}
                    disabled={busy}
                    className="rounded-xl bg-accent px-4 py-2 text-sm font-medium text-ink-950 transition hover:bg-accent-soft disabled:opacity-60"
                  >
                    {busy ? "…" : "Send"}
                  </button>
                </div>
              </div>
            )}
          </div>

          {(phase === "chat" || phase === "running") && (
            <div className="hidden w-72 shrink-0 lg:block">
              <RulesetSummary state={ruleset} />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
