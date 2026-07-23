# SKILL.md — Financial Due Diligence
## Morsebridge Healthcare | Healthcare PE Workspace

---

## Purpose
Run a full Financial Due Diligence on a Healthcare IT target company. Analyse the financial model, identify revenue quality issues, flag margin and working capital concerns, produce a bear/base/bull scenario summary, and output a PowerPoint report. Includes a hard gate: any Critical red flag triggers a HOLD recommendation pending resolution.

---

## Trigger Prompt Examples
- "Analyse the RevSpring financials"
- "Run financial DD on [company]"
- "Flag revenue quality issues in the financial model"
- "Build the bear/base/bull scenario summary"

---

## Input Files
- Financial Model (.xlsx) in Companies Analyzed/[Company]/Financial DD/
- CIM Screening Report in Companies Analyzed/[Company]/CIM Analysis/
- SCORING-CRITERIA.xlsx in Financial DD/ folder

---

## Analysis Framework

### 1. Revenue Quality Analysis
Examine all revenue streams and assess quality on four dimensions:

| Dimension | What to Assess |
|---|---|
| Recurring vs. non-recurring | % of revenue that is SaaS, subscription, or long-term contract |
| Revenue visibility | Backlog, contracted ARR, renewal schedule |
| NRR trend | Net Revenue Retention over 3 years — is it stable, improving, or declining |
| Concentration risk | Top 1, top 5, top 10 client revenue as % of total |

Flag if:
- Non-recurring revenue exceeds 30% of total
- NRR is below 95% or declining
- Top client exceeds 15% of revenue
- Revenue growth is decelerating faster than market

### 2. Margin Analysis
- Gross margin trend (3-year): expanding, stable, or compressing
- EBITDA margin trend (3-year) and comparison to public comps
- Key cost drivers: headcount, hosting/infrastructure, S&M efficiency
- Margin bridge: organic improvement vs. acquisition contribution
- One-time vs. recurring cost items (identify any add-backs)

### 3. Working Capital Analysis
- DSO (Days Sales Outstanding) trend
- Deferred revenue balance and trend (indicator of billing in advance = quality)
- Cash conversion cycle
- NWC as % of revenue: is it stable or creeping up

### 4. Leverage and Debt Analysis
- Current debt structure (tranches, rates, maturities)
- Net Debt / EBITDA at entry and projected at exit
- Covenant headroom: minimum EBITDA covenant vs. bear case EBITDA
- Interest coverage ratio (EBITDA / interest expense)
- Debt service capacity in bear case

### 5. Cash Flow Analysis
- Unlevered Free Cash Flow (EBITDA - Capex - NWC change)
- Capex intensity (% of revenue) and trend
- Cash conversion rate (FCF / EBITDA)
- Any material one-time cash outflows

### 6. Scenario Analysis
Produce bear/base/bull projections for 2 years forward (FY2025E and FY2026E):

| Scenario | Revenue Growth | EBITDA Margin | Key Assumption |
|---|---|---|---|
| Bear | Low end (Assumptions sheet) | Low end | Slower cross-sell, pricing pressure |
| Base | Mid (Assumptions sheet) | Mid | Management case with modest haircut |
| Bull | High end (Assumptions sheet) | High end | Full cross-sell, M&A contribution |

Pull figures directly from the Financial Model Assumptions sheet — do not create new numbers.

### 7. LBO Returns Check
From the LBO Returns sheet:
- Base case MOIC and IRR at 5-year hold
- Entry multiple vs. exit multiple assumption
- Minimum MOIC scenario (bear, longest hold)
- Whether bear case IRR clears 20% minimum threshold

---

## Hard Gate: Critical Red Flag Triggers

If ANY of the following are present, output must include **HOLD — PENDING RESOLUTION** in the recommendation, with the specific flag identified:

| Critical Flag | Threshold |
|---|---|
| Revenue declining YoY | Any period in last 3 years |
| EBITDA margin | Below 20% in most recent year |
| Net Debt / EBITDA | Above 6.0x at entry |
| Covenant headroom | Bear case EBITDA below covenant floor |
| Bear case IRR | Below 20% |
| Cash conversion | FCF negative in any projected year |
| NRR | Below 90% |
| Top client concentration | Single client above 30% of revenue |

---

## Output: Financial DD Report (PowerPoint)

### File name: [CompanyName]_Financial_DD.pptx
### Save to: Companies Analyzed/[CompanyName]/Financial DD/

### Slide Structure (12-14 slides):

| Slide | Content |
|---|---|
| 1 | Cover: Company name, "Financial Due Diligence", date, Morsebridge Healthcare |
| 2 | Executive Summary: 5 key findings, financial DD score, recommendation (Proceed / Hold) |
| 3 | Revenue Overview: 5-year revenue bridge by product line, YoY growth chart |
| 4 | Revenue Quality: recurring vs. non-recurring mix, NRR trend, concentration heatmap |
| 5 | Margin Analysis: gross margin and EBITDA margin trend, vs. public comps |
| 6 | Cost Structure: cost waterfall, key drivers, add-back analysis |
| 7 | Working Capital: DSO trend, deferred revenue, NWC % of revenue |
| 8 | Debt Structure: tranche table, leverage chart, covenant headroom bar |
| 9 | Cash Flow Analysis: FCF bridge, capex intensity, cash conversion rate |
| 10 | Scenario Analysis: bear/base/bull table (revenue, EBITDA, FCF) for FY2025E-FY2026E |
| 11 | LBO Returns: MOIC/IRR table by scenario, sensitivity matrix |
| 12 | Red Flag Summary: all flags identified, severity, status (open/mitigated) |
| 13 | Financial DD Score: scorecard table, total score, band |
| 14 | Key Questions for Management (financial items requiring management confirmation) |

---

## Formatting Rules
- Dark navy (#0D1B2A) title slides, white text
- Content slides: white background, navy headings, accent blue for data highlights
- Red flag items always in red text (#C00000)
- Proceed recommendation: green box. Hold recommendation: amber box.
- All charts sourced with "Source: Morsebridge Financial Model" or "Source: CIM"
- Arial throughout (titles 24pt, body 18pt, table text 14pt)
- Footer: "Confidential | Morsebridge Healthcare | Financial DD | [Company]"
- Maximum 14 slides

---

## Constraints
- All numbers must be pulled from the Financial Model — do not create new figures
- Do not smooth or adjust numbers without noting the basis
- If a metric cannot be calculated from available data, state "Insufficient data — management confirmation required"
- Hard gate overrides score — a high score does not neutralise a Critical flag
