from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from pydantic import Field

from trustedsql_gnn.contracts import (
    HistoryTurn,
    IntentResolution,
    Mention,
    RuntimeIntentRequest,
    StrictModel,
    TurnLabels,
)
from trustedsql_gnn.inference.predictor import IntentPredictor
from trustedsql_gnn.integration.legacy_adapter import LegacyIntentAdapter
from trustedsql_gnn.paths import GNNPaths


class RuntimeTurnRecord(StrictModel):
    turn_id: int
    text: str
    resolution: IntentResolution
    legacy_adapter: dict[str, Any]


class RuntimeSessionState(StrictModel):
    conversation_id: str
    role: str
    turns: list[RuntimeTurnRecord] = Field(default_factory=list)


class RuntimePredictionResult(StrictModel):
    request: RuntimeIntentRequest
    resolution: IntentResolution
    legacy_adapter: dict[str, Any]
    shadow: dict[str, Any]


class PredictorProtocol(Protocol):
    def predict_turn(self, request: RuntimeIntentRequest) -> IntentResolution:
        ...


class LegacyAdapterProtocol(Protocol):
    def resolve(self, *, role: str, resolution: IntentResolution) -> dict[str, Any]:
        ...


class RuntimeIntentEngine:
    """Stateful shadow runtime wrapper around the standalone intent predictor.

    This class deliberately does not authorize, block, or rewrite policy. It only
    stores prior turn predictions and emits a candidate resolution plus audit log.
    """

    def __init__(
        self,
        *,
        predictor: PredictorProtocol,
        legacy_adapter: LegacyAdapterProtocol,
        log_path: str | Path | None = None,
        gnn_authority: bool = False,
    ):
        self.predictor = predictor
        self.legacy_adapter = legacy_adapter
        self.log_path = Path(log_path) if log_path else None
        self.gnn_authority = gnn_authority
        self.sessions: dict[str, RuntimeSessionState] = {}

    @classmethod
    def from_checkpoint(
        cls,
        *,
        root: str | Path,
        checkpoint_path: str | Path,
        device: str = "cpu",
        allow_hash_encoder: bool = False,
        log_path: str | Path | None = None,
        gnn_authority: bool = False,
    ) -> "RuntimeIntentEngine":
        root_path = Path(root)
        paths = GNNPaths.from_project_root(root_path, checkpoint_path=checkpoint_path)
        predictor = IntentPredictor(
            root=root_path,
            checkpoint_path=paths.checkpoint_path,
            device=device,
            allow_hash_encoder=allow_hash_encoder,
        )
        adapter = LegacyIntentAdapter.load(paths.config_dir / "legacy_intent_mapping_v1.json")
        return cls(
            predictor=predictor,
            legacy_adapter=adapter,
            log_path=log_path,
            gnn_authority=gnn_authority,
        )

    def predict_next(
        self,
        *,
        conversation_id: str,
        role: str,
        text: str,
        mentions: list[Mention] | None = None,
        turn_id: int | None = None,
    ) -> RuntimePredictionResult:
        session = self._session(conversation_id=conversation_id, role=role)
        next_turn_id = turn_id or (session.turns[-1].turn_id + 1 if session.turns else 1)
        if session.turns and next_turn_id <= session.turns[-1].turn_id:
            raise ValueError("runtime_turn_id_must_increase")

        request = RuntimeIntentRequest(
            conversation_id=conversation_id,
            role=role,  # type: ignore[arg-type]
            current_turn_id=next_turn_id,
            history=[
                HistoryTurn(
                    turn_id=item.turn_id,
                    text=item.text,
                    predicted_or_gold_state=_resolution_to_turn_labels(item.resolution),
                )
                for item in session.turns
            ],
            current_text=text,
            current_mentions=mentions or [],
        )
        resolution = self.predictor.predict_turn(request)
        graph_debug = getattr(self.predictor, "last_graph_debug", None)
        legacy = self.legacy_adapter.resolve(role=role, resolution=resolution)
        record = RuntimeTurnRecord(
            turn_id=next_turn_id,
            text=text,
            resolution=resolution,
            legacy_adapter=legacy,
        )
        session.turns.append(record)
        result = RuntimePredictionResult(
            request=request,
            resolution=resolution,
            legacy_adapter=legacy,
            shadow={
                "runtime_mode": "shadow",
                "gnn_authority": self.gnn_authority,
                "used_previous_predictions": bool(request.history),
                "history_turn_count": len(request.history),
                "graph_debug": graph_debug,
            },
        )
        self._append_log(conversation_id, result)
        return result

    def get_session(self, conversation_id: str) -> RuntimeSessionState | None:
        return self.sessions.get(conversation_id)

    def reset_session(self, conversation_id: str) -> None:
        self.sessions.pop(conversation_id, None)

    def _session(self, *, conversation_id: str, role: str) -> RuntimeSessionState:
        session = self.sessions.get(conversation_id)
        if session is None:
            session = RuntimeSessionState(conversation_id=conversation_id, role=role)
            self.sessions[conversation_id] = session
        elif session.role != role:
            raise ValueError("runtime_session_role_changed")
        return session

    def _append_log(self, conversation_id: str, result: RuntimePredictionResult) -> None:
        if self.log_path is None:
            return
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "conversation_id": conversation_id,
            "turn_id": result.request.current_turn_id,
            "current_text": result.request.current_text,
            "resolution": result.resolution.model_dump(mode="json"),
            "legacy_adapter": result.legacy_adapter,
            "shadow": result.shadow,
        }
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def _resolution_to_turn_labels(resolution: IntentResolution) -> TurnLabels:
    return TurnLabels(
        semantic_intent=resolution.primary_intent,
        operation=resolution.operation,
        scope=resolution.scope,
        target_relation=resolution.target_relation,
        transition=resolution.transition,
        target_concepts=resolution.target_concepts,
        reference_targets=[],
        security_transition=resolution.security_transition,
    )

