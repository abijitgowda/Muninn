---
title: Agent Framework
kind: concept
summary: Software architectures that enable LLMs to autonomously plan, use tools,
  and execute multi-step workflows beyond simple prompt-response interactions.
tags:
- tech/ai
- software-engineering
- concept
sources:
- '[[2026-02-10-url-transformer-architecture-explained]]'
created: '2026-02-15'
updated: '2026-05-25'
confidence: medium
lifecycle: draft
provenance:
  extracted: 0.6
  inferred: 0.4
  ambiguous: 0.0
schema: tech/ai
strength: 1.0
access_count: 2
last_accessed: '2026-05-25T20:40:36'
consolidation_count: 0
related_concepts:
- '[[LLM Wiki]]'
- '[[Retrieval Augmented Generation]]'
- '[[Transformer Architecture]]'
field: ai/applications
---




An agent framework is a software architecture that wraps a large language model with capabilities for autonomous planning, tool use, memory, and multi-step execution. Unlike a simple chatbot that responds to individual prompts, an agent can decompose complex goals into sub-tasks, invoke external tools (file systems, APIs, databases, browsers), observe results, and iterate until the goal is achieved. The concept draws from classical AI research on planning and reasoning but has been reinvigorated by the capabilities of [[Transformer Architecture]] models that can follow instructions, generate code, and reason about tool outputs within their [[Context Window]].

Major agent frameworks include [[Anthropic]]'s Claude Code (which operates as a coding agent in the terminal), [[OpenAI]]'s Assistants API, LangChain's agent modules, and AutoGPT. The [[Muninn]] project itself is an agent-based system: it uses Claude Code as its backbone to autonomously ingest sources, extract entities, create and update wiki pages, and perform maintenance operations like cross-linking and linting. [[Dario Amodei]] has described agents as "the most important application of large language models," noting that the transition from chat interfaces to autonomous agents represents a qualitative shift in what AI systems can accomplish.

The key challenge for agent frameworks is reliability. Each step in a multi-step plan introduces the possibility of error, and errors compound. [[AI Safety]] concerns are particularly acute for agents because they can take actions in the real world — executing code, sending messages, modifying files — rather than merely generating text. Techniques like [[Retrieval Augmented Generation]] help ground agent decisions in factual evidence, and the [[LLM Wiki]] pattern gives agents persistent memory that survives between sessions, reducing the need to re-derive context from scratch on each invocation.

## Sources

- [[2026-02-10-url-transformer-architecture-explained]]
