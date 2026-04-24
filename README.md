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

## GitHub to Hugging Face mirror

This repo includes [.github/workflows/deploy-hf-space.yml](./.github/workflows/deploy-hf-space.yml), which force-pushes the current GitHub repository state into a Hugging Face Space on every GitHub push.

Configure these GitHub Actions secrets in your GitHub repository:

| Secret | Description |
| --- | --- |
| `HF_TOKEN` | Hugging Face user access token with write access to the target Space |
| `HF_SPACE_REPO` | Space repo in `username/space-name` format |

Once those are set, pushes to GitHub will mirror into the Hugging Face Space automatically.
