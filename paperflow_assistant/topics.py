"""Project completed model classifications into the existing topic contract."""
from paperflow_topic_models import TopicProposal, TopicAssignment


class AssistantTopicCurator:
    version = 'assistant-category-v2'

    def __init__(self, store):
        self.store = store

    def propose(self, candidates, settings):
        groups = {}
        for candidate in candidates:
            classification = self.store.classification(candidate.item_id)
            result = self.store.analysis(candidate.item_id) if not classification else None
            if classification or result:
                category = classification['category'] if classification else result['analysis']['category']
                run_id = classification['job_id'] if classification else result['run_id']
                groups.setdefault(category, []).append(TopicAssignment(
                    item_id=candidate.item_id, observation_id=candidate.observation_id,
                    first_seen_at=candidate.first_seen_at,
                    reason=f"Research assistant classification; run {run_id}"))
        return [TopicProposal(key='assistant:'+category, title=category.replace('-', ' ').title(),
            terms=(), assignments=tuple(members)) for category, members in sorted(groups.items())
            if len(members) >= settings.min_items][:settings.max_topics]
