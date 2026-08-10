"""Lazy public entry points for the dataset-construction workflows.

Imports are intentionally local so documentation and contract inspection do
not require optional model-provider or spreadsheet dependencies.
"""

from __future__ import annotations

from typing import Any, Dict


def generate_benign(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Generate the policy-compliant benign conversation family."""

    from .benign_dataset.generator import generate_benign_dataset

    return generate_benign_dataset(*args, **kwargs)


def generate_single_turn_prompt_injection(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Generate the single-turn prompt-injection family."""

    from .singleturn_prompt_injection.generator import generate_singleturn_pi_dataset

    return generate_singleturn_pi_dataset(*args, **kwargs)


def generate_multi_turn(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Generate the dynamic multi-turn conversation family."""

    from .multiturn_dynamic.generator import generate_multiturn_dataset

    return generate_multiturn_dataset(*args, **kwargs)


def generate_policy_grounded_gnn(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Generate the policy-grounded corpus and its graph artifacts."""

    from .gnn_dataset.generator import generate_gnn_dataset

    return generate_gnn_dataset(*args, **kwargs)


def generate_intent_conversations(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Build intent conversations from deterministic task contracts."""

    from .gnn_dataset.execution_v2 import generate_execution_v2_dataset

    return generate_execution_v2_dataset(*args, **kwargs)


def build_intent_release(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Convert conversation splits into the sequence-level release schema."""

    from .gnn_dataset.release_v2_packager import build_release_v2

    return build_release_v2(*args, **kwargs)


def package_frozen_datatrain(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Verify and repackage an already promoted frozen DataTrain corpus."""

    from .gnn_dataset.datatrain_v1_builder import build_datatrain_v1

    return build_datatrain_v1(*args, **kwargs)
