---
title: Attention Mechanism
kind: concept
summary: A neural network component that allows models to dynamically weight the relevance
  of different parts of an input sequence when producing each output element.
tags:
- tech/ai
- deep-learning
- concept
sources:
- '[[2026-02-10-url-transformer-architecture-explained]]'
created: '2026-02-10'
updated: '2026-05-26'
confidence: high
lifecycle: stable
provenance:
  extracted: 0.9
  inferred: 0.1
  ambiguous: 0.0
schema: tech/ai
strength: 1.2
access_count: 5
last_accessed: '2026-05-26T01:03:23'
consolidation_count: 0
related_concepts:
- '[[Transformer Architecture]]'
- '[[Context Window]]'
field: ai/architectures
---






The attention mechanism is a foundational component of modern deep learning that enables a neural network to selectively focus on the most relevant parts of its input when computing each output element. First introduced for neural machine translation by Bahdanau et al. in 2014, attention solved the "information bottleneck" problem of encoder-decoder architectures by allowing the decoder to look back at all encoder hidden states rather than relying on a single fixed-length context vector. The breakthrough that led to the [[Transformer Architecture]] was the realization that attention could serve as the primary computation mechanism rather than merely an add-on to recurrent networks.

In the transformer, attention takes the form of scaled dot-product attention over queries, keys, and values. Multi-head attention extends this by running several attention functions in parallel, each learning to attend to different types of relationships in the data. This is what enables large language models built by [[OpenAI]], [[Anthropic]], and [[Google DeepMind]] to capture long-range dependencies across thousands of tokens. The computational cost of standard attention scales quadratically with sequence length, which is the fundamental constraint limiting [[Context Window]] sizes and has driven significant research into efficient attention variants like FlashAttention, sparse attention, and linear attention approximations.

The concept of attention has deep connections to [[Spreading Activation]] in cognitive science: both describe mechanisms by which relevant information is selectively amplified while irrelevant information is suppressed. [[Geoffrey Hinton]] has noted the parallels between artificial attention and biological attention, though he cautions against drawing the analogy too literally. In practice, understanding attention patterns has become a key tool for model interpretability, with attention visualization helping researchers understand what a model "looks at" when making predictions.

## Sources

- [[2026-02-10-url-transformer-architecture-explained]]
