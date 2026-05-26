---
title: Retrieval Augmented Generation
kind: concept
summary: A technique that augments LLM generation with relevant documents retrieved
  from an external corpus at query time.
tags:
- tech/ai
- information-retrieval
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
- '[[LLM Wiki]]'
- '[[Spreading Activation]]'
field: ai/information-retrieval
---



Retrieval Augmented Generation (RAG) is a widely adopted pattern in which a language model's generation step is preceded by a retrieval step that fetches relevant documents from an external corpus. First formalized by Lewis et al. at Facebook AI Research in 2020, RAG addresses a fundamental limitation of parametric models: their knowledge is frozen at training time. By grounding generation in retrieved evidence, RAG enables models to answer questions about information that was not part of their training data, reducing hallucination and improving factual accuracy.

The standard RAG pipeline operates in three stages: indexing (embedding documents into a vector store), retrieval (finding the top-k most relevant chunks for a given query), and generation (feeding those chunks alongside the query to the LLM). While effective for one-shot question answering, RAG has a structural limitation highlighted by the [[LLM Wiki]] concept: every query starts from scratch. There is no persistent knowledge layer between sessions. The system never accumulates understanding — it merely re-derives it each time. This makes RAG particularly inefficient for domains where the same corpus is queried repeatedly.

An alternative retrieval mechanism explored in the LLM Wiki context is [[Spreading Activation]], which leverages the graph structure of interlinked pages rather than relying solely on embedding similarity. Where RAG retrieves independent document chunks, spreading activation surfaces entire clusters of related concepts through their wikilink connections — a pattern closer to how associative memory works in human cognition.

## Sources

- [[2026-01-15-url-karpathy-llm-wiki]]
