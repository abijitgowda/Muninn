---
title: LLM Wiki
kind: concept
summary: A persistent, interlinked knowledge base maintained by large language models,
  proposed by Andrej Karpathy as an alternative to repeated RAG queries.
tags:
- tech/ai
- knowledge-management
- concept
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
access_count: 2
last_accessed: '2026-05-25T20:38:52'
consolidation_count: 0
related_concepts:
- '[[Retrieval Augmented Generation]]'
- '[[Personal Knowledge Management]]'
- '[[Spreading Activation]]'
field: ai/knowledge-systems
---




The LLM Wiki is a concept introduced by [[Andrej Karpathy]] that reimagines how large language models interact with accumulated knowledge. Rather than relying on [[Retrieval Augmented Generation]] to re-derive answers from a raw corpus on every query, the LLM Wiki compiles knowledge once into a persistent layer of interconnected markdown files. The model reads incoming sources — articles, tweets, PDFs, browser history — extracts entities and concepts, and either creates new pages or merges information into existing ones. Over time, this produces a dense, interlinked knowledge graph where every claim traces back to a source.

The key insight is that traditional RAG treats the LLM as stateless: each session starts from zero. The wiki pattern gives the model a durable memory. When new information arrives, it does not replace old pages but merges with them, preserving wikilinks and provenance. This mirrors how human experts maintain mental models — incrementally updating rather than rebuilding from scratch. The wiki is stored as plain markdown, viewable in tools like [[Obsidian]], and uses `[[wikilinks]]` as its connective tissue.

Retrieval within an LLM Wiki follows a [[Spreading Activation]] pattern rather than flat keyword search. Starting from a relevant node, activation flows through wikilinks to surface contextually related pages. This approach is particularly effective for [[Personal Knowledge Management]], where the goal is not just to store information but to make it discoverable through associative connections — the same way a human might recall a concept by thinking about a neighboring idea.

## Sources

- [[2026-01-15-url-karpathy-llm-wiki]]
