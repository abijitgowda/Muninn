---
source_type: url
source_url: "https://gist.github.com/karpathy/1dd0294ef9567971c1e4348a90d69285"
title: "LLM Wiki — Andrej Karpathy"
read_date: "2026-01-15"
ingested_at: "2026-01-15T10:30:00"
---

# LLM Wiki

*Extracted via trafilatura from Karpathy's GitHub Gist.*

The core idea: instead of asking an LLM the same questions over and over (RAG every time), you compile knowledge once into interconnected markdown files and keep them current. The LLM is the maintainer. You are the reader.

## The problem with pure RAG

Retrieval Augmented Generation works well for one-shot questions, but it has a fundamental limitation: every query starts from scratch. The retrieval step searches a corpus, pulls relevant chunks, and feeds them to the model. There is no persistent knowledge layer between sessions. If you ask the same question tomorrow, the system does exactly the same work again.

This is wasteful. Worse, it means the system never builds a coherent picture of the domain. Each answer is an isolated response to an isolated query, with no accumulation of understanding.

## The wiki alternative

An LLM Wiki is a persistent knowledge base — a folder of markdown files — that an LLM maintains over time. Sources come in (articles, tweets, PDFs, browser history). The LLM reads them, extracts entities and concepts, and either creates new wiki pages or merges information into existing ones. Over time, the wiki grows into a dense, interlinked graph of knowledge.

The key properties:
- **Persistent**: knowledge is compiled once and reused, not re-derived every query.
- **Interlinked**: pages reference each other via `[[wikilinks]]`, forming a knowledge graph.
- **Source-tracked**: every claim traces back to the source it came from.
- **LLM-maintained**: the model handles extraction, merging, cross-linking, and cleanup.
- **Human-readable**: it's just markdown. Open it in Obsidian or any text editor.

## Spreading activation for retrieval

When answering questions, the wiki uses a brain-like retrieval pattern called spreading activation. Instead of flat keyword search, you start at a relevant node and let activation spread through wikilinks to related nodes. This naturally surfaces contextually relevant information even when exact keyword matches fail.

This is analogous to how human memory works: thinking about "neural networks" naturally activates related concepts like "backpropagation," "gradient descent," and "Geoffrey Hinton" — not because of keyword overlap but because of structural connections in memory.

## Personal knowledge management

The LLM Wiki pattern transforms personal knowledge management. Traditional PKM tools (Notion, Roam, Obsidian) require the human to do the organizing. The LLM Wiki flips this: the human feeds sources, the LLM organizes. You get the benefits of a meticulously maintained personal wiki without the maintenance burden.

The result is a "second brain" that actually stays current — because the LLM never gets tired of filing things away.
