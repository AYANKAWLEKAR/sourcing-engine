# Morsebridge Healthcare — Deal Intelligence Agent (deploy-ready)

A filesystem-native Claude agent that runs the full PE deal lifecycle (Deal Sourcing → CIM
screening → Commercial / Financial / Management DD → IC Memo) for Morsebridge Healthcare.

This is the **automated, context-aware** build: the agent reads prior deal files, writes
finished artifacts back to storage, and benchmarks every output against the RevSpring golden
examples. Because it needs persistent read/write, it runs on **Claude Code** or the **Agent
SDK** — not a Claude.ai Project (a Project cannot write to this folder tree).

## What's wired in

- **`CLAUDE.md`** at the workspace root — auto-loads when Claude Code is launched here. Beyond
  the original fund brief it now defines three protocols:
  - **Context-Awareness** — read prior work + upstream DD outputs before acting.
  - **Storage / Output Contract** — write artifacts to `Companies Analyzed/[Company]/[Workflow]/`
    (and watchlists to `Deal Sourcing/Reports/`), create folders first, confirm saved paths.
  - **Golden-Example** — compare each artifact to the matching `RevSpring` reference before finalizing.
- **6 workflow skills** (`*/SKILL.md` + `SCORING-CRITERIA.xlsx`) — invoked via the CLAUDE.md
  router (read-on-demand pattern; no frontmatter/skill-conversion needed).
- **`Companies Analyzed/RevSpring/`** — the golden reference deal (fully worked example).

## Launch (Claude Code)

```bash
cd morsebridge-agent
claude
```

Launching from **inside this folder** is what makes `CLAUDE.md` auto-load and makes every
relative path (`CIM Analysis/SKILL.md`, `Companies Analyzed/...`) resolve. Then drive it with
one-sentence prompts:

- `Build me this week's target watchlist`
- `Screen the [Company] CIM`
- `Run financial DD on [Company]`
- `Draft the IC memo for [Company]`

## Requirements

- **Web search** enabled (Deal Sourcing, CIM, Commercial DD, Management DD all use it).
- **Document generation** — the `docx` / `pptx` / `xlsx` skills produce the Office artifacts.
- **Write access** to this directory (default in Claude Code).

## Automation / headless

For scheduled or unattended runs (e.g. a weekly watchlist), use the **Agent SDK** with
`CLAUDE.md` as the system prompt and filesystem + web-search + code-execution tools, or wrap the
Claude Code invocation in a scheduled task. Keep confidential deal data local; the sourcing/DD
skills issue outbound web-search queries, so never let deal-specific figures go into a query.

## Notes

- Folder structure is intentional and **must be preserved** — paths are how the agent finds
  inputs and files outputs. (Do not flatten; flattening is only for the Claude.ai Project surface.)
- Stale lock/tmp files from the source copy have been removed.
