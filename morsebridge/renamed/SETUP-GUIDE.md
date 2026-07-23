# Setup Guide — Morsebridge Agent as a Claude Project

A flat, upload-ready copy. Every file has a unique name so you can drag them all into one
Project without collisions. Follow these 5 steps.

## Steps

1. **Create a Project** — in Claude, New Project → name it "Morsebridge Healthcare".
2. **Set the instructions** — open `CLAUDE.md`, copy its full contents, and paste them into the
   Project's **Custom instructions** field. (This is the file that shows up as
   **"Instructions · CLAUDE.md"** in the Working folders panel.)
3. **Upload everything else** — select all remaining files in this folder and add them to
   **Project knowledge**:
   - the 6 `*-SKILL.md` procedures + 6 `*-SCORING-CRITERIA.xlsx` rubrics
   - the `RevSpring_*` golden examples (these are your quality benchmark)
   - `2026-06-05_Watchlist.xlsx` (example output)
4. **Turn on web search** for the Project — Deal Sourcing, CIM, Commercial DD, and Management DD
   all rely on it.
5. **Run a prompt** — e.g. `Build me a target watchlist` or `Screen the [Company] CIM`.

## Adding files / web links per deal

- **Files** (a new CIM, a financial model): drop them straight into the Project knowledge, or
  attach them to a single message with the "+" / paperclip.
- **Web links**: paste the URL in your message and ask Claude to read it, or attach it — with web
  search on it will fetch the page.

## About the side panels (they are automatic)

You do **not** configure these — the Claude app renders them from the session:

- **Working folders** — mirrors what you uploaded; your instructions file appears as
  "Instructions · CLAUDE.md".
- **Context** — fills itself in as Claude reads files and calls tools.
- **Progress** — appears on its own for longer, multi-step tasks.

## What this Project can and cannot do

- **Can:** read all uploaded files (past deals, rubrics, golden examples), research the web, and
  compare each artifact it produces against the matching RevSpring example.
- **Cannot:** write results back into folders. It hands you **downloadable** .docx/.pptx/.xlsx
  files — save each one wherever you keep that deal.
- If you want the agent to persist outputs and auto-chain workflows with no downloading, use the
  Claude Code build in the sibling `morsebridge-agent/` folder instead.

## Naming (why the files look different here)

A Project stores files flat by name, so the six `SKILL.md` files (and six `SCORING-CRITERIA.xlsx`)
were renamed with a workflow prefix — e.g. `CIM-Analysis-SKILL.md`,
`CIM-Analysis-SCORING-CRITERIA.xlsx`. The instructions (`CLAUDE.md`) already point at these exact
names, so nothing else to change.
