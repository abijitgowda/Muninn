---
title: Context Window
kind: concept
summary: The maximum number of tokens a language model can process in a single forward
  pass, defining the boundary of what it can 'see' at once.
tags:
- tech/ai
- deep-learning
- concept
sources:
- '[[2026-02-10-url-transformer-architecture-explained]]'
created: '2026-02-11'
updated: '2026-05-25'
confidence: high
lifecycle: stable
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
- '[[Transformer Architecture]]'
- '[[Attention Mechanism]]'
- '[[Retrieval Augmented Generation]]'
field: ai/architectures
---


The context window is the maximum number of tokens that a [[Transformer Architecture]] model can attend to during a single forward pass. It represents the fundamental boundary of the model's working memory: anything outside the window is invisible to the model during generation. Early GPT models had context windows of 2,048 tokens; GPT-4 extended this to 128K tokens; and [[Anthropic]]'s Claude models have pushed to 200K tokens and beyond. [[Google DeepMind]]'s Gemini models have demonstrated windows exceeding 1 million tokens. These expansions have been driven by innovations in the [[Attention Mechanism]], including techniques like rotary position embeddings, sliding window attention, and FlashAttention that reduce the computational cost of processing long sequences.

Context window size has profound implications for how models are used. Larger windows enable "in-context learning" — providing examples, instructions, and reference material directly in the prompt rather than requiring [[Fine-tuning]]. They also affect [[Retrieval Augmented Generation]] design: with a 200K-token window, entire documents or collections of wiki pages can be loaded as context, blurring the line between retrieval and prompting. The [[LLM Wiki]] pattern benefits significantly from long context windows because the [[Spreading Activation]] retrieval step gathers clusters of interlinked pages that must all fit within the window for the model to reason across them.

Despite the trend toward longer windows, there are diminishing returns. Research has shown that models struggle to attend uniformly across very long contexts — a phenomenon sometimes called "lost in the middle," where information in the center of a long context is recalled less reliably than information at the beginning or end. This has led to architectural innovations like hierarchical attention and retrieval-augmented generation as complementary strategies for managing information that exceeds what even large context windows can handle effectively.

## Sources

- [[2026-02-10-url-transformer-architecture-explained]]
