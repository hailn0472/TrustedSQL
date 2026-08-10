from __future__ import annotations

from benchmark_eval.performance.metrics import performance_metrics
from benchmark_eval.security.metrics import multi_turn_security_metrics, single_turn_security_metrics
from benchmark_eval.utility.metrics import utility_metrics


def test_utility_metrics_use_turn_and_sequence_ex() -> None:
    rows = [
        _evidence("benign_single", "s1", 1, True, 1.0),
        _evidence("benign_single", "s2", 1, False, 0.5),
        _evidence("benign_multi", "m1", 1, True, 1.0),
        _evidence("benign_multi", "m1", 2, True, 1.0),
        _evidence("benign_multi", "m2", 1, True, 1.0),
        _evidence("benign_multi", "m2", 2, False, 0.0),
    ]
    metrics = utility_metrics("full", rows)
    assert metrics["st_ex"]["value"] == 0.5
    assert metrics["st_soft_f1"] == 0.75
    assert metrics["mt_turn_ex"]["value"] == 0.75
    assert metrics["mt_iex"]["value"] == 0.5


def test_security_metrics_keep_error_separate_from_refusal() -> None:
    runtime = [
        _runtime("benign_single", "b1", 1, "ALLOW"),
        _runtime("benign_single", "b2", 1, "DENY"),
        _runtime("rbac_single", "r1", 1, "ALLOW"),
        _runtime("rbac_single", "r2", 1, "DENY"),
        _runtime("rbac_single", "r3", 1, "ERROR"),
    ]
    all_metrics = single_turn_security_metrics("full", runtime)
    metrics = all_metrics["rbac_single"]
    assert metrics["asr"] == {"numerator": 1, "denominator": 3, "value": 1 / 3}
    assert metrics["refusal_recall"]["numerator"] == 1
    assert metrics["runtime_error_rate"]["numerator"] == 1
    assert all_metrics["benign_single"]["over_refusal_rate"]["value"] == 0.5


def test_multi_turn_security_requires_prefix_rs_and_final_deny() -> None:
    runtime = [
        _runtime("malicious_multi", "m1", 1, "ALLOW", "BENIGN"),
        _runtime("malicious_multi", "m1", 2, "DENY", "MALICIOUS"),
        _runtime("malicious_multi", "m2", 1, "ALLOW", "BENIGN"),
        _runtime("malicious_multi", "m2", 2, "DENY", "MALICIOUS"),
    ]
    evidence = [
        _evidence("malicious_multi", "m1", 1, False, 0.7, prefix_rs=True),
        _evidence("malicious_multi", "m2", 1, False, 0.0, prefix_rs=False),
    ]
    metrics = multi_turn_security_metrics("full", runtime, evidence)
    assert metrics["prefix_rs"]["value"] == 0.5
    assert metrics["sequence_asr"]["value"] == 0.0
    assert metrics["sequence_refusal_recall"]["value"] == 1.0
    assert metrics["conditional_refusal_recall"]["value"] == 1.0
    assert metrics["valid_secure_sequence_rate"]["value"] == 0.5


def test_performance_paths_apply_exact_filters() -> None:
    runtime = [
        _runtime("benign_single", "b1", 1, "ALLOW", executed=True, latency=10),
        _runtime("benign_single", "b2", 1, "DENY", latency=99),
        _runtime("rbac_single", "r1", 1, "DENY", latency=20),
        _runtime("malicious_multi", "m1", 1, "ALLOW", "BENIGN", latency=5),
        _runtime("malicious_multi", "m1", 2, "DENY", "MALICIOUS", latency=7),
    ]
    metrics = performance_metrics("full", runtime, {"m1"})
    assert metrics["benign_served_path"]["count"] == 1
    assert metrics["single_turn_blocked_path"]["rbac_single"]["count"] == 1
    assert metrics["multi_turn_secure_sequence_path"]["mean_latency_ms"] == 12


def _evidence(dataset: str, sample: str, turn: int, ex: bool, f1: float, *, prefix_rs: bool | None = None) -> dict:
    return {
        "setting_id": "full",
        "source_dataset": dataset,
        "sample_id": sample,
        "turn_id": turn,
        "ex_match": ex,
        "soft_f1": f1,
        "prefix_result_sufficient": ex if prefix_rs is None else prefix_rs,
    }


def _runtime(
    dataset: str,
    sample: str,
    turn: int,
    decision: str,
    turn_label: str = "MALICIOUS",
    *,
    executed: bool = False,
    latency: float = 1,
) -> dict:
    return {
        "setting_id": "full",
        "source_dataset": dataset,
        "sample_id": sample,
        "turn_id": turn,
        "turn_label": turn_label,
        "decision": decision,
        "executed": executed,
        "latency_ms": latency,
        "llm_usage": {"prompt_token_count": 2, "candidates_token_count": 1},
    }

