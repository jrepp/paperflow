---
title: "Paperflow: Core Reader Design"
project_id: paperflow
doc_uuid: 8e45628c-460a-42f2-84f5-70abbd3475ce
tags: [designs]
---

# Paperflow: Core Reader Design

Status: Option D selected by the user; identity-free MVP and subsequent release slices recorded. Interaction and accessibility validation still required.
Updated: 2026-11-15.

## Purpose

Paperflow helps people discover worthwhile material, understand it, investigate
its evidence, apply what they learn, and synthesize it for others. Its scope
includes research papers, articles, clippings, and discussions organized into
channels with explainable signal priority.

This document is the core reference for reader personas, user journeys, and
interaction design. It describes intended behavior, not completed features.
Visual themes support these journeys; they do not define them.

## Selected direction and delivery plan

**Option D — Everyday reader is selected.** Today / Saved / Research is the
navigation direction; the quiet reader is the primary surface. A, B and C inform
supporting flows. This is a selected product direction, not a claim that the
mocked interface has been implemented or accessibility-validated.

The MVP runs locally **without identity**. Provider-neutral browser identity
integration follows only after the MVP runs.
See [Reader release plan](../plans/reader-release.md) for the required UX slices,
acceptance gates, identity migration boundary and path to a published release.

## Product foundation

- Store ingested content, summaries, provenance, and reader state locally.
  Browsing saved material should work without live upstream access. Clearly
  indicate when only metadata or an excerpt is available.
- Use FastAPI for the service tier and Tailwind for the web interface.
- Support focused, enjoyable reading across desktop, tablet, and mobile.
- Provide pluggable visual themes so readers can choose their own atmosphere.
- Separate the reading experience from source management and ingestion
  operations, while making relevant freshness and availability visible.
- Manage ingestion stages with a watchdog and expose progress and performance
  through OpenTelemetry. Durable local job state remains the source of truth.

The initial visual sheet explored Reading Room, Field Guide, and Quiet Signal.
These are style explorations, not selected interaction designs.

## Current priority: a daily reading replacement

The primary product goal is now to replace everyday Hacker News/Reddit reading
sessions with a calmer, highly accessible local reading surface. Research is a
natural deepening of reading, not a prerequisite for opening the product. This
supersedes the earlier curator-first entry hypothesis; the existing curator
backend remains the foundation for the deeper investigation workspace.

### Journey priorities

| Priority | Journey and entry | Smallest useful flow | Completion signal |
| --- | --- | --- | --- |
| P0 | Catch up: open Today | Scan a finite batch → open a source or discussion → return to the same position | A useful read and a clear end of batch |
| P0 | Read comfortably: open any item | Choose available source/excerpt → adjust reading settings → read or open original → resume later | Reading without strain or lost context |
| P0 | Understand the conversation | Open Discussion independently of the linked article → inspect sampled comments and their context → return | Know what people are discussing and what is missing locally |
| P0 foundation | Save and resume | Save an item without choosing a bucket → reopen Saved → resume at the same passage | A dependable personal queue |
| P1 | Turn curiosity into research | Add to research → choose/create question → retain source and return to reading | Collected material without interrupting the reading session |
| P1 | Investigate and synthesize | Question workspace → claim → source passage → review interpretation → preview/export brief | Traceable output with unresolved questions visible |
| P2 | Develop understanding over time | Follow explicit source relationships → collect foundations → revisit | A useful path through a subject; no forced learning curriculum |
| Supporting | Keep sources healthy | Freshness notice → Sources → inspect/pause/retry → return | A trustworthy feed; operational detail stays out of reading |

P0 is a UX/release priority, not a claim that all required APIs already exist.
Accessibility is a completion requirement in every priority, not a later phase.

### Information architecture

Primary navigation: **Today · Saved · Research**. Search is available globally;
Sources and Reading settings are secondary destinations. Desktop may use a
restrained side rail; mobile uses labeled navigation and one primary content
column. Do not expose persona selection, pipeline stages, or evidence forms on
first entry. Restore the last reading surface when appropriate, while keeping
Today one action away.

- **Today:** bounded, paginated editorial rows. Title, source/community, available
  excerpt or labeled generated summary, age, and distinct Read source/Discussion
  actions. Community votes and comment counts are secondary observed signals,
  never implied evidence quality. Latest and channel filters have clear meaning;
  avoid an unexplained universal quality score. Do not reorder a visible batch
  while it is being read. Announce new arrivals and let the reader refresh.
- **Saved:** an intentionally simple personal reading queue, independent of
  research buckets. Unread/in-progress/finished are reader state, not claims
  about comprehension. Archiving or un-saving is reversible.
- **Reader:** a dedicated page by default, optional desktop preview for scanning.
  Source and Discussion are separate views; Research is a contextual entry.
  Clearly label metadata-only, excerpt, full text, and generated summary states.
  Never style a generated interpretation as an author quotation. Put Open
  original next to source availability. Keep research chrome out of the prose.
- **Discussion:** retain author/source attribution, parent context, timestamps
  and observed signal age where available. A sampled set is labeled sampled,
  with an Open original discussion action; do not imply it is the complete thread
  or that sampled comments are validated as high quality. Collapse branches with
  real buttons, explicit counts, and accessible expanded state. Avoid deeply
  indented text on narrow screens. Posting and voting remain upstream actions.
- **Research:** questions, then Overview/Sources/Claims/Brief within a question.
  On desktop, inspect evidence in a contextual rail; on mobile, use a dedicated
  screen and a stable Back to claim action. Link types remain distinct from
  supports/challenges/qualifies assessments. Reviewed does not mean proven.

### Accessible reading contract

Target WCAG 2.2 AA, with stronger reading defaults where practical. This is a
design target; static mocks do not demonstrate conformance. Use the
[W3C WCAG 2.2 reference](https://www.w3.org/TR/WCAG22/) for implementation checks.

- Use semantic HTML landmarks, heading levels, lists, links and buttons; provide
  skip links. Reading order in the DOM must match the visual order. Images need
  meaningful alternatives; generated mock screenshots are not the UI itself.
- Everything works with keyboard and screen reader. Focus is conspicuous,
  restored on returning from a reader or dialog, and never hidden by sticky
  controls. Shortcuts are discoverable, optional, and inactive while typing.
- Reflow at 320 CSS pixels and support browser zoom/text enlargement without
  losing actions or forcing horizontal scrolling for ordinary prose. Respect
  user text-spacing overrides without clipped or overlapping content.
- Meet AA contrast: 4.5:1 for normal text and 3:1 for large text; essential controls
  and state indicators need 3:1 contrast. Offer a higher-contrast reading option.
  Choose at least 44×44 CSS-pixel control targets as our product standard.
- Offer independent font family, type size, line spacing and reading-width
  settings, with an obvious reset. Starting hypotheses: 20px body, 1.65 line
  height and 60–70 characters per line; these are adjustable, not prescriptions.
  Support light, dark, and system/forced-color preferences. Do not imply that a
  particular font solves dyslexia. Do not use justified body text by default.
- Keep text selectable, copyable, searchable and compatible with browser or OS
  read-aloud tools. A dedicated audio/TTS system is a later option, not required
  for basic access. Respect reduced motion; no animated feed insertion,
  autoplay, flashing badges, or automatic scroll advancement.
- Use explicit labels for saved, unread, selected, stale, and needs-review states.
  Never rely on color alone or hide necessary actions behind hover. One clear
  primary action per task; expandable details rather than dense toolbars.

Reader accessibility preferences are independent of decorative themes. Themes
may alter typography and palette only within tested contrast, focus, spacing,
reflow and navigation contracts. Loading another theme must not erase explicit
reader preferences. A compact list-density preference must not shrink touch
controls or the actual reading text.

### Continuity and error states to prototype

Use a URL-backed item/view route plus saved list filters and position. Returning
from article, discussion, evidence, or original-source browsing must not reset
the feed. Preserve scroll anchors per revision; if source text changes, offer to
resume near the old passage instead of silently applying a stale offset.

Prototype no-new-items separately from stale sources. Show available local text
when extraction fails; expose retry in the appropriate context. Save feedback
must be announced without stealing focus. Stale edits keep the user's draft and
offer reload/compare. Local persistence is not cross-device synchronization:
responsive layouts come first; syncing separate installations is still undecided.

### Backend work required for the reading release

Already available: monitored source ingestion, observations, item/revision reads,
local SQLite storage, paginated research views, buckets, annotations, claim and
evidence review, relationships, optimistic edits, and retry-safe research writes.

Not yet implemented: reader-state APIs (saved/read/dismissed, feed cursor,
reading position), persisted accessibility preferences, a bounded reader feed
projection combining observations with user state, full linked-article
extraction, generated summaries, and complete threaded discussion storage.
A source revision is not a generated-summary model. Summary provenance and
input revisions must be explicit when that stage is added.

The current HN integration stores bounded comment samples. Reddit ingestion
still awaits approved Data API access. The UX can replace the reading habit,
but we cannot yet claim complete HN discussions or live Reddit coverage. Design
source availability honestly and keep Open original useful throughout.

### Mock concepts for review

| Mock | What it tests | Role in the recommended product |
| --- | --- | --- |
| A — Briefing to reader | Can a finite discovery batch lead to comfortable reading and a reliable return? | Discovery/preview option |
| B — Research workspace | Can collected sources and the current research question remain understandable? | Recommended research workspace, not default home |
| C — Claim investigation | Can the reader distinguish source passage, interpretation and review decision? | Focused secondary mode |
| D — Everyday reader | Can Today, a quiet reader and accessible settings replace habitual social-news browsing? | Selected primary direction |

Use the same neutral styling and illustrative Rust-safety content to compare
structure rather than palettes. Each concept includes desktop and mobile.
The images are composition mocks, not implemented controls or accessibility
proof. D takes priority; A/B/C are complementary flows, not four separate apps.

Mock files and text descriptions: [Reader concepts v1](reader-v1/concepts.md).

### Task-based evaluation

1. From Today, open a source and its discussion, then return to the same row.
2. Save an item without creating a research question; resume it in Saved.
3. Increase type size and spacing, switch contrast, and complete the same task
   at 320px width and with browser zoom. Confirm settings persist.
4. Complete reading, saving, and returning with keyboard only, then VoiceOver
   on macOS/iOS and NVDA on Windows during implementation validation.
5. Identify whether text is an excerpt, full source, summary, or comment sample.
6. Add a source to research and return immediately to reading; later inspect
   supporting and challenging evidence without confusing links with support.
7. Reach the end of a batch without being forced into more scrolling; choose
   whether to load another batch. Recover clearly when a source is stale.

Measure task completion, wrong turns, lost-position incidents, reading-setting
success and provenance comprehension. Do not optimize for session length or
feed consumption. Compare with the same tasks on the user's current reading
workflow; the mocks are hypotheses until tested.

## Personas

The primary audience comprises researchers, autodidacts, and explorers.
Curators are also an explicit design audience for research buckets and evidence
assembly. Practitioners remain a proposed additional lens. Validate workflow
details and relative priority through journey prototypes.

| Persona | Core question | Desired outcome | Design implications |
| --- | --- | --- | --- |
| Researcher | What evidence answers this question? | A defensible understanding of a problem | Precise search, citations, conflicting findings, provenance, and traceable summaries |
| Autodidact | How can I understand this subject? | Knowledge that develops and persists over time | Foundational context, explanations, learning sequences, revisiting, and progress continuity |
| Explorer | What is interesting and worth my attention? | Useful discoveries and unexpected connections | Approachable summaries, serendipity, low-friction branching, and orientation |
| Practitioner / builder | What can I apply to my work? | A method or insight that can be used and evaluated | Practical takeaways, code links, prerequisites, limitations, and applicability |
| Curator / synthesizer | How can I make these sources useful to others? | A coherent collection, brief, or publication | Collections, annotations, comparisons, provenance, and export to Paperflow publishing |

Personas are modes of intent, not fixed account types. One person may explore a
subject, learn its foundations, research a question, apply a method, and curate
the results. Do not require persona selection or build separate persona silos.

The ingestion operator is a separate operational role. That person needs source
configuration, freshness, backlog, failures, and recovery controls in a
management workspace. A reader may also perform this role.

## Journey model

The five outcome journeys are **discover → understand → investigate → apply →
synthesize**. This is a useful progression, not a mandatory sequence. Each
journey needs a focused entry, a clear next action, and a recognizable outcome.

| Journey | Entry and intent | Main interaction | Successful outcome |
| --- | --- | --- | --- |
| Discover | Briefing, channel, or topic: find worthwhile material | Scan compact summaries; inspect why an item surfaced; save or dismiss | A useful discovery or manageable reading queue |
| Understand | Saved item or continue reading: learn what it means | Read a summary, then source content; consult context; annotate and resume | Understanding with a preserved place and useful notes |
| Investigate | Question, search, citation, or claim: assess the evidence | Inspect provenance, compare sources, follow citations and substantive discussion | An evidence-backed answer with uncertainty and disagreement visible |
| Apply | Relevant method or practical takeaway: use what was learned | Inspect prerequisites, implementation links, examples, and limitations | An actionable next step grounded in the source |
| Synthesize | Collection, notes, or question: connect findings | Organize and compare items, add framing, and prepare a brief or publication | A coherent output with traceable sources |

## Focused product experiences

These four experiences organize navigation around user intent. They are not
separate products or a requirement for four equally prominent navigation tabs.

### Catch up

Entry question: **What deserves my attention?**

- Provide a bounded briefing since the last visit, with a clear caught-up state.
- Group or filter by topic, channel, and priority without requiring configuration
  before the first useful item appears.
- Show the takeaway, source, age, reading time, and a concise explanation of why
  each item surfaced.
- Make saving and dismissing quick, reversible actions.
- Preserve the briefing position when returning from an item.
- Distinguish no new material from stale or failed ingestion.

Success means finding worthwhile material and reaching a stopping point, not
maximizing scrolling or time in the application.

### Read

Entry question: **What do I want to understand now?**

- Enter from saved items, a briefing, a direct item link, or continue reading.
- Provide progressive detail: summary, extracted source content, then optional
  evidence and discussion. Keep generated summaries clearly distinct from
  author-written text and imported excerpts.
- Preserve reading position and the originating list or collection.
- Keep save, annotation, text settings, and open-original actions available
  without persistent dashboard chrome.
- Make unfinished extraction or summary generation explicit; allow reading
  available source material while other stages are pending.
- Support meaningful reading without requiring a network connection once the
  relevant content is stored locally.

Success means understanding an item and being able to resume it later without
reconstructing context.

### Explore

Entry question: **What connects to this idea?**

- Enter from search, a topic, an item, a citation, or a discussion.
- Offer related material, foundational context, and competing perspectives.
- Preserve the current reading queue and make the path back predictable.
- Consider a preview pane on desktop; use a dedicated detail screen with clear
  back navigation on mobile.
- Explain relationships rather than presenting an unexplained list of similar
  titles.

Success means discovering a useful connection without losing orientation.

### Manage sources

Entry question: **Is my information flow useful and healthy?**

- Add, tune, pause, and inspect channels and their source adapters.
- Explain how priority and filtering settings affect what readers see.
- Show freshness, ingestion stages, queued work, active jobs, and failures.
- Provide retry and recovery actions with understandable outcomes.
- Put detailed performance, logs, and trace links here, with only relevant
  freshness or availability notices in reading screens.

Success means a trustworthy flow of relevant material and diagnosable failures.

## Curate research and investigate claims

A research bucket is a durable workspace around a question, thesis, project, or
paper being written. It can contain articles, papers, discussions, clippings,
notes, and claims. An item may belong to multiple buckets without being copied.
Channels describe where material arrives; buckets describe why it matters to a
research effort. Bucket-specific notes, roles, and ordering belong to membership.

Two core curator journeys must be supported:

1. **Assemble research for a paper:** create a bucket, record the research
   question, collect material, annotate relevant passages, organize claims and
   evidence, and export a source-linked outline or brief for writing.
2. **Investigate an article:** save it to a bucket, select or propose its claims,
   resolve papers it cites, search for additional supporting and challenging
   papers, inspect evidence, and record reviewed conclusions and open questions.

Supporting or challenging evidence attaches to a specific claim and source
passage. A paper can support one claim while challenging another. Similarity,
popularity, and citation links alone do not establish evidentiary support.
Machine-proposed claims and relationships remain visibly unreviewed until a
curator accepts, edits, or rejects them. Finding no evidence is an unresolved
result, not proof that a claim is false.

The bucket view should progressively expose overview, collected material,
claims and evidence, and synthesis. Start with a simple save-to-bucket action;
do not require an evidence form to collect an article. Preserve the original
reader context when entering a bucket or following its references.

## Shared interaction principles

### Explainable discovery

Keep relevance, freshness, community attention, and assessed evidence quality
distinct. Votes and comment counts are observations, not proof of quality.
Quality assessments should point to the evidence or comments behind them.
Missing signals mean unknown, and signal age should be available when relevant.

An article found in multiple channels should retain its discovery paths without
creating redundant reader cards. A discussion about that article remains a
distinct linked item. Show source attribution and original links throughout.

### Continuity across journeys

Read/unread, saved, dismissed, annotations, and reading position are durable
reader state. Navigation should preserve list filters, position, and context.
Opening a reference must not silently replace the current reading queue.
Cross-device layouts are required; synchronization of reader state between
separate devices or installations remains an open product decision.

### Responsive behavior and accessibility

Use readable line lengths, adjustable text, generous touch targets, clear focus
states, keyboard navigation, and semantic structure. Mobile should have a
purposeful single-column experience rather than a compressed desktop sidebar.
Do not encode status or priority through color alone. Respect reduced motion.

### Pluggable visual styles

Themes may change color, typography, surfaces, borders, and decorative treatment.
They must preserve information hierarchy, navigation, action meaning, contrast,
focus visibility, and responsive behavior. Reading preferences such as text
size and line spacing should remain independently adjustable.

Evaluate usability in a neutral theme before choosing visual directions. A
different palette is not a different journey design.

## Next design comparison

Compare these three interaction patterns using identical sample content and a
consistent neutral style:

| Pattern | End-to-end flow | Question to validate |
| --- | --- | --- |
| Daily briefing | Finite digest → preview → focused reading → return | Can readers find value quickly and reach a satisfying stopping point? |
| Reading inbox | Triage list → save or dismiss → personal queue → resume | Can readers organize attention without creating a second inbox burden? |
| Topic paths | Topic overview → related items → contextual reading → return | Can readers build understanding and explore without getting lost? |

Show complete desktop and mobile journeys, including the return path, rather
than isolated screens. Include empty, caught-up, pending-content, offline, and
stale-source states. Use representative tasks for all five outcome journeys.

The selected reading-first direction is Today for entry, Saved for continuity,
and Research for deeper investigation. Topic exploration remains secondary.
See the prioritized journeys and mock concepts above; interaction details still
need task-based validation.

## Validation questions

- Can each primary persona identify a useful next action without setup?
- Can readers distinguish generated interpretation from source evidence?
- Can they explain why an item appeared and adjust an unhelpful channel?
- Can they move into references and discussions, then return without losing
  their place?
- Can autodidacts find necessary foundations and revisit unfinished material?
- Can practitioners identify both applicability and limitations?
- Can curators assemble an output while preserving source attribution?
- Can a curator group material into multiple research buckets without copying
  it, then use an article's claims to find supporting and challenging papers?
- Can a curator distinguish a retrieved candidate from reviewed evidence and
  see exactly which passage supports an assessment?
- Can readers complete the same core tasks on a narrow screen and with a
  keyboard?
- Does operational detail stay out of the reading experience until needed?

## Related local documents

- [Reader release plan and UX slices](../plans/reader-release.md)

- [Source ingestion and refresh plan](../plans/new-paper-sources.md)
- [Ingestion and research curation model](ingestion-curation-model.md)
- [Periodical roadmap](../plans/periodical-roadmap.md)
- [Project workflow and module boundaries](../../AGENTS.md)

This document complements those plans. Database schemas, API contracts,
ingestion scheduling, and telemetry implementation should be specified in
technical design documents while preserving the reader outcomes defined here.


## Content type, topic, and source controls

The feed labels each object's stored domain type: Paper, Article, Discussion,
Note, or Manuscript. Papers have a textual badge and subtle row marker, available
author/publication metadata, and a Read paper action. The reading view preserves
the type and labels the external action Open original paper. Abstract, excerpt,
and full-text availability remain separate from object type; a paper badge does
not imply that a local PDF exists.

A Content type control selects All types or one domain type. The Topic or source
picker separately identifies editorial topics and stable source feeds. Discussion
means the ingested discussion object, which may link to an external article.
These filters compose with title search and Saved; changing type resets pagination
but preserves the source/topic selection. Source and topic counts use the selected
type. The emerging-topic strip shows only themes with matching items. Empty
combinations explain how to change filters rather than silently broadening results.
Labels, pressed state, visible emphasis, and keyboard focus identify controls;
color alone never carries type or selection meaning.

## Sources heads-up monitor

Sources combines stored reading counts and ingestion state into one card per
source feed. The overview shows collection mode, running/queued feeds, attention
count, and paused feeds. Cards prioritize failures and running work, display
last successful collection and next collection mode, and keep Read feed, Fetch
now, and Pause/Resume close to the relevant source. Expand Run details for batch
size, duration, consecutive failures, timestamps, and trace ID; recent runs are a
separate compact history. Counts per source are not a deduplicated library total.

Status refreshes every five seconds while the page is visible. Background refresh
preserves scroll, focus, and open card details, and stops when leaving Sources.
Stage counts describe only the current stage, never an invented overall percent.
A failed status request leaves the last rendered data with an explicit warning.
OTLP configuration is distinguished from delivery verification. Manual collection
is labeled explicitly and does not present a past schedule as an imminent fetch.
