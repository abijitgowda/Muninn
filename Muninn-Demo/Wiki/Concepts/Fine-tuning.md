---
title: Fine-tuning
kind: concept
summary: The process of adapting a pre-trained language model to a specific task or
  domain by training it further on a targeted dataset.
tags:
- tech/ai
- machine-learning
- concept
sources:
- '[[2026-02-10-url-transformer-architecture-explained]]'
created: '2026-02-12'
updated: '2026-05-25'
confidence: high
lifecycle: stable
provenance:
  extracted: 0.7
  inferred: 0.3
  ambiguous: 0.0
schema: tech/ai
strength: 1.1
access_count: 1
last_accessed: '2026-05-25T20:40:36'
consolidation_count: 0
related_concepts:
- '[[Transformer Architecture]]'
- '[[Retrieval Augmented Generation]]'
- '[[LLM Wiki]]'
field: ai/training-methods
---


Fine-tuning is the process of taking a pre-trained [[Transformer Architecture]] model and continuing its training on a smaller, task-specific dataset to adapt it for particular applications. The technique exploits transfer learning: the pre-trained model has already learned general language patterns from billions of tokens, so fine-tuning only needs to adjust weights to reflect the narrower domain. This dramatically reduces both the data and compute required compared to training from scratch, making fine-tuning the standard approach for deploying models in production settings. [[OpenAI]] popularized this approach with GPT-3's fine-tuning API, and [[Anthropic]]'s Claude models also support fine-tuning for enterprise customers.

The landscape of fine-tuning has diversified significantly. Full fine-tuning updates all model parameters but is expensive and risks catastrophic forgetting. Parameter-efficient methods like LoRA (Low-Rank Adaptation) and QLoRA freeze most weights and train only small adapter layers, reducing memory requirements by 10-100x. Reinforcement learning from human feedback (RLHF), the technique used to align models like Claude and GPT-4, is itself a form of fine-tuning where the reward signal comes from human preference judgments rather than labeled data. [[Ilya Sutskever]] was among the first to demonstrate the power of large-scale fine-tuning during his time at [[OpenAI]].

Fine-tuning and [[Retrieval Augmented Generation]] are often presented as competing approaches to domain adaptation, but in practice they are complementary. Fine-tuning bakes knowledge into model weights for speed and fluency, while RAG grounds generation in retrievable evidence for accuracy and traceability. The [[LLM Wiki]] pattern represents a third path: rather than fine-tuning the model or retrieving from raw documents, it maintains a curated knowledge layer that the model reads as context, combining the persistence of fine-tuning with the updatability of RAG.

## Sources

- [[2026-02-10-url-transformer-architecture-explained]]
