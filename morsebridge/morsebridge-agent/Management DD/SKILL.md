# SKILL.md — Management Due Diligence
## Morsebridge Healthcare | Healthcare PE Workspace

---

## Purpose
Prepare a structured Management DD package for a Healthcare IT target company. Covers pre-meeting research, an interview guide for the CEO and CFO, and a post-meeting Management DD Report as a PowerPoint. Score is with commentary only — no hard pass/fail gate.

---

## Trigger Prompt Examples
- "Prepare my management DD interview guide for the RevSpring CEO and CFO"
- "Build the management DD report for [company]"
- "Research the RevSpring leadership team"

---

## Input Files
- Management DD Brief (Word doc) in Companies Analyzed/[Company]/Management DD/
- CIM Screening Report in Companies Analyzed/[Company]/CIM Analysis/
- Commercial DD Report in Companies Analyzed/[Company]/Commercial DD/
- Financial DD Report in Companies Analyzed/[Company]/Financial DD/
- SCORING-CRITERIA.xlsx in Management DD/ folder

---

## Step 1: Pre-Meeting Research

Run web searches on each named executive. Minimum 2 searches per executive (name + company, name + LinkedIn/prior role).

For each executive, extract:
- Current role and tenure at the company
- Prior roles (last 2-3 positions)
- PE-backed company experience (critical — note whether they have prior PE partnership experience)
- Sector expertise and domain depth
- Public speaking, conference appearances, press quotes
- Any public controversies, litigation, or material departures from prior roles
- Equity participation / rollover stake (if disclosed)

---

## Step 2: Management Assessment Framework

Assess the leadership team across five dimensions:

### 1. PE Partnership Readiness
- Has the CEO managed through a PE hold period before?
- Does the management team understand PE reporting cadence (monthly P&L, board prep, portfolio monitoring)?
- Is there a CFO capable of handling PE-grade financial reporting?
- Score 1-5. Critical dimension — a score of 1 or 2 here must be flagged in the report.

### 2. Strategy and Vision Clarity
- Can the CEO articulate a clear 3-5 year value creation plan?
- Is the strategy consistent with what is in the CIM?
- Are growth assumptions realistic vs. the financial model?
- Does leadership acknowledge risks rather than deflecting?

### 3. Operational Depth
- Does the management team have hands-on operational experience, not just financial/commercial?
- Is there a credible plan to hit the base case operational milestones?
- Is there a second tier of leadership below the C-suite?

### 4. Team Stability and Retention
- Average C-suite tenure
- Any recent departures at VP level or above
- Equity plan: are key people retained with meaningful stakes?
- Succession plan for CEO and CFO

### 5. Cultural and Communication Fit
- Does the management team communicate in a PE-compatible register (data-driven, direct, no spin)?
- Is there evidence of prior investor transparency (board materials, reporting quality)?
- Red flags: excessive optimism, deflection of hard questions, inability to discuss failures

---

## Step 3: Interview Guide

Produce a structured interview guide for two sessions:

### CEO Interview (60 minutes)
Organise questions into 5 blocks:

**Block 1: Strategy (15 min)**
- Describe the company's market position in one sentence. What do you do that no competitor does at your scale?
- What is the single biggest risk to your FY2025 revenue plan and how are you managing it?
- Where do you see the business in 5 years? What needs to be true for that to happen?

**Block 2: Growth and Commercial (15 min)**
- Walk me through your top 3 revenue growth drivers for the next 2 years.
- What is the cross-sell penetration rate in your existing client base and what is the ceiling?
- Where have you lost deals in the last 12 months and why?

**Block 3: Operations (10 min)**
- What are the top 2 operational constraints on scaling from here?
- How does your technology stack compare to your nearest competitor today?

**Block 4: Team and Organisation (10 min)**
- Which members of your leadership team are indispensable to this plan?
- What has been the biggest people mistake you have made in this role?

**Block 5: PE Partnership (10 min)**
- What did you learn from working with [prior PE sponsor] that you would do differently?
- What do you need from Morsebridge that you did not get from your prior sponsor?
- What would cause this deal not to work?

### CFO Interview (45 minutes)
Organise questions into 4 blocks:

**Block 1: Financial Model Integrity (15 min)**
- Walk me through the key assumptions behind your FY2025 revenue plan.
- What is the biggest variance risk in your EBITDA forecast?
- How confident are you in the Q1 2025 actuals vs. plan?

**Block 2: Revenue Quality (15 min)**
- What percentage of FY2024 revenue was fully contracted at the start of the year?
- What is your net revenue retention and how has it trended over the last 3 years?
- Walk me through your top 5 clients and their renewal status.

**Block 3: Working Capital and Cash (10 min)**
- What is your DSO and how has it trended?
- Are there any material one-time items in the FY2023 EBITDA that we should understand?
- What is your current cash position and runway?

**Block 4: PE Readiness (5 min)**
- What does your current board reporting package include?
- Have you managed an audit process with a Big 4 firm?

---

## Output Files

### File 1: Management DD Interview Guide (Word Document)
- File name: [CompanyName]_Mgmt_DD_Interview_Guide.docx
- Save to: Companies Analyzed/[CompanyName]/Management DD/
- Contents: executive research summaries (one page per exec), CEO interview guide, CFO interview guide
- Maximum 8 pages

### File 2: Management DD Report (PowerPoint) — produced post-meeting
- File name: [CompanyName]_Management_DD.pptx
- Save to: Companies Analyzed/[CompanyName]/Management DD/

### Slide Structure (8-10 slides):

| Slide | Content |
|---|---|
| 1 | Cover: Company name, "Management Due Diligence", date |
| 2 | Executive Summary: 4 key findings, overall management score, commentary |
| 3 | Leadership Team Overview: org chart / table with name, role, tenure, prior PE exp |
| 4 | PE Partnership Readiness: score and evidence |
| 5 | Strategy and Vision Assessment: consistency with CIM, realism of growth plan |
| 6 | Operational Depth and Second-Tier Leadership |
| 7 | Team Stability: tenure chart, equity plan, retention risk |
| 8 | Key Concerns and Open Questions for IC Memo |
| 9 | Management DD Score: scorecard table, total, band |

---

## Formatting Rules
- Same deck styling as Commercial DD and Financial DD (dark navy covers, white content slides)
- Arial throughout
- Score with commentary — no hard pass/fail box
- Red flags in red text
- Footer: "Confidential | Morsebridge Healthcare | Management DD | [Company]"
- Maximum 10 slides

---

## Constraints
- Do not make personal judgments about individuals without factual basis
- PE partnership readiness is the most important dimension — weight it accordingly in commentary
- If an executive has no prior PE experience, flag this explicitly but do not automatically score low — assess whether there is a strong CFO or operating partner to compensate
- Interview guide questions are starting points — Claude should add 2-3 company-specific questions based on flags identified in Commercial DD and Financial DD reports
