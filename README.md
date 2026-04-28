---
title: CricHeroes Extras Dashboard
emoji: 🏏
colorFrom: red
colorTo: blue
sdk: docker
app_port: 7860
---

# CricHeroes Extras Dashboard

This Space serves the Dash app from [app.py](./app.py) using the commentary CSVs in [ball-by-ball](./ball-by-ball) and the tournament data in [scorecards](./scorecards).

## Pipeline

Use the single-command refresh flow:

```bash
./refresh_pipeline.sh
```

It does:

- refresh scorecards and points table
- rebuild commentary CSVs from the downloaded PDFs
- validate commentary extras against scorecards

Run the dashboard locally with:

```bash
python -Bu extras_discipline_dashboard.py --input-dir ball-by-ball --points-table scorecards/tournament_points_table.json
```

For a quick CSV rebuild only, you can also use:

```bash
./generate_commentary_csvs.sh
```

## Scripts

| Script | What it does |
| --- | --- |
| `download_scorecards.py` | Refreshes the CricHeroes past matches page, updates `scorecards/past_matches.json`, refreshes the tournament points table, and downloads any scorecard HTML/JSON files that are not already present locally. |
| `refresh_pipeline.sh` | Single-command wrapper that refreshes scorecards, rebuilds commentary CSVs, and validates commentary extras against scorecards after confirming the PDFs are already downloaded in the expected format. |
| `build_match_commentary_csvs.py` | Reads all CricHeroes commentary PDFs in `ball-by-ball`, renames undated PDFs using the match date extracted from the PDF text, merges innings PDFs into one per-match CSV, and rewrites the CSV set. |
| `generate_commentary_csvs.sh` | Thin wrapper that runs `build_match_commentary_csvs.py`. |
| `extract_commentary_csv.py` | Low-level parser used by the rebuild step to extract raw text from a single PDF and convert commentary rows into structured ball-by-ball records. |
| `test/test_scorecard_extras.py` | Compares commentary-derived extras with scorecard extras for each innings and reports `PASS`/`FAIL`. |
| `extras_discipline_dashboard.py` | Loads commentary CSVs and the points table JSON, then serves the Dash analytics UI. |
| `app.py` | Hugging Face Spaces entrypoint that starts the Dash app with environment-configurable paths and port. |

## Missing CSV Rows Vs PDFs

If the parity check says `missing commentary CSV`, that does not necessarily mean the match CSV file is missing. That check works at the innings level.

Current behavior:

| Match | PDF state | Why parity reports missing commentary |
| --- | --- | --- |
| `Akatsuki Corps vs The Fibrillators (2026-04-10)` | Only `Fibrillators_...pdf` is present | The generated match CSV contains only `The Fibrillators` batting innings, so `Akatsuki Corps` is missing from the CSV contents. |
| `Minions vs Akatsuki Corps (2026-04-20)` | Only `Minions_...pdf` is present | The generated match CSV contains only `Minions` batting innings, so `Akatsuki Corps` is missing from the CSV contents. |
| `Illuminati vs Akatsuki Corps (2026-04-24)` | Both innings PDFs are present | No innings is missing; the remaining failures are extras mismatches, not missing CSV data. |

So the real issue is missing innings PDFs for some matches, not missing per-match CSV files.

## GitHub to Hugging Face mirror

This repo includes [.github/workflows/deploy-hf-space.yml](./.github/workflows/deploy-hf-space.yml), which force-pushes the current GitHub repository state into a Hugging Face Space on every GitHub push.

Configure these GitHub Actions secrets in your GitHub repository:

| Secret | Description |
| --- | --- |
| `HF_TOKEN` | Hugging Face user access token with write access to the target Space |
| `HF_SPACE_REPO` | Space repo in `username/space-name` format |

Once those are set, pushes to GitHub will mirror into the Hugging Face Space automatically.
