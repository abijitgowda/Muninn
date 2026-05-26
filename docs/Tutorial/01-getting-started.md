---
title: "01 — Getting started"
kind: doc
tags: [tutorial]
lifecycle: pinned
---

# 01 — Getting started

You have an empty vault. By the end of this 5-minute walkthrough you'll have:

- Verified the vault opens in Obsidian
- Verified the CLI runs
- Pulled the Ollama model the ingest pipeline needs

## Prerequisites

- macOS (the launchd scheduler is Mac-specific)
- [Obsidian](https://obsidian.md/)
- [Ollama](https://ollama.com/) installed and running (`ollama --version`)
- Python 3.11+

## Step 1 — Open the vault

In Obsidian: **File → Open vault → Open folder as vault** → pick `Muninn-Vault/`.

The left sidebar should show: `Raw`, `Schema`, `tutorial`, `Wiki`, `Welcome.md`, `README.md`, `AGENTS.md`.

Open `Welcome.md` to confirm wikilinks render.

## Step 2 — Install and verify the CLI

```bash
cd ~/git/Muninn
python3 -m venv .venv
.venv/bin/pip install -e ".[all]"
sudo ln -sf $(pwd)/.venv/bin/muninn /usr/local/bin/muninn
muninn --help
```

You should see the subcommand list (`init`, `ingest`, `ingest-url`, `lint`, `maintain`, `query`, `serve`, `status`, `sources`).

## Step 3 — Pull the Ollama model

```bash
ollama pull gemma4:e4b          # ingest (strict JSON extraction)
ollama pull mxbai-embed-large  # local embeddings for Copilot (fully private)
```

Verify it answers:

```bash
ollama run gemma4:e4b "Say hi in 5 words."
```

## Step 4 — Initialize

```bash
muninn init
```

This:
1. Copies `wiki.example.yaml` → `wiki.yaml`
2. Copies `.env.example` → `.env`
3. Installs the LaunchAgents (you can skip with `--no-launchd`)
4. Symlinks skills into `.claude/skills/`
5. Prints next steps

## Step 5 — Configure

Edit `wiki.yaml`:
- Enable/disable sources
- Adjust browser history paths
- Set `ollama_model_ingest` / `ollama_model_query`

Edit `.env` (secrets only):
- `X_BEARER_TOKEN` if using X
- `MUNINN_API_TOKEN` if protecting the serve endpoint

## Step 6 — Read the runbooks

- `scripts/ingest-runbook.md`
- `scripts/lint-runbook.md`
- `scripts/query-runbook.md`

Then go to [[tutorial/02-first-ingest]].
