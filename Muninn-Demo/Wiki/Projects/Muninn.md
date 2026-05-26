---
title: Muninn
kind: project
summary: A local LLM-powered wiki that organizes your digital footprint into an interlinked
  knowledge base using Obsidian and Ollama.
tags:
- tech/ai
- knowledge-management
- project
sources:
- '[[2026-01-15-url-karpathy-llm-wiki]]'
created: '2026-01-15'
updated: '2026-05-25'
confidence: medium
lifecycle: draft
provenance:
  extracted: 0.8
  inferred: 0.2
  ambiguous: 0.0
schema: tech/ai
strength: 1.0
access_count: 1
last_accessed: '2026-05-25T17:54:12'
consolidation_count: 0
status: active
started: '2026-01-01'
owners:
- '[[Nisjit]]'
goal: Build a local, self-maintaining knowledge base that turns raw digital inputs
  into an interlinked wiki
---



Muninn is a local LLM-powered wiki that implements [[Andrej Karpathy]]'s [[LLM Wiki]] pattern. It watches your digital footprint — browser history, tweets, git repos, YouTube transcripts, personal notes — and turns them into an interlinked wiki of markdown pages you can browse in [[Obsidian]].

The architecture follows a three-layer model: a Raw layer (immutable source snapshots), a Wiki layer (LLM-generated pages organized into Concepts, Entities, Topics, and Projects), and Skills (rules that govern how the LLM extracts knowledge). One LLM call per source item for extraction; page creation, merging, and cross-linking are deterministic Python.

Retrieval uses [[Spreading Activation]] across wikilinks combined with keyword and vector search. Pages carry strength that decays over time, access counts that grow on query citation, and nightly consolidation that rewrites for clarity. Weekly maintenance handles dedup, forgetting, and structural plasticity (splitting overgrown pages, clustering related ones).

Everything runs locally via Ollama — no cloud APIs. Local models like Gemma 4, [[Qwen]] 3, and Granite handle extraction and synthesis.

**Repository:** [github.com/abijitgowda/Muninn](https://github.com/abijitgowda/Muninn)

## Sources

- [[2026-01-15-url-karpathy-llm-wiki]]
