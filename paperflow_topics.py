"""Rolling editorial topic curation with a replaceable, local proposal strategy."""
from collections import Counter
from datetime import timedelta
from itertools import pairwise
import hashlib
import re
from typing import Protocol
from uuid import UUID, NAMESPACE_URL, uuid5

from paperflow_domain import now
from paperflow_curation import NotFoundError
from paperflow_storage import ConflictError, ResearchStorage
from paperflow_topic_models import (
    Topic, TopicAssignment, TopicCandidate, TopicEdition, TopicProposal, TopicRun,
    TopicSettings, TopicSummary, TopicEdit, CreateTopic, EditTopic, relevance,
)

STOP = set('a about an and are as at be been being by can could did do does for from had has have how i if in into is it its just may more most new not of on or our out over paper papers research says show some study than that the their them there these they this those to under up use used using was we were what when where which while who why will with would you your'.split())


# Generic headline words cannot carry a topic alone; keep them in phrases.
LOW_SIGNAL_SINGLETONS = {'down', 'story', 'stories', 'today', 'update', 'updates', 'news', 'latest', 'things', 'something'}

def tokens(value):
    return [word for word in re.findall(r"[a-z][a-z0-9+#-]{2,}", value.lower()) if word not in STOP]


class TopicCurator(Protocol):
    version: str
    def propose(self, candidates: list[TopicCandidate], settings: TopicSettings) -> list[TopicProposal]: ...


class KeywordTopicCurator:
    """Explainable baseline, not an LLM or a claim of semantic understanding.

    Prefer shared title phrases. Greedy non-overlapping assignments keep the
    default consumption bins distinct; editor-created topics may overlap them.
    """
    version = 'title-phrases-v2'

    def propose(self, candidates, settings):
        features = {}
        labels = {}
        for candidate in candidates:
            words=tokens(candidate.title)
            phrases={word for word in words if word not in LOW_SIGNAL_SINGLETONS}
            phrases.update(' '.join(pair) for pair in pairwise(words))
            features[candidate.item_id]=phrases
            for phrase in phrases:
                labels.setdefault(phrase,phrase)
        counts=Counter(term for values in features.values() for term in values)
        ranked=sorted((term for term,count in counts.items() if count>=settings.min_items),
                      key=lambda term:(-counts[term]*(2 if ' ' in term else 1),-len(term.split()),term))
        unused={c.item_id for c in candidates};proposals=[]
        for term in ranked:
            members=[c for c in candidates if c.item_id in unused and term in features[c.item_id]]
            if len(members)<settings.min_items:
                continue
            key='phrase:'+hashlib.sha256(term.encode()).hexdigest()[:24]
            proposals.append(TopicProposal(key=key,title=term.title(),terms=(term,),assignments=tuple(
                TopicAssignment(item_id=c.item_id,observation_id=c.observation_id,first_seen_at=c.first_seen_at,
                                reason=f'Shared title phrase: {term}') for c in members)))
            unused.difference_update(c.item_id for c in members)
            if len(proposals)>=settings.max_topics:
                break
        return proposals


class TopicService:
    def __init__(self, storage: ResearchStorage, settings: TopicSettings | None = None, curator: TopicCurator | None = None):
        self.storage=storage;self.settings=settings or TopicSettings();self.curator=curator or KeywordTopicCurator()

    def curate(self, *, at=None, run_id=None):
        end=at or now();start=end-timedelta(hours=self.settings.window_hours)
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError('Curation time must include a timezone')
        run_id=run_id or uuid5(NAMESPACE_URL,'paperflow-topics:'+end.isoformat()+self.curator.version+self.settings.model_dump_json())
        with self.storage.transaction(write=True) as repo:
            existing=repo.topic_run(run_id)
            if existing:
                return existing
            recent=repo.topic_runs(1)
            if recent and recent[0].window_end>end:
                raise ConflictError('A newer curation run already exists')
            candidates=repo.topic_candidates(start,end,self.settings.max_candidates)
            proposals=self.curator.propose(candidates,self.settings)
            if len(proposals)>self.settings.max_topics or len({p.key for p in proposals})!=len(proposals):
                raise ValueError('Curator returned too many or duplicate topics')
            by_item={c.item_id:c for c in candidates}
            for proposal in proposals:
                if len({a.item_id for a in proposal.assignments})!=len(proposal.assignments):
                    raise ValueError('Curator repeated an item within a topic')
                for assignment in proposal.assignments:
                    candidate=by_item.get(assignment.item_id)
                    if not candidate or candidate.observation_id!=assignment.observation_id or candidate.first_seen_at!=assignment.first_seen_at:
                        raise ValueError('Curator assignment is outside the input window or has incorrect provenance')
            all_topics=repo.topics();by_key={t.key:t for t in all_topics}
            # Persistent thematic channels use editor-defined terms, not changing
            # machine labels. They remain discoverable even when temporarily empty.
            for topic in all_topics:
                if topic.persistence=='persistent':
                    proposals=[p for p in proposals if p.key!=topic.key]
                    matches=[]
                    for candidate in candidates:
                        text=' '.join(tokens(candidate.title+' '+candidate.excerpt))
                        matched=[term for term in topic.terms if ' '.join(tokens(term)) and f" {' '.join(tokens(term))} " in f' {text} ']
                        if matched:
                            matches.append(TopicAssignment(item_id=candidate.item_id,observation_id=candidate.observation_id,
                                first_seen_at=candidate.first_seen_at,reason='Editorial terms: '+', '.join(matched)))
                    proposals.append(TopicProposal(key=topic.key,title=topic.title,terms=topic.terms,assignments=tuple(matches)))
            # Explicit editorial inclusion/exclusion wins over automatic matching.
            for topic in all_topics:
                if not topic.included_item_ids and not topic.excluded_item_ids:
                    continue
                existing = next((p for p in proposals if p.key == topic.key), None)
                members = {a.item_id: a for a in existing.assignments} if existing else {}
                for item_id in topic.included_item_ids:
                    candidate = by_item.get(item_id)
                    if candidate:
                        members[item_id] = TopicAssignment(item_id=item_id, observation_id=candidate.observation_id,
                            first_seen_at=candidate.first_seen_at, reason='Editorial inclusion')
                for item_id in topic.excluded_item_ids:
                    members.pop(item_id, None)
                proposals = [p for p in proposals if p.key != topic.key]
                proposals.append(TopicProposal(key=topic.key, title=existing.title if existing else topic.title,
                    terms=topic.terms, assignments=tuple(members.values())))
            editions=[];assigned=set();touched=set()
            for proposal in proposals:
                previous=by_key.get(proposal.key)
                topic=previous or Topic(key=proposal.key,title=proposal.title,terms=proposal.terms,
                    editorial_status='published' if self.settings.auto_publish else 'draft',created_at=end)
                newest=max((a.first_seen_at for a in proposal.assignments),default=topic.last_seen_at)
                updates=dict(current_run_id=run_id,last_seen_at=newest,
                             expires_at=newest+timedelta(hours=self.settings.window_hours) if newest else None,
                             version=topic.version+1 if previous else 1)
                if not topic.editorial_title:
                    updates['title']=proposal.title
                topic=topic.model_copy(update=updates)
                repo.put_topic(topic,expected_version=previous.version if previous else None)
                editions.append(TopicEdition(topic_id=topic.id,title=topic.title,assignments=proposal.assignments))
                touched.add(topic.id)
                if topic.editorial_status=='published':
                    assigned.update(a.item_id for a in proposal.assignments)
            # Unmatched topics have an empty current edition, preserving prior
            # editions and editor decisions while making the live feed accurate.
            for topic in all_topics:
                if topic.id not in touched:
                    repo.put_topic(topic.model_copy(update={'current_run_id':run_id,'version':topic.version+1}),topic.version)
                    editions.append(TopicEdition(topic_id=topic.id,title=topic.title,assignments=()))
            run=TopicRun(id=run_id,created_at=end,window_start=start,window_end=end,processor_version=self.curator.version,
                         input_count=len(candidates),unassigned_count=len(candidates)-len(assigned),editions=tuple(editions))
            repo.save_topic_run(run)
            return run

    def summaries(self, *, at=None, editorial=False, saved=False, query='', kind=None):
        at=at or now();result=[]
        with self.storage.transaction() as repo:
            for topic in repo.topics():
                members=self._current_members(repo,topic,at)
                lifecycle='active' if members else 'quiet' if topic.persistence=='persistent' else 'expired'
                if not editorial and (topic.editorial_status!='published' or lifecycle=='expired'):
                    continue
                visible=[]
                for member in members:
                    from paperflow_domain import Item
                    item=repo.get(Item,member.item_id)
                    state=repo.reader_state(member.item_id)
                    if (kind and item.kind!=kind) or query.lower() not in item.title.lower() or (not state.saved if saved else state.status=='dismissed'):
                        continue
                    visible.append(state)
                newest=max((m.first_seen_at for m in members),default=None)
                result.append(TopicSummary(topic=topic,item_count=len(visible),unread_count=sum(s.status=='unread' for s in visible),
                    saved_count=sum(s.saved for s in visible),relevance=relevance(topic.sensitivity,newest,at),lifecycle=lifecycle))
        return sorted(result,key=lambda row:(-row.relevance,-row.item_count,row.topic.title))

    def _current_members(self,repo,topic,at):
        if not topic.current_run_id:
            return []
        start=at-timedelta(hours=self.settings.window_hours)
        return [m for m in repo.topic_members(topic.id,topic.current_run_id) if start<=m.first_seen_at<at]

    def resolve(self, selector):
        with self.storage.transaction() as repo:
            topics = repo.topics()
            exact = [t for t in topics if t.title.casefold() == selector.casefold()]
            matches = exact or [t for t in topics if len(selector)>=4 and str(t.id).startswith(selector.lower())]
            if not matches:
                raise NotFoundError('Topic not found; use list --editorial')
            if len(matches)!=1:
                raise ConflictError('Ambiguous topic; use an exact UUID from list --editorial')
            topic = matches[0]
            return {'id': str(topic.id), 'title': topic.title, 'version': topic.version}

    def config(self, topic_id):
        with self.storage.transaction() as repo:
            topic = repo.get_topic(topic_id)
            if not topic:
                raise NotFoundError('Topic not found')
            return topic

    def detail(self,topic_id):
        with self.storage.transaction() as repo:
            topic=repo.get_topic(topic_id)
            if not topic:
                raise NotFoundError('Topic not found')
            return {'topic':topic.model_dump(mode='json'),'assignments':[m.model_dump(mode='json') for m in self._current_members(repo,topic,now())],
                    'edits':[e.model_dump(mode='json') for e in repo.topic_edits(topic_id)]}

    def brief(self, topic_id, *, limit=5, offset=0, excerpt_chars=240):
        """Bounded source excerpts with provenance; no generated synthesis claims."""
        from paperflow_domain import Item
        from paperflow_ingest_models import SourceObservation
        with self.storage.transaction() as repo:
            topic = repo.get_topic(topic_id)
            if not topic:
                raise NotFoundError('Topic not found')
            members = sorted(self._current_members(repo, topic, now()),
                             key=lambda a: (a.first_seen_at, str(a.item_id)), reverse=True)
            records = []
            for member in members[offset:offset+limit]:
                item = repo.get(Item, member.item_id)
                observation = repo.get(SourceObservation, member.observation_id)
                excerpt = ' '.join(observation.entry.excerpt.split())
                records.append({'item_id': str(item.id), 'title': item.title[:240],
                    'url': str(item.canonical_url) if item.canonical_url else None,
                    'channel': observation.channel, 'observation_id': str(observation.id),
                    'excerpt': excerpt[:excerpt_chars], 'excerpt_truncated': len(excerpt)>excerpt_chars,
                    'reason': member.reason, 'first_seen_at': member.first_seen_at.isoformat()})
            return {'topic': {'id': str(topic.id), 'title': topic.title, 'version': topic.version,
                    'status': topic.editorial_status, 'sensitivity': topic.sensitivity, 'persistence': topic.persistence,
                    'editorial_summary': topic.description[:500], 'summary_truncated': len(topic.description)>500},
                    'basis': 'source excerpts; not a generated synthesis', 'total': len(members),
                    'records': records, 'next_offset': offset+limit if offset+limit<len(members) else None}

    def create(self, request: CreateTopic):
        if request.title.casefold()=='latest':
            raise ValueError('Latest is the built-in persistent topic index')
        topic=Topic(key='editorial:'+str(uuid5(NAMESPACE_URL,request.title.casefold())),title=request.title,
                    description=request.description,sensitivity=request.sensitivity,persistence='persistent',terms=request.terms,editorial_title=True)
        with self.storage.transaction(write=True) as repo:
            if any(t.key==topic.key for t in repo.topics()):
                raise ConflictError('A persistent topic with this title already exists')
            repo.put_topic(topic);repo.save_topic_edit(TopicEdit(topic_id=topic.id,editor=request.editor,before=None,after=topic))
        return topic

    def edit(self, topic_id: UUID, request: EditTopic):
        with self.storage.transaction(write=True) as repo:
            previous=repo.get_topic(topic_id)
            if not previous:
                raise NotFoundError('Topic not found')
            if previous.version!=request.expected_version:
                raise ConflictError('Topic changed; reload before editing')
            updates=request.model_dump(exclude_unset=True,exclude={'editor','note','expected_version'})
            if 'title' in updates: updates['editorial_title']=True
            updates['version']=previous.version+1
            topic=Topic.model_validate({**previous.model_dump(),**updates})
            if topic.persistence=='persistent' and not topic.terms:
                raise ValueError('Persistent themes need at least one matching term')
            if set(topic.included_item_ids) & set(topic.excluded_item_ids):
                raise ValueError('An item cannot be both included and excluded')
            changed_ids = set(topic.included_item_ids) | set(topic.excluded_item_ids)
            if changed_ids:
                from paperflow_domain import Item
                for item_id in changed_ids:
                    if not repo.get(Item, item_id):
                        raise NotFoundError(f'Item not found: {item_id}')
            repo.put_topic(topic,previous.version)
            repo.save_topic_edit(TopicEdit(topic_id=topic.id,editor=request.editor,note=request.note,before=previous,after=topic))
            return topic

    def runs(self,limit=20):
        with self.storage.transaction() as repo:
            return repo.topic_runs(limit)
