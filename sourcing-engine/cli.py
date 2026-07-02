"""Thin CLI to drive the Buy-Box agent, RAG retriever, and ABN connector.

Usage:
    python cli.py buybox                       # interactive multi-turn agent (live Ollama)
    python cli.py buybox --buy-box "..."       # seed the first turn non-interactively
    python cli.py sources "B2B testing & certification services in QLD"
    python cli.py ruleset                       # print the base Origo ruleset summary
    python cli.py fetch-abn 51824753556         # ABN Lookup API: full detail for an ABN
    python cli.py fetch-abn "Xero" --state VIC  # ABN Lookup API: scored name matches
    python cli.py asic-load                     # load/verify the ASIC spine into DuckDB
    python cli.py asic-lookup 000000019         # point-lookup a company by ACN/ABN
    python cli.py asic-fetch --types APTY --min-years 20 --limit 20  # candidate slice
"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from sourcing.agent.buybox_agent import BuyBoxAgent
from sourcing.config import get_settings
from sourcing.llm import get_llm_client
from sourcing.models.filter_rule import DiscoveryAction
from sourcing.rag.embeddings import get_embedding_provider
from sourcing.rag.registry_seed import load_seed_registry
from sourcing.rag.retriever import SourceRetriever, required_fields
from sourcing.rag.vector_store import InMemoryVectorStore
from sourcing.ruleset.loader import load_origo_ruleset

app = typer.Typer(add_completion=False, help="Origo Sourcing Engine — Step 1 CLI")
console = Console()


def _ruleset_table(ruleset) -> Table:
    table = Table(title=f"Ruleset: {ruleset.name}  (confirmed={ruleset.confirmed})")
    table.add_column("field")
    table.add_column("tier")
    table.add_column("discovery_action")
    table.add_column("weight", justify="right")
    table.add_column("logic", overflow="fold")
    for r in ruleset.rules:
        table.add_row(
            r.field,
            r.screen_tier.value,
            r.discovery_action.value,
            "" if r.weight is None else f"{r.weight:.2f}",
            str(r.logic),
        )
    return table


@app.command()
def ruleset() -> None:
    """Print the base Origo ruleset loaded from the CSV."""
    rs = load_origo_ruleset()
    console.print(_ruleset_table(rs))
    console.print(f"[bold]{len(rs.rules)}[/] rules loaded.")


@app.command()
def buybox(
    buy_box: str = typer.Option(
        None, "--buy-box", "-b", help="Seed the first turn instead of prompting."
    ),
) -> None:
    """Run the Buy-Box agent as a live multi-turn conversation against Ollama."""
    settings = get_settings()
    llm = get_llm_client(settings)
    base = load_origo_ruleset()
    agent = BuyBoxAgent(
        llm=llm,
        base_ruleset=base,
        model=settings.agent_model,
        max_questions=settings.max_clarifying_questions,
    )
    console.print(
        f"[bold cyan]Buy-Box Agent[/] (model={settings.agent_model}, "
        f"cap={settings.max_clarifying_questions} questions). Type 'quit' to abort.\n"
    )

    if buy_box is None:
        buy_box = console.input("[bold]Describe your buy box:[/] ")

    user_msg = buy_box
    while True:
        turn = agent.step(user_msg)
        if turn.text:
            console.print(f"[green]agent[/]: {turn.text}")
        for tr in turn.tool_results:
            console.print(f"  [dim]tool {tr['tool']} -> {tr['result']}[/]")
        if turn.done:
            break
        user_msg = console.input("[bold]you[/]: ")
        if user_msg.strip().lower() in {"quit", "exit"}:
            break

    console.print()
    console.print(_ruleset_table(turn.ruleset))
    if turn.ruleset.confirmed:
        console.print(f"[bold green]CONFIRMED[/] — thesis: {turn.ruleset.thesis_summary}")
    elif turn.needs_review:
        console.print("[bold yellow]NEEDS REVIEW[/] — question cap hit before confirmation.")
    else:
        console.print("[yellow]Conversation ended without confirmation.[/]")


@app.command()
def sources(
    query: str = typer.Argument(..., help="Buy-box / sector intent text."),
    k: int = typer.Option(8, help="Number of sources to return."),
) -> None:
    """Build a ruleset from the query and return a ranked, explainable Source Plan."""
    settings = get_settings()
    rs = load_origo_ruleset()
    rs.thesis_summary = query

    registry = load_seed_registry()
    retriever = SourceRetriever(InMemoryVectorStore(), get_embedding_provider(settings))
    retriever.index(registry)
    plan = retriever.retrieve(rs, k=k)

    table = Table(title=f'Source Plan for: "{query}"')
    table.add_column("rank", justify="right")
    table.add_column("source")
    table.add_column("score", justify="right")
    table.add_column("tags")
    table.add_column("fields", overflow="fold")
    table.add_column("rationale", overflow="fold")
    for i, item in enumerate(plan, 1):
        table.add_row(
            str(i),
            item.source_id,
            f"{item.score:.3f}",
            ", ".join(item.invariant_tags),
            ", ".join(item.fields_contributed),
            item.rationale,
        )
    console.print(table)

    ids = {p.source_id for p in plan}
    spine_ok = bool(ids & {"abn_bulk_extract", "abn_lookup_api", "asic_company_dataset"})
    text_ok = bool(ids & {"google_maps", "yellow_pages", "industrynet", "website_fetch"})
    console.print(f"[bold]Invariants[/]: spine={'OK' if spine_ok else 'MISSING'}, "
                  f"text_source={'OK' if text_ok else 'MISSING'}")
    n_score = sum(1 for r in rs.rules if r.discovery_action == DiscoveryAction.SCORE)
    console.print(f"[dim]{len(required_fields(rs))} discovery-relevant fields; "
                  f"{n_score} SCORE rules.[/]")


@app.command("fetch-abn")
def fetch_abn(
    query: str = typer.Argument(..., help="ABN (11 digits) for detail, or a name to match."),
    state: str = typer.Option(None, "--state", help="State filter for name search (e.g. QLD)."),
) -> None:
    """Query the live ABN Lookup API (APIConnector — the resolution bridge).

    Autodetects the query type:
      - 11 digits   → AbnDetails (one full entity record)
      - anything else → MatchingNames (up to 20 scored candidates)
    """
    from sourcing.connectors.abn import ABNLookupAPIConnector

    settings = get_settings()
    if not settings.abn_lookup_guid:
        console.print("[red]ABN_LOOKUP_GUID not set. Add it to .env.[/]")
        raise typer.Exit(1)

    connector = ABNLookupAPIConnector.from_settings()

    digits = query.replace(" ", "").replace("-", "")
    if digits.isdigit() and len(digits) == 11:
        params, label = {"abn": digits}, f"ABN {digits}"
    else:
        params, label = {"name": query, "state": state}, f'name "{query}"'

    console.print(f"[cyan]Querying[/] {label} via ABN Lookup API…")
    try:
        raw_records = connector.fetch(params)
    except Exception as exc:
        console.print(f"[red]Error:[/] {exc}")
        raise typer.Exit(1) from exc

    table = Table(title=f"ABN Lookup — {label}  ({len(raw_records)} records)")
    table.add_column("ABN")
    table.add_column("Name", overflow="fold")
    table.add_column("State")
    table.add_column("Postcode")
    table.add_column("Score/Yrs", justify="right")

    for raw in raw_records:
        company = connector.normalize(raw)
        score = raw.get("raw", {}).get("Score")
        extra = str(score) if score is not None else (
            str(company.age.years_operating) if company.age.years_operating is not None else ""
        )
        table.add_row(
            company.abn or "",
            company.legal_name or "",
            company.location.state or "",
            company.location.postcode or "",
            extra,
        )

    console.print(table)
    console.print(f"[bold]{len(raw_records)}[/] records returned.")


@app.command("asic-load")
def asic_load(
    force: bool = typer.Option(False, "--force", help="Drop and reload the table."),
) -> None:
    """Load/verify the ASIC company spine into DuckDB and print coverage stats."""
    from sourcing.connectors.asic_bulk import ASICBulkConnector

    settings = get_settings()
    if not settings.asic_csv_path:
        console.print("[red]ASIC_CSV_PATH not set. Add it to .env.[/]")
        raise typer.Exit(1)

    connector = ASICBulkConnector.from_settings()
    console.print("[cyan]Loading ASIC company dataset into DuckDB…[/]")
    n = connector.ensure_loaded(force=force)
    cov = connector.query(
        "SELECT count(*) AS total, count(abn) AS with_abn, count(DISTINCT acn) AS distinct_acn "
        "FROM asic_companies"
    )[0]
    table = Table(title="ASIC spine loaded")
    table.add_column("metric")
    table.add_column("value", justify="right")
    table.add_row("total rows", f"{n:,}")
    table.add_row("rows with ABN", f"{cov['with_abn']:,}")
    table.add_row("distinct ACNs", f"{cov['distinct_acn']:,}")
    table.add_row("ABN coverage", f"{100*cov['with_abn']/cov['total']:.1f}%")
    console.print(table)
    connector.close()


@app.command("asic-lookup")
def asic_lookup(
    value: str = typer.Argument(..., help="ACN (9 digits) or ABN (11 digits)."),
) -> None:
    """Point-lookup a company in the ASIC spine by ACN or ABN."""
    from sourcing.connectors.asic_bulk import ASICBulkConnector

    connector = ASICBulkConnector.from_settings()
    digits = "".join(ch for ch in value if ch.isdigit())
    rec = connector.lookup_abn(digits) if len(digits) == 11 else connector.lookup_acn(digits)
    if rec is None:
        console.print(f"[yellow]No match for {value}.[/]")
        connector.close()
        raise typer.Exit(0)

    company = connector.normalize(rec)
    console.print(f"[bold]{company.legal_name}[/]")
    console.print(f"  ACN: {company.acn}   ABN: {company.abn}")
    console.print(f"  status: {rec.get('status_code')}   registered: {company.age.abn_registered} "
                  f"({company.age.years_operating} yrs)")
    console.print(f"  structure: {company.ownership.structure_guess}")
    connector.close()


@app.command("asic-fetch")
def asic_fetch(
    entity_types: str = typer.Option("APTY", "--types", help="Comma-separated ASIC Type codes."),
    min_years: int = typer.Option(0, "--min-years", help="Minimum years since registration."),
    limit: int = typer.Option(20, "--limit", "-n", help="Row cap."),
    save: bool = typer.Option(False, "--save", help="Upsert the slice to Postgres companies."),
) -> None:
    """Pull a filtered candidate slice from the ASIC spine (optionally persist it)."""
    from sourcing.connectors.asic_bulk import ASICBulkConnector

    connector = ASICBulkConnector.from_settings()
    params = {
        "entity_types": [t.strip() for t in entity_types.split(",") if t.strip()],
        "min_years": min_years,
        "limit": limit,
    }
    rows = connector.fetch(params)
    companies = [connector.normalize(r) for r in rows]

    table = Table(title=f"ASIC slice — {params['entity_types']}, ≥{min_years}y  ({len(companies)})")
    table.add_column("ACN")
    table.add_column("ABN")
    table.add_column("Name", overflow="fold")
    table.add_column("Yrs", justify="right")
    for c in companies:
        table.add_row(c.acn or "", c.abn or "", c.legal_name or "",
                      str(c.age.years_operating) if c.age.years_operating is not None else "")
    console.print(table)

    if save:
        from sourcing.connectors.ingest import upsert_companies
        from sourcing.db import session_scope

        with session_scope() as session:
            n = upsert_companies(session, companies)
        console.print(f"[green]Saved {n} records[/] to companies table.")
    connector.close()


if __name__ == "__main__":
    app()
