from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Optional, Sequence

import pandas as pd


def export_dataset_summary_excel(
    records: Sequence[Dict[str, Any]],
    output_path: str,
    *,
    expected_counts: Optional[Dict[str, int]] = None,
    group_field: str = "primary_type",
    group_sheet_name: str = "Primary_Counts",
    extra_sheets: Optional[Dict[str, pd.DataFrame]] = None,
) -> None:
    group_counts = Counter(_group_value(record, group_field) for record in records)
    turn_counts = Counter(len(record.get("turns", [])) for record in records)
    role_counts = Counter(record.get("role") for record in records)
    policy_counts = Counter()
    rbac_counts = Counter()
    injection_counts = Counter()
    mt_counts = Counter()

    for record in records:
        tags = record.get("attack_tags", {})
        _update_counter(policy_counts, tags.get("violated_policies"))
        _update_counter(rbac_counts, tags.get("rbac_violation"))
        _update_counter(injection_counts, tags.get("injection_type"))
        _update_counter(mt_counts, tags.get("mt_pattern"))

    expected_total = sum(expected_counts.values()) if expected_counts else len(records)
    summary_df = pd.DataFrame(
        [
            {"metric": "total_records", "value": len(records)},
            {"metric": "expected_total", "value": expected_total},
            {"metric": "malicious_records", "value": sum(1 for record in records if record.get("seq_label") == "MALICIOUS")},
            {"metric": "benign_records", "value": sum(1 for record in records if record.get("seq_label") == "BENIGN")},
        ]
    )
    if expected_counts:
        group_df = pd.DataFrame(
            [
                {
                    group_field: key,
                    "count": group_counts.get(key, 0),
                    "expected_count": expected,
                    "matches_plan": group_counts.get(key, 0) == expected,
                }
                for key, expected in expected_counts.items()
            ]
        )
    else:
        group_df = pd.DataFrame(
            [{group_field: key, "count": count} for key, count in sorted(group_counts.items())]
        )

    turns_df = pd.DataFrame(
        [{"num_turns": turns, "count": count} for turns, count in sorted(turn_counts.items())]
    )
    roles_df = pd.DataFrame(
        [{"role": role, "count": count} for role, count in sorted(role_counts.items())]
    )
    rbac_df = pd.DataFrame(
        [{"rbac_violation": tag, "count": count} for tag, count in sorted(rbac_counts.items())]
    )
    policy_df = pd.DataFrame(
        [{"violated_policy": tag, "count": count} for tag, count in sorted(policy_counts.items())]
    )
    injection_df = pd.DataFrame(
        [{"injection_type": tag, "count": count} for tag, count in sorted(injection_counts.items())]
    )
    mt_df = pd.DataFrame(
        [{"mt_pattern": tag, "count": count} for tag, count in sorted(mt_counts.items())]
    )

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df.to_excel(writer, index=False, sheet_name="Summary")
        group_df.to_excel(writer, index=False, sheet_name=group_sheet_name[:31])
        turns_df.to_excel(writer, index=False, sheet_name="Turn_Counts")
        roles_df.to_excel(writer, index=False, sheet_name="Roles")
        rbac_df.to_excel(writer, index=False, sheet_name="RBAC_Tags")
        policy_df.to_excel(writer, index=False, sheet_name="Policy_Tags")
        injection_df.to_excel(writer, index=False, sheet_name="Injection_Tags")
        mt_df.to_excel(writer, index=False, sheet_name="MT_Tags")
        for sheet_name, df in (extra_sheets or {}).items():
            df.to_excel(writer, index=False, sheet_name=sheet_name[:31])

        for worksheet in writer.sheets.values():
            for column_cells in worksheet.columns:
                values = [cell.value for cell in column_cells if cell.value is not None]
                max_len = max((len(str(value)) for value in values), default=8)
                worksheet.column_dimensions[column_cells[0].column_letter].width = min(max_len + 2, 52)


def _update_counter(counter: Counter, value: Any) -> None:
    if isinstance(value, list):
        counter.update(item for item in value if item is not None)
    elif value is not None:
        counter[value] += 1


def _group_value(record: Dict[str, Any], group_field: str) -> Any:
    if group_field == "mt_pattern":
        tags = record.get("attack_tags") if isinstance(record.get("attack_tags"), dict) else {}
        return tags.get("mt_pattern")
    if "." not in group_field:
        return record.get(group_field)

    value: Any = record
    for part in group_field.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value
