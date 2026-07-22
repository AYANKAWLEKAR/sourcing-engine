# SKILL.md — CIM Analysis
## Morsebridge Healthcare | Healthcare PE Workspace

---

## Purpose
Screen an incoming CIM (Confidential Information Memorandum) for a Healthcare IT target company. Produce a structured CIM Screening Report as a Word document and a scored assessment against our Healthcare IT acquisition criteria. Output a binary go/no-go recommendation to proceed to full DD.

---

## Trigger Prompt Examples
- "Screen the RevSpring CIM"
- "Review this CIM and give me a deal snapshot"
- "Score this CIM against our criteria"
- "Does this go to full DD?"

---

## Input Files
- CIM document (Word or PDF) in Companies Analyzed/[Company]/CIM Analysis/
- SCORING-CRITERIA.xlsx in CIM Analysis/ folder

---

## Screening Process

### Step 1: Read the CIM
Extract the following data points. If any are absent from the CIM, flag as "Not Disclosed" and note this as a risk.

**Business fundamentals:**
- Business description and primary product(s)
- Revenue model (SaaS / transaction / services / hybrid)
- Total revenue (last 3 years if available)
- EBITDA and EBITDA margin (last 3 years if available)
- Revenue growth rate (YoY and CAGR)
- Recurring revenue percentage
- Net Revenue Retention (NRR) if disclosed
- Customer count and concentration (top client % of revenue)
- Contract length and renewal terms

**Market and competitive position:**
- TAM and market growth rate
- Competitive positioning and differentiators
- Key regulatory or compliance considerations

**Management:**
- CEO and CFO background (years in role, prior PE experience)
- Management rollover / equity participation

**Deal:**
- Seller and ownership history
- Indicated transaction value or EV range
- Process type (auction / bilateral / proprietary)

### Step 2: Apply Mandatory Filters
Check all of the following. If ANY filter fails, the output must include a HARD PASS recommendation regardless of score.

| Filter | Threshold |
|---|---|
| Minimum Revenue | $80M |
| Minimum EBITDA | $25M |
| Minimum Recurring Revenue | 60% |
| Maximum Single-Client Concentration | 40% |
| Geography | North America only |
| Business Model | Must have software/technology platform |
| EV Range | $500M — $2B+ |

### Step 3: Score the CIM
Use SCORING-CRITERIA.xlsx. Score each criterion 1-5. Calculate weighted total. Record scores directly in the screening report table.

### Step 4: Identify Red Flags
Flag any of the following if present:

- Revenue declining YoY in any period
- EBITDA margin below 20%
- NRR below 95% or not disclosed
- Customer concentration above 25% (single client)
- Management team with less than 2 years average tenure
- Litigation or regulatory investigation disclosed
- Earn-out above 20% of deal value
- Contracts shorter than 12 months with no auto-renewal
- Technology infrastructure described as legacy or on-premise only
- No disclosed EV range (flag as process risk)

---

## Output: CIM Screening Report (Word Document)

### File name: [CompanyName]_CIM_Screening_Report.docx
### Save to: Companies Analyzed/[CompanyName]/CIM Analysis/

### Document Structure:

**Section 1: Deal Snapshot (one page)**
- Company name, sector, HQ, ownership, deal type
- Indicated EV and implied multiples
- One-paragraph business description
- Three-bullet investment highlights
- Three-bullet preliminary concerns

**Section 2: Mandatory Filter Checklist**
- Table showing pass/fail for each filter
- If any fail: HARD PASS highlighted in red

**Section 3: Scoring Summary**
- Table with criterion, score (1-5), weight, weighted score
- Total weighted score out of 100
- Score interpretation: 80+ = Strong Proceed, 65-79 = Conditional Proceed, below 65 = Pass

**Section 4: Red Flags**
- Numbered list of all flags identified
- Severity for each: Critical / Significant / Minor
- One sentence on implication for each flag

**Section 5: Recommendation**
- PROCEED TO FULL DD / CONDITIONAL PROCEED / HARD PASS
- If Conditional Proceed: list the specific conditions to be resolved before proceeding
- Maximum 3 sentences. No filler language.

---

## Formatting Rules
- Arial 11pt, dark navy headings (#1B3A5C)
- All tables use alternating row shading
- Red flag items in red text
- Recommendation section in bold, coloured box (green for Proceed, amber for Conditional, red for Pass)
- Footer: "Confidential | Morsebridge Healthcare | For Internal Use Only"
- Maximum 6 pages

---

## Constraints
- Do not reproduce large sections of the CIM verbatim
- Do not state opinions without factual basis from the CIM
- If a data point is absent, state "Not Disclosed" — do not estimate
- One-sentence prompts are sufficient; read this SKILL.md and execute fully
