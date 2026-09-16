from app.qa.evidence_checker import EvidenceChecker
from app.qa.models import RetrievalDocument


def document(score: float | None) -> RetrievalDocument:
    return RetrievalDocument(chunk_id="1", content="evidence", rerank_score=score)


def test_empty_documents_are_insufficient() -> None:
    assert EvidenceChecker(0.5).is_sufficient([]) is False


def test_low_score_is_insufficient() -> None:
    assert EvidenceChecker(0.5).is_sufficient([document(0.49)]) is False


def test_score_at_threshold_is_sufficient() -> None:
    assert EvidenceChecker(0.5).is_sufficient([document(0.5)]) is True
