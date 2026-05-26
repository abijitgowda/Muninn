---
title: GPU Computing
kind: concept
summary: The use of graphics processing units for general-purpose parallel computation,
  which has become the dominant hardware paradigm for training and running AI models.
tags:
- tech/hardware
- tech/ai
- concept
sources:
- '[[2026-02-10-url-transformer-architecture-explained]]'
created: '2026-02-10'
updated: '2026-05-25'
confidence: high
lifecycle: stable
provenance:
  extracted: 0.8
  inferred: 0.2
  ambiguous: 0.0
schema: tech/hardware
strength: 1.1
access_count: 1
last_accessed: '2026-05-25T20:40:36'
consolidation_count: 0
related_concepts:
- '[[Transformer Architecture]]'
- '[[Attention Mechanism]]'
field: computing/hardware
---



GPU computing refers to the use of graphics processing units (GPUs) for general-purpose computation beyond their original role in rendering graphics. The paradigm shift began in the mid-2000s when researchers recognized that GPUs, with their thousands of small cores optimized for parallel floating-point operations, were dramatically more efficient than CPUs for matrix-heavy workloads. [[NVIDIA]]'s introduction of CUDA (Compute Unified Device Architecture) in 2006 provided the programming framework that made GPU computing accessible to non-graphics developers and established the software ecosystem that would later become indispensable for AI. [[Jensen Huang]]'s strategic decision to invest in CUDA created a moat that competitors have struggled to replicate for nearly two decades.

The rise of deep learning and the [[Transformer Architecture]] made GPUs the essential hardware for AI research and deployment. Training a large language model involves billions of matrix multiplications — the [[Attention Mechanism]] at the heart of transformers is fundamentally a series of matrix operations that map perfectly onto GPU architecture. [[NVDA]] (NVIDIA) has captured the vast majority of the AI training hardware market with its A100, H100, and Blackwell GPU families. Competitors include [[AMD]] with its Instinct MI series, [[Google]] with custom TPUs, and emerging startups building specialized AI accelerators. [[TSMC]] manufactures the advanced chips for both NVIDIA and AMD, making it the critical bottleneck in the global AI hardware supply chain.

The economics of GPU computing have become a defining constraint of the AI industry. Training a frontier model like GPT-4 or Claude requires tens of thousands of GPUs running for months, representing investments of $100 million or more in compute alone. This has created a capital-intensive barrier to entry that favors well-funded organizations like [[OpenAI]], [[Anthropic]], and [[Google DeepMind]]. [[Lisa Su]] at AMD has positioned the company as the primary alternative to NVIDIA's dominance, while the broader industry debates whether the current GPU shortage is a temporary bottleneck or a structural feature of the AI era.

## Sources

- [[2026-02-10-url-transformer-architecture-explained]]
