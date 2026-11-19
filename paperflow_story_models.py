"""Durable story and source contribution records."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from paperflow_domain import AuthoredRecord, Record, Text, now


class Story(Record):
    title: Text
    version: int = Field(default=1, ge=1)
    account: str = ""
    material_at: AwareDatetime = Field(default_factory=now)


class StoryFinding(Record):
    rule_id: Text
    rule_version: Text
    result: Text
    rationale: Text
    passages: tuple[str, ...] = ()
    missing_evidence: str = ""


class StoryAssessment(AuthoredRecord):
    revision_id: UUID
    story_id: UUID | None = None
    story_version: int | None = Field(default=None, ge=1)
    outcome: Literal['new_story', 'repeated_coverage', 'material_contribution', 'unresolved']
    category: str | None = Field(default=None, max_length=100)
    category_model: str | None = Field(default=None, max_length=200)
    findings: tuple[StoryFinding, ...] = ()
    evidence_basis: Literal['full_text', 'abstract', 'excerpt']
    rule_set_version: Text
    prior_assessment_id: UUID | None = None

    @model_validator(mode='after')
    def validate_candidate(self):
        if self.outcome in ('repeated_coverage', 'material_contribution') and (self.story_id is None or self.story_version is None):
            raise ValueError('existing story and version required')
        if self.outcome == 'new_story' and self.story_version is not None:
            raise ValueError('new story has no prior version')
        return self


class StorySource(Record):
    story_id: UUID
    item_id: UUID
    revision_id: UUID
    assessment_id: UUID
    first_attached_at: AwareDatetime = Field(default_factory=now)


class StoryCandidate(Record):
    story: Story
    sources: tuple[StorySource, ...] = ()
    matched: tuple[str, ...] = ()
    score: float = 0
