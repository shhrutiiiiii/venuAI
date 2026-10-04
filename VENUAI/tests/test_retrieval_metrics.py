"""Real tests for the new rank-aware retrieval metrics (MRR, nDCG) added
to VENUAI/evals/metrics.py — extending the existing context_precision/
context_recall, which only check set membership, not ranking order."""

from VENUAI.evals.metrics import mean_reciprocal_rank, ndcg_at_k


def test_mrr_perfect_when_relevant_doc_is_first():
    assert mean_reciprocal_rank(["d1", "d2", "d3"], ["d1"]) == 1.0


def test_mrr_half_when_relevant_doc_is_second():
    assert mean_reciprocal_rank(["d2", "d1", "d3"], ["d1"]) == 0.5


def test_mrr_zero_when_relevant_doc_not_retrieved():
    assert mean_reciprocal_rank(["d2", "d3"], ["d1"]) == 0.0


def test_mrr_zero_on_empty_inputs():
    assert mean_reciprocal_rank([], ["d1"]) == 0.0
    assert mean_reciprocal_rank(["d1"], []) == 0.0


def test_ndcg_perfect_when_all_relevant_at_top():
    assert ndcg_at_k(["d1", "d2"], ["d1", "d2"]) == 1.0


def test_ndcg_lower_when_relevant_doc_ranked_later():
    score_top = ndcg_at_k(["d1", "d2", "d3"], ["d1"])
    score_bottom = ndcg_at_k(["d2", "d3", "d1"], ["d1"])
    assert score_top > score_bottom


def test_ndcg_zero_when_nothing_relevant_retrieved():
    assert ndcg_at_k(["d2", "d3"], ["d1"]) == 0.0


def test_ndcg_respects_k_cutoff():
    # relevant doc is ranked 3rd; with k=2 it never gets counted.
    assert ndcg_at_k(["d2", "d3", "d1"], ["d1"], k=2) == 0.0
    assert ndcg_at_k(["d2", "d3", "d1"], ["d1"], k=3) > 0.0
