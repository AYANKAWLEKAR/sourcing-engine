"""End-to-end run API demo (Part C acceptance) — buy box in, persisted shortlist out.

Drives the REAL HTTP surface: starts the FastAPI app in-process (uvicorn on a
background thread) unless --base-url points at a running `python cli.py serve`,
then:

    POST /runs                → start; answers clarifying turns (auto)
    GET  /runs/{id}           → poll, printing each stage transition
    GET  /runs/{id}/companies/{abn} + /sources  → detail + provenance receipts
    POST /runs/{id}/select    → mark the top pick for diligence

All LLM work on the Claude API. Live connectors spend Apify credits.
Run: python scripts/run_api_demo.py
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

# --- demo overrides FIRST (so the .env bootstrap below can't shadow them) ----
# run_plan_k stays at the default 8: at k=8 the plan includes google_maps +
# yellow_pages (real discovery); unbuilt registry entries are skipped with warnings.
# The caps below keep this demo cheap (Apify credits + Claude tokens), not because of
# any latency limit — raise them for a fuller run.
os.environ.setdefault("RUN_MAX_PLACES", "10")        # small scrape tiles
os.environ.setdefault("RUN_JUDGE_K", "8")            # bound demo judge spend
os.environ.setdefault("RUN_TOP_K", "8")

# --- .env bootstrap (fills in credentials etc. without overriding the above) --
ENV = Path(__file__).resolve().parents[1] / ".env"
if ENV.exists():
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

import httpx  # noqa: E402

BASE = sys.argv[sys.argv.index("--base-url") + 1] if "--base-url" in sys.argv else ""
POLL_S = 15
AGENT_TIMEOUT = 120.0  # a Claude buy-box turn is seconds; generous ceiling

BUY_BOX = (
    "Founder-owned HVAC and air-conditioning installation/servicing businesses in "
    "Queensland. B2B focus, at least 3 years operating. Use sensible defaults for "
    "everything else and finalize the ruleset without asking questions."
)


def _start_inprocess_server() -> str:
    import uvicorn

    from sourcing.api.app import app

    config = uvicorn.Config(app, host="127.0.0.1", port=8010, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get("http://127.0.0.1:8010/docs", timeout=2)
            break
        except Exception:
            time.sleep(0.2)
    return "http://127.0.0.1:8010"


def main() -> None:
    base = BASE or _start_inprocess_server()
    print(f"=== Run API demo @ {base} ===")
    print(f'buy box: "{BUY_BOX[:80]}…"\n')

    client = httpx.Client(base_url=base, timeout=AGENT_TIMEOUT)

    # 1. Start the run (blocks for one agent turn).
    t0 = time.time()
    resp = client.post("/runs", json={"message": BUY_BOX})
    body = resp.json()
    run_id = body["run_id"]
    print(f"POST /runs → {resp.status_code}  run_id={run_id}  ({time.time()-t0:.0f}s)")
    print(f"  agent: {body['reply'][:140]}")

    # 2. Answer clarifying turns until the ruleset confirms (bounded by the cap).
    turns = 0
    while not body["ruleset_confirmed"] and not body["needs_review"] and turns < 6:
        turns += 1
        t0 = time.time()
        resp = client.post(
            f"/runs/{run_id}/buybox",
            json={"message": "Use sensible defaults for everything and finalize now."},
        )
        body = resp.json()
        print(f"POST /runs/{run_id}/buybox → confirmed={body['ruleset_confirmed']} "
              f"({time.time()-t0:.0f}s)  agent: {body['reply'][:100]}")

    if not body["ruleset_confirmed"]:
        print("NEEDS REVIEW — agent did not confirm; aborting demo.")
        sys.exit(1)
    print("\nruleset confirmed → pipeline launched; polling…\n")

    # 3. Poll to completion, printing stage transitions.
    seen: list[str] = []
    while True:
        status = client.get(f"/runs/{run_id}").json()
        for h in status["stage_history"]:
            if h["status"] not in seen:
                seen.append(h["status"])
                print(f"  stage → {h['status']:<10} at {h['at'][11:19]}  "
                      f"coverage={status['coverage']}")
        if status["status"] in {"complete", "failed"}:
            break
        time.sleep(POLL_S)

    if status["status"] == "failed":
        print(f"\nRUN FAILED: {status['error']}")
        sys.exit(1)

    # 4. The persisted shortlist.
    print(f"\n=== GET /runs/{run_id} → shortlist ({len(status['shortlist'])}) ===")
    print(f"{'#':>2}  {'company':<36} {'abn':<12} {'S_final':>7} {'judge':>5}  standout")
    for i, rc in enumerate(status["shortlist"], 1):
        rec = rc["record"]
        print(f"{i:>2}  {(rec.get('legal_name') or '')[:35]:<36} {rec.get('abn') or '':<12} "
              f"{rc['s_final']:>7.3f} {rc.get('judge_fit') or 0:>5.2f}  "
              f"{'; '.join(rc.get('standout_signals', [])[:2])}")

    # 5. Company detail + provenance receipts for the top pick.
    top_abn = status["shortlist"][0]["record"]["abn"]
    detail = client.get(f"/runs/{run_id}/companies/{top_abn}").json()
    print(f"\n=== GET /runs/{run_id}/companies/{top_abn} ===")
    rec = detail["record"]
    print(f"  {rec['legal_name']}  (ACN {rec.get('acn') or '—'})  "
          f"{rec['location'].get('state')} {rec['location'].get('postcode') or ''}")
    print(f"  model={rec.get('business_model')}  categories={rec['sector']['category_text'][:2]}")

    sources = client.get(f"/runs/{run_id}/companies/{top_abn}/sources").json()
    print(f"\n=== …/sources — per-field provenance ({len(sources['provenance'])}) ===")
    for p in sources["provenance"][:8]:
        print(f"  {p['field']:<28} ← {p['source']:<22} conf={p['confidence']}")

    # 6. Select for diligence.
    sel = client.post(f"/runs/{run_id}/select", json={"abn": top_abn}).json()
    print(f"\nPOST /runs/{run_id}/select → selected={sel['selected']}")
    print(f"\nRESULT: run {run_id} complete and persisted — "
          f"stages {seen} — top-{len(status['shortlist'])} retrievable via GET /runs/{run_id}.")


if __name__ == "__main__":
    main()
