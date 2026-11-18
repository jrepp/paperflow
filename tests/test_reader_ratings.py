from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from paperflow_assistant.models import AssistantSettings
from paperflow_ratings import ratings_router
from paperflow_story_models import Story, StoryAssessment
from test_publish_site import _curated_site


def test_gateway_ratings_are_versioned_idempotent_and_pseudonymous(tmp_path):
    _, storage, assistant, item = _curated_site(tmp_path)
    analysis = assistant.analysis(item.id)
    with storage.transaction(write=True) as repo:
        story = Story(title=item.title)
        revision = repo.revisions_for_item(item.id)[-1]
        assessment = repo.apply_story_assessment(StoryAssessment(revision_id=revision.id, outcome='new_story',
            creator='test', evidence_basis=revision.evidence_basis, rule_set_version='v1'), story)
        story = repo.publish_story(story.id, 1, item.title, 'Published account', [assessment.id])

    token = 'test-only-gateway-token-' + str(uuid4())
    path = tmp_path / 'gateway-token'
    path.write_text(token)
    def client():
        app = FastAPI()
        app.include_router(ratings_router(storage, AssistantSettings(path=assistant.path, enabled=True), path))
        return TestClient(app)
    headers = {'x-paperflow-rating-gateway': token, 'x-paperflow-client-ip': '2001:db8::3',
               'user-agent': 'test-browser', 'accept-language': 'en'}
    feedback = {'kind': 'story', 'id': str(story.id), 'version': str(story.version), 'rating': 'helpful'}
    url = '/api/publish/ratings'
    with client() as c:
        assert c.post(url, json=feedback).status_code == 403
        assert c.post(url, json=feedback, headers={**headers, 'x-paperflow-client-ip': 'invalid'}).status_code == 400
        assert c.post(url, json={**feedback, 'version': '00000000000000001'}, headers=headers).status_code == 404
        assert c.post(url, json=feedback, headers=headers).json() == {'rating': 'helpful'}
        assert c.post(url, json=feedback, headers=headers).status_code == 200
    with client() as restarted:
        assert restarted.post(url, json={**feedback, 'rating': 'needs_work'}, headers=headers).status_code == 200
        assert restarted.post(url, json=feedback, headers={**headers, 'user-agent': 'second browser'}).status_code == 200
        article = {**feedback, 'kind': 'item', 'id': str(item.id), 'version': analysis['run_id']}
        assert restarted.post(url, json=article, headers=headers).status_code == 200
        assert restarted.post(url, json={**article, 'id': str(uuid4())}, headers=headers).status_code == 404
    with storage.transaction() as repo:
        rows = repo._conn.execute('SELECT reader_hash,kind,rating FROM research_reader_ratings').fetchall()
        assert len(rows) == 3
        assert len({r[0] for r in rows}) == 2
        assert all(len(r[0]) == 64 for r in rows)
        assert {r[2] for r in rows if r[1] == 'story'} == {'needs_work', 'helpful'}
        columns = [r[1] for r in repo._conn.execute('PRAGMA table_info(research_reader_ratings)')]
        assert columns == ['reader_hash', 'kind', 'target_id', 'version', 'rating']
