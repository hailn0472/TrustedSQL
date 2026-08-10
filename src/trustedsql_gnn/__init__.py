"""Multi-turn GNN intent resolver research package."""

__version__ = "0.1.0"

__all__ = ["GNNIntentPhase"]


def __getattr__(name: str):
    if name == "GNNIntentPhase":
        from trustedsql_gnn.inference.phase import GNNIntentPhase

        return GNNIntentPhase
    raise AttributeError(name)
