# Research Radar Periodical Roadmap

The periodical should follow a learning journey, not a feed. Each issue should
feel durable enough to reread later: a guided walk through a problem, its
characters, its machinery, and the overlooked research corners that quietly make
the field work.

## Issue 1: The Research Radar House Style

Purpose: establish the typography, voice, and editorial contract for the series.

Through line:

Research is not only a leaderboard of results. It is a cast of ideas, tools,
benchmarks, failed assumptions, small methods, and unsung heroes. The first issue
should show how the periodical will tell those stories: clear typography,
confident pacing, careful exposition, and a willingness to walk into dusty
corners when those corners explain why modern systems work.

Planned sections:

- Typography as editorial structure
- The voice: precise, narrative, skeptical, and generous
- Characters: papers, datasets, benchmarks, methods, labs, and maintainers
- Unsung heroes: glue work, evaluation, preprocessing, data curation, and small
  algorithmic choices
- How future issues will move from one focal idea to its supporting cast

Output goal:

A short, polished house-style issue that becomes the template for later issues.
It should define page rhythm, headings, sidebars, pull quotes, bibliography
style, and how much technical depth belongs on a page.

## Issue 2: Transformer Architecture

Purpose: follow the path into attention, sequence modeling, scaling constraints,
and the architectural decisions that made transformers reusable.

Candidate story:

Start with the familiar headline, then move past it: attention as routing,
positional structure, residual streams, normalization, KV caches, and the system
choices that turn a model block into an ecosystem.

## Issue 3: Embeddings

Purpose: explain embeddings as the connective tissue between language, retrieval,
classification, recommendation, memory, and semantic interfaces.

Candidate story:

Embeddings are often treated as a utility artifact, but they quietly decide what
can be compared, retrieved, clustered, remembered, and composed.

## Issue 4: Optimizations for Small Language Models

Purpose: explore the practical craft of making smaller models useful: data
quality, distillation, quantization, adapters, decoding, caching, architecture
variants, and deployment constraints.

Candidate story:

Small language models are not merely compressed large models. They force a
different design discipline around what is worth keeping, what can be recovered
through tooling, and what has to be moved into retrieval, routing, or product
architecture.

## Editorial Rules

- Each issue gets one through line.
- Papers are characters in the story, not isolated abstracts.
- Include supporting papers, citations, and concepts only when they help the
  through line.
- Prefer durable explanations over novelty summaries.
- Make room for neglected infrastructure and quiet technical decisions.
- The reader should leave with a map, not just a list.
