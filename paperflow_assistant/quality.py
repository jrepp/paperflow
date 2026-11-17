"""Mechanical validation is distinct from sampled model-estimated quality."""
import math
import re

from paperflow_domain import ContentRevision

EVALUATOR_VERSION = "curation-evidence-v1"


def citations(analysis):
    return [c for finding in [*analysis.findings, *analysis.biases, *analysis.effects]
            for c in finding.citations]


def validate_citations(refs, storage):
    with storage.transaction() as repo:
        for citation in refs:
            revision = repo.get(ContentRevision, citation.revision_id)
            if not revision or revision.markdown[citation.start:citation.start + len(citation.quote)] != citation.quote:
                raise ValueError("Citation does not match its source revision")


def validate_analysis(analysis, revision, settings, storage):
    if analysis.evidence_basis != revision.evidence_basis:
        raise ValueError("Analysis misstates evidence basis")
    if analysis.category not in settings.categories:
        raise ValueError("Category is outside the configured taxonomy")
    refs = citations(analysis)
    if revision.id not in {c.revision_id for c in refs}:
        raise ValueError("Analysis must cite the input revision")
    validate_citations(refs, storage)
    for effect in analysis.effects:
        if len(effect.mechanism) < effect.order:
            raise ValueError("Effects require an explicit chain for each order")
    prose = [analysis.summary, *analysis.limitations, *analysis.research_gaps]
    prose += [f.statement for f in [*analysis.findings, *analysis.biases]]
    sentences = [s for p in prose for s in re.split(r"(?<=[.!?])\s+", p) if s.strip()]
    long_sentences = sum(len(s.split()) > 25 for s in sentences)
    return {"citation_count": len(refs), "valid_citations": len(refs),
            "findings": len(analysis.findings), "effects": len(analysis.effects),
            "bias_assessments": len(analysis.biases), "sentence_count": len(sentences),
            "long_sentences": long_sentences, "questions": sum(p.count('?') for p in prose),
            "evidence_basis": revision.evidence_basis,
            "citation_note": "Exact quote matches establish provenance, not entailment."}


def wilson(passed, total):
    """95% interval for an observed pass proportion, never a truth probability."""
    if not total:
        return None
    z = 1.959963984540054
    p = passed / total
    d = 1 + z*z/total
    center = (p + z*z/(2*total)) / d
    half = z * math.sqrt(p*(1-p)/total + z*z/(4*total*total)) / d
    return [max(0, center-half), min(1, center+half)]
