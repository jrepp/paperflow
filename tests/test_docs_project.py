"""Guard the pinned Docuchango adapter against silent documentation omissions."""
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.docs_validate import RepositoryDocValidator, uncovered_documents


def document(title):
    return f'---\ntitle: {title}\nproject_id: test-project\ndoc_uuid: {uuid4()}\ntags: []\n---\n\n# {title}\n'


@pytest.fixture
def repository(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path/'docs/guides').mkdir(parents=True)
    (tmp_path/'docs/guides/guide.md').write_text(document('Example guide'))
    (tmp_path/'README.md').write_text('# Project\n\n[Guide](docs/guides/guide.md)\n')
    (tmp_path/'docs/README.md').write_text('# Documentation\n\n[Guide](guides/guide.md)\n')
    (tmp_path/'AGENTS.md').write_text(document('Agent instructions'))
    (tmp_path/'CHANGELOG.md').write_text('# Changelog\n\nNo release yet.\n')
    config = {
        'version':'1', 'docuchango_version':'1.20.0',
        'project':{'id':'test-project', 'name':'Test project'},
        'structure':{'scan_subfolders':True,'doc_types':{'guides':{'schema':'generic','folders':['guides'],'naming_standard':'kebab-case'}}},
        'security':{'allow_external_paths':True},
        'indexes':[{'name':name,'path':path,'targets':[], 'require_entries':False} for name,path in
                   [('Docs','README.md'),('Root','../README.md'),('Agents','../AGENTS.md'),('Releases','../CHANGELOG.md')]],
    }
    (tmp_path/'docs/docs-project.yaml').write_text(yaml.safe_dump(config))
    return tmp_path


def test_registered_entrypoint_bodies_and_frontmatter_are_validated(repository):
    (repository/'README.md').write_text('# Project\n\n[Missing](absent.md)\n')
    (repository/'AGENTS.md').write_text('# Agent instructions without frontmatter\n')
    validator = RepositoryDocValidator(repository)
    validator.scan_documents()
    assert len(validator.documents) == 5
    assert not uncovered_documents(validator)
    validator.extract_links()
    validator.validate_links()
    assert any(link.source_doc == repository/'README.md' and not link.is_valid
               for link in validator.all_links)
    assert 'Missing YAML frontmatter' in validator.file_to_doc[repository/'AGENTS.md'].errors
    assert not validator.file_to_doc[repository/'CHANGELOG.md'].errors


def test_untracked_new_folders_and_nested_readmes_cannot_escape(repository):
    (repository/'.gitignore').write_text('artifacts/\n')
    (repository/'artifacts').mkdir()
    (repository/'artifacts/runtime.md').write_text('# Runtime artifact\n')
    missing = {repository/'notes.md', repository/'docs/guides/README.md', repository/'notes.mdx', repository/'notes.rst'}
    for path in missing:
        path.write_text('# New documentation\n')
    validator = RepositoryDocValidator(repository)
    validator.scan_documents()
    assert uncovered_documents(validator) == missing
    # Source files remain governed even if a later ignore rule would match them.
    subprocess.run(['git','add','-f','artifacts/runtime.md'], cwd=repository, check=True)
    assert uncovered_documents(validator) == missing | {repository/'artifacts/runtime.md'}


def test_index_configuration_cannot_escape_repository(repository):
    path = repository/'docs/docs-project.yaml'
    config = yaml.safe_load(path.read_text())
    config['indexes'].append({'name':'Escaping index','path':'../../outside.md','targets':[],'require_entries':False})
    path.write_text(yaml.safe_dump(config))
    validator = RepositoryDocValidator(repository)
    validator.scan_documents()
    assert any('Index leaves repository' in error for error in validator.errors)


@pytest.mark.parametrize('mutation,code', [
    ('undefined', 'TRC-001'),
    ('duplicate', 'TRC-002'),
    ('disposition', 'TRC-003'),
    ('coverage', 'TRC-004'),
    ('requirement', 'TRC-005'),
    ('valid', None),
])
def test_rc_traceability_contract(repository, mutation, code):
    config_path = repository/'docs/docs-project.yaml'
    config = yaml.safe_load(config_path.read_text())
    config['structure']['doc_types']['guides']['check_traceability'] = True
    config_path.write_text(yaml.safe_dump(config))
    spec = document('Acceptance specification').replace('tags: []', 'tags: []\nid: acceptance-spec')
    spec += '\n## FR-1: Durable records\n\n### AC-1: Restart\n\nRequirement: FR-1\n\nRecords survive restart.\n'
    implementation = document('Acceptance implementation').replace('tags: []', 'tags: []\nid: acceptance-tests\nimplements: [acceptance-spec]')
    implementation += '\n| Criterion | Requirement | Disposition |\n| --- | --- | --- |\n| acceptance-spec::AC-1 | acceptance-spec::FR-1 | covered |\n'
    if mutation == 'undefined':
        implementation = implementation.replace('::AC-1', '::AC-99')
    elif mutation == 'duplicate':
        spec += '\n### AC-1: Duplicate\n\nRequirement: FR-1\n'
    elif mutation == 'disposition':
        implementation = implementation.replace('| covered |', '| |')
    elif mutation == 'coverage':
        implementation = implementation.split('\n| Criterion')[0]
    elif mutation == 'requirement':
        spec = spec.replace('Requirement: FR-1', 'No requirement clause')
    (repository/'docs/guides/spec.md').write_text(spec)
    (repository/'docs/guides/implementation.md').write_text(implementation)
    validator = RepositoryDocValidator(repository)
    validator.scan_documents()
    validator.check_traceability()
    errors = [error for doc in validator.documents for error in doc.errors]
    if code:
        assert any(code in error for error in errors), errors
    else:
        assert not errors
