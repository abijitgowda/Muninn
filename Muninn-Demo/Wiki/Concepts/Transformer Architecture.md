---
title: Transformer Architecture
kind: concept
summary: The dominant neural network architecture for sequence modeling, introduced
  in 2017, that relies entirely on self-attention mechanisms rather than recurrence.
tags:
- tech/ai
- deep-learning
- concept
sources:
- '[[2026-02-10-url-transformer-architecture-explained]]'
created: '2026-02-10'
updated: '2026-05-25'
confidence: high
lifecycle: stable
provenance:
  extracted: 0.9
  inferred: 0.1
  ambiguous: 0.0
schema: tech/ai
strength: 1.4
access_count: 3
last_accessed: '2026-05-25T20:40:36'
consolidation_count: 0
related_concepts:
- '[[Attention Mechanism]]'
- '[[Context Window]]'
- '[[Fine-tuning]]'
field: ai/architectures
---




The transformer is a neural network architecture introduced by Vaswani et al. in the 2017 paper "Attention Is All You Need." It replaced recurrent neural networks (RNNs) and LSTMs as the dominant architecture for sequence tasks by demonstrating that [[Attention Mechanism]] alone, without any recurrence or convolution, could achieve superior performance on machine translation and other language benchmarks. The key innovation was self-attention: every token in a sequence can attend to every other token simultaneously, enabling massive parallelism during training that RNNs could not match. This parallelism is what made the transformer a natural fit for [[GPU Computing]], since attention computations map efficiently onto the matrix-multiplication hardware that GPUs excel at.

The architecture consists of an encoder and a decoder, each built from stacked layers of multi-head self-attention and feed-forward networks. In practice, most modern large language models use decoder-only variants (GPT, Claude, LLaMA) or encoder-only variants (BERT). [[Geoffrey Hinton]]'s earlier work on distributed representations and backpropagation laid the theoretical groundwork, but it was the transformer's scalability that enabled the leap from millions to trillions of parameters. Organizations like [[OpenAI]], [[Google DeepMind]], and [[Anthropic]] have all built their flagship models on transformer variants, each pushing the boundaries of model size, training data, and [[Context Window]] length.

The transformer's influence extends well beyond language. Vision transformers (ViT) have challenged convolutional networks in computer vision, and transformer-based architectures now dominate protein structure prediction (AlphaFold), music generation, and code synthesis. [[Andrej Karpathy]] has described the transformer as "the most important neural network architecture ever invented," and the ongoing research into efficient attention, mixture-of-experts routing, and longer context windows continues to expand its capabilities.

## Sources

- [[2026-02-10-url-transformer-architecture-explained]]
