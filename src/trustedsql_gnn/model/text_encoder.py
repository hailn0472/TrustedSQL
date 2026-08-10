from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import numpy as np


class HashTextEncoder:
    """Deterministic smoke-test encoder. Not accepted for production training by default."""

    def __init__(self, dimension: int = 384):
        self.dimension = dimension
        self.model_name = f"hash-test-{dimension}"

    def encode(self, texts: list[str]) -> np.ndarray:
        rows: list[np.ndarray] = []
        for text in texts:
            seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
            rng = np.random.default_rng(seed)
            vector = rng.standard_normal(self.dimension).astype(np.float32)
            norm = np.linalg.norm(vector)
            rows.append(vector / norm if norm else vector)
        return np.stack(rows) if rows else np.empty((0, self.dimension), dtype=np.float32)


class FrozenTextEncoder:
    def __init__(
        self,
        *,
        model_name: str,
        cache_dir: str | Path,
        embedding_cache: str | Path | None,
        allow_hash_fallback: bool = False,
        local_files_only: bool = False,
        cache_namespace: str | None = None,
    ):
        self.model_name = model_name
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.embedding_cache = Path(embedding_cache) if embedding_cache is not None else None
        if self.embedding_cache is not None:
            self.embedding_cache.parent.mkdir(parents=True, exist_ok=True)
        self.allow_hash_fallback = allow_hash_fallback
        self.local_files_only = local_files_only
        self._backend = None
        self._canonical_cache_namespace = cache_namespace or model_name
        self._cache_namespace = self._canonical_cache_namespace
        self.dimension = 384
        if self.embedding_cache is not None:
            self._init_db()

    def _load_backend(self):
        if self._backend is not None:
            return self._backend
        try:
            from sentence_transformers import SentenceTransformer

            self._backend = SentenceTransformer(
                self.model_name,
                cache_folder=str(self.cache_dir),
                local_files_only=self.local_files_only,
            )
            get_dimension = getattr(self._backend, "get_embedding_dimension", None)
            if get_dimension is None:
                get_dimension = self._backend.get_sentence_embedding_dimension
            self.dimension = int(get_dimension())
            self._cache_namespace = self._canonical_cache_namespace
        except Exception:
            if not self.allow_hash_fallback:
                raise
            self._backend = HashTextEncoder(self.dimension)
            self._cache_namespace = self._backend.model_name
        return self._backend

    def encode(self, texts: list[str]) -> np.ndarray:
        backend = self._load_backend()
        if self.embedding_cache is None:
            if not texts:
                return np.empty((0, self.dimension), dtype=np.float32)
            vectors = np.asarray(backend.encode(texts), dtype=np.float32)
            self.dimension = int(vectors.shape[1])
            return vectors
        output: list[np.ndarray | None] = [None] * len(texts)
        missing_indices: list[int] = []
        missing_texts: list[str] = []
        with sqlite3.connect(self.embedding_cache) as connection:
            for index, text in enumerate(texts):
                digest = self._key(text)
                row = connection.execute(
                    "SELECT vector FROM embeddings WHERE cache_key = ?",
                    (digest,),
                ).fetchone()
                if row is None:
                    missing_indices.append(index)
                    missing_texts.append(text)
                else:
                    output[index] = np.frombuffer(row[0], dtype=np.float32)
            if missing_texts:
                vectors = np.asarray(
                    backend.encode(missing_texts),
                    dtype=np.float32,
                )
                self.dimension = int(vectors.shape[1])
                for index, text, vector in zip(missing_indices, missing_texts, vectors):
                    output[index] = vector
                    connection.execute(
                        "INSERT OR REPLACE INTO embeddings(cache_key, model_name, dimension, vector) VALUES (?, ?, ?, ?)",
                        (self._key(text), self._cache_namespace, self.dimension, vector.tobytes()),
                    )
                connection.commit()
        if not output:
            return np.empty((0, self.dimension), dtype=np.float32)
        return np.stack([item for item in output if item is not None])

    def _key(self, text: str) -> str:
        return hashlib.sha256(f"{self._cache_namespace}\0{text}".encode("utf-8")).hexdigest()

    def _init_db(self) -> None:
        if self.embedding_cache is None:
            return
        with sqlite3.connect(self.embedding_cache) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS embeddings(
                    cache_key TEXT PRIMARY KEY,
                    model_name TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector BLOB NOT NULL
                )
                """
            )
            connection.commit()

