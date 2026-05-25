# Organization Taxonomy Strategy

## Goal

Use one taxonomy for both:

- physical storage under `Books`
- BOOX library shelves

This keeps storage layout and library navigation aligned.

## Recommended Top-Level Categories

### AI

Use for research papers, model notes, agent architecture documents, and AI strategy material.

### Manuals

Use for technical references, hardware guides, platform manuals, and vendor documentation.

### Projects

Use for active plans, product work, proposals, project notes, and exported working documents.

### Personal

Use for personal records, worksheets, balances, and non-work reference material.

## Placement Rules

1. Move long-lived reading material out of `Download` and into `Books/<Category>`.
2. Leave unrelated app folders in `Download` alone.
3. Avoid physically moving note-source files unless there is a strong reason.
4. Shelf note-export PDFs in the matching project shelf even if their source files stay elsewhere.
5. Prefer a small number of stable top-level categories over many narrow buckets.

## Plan Design Guidance

Use a local sync contract to define:

- the storage root
- directories to scan for current file locations
- the desired physical target paths per category
- the desired shelf membership per category

The repo should only contain a generic example contract, not real document names.

## Why This Works Well

- the physical folders stay easy to browse on-device
- the BOOX library shelf tree matches the storage taxonomy
- the replay plan stays small and idempotent
