"""Mechanical validation is distinct from sampled model-estimated quality."""
import math
import re

from paperflow_domain import ContentRevision

EVALUATOR_VERSION = "curation-evidence-v2"


def citations(analysis):
    return [c for finding in [*analysis.findings, *analysis.biases, *analysis.effects]
            for c in finding.citations]


def validate_citations(refs, storage):
    """Resolve mechanical offsets for unique exact quotes, then enforce fidelity."""
    with storage.transaction() as repo:
        for citation in refs:
            revision = repo.get(ContentRevision, citation.revision_id)
            if not revision:
                raise ValueError("Citation does not match its source revision")
            if revision.markdown[citation.start:citation.start + len(citation.quote)] != citation.quote:
                start = revision.markdown.find(citation.quote)
                if start >= 0:
                    if revision.markdown.find(citation.quote, start + 1) >= 0:
                        raise ValueError("Citation does not match its source revision: ambiguous quote")
                    citation.start = start
                    continue
                # PDF and HTML extraction can wrap a quotation across lines.
                # Match whitespace only, then persist the actual source substring.
                tokens = list(re.finditer(r'\S+', revision.markdown))
                normalized = ' '.join(token.group() for token in tokens)
                quote = ' '.join(citation.quote.split())
                start = normalized.find(quote) if quote else -1
                if start < 0 or normalized.find(quote, start + 1) >= 0:
                    raise ValueError("Citation does not match its source revision: missing or ambiguous quote")
                offsets = []
                for token in tokens:
                    if offsets:
                        offsets.append(token.start() - 1)
                    offsets.extend(range(token.start(), token.end()))
                actual_start, actual_end = offsets[start], offsets[start + len(quote) - 1] + 1
                citation.start = actual_start
                citation.quote = revision.markdown[actual_start:actual_end]


def validate_analysis(analysis, revision, settings, storage, *, public_only=False):
    if analysis.evidence_basis != revision.evidence_basis:
        raise ValueError("Analysis misstates evidence basis")
    if analysis.category not in settings.categories:
        raise ValueError("Category is outside the configured taxonomy")
    refs = citations(analysis)
    if revision.id not in {c.revision_id for c in refs}:
        raise ValueError("Analysis must cite the input revision")
    if public_only:
        with storage.transaction() as repo:
            if any(not repo.is_public_revision(c.revision_id) for c in refs):
                raise ValueError("Public analysis cannot cite private revisions")
    validate_citations(refs, storage)
    for effect in analysis.effects:
        if len(effect.mechanism) < effect.order:
            raise ValueError("Effects require an explicit chain for each order")
    prose = [analysis.summary, *analysis.limitations, *analysis.research_gaps]
    for finding in [*analysis.findings, *analysis.biases]:
        prose.extend((finding.statement, finding.uncertainty))
    for effect in analysis.effects:
        prose.extend((effect.hypothesis, *effect.mechanism, *effect.assumptions,
                      effect.time_horizon, effect.falsifier, effect.uncertainty))
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
