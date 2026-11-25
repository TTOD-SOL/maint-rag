from rag.generate import SAFETY_LINE, answer_question
from tests.conftest import FakeLLM, FakeRetriever


def test_citations_map_to_hits():
    res = answer_question("How do I verify zero energy?", FakeRetriever(), FakeLLM("Do X [1] and Y [2]."))
    assert [c.n for c in res.citations] == [1, 2]
    assert res.citations[0].source == "osha_3120_loto.pdf"
    assert res.grounded and not res.refused


def test_invalid_citation_not_grounded():
    res = answer_question("vfd fault?", FakeRetriever(), FakeLLM("See [9]."))
    assert not res.grounded
    assert res.citations == []


def test_refusal_detected():
    res = answer_question("capital of France?", FakeRetriever(),
                          FakeLLM("I don't have enough information in the provided documents to answer that."))
    assert res.refused and not res.grounded


def test_loto_question_gets_safety_line():
    res = answer_question("What are the lockout tagout steps?", FakeRetriever(), FakeLLM("Isolate energy [1]."))
    assert SAFETY_LINE in res.answer or "LOTO" in res.answer
