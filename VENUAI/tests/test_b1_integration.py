"""Integration fixes after merging B1: claim-level grounding and safeguard parsing."""
from types import SimpleNamespace as NS

from VENUAI import answer_pipeline as ap
from VENUAI import safety_classifiers as sc
from VENUAI.evidence import SupportStatus


def _pr(statuses, lf=None):
    claims = [NS(support_status=s) for s in statuses]
    return NS(evidence_graph=NS(claims=claims), local_faithfulness=lf)


def test_grounding_prefers_nli_supported_fraction():
    lf = {"score": 0.7, "supported_fraction": 0.75, "claims": [{}], "model": "nli"}
    g, basis = ap.claim_level_grounding(_pr([SupportStatus.UNSUPPORTED], lf), 0.3)
    assert g == 0.75 and basis.startswith("nli_claims")


def test_grounding_claim_overlap_partial_credit():
    g, basis = ap.claim_level_grounding(
        _pr([SupportStatus.SUPPORTED, SupportStatus.PARTIALLY_SUPPORTED, SupportStatus.UNSUPPORTED,
             SupportStatus.UNSUPPORTED]), 0.9)
    assert g == 0.375 and basis == "claim_overlap"   # not inflated by the legacy number


def test_grounding_falls_back_to_token_f1_without_claims():
    assert ap.claim_level_grounding(_pr([]), 0.3) == (0.3, "token_f1")


def test_safeguard_parses_json_and_fails_open_on_truncation(monkeypatch):
    monkeypatch.setenv("SAFEGUARD_ENABLED", "true")
    monkeypatch.setattr(sc, "_classify_call",
                        lambda *a, **k: '{"violation":0,"category":"NONE","rationale":"ok"}')
    assert sc.safeguard_classify("hello")["status"] == "passed"

    def boom(*a, **k):
        raise RuntimeError("classifier output truncated (finish_reason=length)")
    monkeypatch.setattr(sc, "_classify_call", boom)
    assert sc.safeguard_classify("hello")["status"] == "unavailable"
