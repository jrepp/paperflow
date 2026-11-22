"""SQLite story operations; all writes participate in the caller's transaction."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, UUID, uuid5

from paperflow_domain import ContentRevision, Item
from paperflow_story_models import Story, StoryAssessment, StoryCandidate, StorySource
from paperflow_storage import ConflictError, ReferenceError


def _tokens(text):
    return {word for word in re.findall(r'\w+', text.lower()) if len(word) > 3}


class StoryRepositoryMixin:
    def get_story(self, story_id):
        row = self._conn.execute('SELECT payload FROM research_stories WHERE id=?', (str(story_id),)).fetchone()
        return Story.model_validate_json(row[0]) if row else None

    def story_schema_available(self):
        return bool(self._conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='research_stories'").fetchone())

    def story_version_assessment_ids(self, story_id, version):
        row = self._conn.execute('SELECT assessment_ids FROM research_story_versions WHERE story_id=? AND version=?',
                                 (str(story_id), version)).fetchone()
        return tuple(UUID(value) for value in json.loads(row[0])) if row else None

    def story_associated_items(self):
        return {UUID(row[0]) for row in self._conn.execute('SELECT DISTINCT item_id FROM research_story_sources')}

    def story_sources(self, story_id):
        return [StorySource.model_validate_json(r[0]) for r in self._conn.execute(
            'SELECT payload FROM research_story_sources WHERE story_id=? ORDER BY rowid', (str(story_id),))]

    def story_assessments(self, revision_id=None, story_id=None):
        clauses, args = [], []
        if revision_id is not None:
            clauses.append('revision_id=?'); args.append(str(revision_id))
        if story_id is not None:
            clauses.append('story_id=?'); args.append(str(story_id))
        where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
        return [StoryAssessment.model_validate_json(r[0]) for r in self._conn.execute(
            'SELECT payload FROM research_story_assessments' + where + ' ORDER BY rowid', args)]

    def story_pending(self, story_id):
        return [StoryAssessment.model_validate_json(r[0]) for r in self._conn.execute(
            'SELECT a.payload FROM research_story_pending p JOIN research_story_assessments a ON a.id=p.assessment_id WHERE p.story_id=? ORDER BY p.rowid', (str(story_id),))]

    def published_stories(self):
        return [Story.model_validate_json(r[0]) for r in self._conn.execute(
            "SELECT payload FROM research_stories WHERE json_extract(payload,'$.account') != '' ORDER BY julianday(json_extract(payload,'$.material_at')) DESC, id")]

    def _story_index(self):
        # Build the small local index in bounded queries, avoiding one SQL round
        # trip per source when a corpus contains many syndicated copies.
        stories = {str(s.id): s for s in (Story.model_validate_json(r[0]) for r in
                   self._conn.execute('SELECT payload FROM research_stories ORDER BY id'))}
        sources = {key: [] for key in stories}
        documents = {key: [story.title, story.account] for key, story in stories.items()}
        items = {}
        for payload, item_payload, revision_text in self._conn.execute(
            "SELECT s.payload,i.payload,substr(json_extract(r.payload,'$.markdown'),1,4000) FROM research_story_sources s "
            'JOIN research_items i ON i.id=s.item_id '
            "JOIN research_revisions r ON r.id=json_extract(s.payload,'$.revision_id') ORDER BY s.rowid"):
            source = StorySource.model_validate_json(payload)
            item = Item.model_validate_json(item_payload)
            key = str(source.story_id)
            sources[key].append(source)
            documents[key].extend((item.title, revision_text))
            items[str(item.id)] = item
        identifiers = {}
        for item_id, namespace, value in self._conn.execute('SELECT item_id,namespace,value FROM research_identifiers'):
            identifiers.setdefault(item_id, set()).add((namespace.lower(), value.lower()))
        return stories, sources, documents, items, identifiers

    def story_documents(self):
        stories, _, documents, _, _ = self._story_index()
        return [(key, '\n'.join(parts), stories[key].material_at) for key, parts in documents.items()]

    def story_candidates(self, revision_id, limit=5, semantic_ids=()):
        if not 1 <= limit <= 100:
            raise ValueError('invalid candidate limit')
        revision = self.get(ContentRevision, revision_id)
        if revision is None: raise ReferenceError('missing ContentRevision')
        item = self.get(Item, revision.item_id)
        query = _tokens(item.title + ' ' + revision.markdown[:4000])
        stories, all_sources, documents, items, all_identifiers = self._story_index()
        identifiers = all_identifiers.get(str(item.id), set())
        semantic = {str(value): rank for rank, value in enumerate(semantic_ids)}
        candidates = []
        for story_id, story in stories.items():
            sources = all_sources[story_id]
            matches = []
            exact = 0
            for source in sources:
                other = items[str(source.item_id)]
                if source.item_id == item.id: matches.append('item'); exact = 3
                if item.canonical_url and other.canonical_url and str(item.canonical_url) == str(other.canonical_url):
                    matches.append('canonical_url'); exact = max(exact, 3)
                for namespace, value in sorted(identifiers & all_identifiers.get(str(source.item_id), set())):
                    matches.append(f'{namespace}:{value}'); exact = max(exact, 3)
            words = _tokens(' '.join(documents[story_id]))
            lexical = len(query & words) / max(1, len(query | words))
            if lexical: matches.append('lexical')
            incoming_title = _tokens(item.title)
            candidate_titles = [_tokens(story.title)] + [_tokens(items[str(source.item_id)].title) for source in sources]
            title_overlap = max((len(incoming_title & title) / max(1, len(incoming_title | title))
                                 for title in candidate_titles), default=0)
            title_score = 0
            if len(incoming_title) >= 2 and title_overlap >= 0.7:
                matches.append('title')
                title_score = 1.5 * title_overlap
            vector = 0
            if story_id in semantic:
                matches.append('semantic'); vector = 0.5 / (1 + semantic[story_id])
            score = exact + title_score + lexical + vector
            if score:
                candidates.append(StoryCandidate(story=story, sources=tuple(sources), matched=tuple(dict.fromkeys(matches)), score=score))
        candidates.sort(key=lambda x: (-x.score, -x.story.material_at.timestamp(), str(x.story.id)))
        return candidates[:limit]

    def _public_study_source(self, revision_id):
        return self.is_public_revision(revision_id) and self._conn.execute(
            'SELECT 1 FROM research_revisions r JOIN research_source_observations o ON o.item_id=r.item_id '
            'WHERE r.id=? LIMIT 1', (str(revision_id),)).fetchone() is not None

    def study_questions(self, source_text, limit=8):
        words = _tokens(source_text)
        rows = self._conn.execute('SELECT question, seed_revision_id FROM research_study_threads ORDER BY rowid DESC')
        ranked = sorted(rows, key=lambda row: len(words & _tokens(row[0])), reverse=True)
        questions = []
        for question, revision_id in ranked:
            if self._public_study_source(revision_id):
                questions.append(question)
                if len(questions) == limit:
                    break
        return questions

    def published_study_threads(self, selected_ids):
        if not selected_ids or not self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='research_study_threads'").fetchone():
            return []
        # Include the reading history when at least one public reading is in this edition.
        rows = self._conn.execute("""
            SELECT t.id, t.question, t.seed_revision_id, m.revision_id, i.payload, m.contribution, m.passage,
                   (SELECT json_extract(o.payload, '$.entry.url') FROM research_source_observations o
                    WHERE o.item_id=i.id ORDER BY o.rowid DESC LIMIT 1)
            FROM research_study_threads t JOIN research_study_readings m ON m.thread_id=t.id
            JOIN research_revisions r ON r.id=m.revision_id JOIN research_items i ON i.id=r.item_id
            WHERE t.id IN (
                SELECT member.thread_id FROM research_study_readings member
                JOIN research_revisions revision ON revision.id=member.revision_id
                WHERE revision.item_id IN (SELECT value FROM json_each(?)))
            ORDER BY m.rowid
        """, (json.dumps([str(value) for value in selected_ids]),))
        threads = {}
        active = set()
        for thread_id, question, seed, revision_id, payload, contribution, passage, source_url in rows:
            if thread_id not in threads:
                threads[thread_id] = ({'id': thread_id, 'question': question, 'readings': []}
                                      if self._public_study_source(seed) else None)
            thread = threads[thread_id]
            if thread is None or not self._public_study_source(revision_id):
                continue
            item = Item.model_validate_json(payload)
            thread['readings'].append((item.title, str(item.canonical_url or source_url), contribution, passage))
            if item.id in selected_ids:
                active.add(thread_id)
        return [thread for key, thread in threads.items() if key in active]

    def apply_story_assessment(self, assessment, story=None, *, study_reading=None):
        assessment = StoryAssessment.model_validate(assessment.model_dump())
        revision = self.get(ContentRevision, assessment.revision_id)
        if revision is None: raise ReferenceError('missing ContentRevision')
        if assessment.evidence_basis != revision.evidence_basis: raise ValueError('assessment evidence basis must match revision')
        # A retry of identical decision inputs returns the committed decision, including
        # the generated story ID; an explicit predecessor requests a new review.
        for previous in self.story_assessments(revision_id=assessment.revision_id):
            if (previous.creator, previous.processor_version, previous.rule_set_version, previous.prior_assessment_id, previous.story_version) == (assessment.creator, assessment.processor_version, assessment.rule_set_version, assessment.prior_assessment_id, assessment.story_version) and (assessment.story_id is None or previous.story_id == assessment.story_id):
                return previous
        if assessment.prior_assessment_id is not None:
            previous = next((a for a in self.story_assessments(revision_id=assessment.revision_id) if a.id == assessment.prior_assessment_id), None)
            if previous is None: raise ReferenceError('missing prior assessment for revision')
        if assessment.outcome == 'new_story':
            if story is None or assessment.story_id not in (None, story.id): raise ValueError('new story required')
            if story.version != 1 or story.account: raise ValueError('new story must start uncurated')
            self._conn.execute('INSERT INTO research_stories VALUES (?,?,?)', (str(story.id), story.version, story.model_dump_json()))
            assessment = assessment.model_copy(update={'story_id': story.id})
        elif assessment.story_id is not None:
            current = self.get_story(assessment.story_id)
            if current is None: raise ReferenceError('missing story')
            if assessment.story_version != current.version: raise ConflictError('story changed; reassess against current version')
        self._conn.execute('INSERT INTO research_story_assessments VALUES (?,?,?,?)',
            (str(assessment.id), str(assessment.revision_id), str(assessment.story_id) if assessment.story_id else None, assessment.model_dump_json()))
        if study_reading is not None:
            question, contribution, passage = study_reading
            question = ' '.join(question.split())
            if not question or not contribution.strip() or not passage or passage not in revision.markdown:
                raise ValueError('Study reading requires a question, a contribution and an exact source passage')
            if not self._public_study_source(revision.id):
                raise ValueError('Study reading requires an observed public source revision')
            thread_id = str(uuid5(NAMESPACE_URL, 'paperflow:study:' + question.casefold()))
            self._conn.execute('INSERT INTO research_study_threads VALUES (?,?,?) ON CONFLICT(id) DO NOTHING',
                               (thread_id, question, str(revision.id)))
            self._conn.execute('INSERT INTO research_study_readings VALUES (?,?,?,?,?) '
                               'ON CONFLICT(thread_id,revision_id) DO NOTHING',
                               (thread_id, str(revision.id), str(assessment.id), contribution.strip(), passage))
        if assessment.outcome != 'unresolved':
            old = next((s for s in self.story_sources(assessment.story_id) if s.item_id == revision.item_id), None)
            source = StorySource(story_id=assessment.story_id, item_id=revision.item_id, revision_id=revision.id,
                assessment_id=assessment.id, first_attached_at=old.first_attached_at if old else assessment.created_at)
            self._conn.execute('INSERT INTO research_story_sources VALUES (?,?,?) ON CONFLICT(story_id,item_id) DO UPDATE SET payload=excluded.payload',
                (str(source.story_id), str(source.item_id), source.model_dump_json()))
            if assessment.outcome in ('new_story', 'material_contribution'):
                self._conn.execute('INSERT INTO research_story_pending VALUES (?,?)', (str(assessment.id), str(assessment.story_id)))
        return assessment

    def publish_story(self, story_id, expected_version, title, account, assessment_ids, source_assessment_ids=None):
        current = self.get_story(story_id)
        if current is None: raise ReferenceError('missing story')
        if current.version != expected_version: raise ConflictError('story changed; reload before publication')
        pending = {a.id for a in self.story_pending(story_id)}
        covered = {UUID(str(value)) for value in assessment_ids}
        if not pending or pending != covered: raise ConflictError('pending contributions changed; curate all outstanding assessments')
        cited = ({s.assessment_id for s in self.story_sources(story_id)} if source_assessment_ids is None
                 else {UUID(str(value)) for value in source_assessment_ids}) | covered
        known = {a.id for a in self.story_assessments(story_id=story_id)}
        if not cited <= known: raise ReferenceError('cited assessment does not belong to story')
        if not account.strip(): raise ValueError('published account cannot be empty')
        updated = current.model_copy(update={'version': current.version + 1, 'title': title, 'account': account, 'material_at': datetime.now(UTC)})
        updated = Story.model_validate(updated.model_dump())
        self._conn.execute('UPDATE research_stories SET version=?,payload=? WHERE id=? AND version=?', (updated.version, updated.model_dump_json(), str(story_id), expected_version))
        self._conn.execute('INSERT INTO research_story_versions VALUES (?,?,?,?)', (str(story_id), updated.version, updated.model_dump_json(), json.dumps(sorted(str(x) for x in cited))))
        self._conn.execute('DELETE FROM research_story_pending WHERE story_id=?', (str(story_id),))
        return updated
