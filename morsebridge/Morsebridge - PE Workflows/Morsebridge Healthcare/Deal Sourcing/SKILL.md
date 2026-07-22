# SKILL.md — Deal Sourcing
## Morsebridge Healthcare | Healthcare PE Workspace

---

## Purpose
Build a weekly Healthcare IT target watchlist by searching for companies showing active M&A signals. Output is a polished, formatted Excel report saved to Deal Sourcing/Reports/ with the naming convention YYYY-MM-DD_Watchlist.xlsx.

---

## Trigger Prompt Examples
- "Build me a target watchlist"
- "Run this week's deal sourcing"
- "What Healthcare IT companies are showing signals this week"
- "Update the watchlist"

---

## Target Parameters

### Sectors to Search
1. Revenue Cycle Management (RCM) and patient financial engagement
2. Clinical documentation and coding automation
3. Population health and value-based care analytics
4. Healthcare data and interoperability platforms
5. Payer technology and claims management
6. Healthcare workforce and scheduling technology
7. Patient access, scheduling, and price transparency

### Deal Size Filter
- Target EV: $500M to $2B+
- Revenue: $80M+ minimum
- EBITDA: $25M+ minimum (prefer $40M+)

### Geography
- North America only (US primary, Canada secondary)

---

## Signal Types to Search For

Search the web for companies exhibiting ANY of the following signals. Use multiple searches across news sources, press releases, LinkedIn, PitchBook announcements, and trade publications (Healthcare IT News, MedCityNews, Becker's Hospital Review, Modern Healthcare).

| Signal Type | What to Look For |
|---|---|
| PE sponsorship approaching exit | Portfolio companies held 4+ years by PE firms |
| Revenue growth acceleration | Press releases citing 20%+ YoY growth, new contract wins |
| Strategic hire signals | C-suite hiring (CFO, Chief Revenue Officer) often precedes sale process |
| M&A activity | Company making acquisitions suggests platform-building ahead of exit |
| New product launches | SaaS launches or platform expansions increasing addressable market |
| Funding rounds | Late-stage VC rounds ($50M+) suggesting pre-exit positioning |
| Industry awards / rankings | KLAS rankings, Best in KLAS, Inc. 5000 recognition |
| Conference presentations | CEO presenting at HIMSS, JP Morgan Healthcare, ViVE |
| Regulatory tailwinds | Companies benefiting from price transparency, interoperability rules |
| Distressed signals | Leadership turnover, layoffs, missed earnings (flag as opportunistic) |

---

## Search Methodology

Run at minimum 8 web searches per weekly report. Suggested query structure:

1. "[sector] healthcare IT company PE-backed 2024 2025 acquisition"
2. "[sector] healthcare software company growth revenue KLAS 2024"
3. "healthcare IT M&A deal announced [current month] [current year]"
4. "PE-backed healthcare technology company CFO hire 2024"
5. "[specific company names from prior watchlists] news update"
6. "healthcare RCM payer technology company funding round 2024 2025"
7. "HIMSS 2025 healthcare IT startup emerging company"
8. "healthcare IT company sale process banker engaged 2024 2025"

For each promising result, run a follow-up search to verify signal strength before including in the watchlist.

---

## Output Format

Produce a single Excel file with two sheets:

### Sheet 1: Watchlist
One row per company. Columns:

| Column | Description |
|---|---|
| Company Name | Full legal name |
| Sub-Sector | From target sector list above |
| HQ | City, State |
| Est. Revenue ($M) | Best available estimate (flag if unverified) |
| Est. EBITDA ($M) | Best available estimate (flag if unverified) |
| Ownership | PE-backed (fund name) / VC-backed / Public / Independent |
| PE Hold (years) | If PE-backed, approximate years held |
| Signal Type | Primary signal from signal type list above |
| Signal Detail | One sentence describing the specific signal observed |
| Signal Strength Score | 1-5 (use SCORING-CRITERIA.xlsx rubric) |
| Source | URL or publication name |
| Analyst Comment | One sentence on why this warrants attention |
| Recommended Action | Monitor / Request CIM / Outreach / Pass |

### Sheet 2: Summary
- Total companies screened this week
- Breakdown by sub-sector
- Breakdown by signal type
- Top 3 companies recommended for outreach this week with one-line rationale each
- Week-over-week changes if prior watchlist exists in the Reports folder

---

## Formatting Rules
- Dark navy header row (hex #1B3A5C), white text, Arial 11pt bold
- Alternating row shading (white / light blue #D6E4F0)
- Signal Strength Score column: color coded (5 = dark green, 4 = light green, 3 = amber, 2 = light red, 1 = dark red)
- Recommended Action column: color coded (Request CIM = dark green, Monitor = amber, Pass = grey)
- Auto-filter on all columns
- Freeze top row
- File saved as: Deal Sourcing/Reports/YYYY-MM-DD_Watchlist.xlsx

---

## Constraints
- Never include companies with EV below $200M (too small for our mandate)
- Never include pure services / BPO companies with no software platform
- Never include companies with >40% single-client revenue concentration if known
- Flag but do not exclude distressed signals — mark as "Opportunistic" in Analyst Comment
- If revenue or EBITDA data is unavailable, mark cell yellow and note "Est. unverified"
- Maximum 15 companies per weekly watchlist (quality over quantity)
