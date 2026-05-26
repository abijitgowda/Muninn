<p align="center">
  <img src="docs/logo.png" alt="Muninn" width="480"/>
</p>

<p align="center"><strong>Your local memory. Private by design.</strong></p>

---

## Why Muninn exists

I wanted a second brain that doesn't phone home. Every LLM wiki tool I found ships my browser history, notes, and reading patterns to OpenAI or Anthropic. My most personal data, on someone else's servers.

So I built one that stays on my machine. Muninn runs in the background — it watches my browser history, git repos, notes, and tweets, and quietly turns them into an interlinked wiki I can query. I don't curate it. I don't feed it. I just live my digital life and it builds my memory for me. Local models like Gemma 4, Qwen 3, and Granite handle extraction and synthesis just fine — no frontier model needed.

Inspired by [Andrej Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

## Quick start

```bash
git clone <repo> && cd Muninn
./scripts/install.sh
source .venv/bin/activate
ollama pull gemma4:e4b
```

Try the demo vault before setting up your own:

```bash
muninn setup-obsidian Muninn-Demo
muninn serve
# Open Muninn-Demo/ in Obsidian → Copilot chat → ask anything
```

Set up your own vault:

```bash
muninn init
muninn ingest --all
muninn query "what have I been reading about?"
```

## Obsidian Copilot

`muninn setup-obsidian` installs and configures the [Copilot](https://github.com/logancyang/obsidian-copilot) plugin automatically. Just run `muninn serve` and open your vault in Obsidian. If prompted, turn off Restricted mode to enable the plugin.

Responses stream with clickable `[[wikilinks]]` to your wiki pages. Open the graph view (`Cmd+G`) to see how pages connect.

## Philosophy

**Private by default.** Ollama runs on your machine. No tokens leave. No API keys needed for the core pipeline. The only network calls are fetches you explicitly opt into.

**Runs in the background.** You don't curate a wiki — you live your digital life and Muninn builds it. Browser history, git projects, YouTube transcripts, inbox drops — sources feed in automatically on a schedule.

**Local models are enough.** An 8B model on a Mac handles JSON extraction and synthesis well. Muninn ships with `gemma4:e4b` as the default. Swap in `qwen3:8b`, `granite4.1:8b`, or anything Ollama supports.

**One LLM call per source item.** Page creation, merging, cross-linking, and indexing are deterministic Python. No LLM variability in the write path.

**Memory, not a database.** Pages have strength that decays, access counts that grow, nightly consolidation that rewrites for clarity, and structural plasticity that splits and clusters pages as the wiki grows.

**Extend with skills, not code.** Source connectors are one Python file + one YAML entry. Or describe what you want to a coding agent — the `muninn-source-connector` skill provides the contract.

## What it does

- **Ingests** from browser history, X/Twitter, git repos, YouTube, Claude Code history, your own notes, inbox files, and URLs
- **Extracts** structured knowledge via local LLM — entities, concepts, claims with provenance markers
- **Builds** an interlinked wiki with `[[wikilinks]]`, automatic cross-linking, and a graph you can explore in Obsidian
- **Queries** via CLI or Obsidian Copilot — adaptive retrieval with spreading activation across the knowledge graph
- **Maintains itself** — nightly consolidation, weekly decay/dedup, structural plasticity (split, cluster, reparent)
- **Stays private** — everything on your machine, serve endpoint on localhost only

## Customization

Don't configure. Just ask your coding agent.

Muninn ships with `.agents/skills/` that give Claude Code, Cursor, or any agent the context to modify the system. Want a new source connector? Want to change how retrieval works? Describe it.

For manual work: `muninn/sources/` for connectors, `wiki.yaml` for configuration, `DESIGN.md` for architecture.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com) with any model (default: `gemma4:e4b`)
- macOS or Linux (Docker supported)
- [Obsidian](https://obsidian.md) (optional — the wiki is plain markdown)

## Architecture

```
Vault/
├── Wiki/              # knowledge pages with [[wikilinks]]
├── Raw/Sources/       # immutable source snapshots
├── Journal/             # your own notes — ingested automatically, kept in place
├── Inbox/             # drop files here — ingested and moved
├── Welcome.md
├── .muninn/           # manifest.db, chroma/, archives/
└── .obsidian/         # Obsidian config
```

See [DESIGN.md](DESIGN.md) for the full architecture, retrieval pipeline, memory model, and implementation details.

## Privacy

Everything local. Ollama on your machine. ChromaDB on disk. Serve binds to `127.0.0.1`. Secrets in `.env` (gitignored). The only network calls are source fetches you opt into.

## License

MIT
