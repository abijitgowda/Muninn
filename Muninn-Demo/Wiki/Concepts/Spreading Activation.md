---
title: Spreading Activation
kind: concept
summary: A brain-inspired retrieval mechanism where activating one node in a knowledge
  graph propagates activation to connected nodes through link traversal.
tags:
- tech/ai
- cognitive-science
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
access_count: 1
last_accessed: '2026-05-25T17:54:12'
consolidation_count: 0
related_concepts:
- '[[Retrieval Augmented Generation]]'
field: cognitive-science/memory
---


Spreading activation is a retrieval model borrowed from cognitive science and network theory. First proposed by Collins and Loftus in 1975 as a theory of semantic memory, it describes how activating one concept in a network causes activation to spread along associative links to related concepts. In the context of knowledge graphs and wiki systems, it provides an alternative to flat keyword search or embedding-based retrieval: instead of searching the entire corpus independently for each query, you start at a known relevant node and let activation propagate through its connections.

The mechanism is particularly well-suited to interlinked knowledge bases like the [[Retrieval Augmented Generation]]-alternative proposed in the [[LLM Wiki]] pattern. When a user asks a question, the system identifies an entry-point page, then traverses outgoing `[[wikilinks]]` to gather contextually relevant neighbors. Activation decays with each hop, so directly connected pages receive strong activation while distant ones receive progressively less. This naturally surfaces clusters of related knowledge without requiring embedding similarity — the graph structure itself encodes relevance.

The analogy to biological memory is direct: thinking about "neural networks" naturally activates "backpropagation," "gradient descent," and associated researchers — not through keyword overlap but through structural connections laid down by prior learning. In a wiki, those structural connections are the wikilinks that the LLM creates and maintains during ingestion, making spreading activation a low-cost, high-signal retrieval strategy.

## Sources

- [[2026-01-15-url-karpathy-llm-wiki]]
