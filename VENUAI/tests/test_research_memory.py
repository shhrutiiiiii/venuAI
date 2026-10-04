"""
Real tests for the Tier 4 Research Memory extension to
VENUAI/memory_manager.py — uses a throwaway SQLite file, no mocking of the
storage layer itself.
"""

from VENUAI.memory_manager import MemoryManager


def test_save_and_retrieve_research_finding(tmp_path):
    mm = MemoryManager(db_path=str(tmp_path / "test_memory.db"))

    mm.save_research_finding(
        research_id="r1", user_id="user-1",
        question="What is the range of Agni-V?",
        answer="5000 km.", evidence_coverage=0.9, critique_passed=True,
    )

    findings = mm.get_research_findings("r1", user_id="user-1")
    assert len(findings) == 1
    assert findings[0]["answer"] == "5000 km."
    assert findings[0]["evidence_coverage"] == 0.9
    assert findings[0]["critique_passed"] == 1


def test_research_findings_are_scoped_to_owning_user(tmp_path):
    mm = MemoryManager(db_path=str(tmp_path / "test_memory.db"))

    mm.save_research_finding(
        research_id="r1", user_id="user-a",
        question="q", answer="a", evidence_coverage=0.5, critique_passed=False,
    )

    # A different user guessing the same research_id must not see it.
    findings = mm.get_research_findings("r1", user_id="user-b")
    assert findings == []


def test_multiple_findings_for_same_research_id_accumulate(tmp_path):
    mm = MemoryManager(db_path=str(tmp_path / "test_memory.db"))

    mm.save_research_finding("r1", "user-1", "sub-q 1", "answer 1")
    mm.save_research_finding("r1", "user-1", "sub-q 2", "answer 2")

    findings = mm.get_research_findings("r1", user_id="user-1")
    assert len(findings) == 2
    assert findings[0]["question"] == "sub-q 1"
    assert findings[1]["question"] == "sub-q 2"
