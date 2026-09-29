# GEO Prompt Tracker

Bulk-runs your prompt list across the **real logged-in UIs** of ChatGPT,
Perplexity, and Gemini (via Camoufox, an anti-fingerprint browser), captures the
**sources each engine cites**, runs each prompt **5× for consistency**, and
extracts **what each answer recommends, in rank order**. A Leaderboard sheet
(and the web UI) shows which brands/products win each prompt: share of answers,
times ranked #1, average position, and a per-platform split.

No search APIs, so the searches run on your free accounts. A cheap analysis API
(OpenAI or Anthropic) only extracts the recommendations from the captured answers.

## Setup

```bash
# Keep the venv OUTSIDE ~/Desktop: iCloud offloads its files and Python hangs.
python3 -m venv ~/.venvs/geo-tool && source ~/.venvs/geo-tool/bin/activate
pip install -r requirements.txt
python -m camoufox fetch          # one-time browser download (~300MB)
cp .env.example .env              # then paste your analysis API key
```

Pacing and platforms live in `geo/config.py`. Prompts live in `prompts.txt`,
which is created from `prompts.example.txt` on first run; edit it directly or in
the web UI (`python -m geo.webapp`).

## Use

```bash
python -m geo.cli check           # validate config (API key, prompts, output file)
python -m geo.cli login           # one-time: log into each platform in the browser
python -m geo.cli run --headful --limit 1 --runs 1   # smoke test one prompt
python -m geo.cli run             # full batch (headless), resumable
python -m geo.cli reanalyze       # re-extract recommendations that failed (e.g. bad API key)
python -m geo.webapp              # web UI at http://127.0.0.1:8000
```

Results land in a timestamped `geo_prompt_tracker_<date>_<time>.xlsx` in
`~/Library/Application Support/GEO Tool/output/`, outside the project so they
stay on this machine (the Desktop may be synced to iCloud). Set `GEO_DATA_DIR`
in `.env` to change it. The web UI lists every run with its leaderboard.
Runs save after every answer; re-running resumes the latest output file and skips
whatever is already filled.

## Layout

| File | Role |
|---|---|
| `geo/config.py` | Pacing, platforms, paths, analysis model |
| `geo/engines/` | Per-platform scrapers + Camoufox launcher |
| `geo/sources.py` | URL cleaning, redirect unwrapping, domain dedupe |
| `geo/analyze.py` | Extracts ranked recommendations from each answer (LLM) |
| `geo/excel_io.py` | Reads prompts, writes results, rebuilds Leaderboard + Domains sheets |
| `geo/runner.py` | Orchestration, pacing, resume, error isolation |
| `geo/cli.py` | `check` / `login` / `run` / `reanalyze` |
| `geo/webapp.py`, `geo/web/` | Local web UI |

## Privacy — what never leaves your machine

These are git-ignored and must never be committed:

| Path | Contains |
|---|---|
| `.env` | Your analysis API key |
| `.browser-profile/` | Logged-in ChatGPT / Perplexity / Gemini sessions |
| `prompts.txt` | Your (possibly client-specific) prompt list |
| `~/Library/Application Support/GEO Tool/` | All results (outside the repo) |

The web UI binds to `127.0.0.1` only and loads no external resources. Answers are
sent to your analysis provider (OpenAI or Anthropic) for recommendation extraction.

The UI can use the Denim INK typeface if you have a licence: drop the `.woff2` /
`.woff` files into `geo/web/assets/fonts/` (git-ignored). Otherwise it falls back
to system fonts.

## Note on selectors

The per-platform selectors in `geo/engines/*.py` target the current chat UIs and
are the most likely thing to need a tweak when a provider changes its markup.
They're all at the top of each engine file. Run the smoke test first.
