"""Document routing and Vertex AI RAG Engine integration for the demo.

The document branch is deliberately separate from TrustedSQL: it never creates
SQL, opens a database connection, or invokes a TrustedSQL security module.  A
grounded answer is returned only when Vertex AI reports at least one retrieved
source in ``grounding_metadata``.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlsplit

DOCUMENT_ROUTE = "rag"
DATABASE_ROUTE = "database"
MAX_RAG_ANSWER_CHARS = 12_000
MAX_RAG_SOURCES = 8
MAX_SOURCE_SNIPPET_CHARS = 360

_CORPUS_NAME = re.compile(
    r"^projects/(?P<project>[^/]+)/locations/(?P<location>[^/]+)/ragCorpora/(?P<corpus>[^/]+)$"
)


class RagError(RuntimeError):
    """A browser-safe RAG branch failure."""


class RagNotConfigured(RagError):
    pass


class RagGroundingError(RagError):
    pass


@dataclass(frozen=True)
class QueryRoute:
    branch: str
    reason: str
    signals: tuple[str, ...]


@dataclass(frozen=True)
class RagSource:
    citation: int
    title: str
    uri: str | None = None
    document_name: str | None = None
    snippet: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "citation": self.citation,
            "title": self.title,
            "uri": self.uri,
            "documentName": self.document_name,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class RagAnswer:
    answer: str
    sources: tuple[RagSource, ...]


@dataclass(frozen=True)
class VertexRagConfig:
    project_id: str
    location: str
    corpus_name: str
    model: str = "gemini-2.5-flash"
    top_k: int = 6
    vector_distance_threshold: float | None = 0.7

    @classmethod
    def from_environment(cls) -> "VertexRagConfig | None":
        project_id = (
            os.environ.get("VERTEX_RAG_PROJECT_ID")
            or os.environ.get("TRUSTEDSQL_VERTEX_PROJECT_ID")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
            or ""
        ).strip()
        location = (os.environ.get("VERTEX_RAG_LOCATION") or "asia-southeast1").strip()
        corpus_name = (os.environ.get("VERTEX_RAG_CORPUS") or "").strip()
        model = (os.environ.get("VERTEX_RAG_MODEL") or "gemini-2.5-flash").strip()
        try:
            top_k = int(os.environ.get("VERTEX_RAG_TOP_K", "6"))
        except ValueError:
            return None
        raw_threshold = os.environ.get("VERTEX_RAG_DISTANCE_THRESHOLD", "0.7").strip()
        try:
            threshold = None if raw_threshold.lower() in {"", "none", "off"} else float(raw_threshold)
        except ValueError:
            return None
        if not project_id or not location or not corpus_name or not model or not 1 <= top_k <= 20:
            return None
        match = _CORPUS_NAME.fullmatch(corpus_name)
        if not match or match.group("project") != project_id or match.group("location") != location:
            return None
        if threshold is not None and not 0.0 <= threshold <= 1.0:
            return None
        return cls(project_id, location, corpus_name, model, top_k, threshold)


def _fold(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.lower())
    ascii_like = "".join(char for char in normalized if not unicodedata.combining(char))
    return " ".join(ascii_like.replace("đ", "d").split())


class KnowledgeQueryRouter:
    """Conservative router: only clear document questions enter RAG.

    Database/personal-record indicators take precedence.  Ambiguous prompts
    default to the database branch so RAG cannot accidentally answer changing
    or identity-bound records from static documents.
    """

    _DOCUMENT_PATTERNS = (
        ("syllabus", r"\bsyllabus\b|\bde cuong\b"),
        ("tuition-policy", r"\bhoc phi\b|\btuition\b|\ble phi\b|\bfee policy\b"),
        ("regulation", r"\bquy dinh\b|\bquy che\b|\bnoi quy\b|\bpolicy\b|\bregulation\b|\bhandbook\b"),
        ("curriculum", r"\bchuong trinh dao tao\b|\bcurriculum\b|\blo trinh hoc\b|\bprogram structure\b"),
        ("course-content", r"\bmon\b.{0,48}\b(hoc|noi dung|day gi)\b|\bnoi dung mon\b|\bcourse content\b|\bcourse description\b|\bwhat does .{0,24} cover\b"),
        ("course-policy", r"\btin chi\b|\bcredit(s)?\b|\btien quyet\b|\bprerequisite(s)?\b|\bclo\b|\bchuan dau ra\b"),
        ("assessment-scheme", r"\bcach tinh diem\b|\bassessment scheme\b|\bgrading scheme\b|\bty le diem\b"),
        ("student-services", r"\bthu vien\b|\bthu tuc\b|\bdon tu\b|\bkhen thuong\b|\bstudent service(s)?\b"),
    )
    _PERSONAL_RECORD_PATTERNS = (
        ("personal-record", r"\bcua toi\b|\bmy\b|\btoi da\b|\btoi dang\b"),
        ("assigned-scope", r"\blop toi\b|\btoi phu trach\b|\bduoc phan cong\b|\bassigned\b|\bteaching scope\b"),
        ("student-record", r"\bsinh vien\b|\bstudent('?s|s)?\b|\bma sinh vien\b|\bstudent code\b"),
        ("live-record", r"\bdiem danh\b|\battendance\b|\btrang thai thanh toan\b|\bpayment status\b|\bda dong\b|\bcon no\b|\bbalance\b"),
    )
    _DATABASE_ACTIONS = (
        ("database-list", r"\bdanh sach\b|\blist\b|\bshow\b|\bdem\b|\bcount\b|\bbao nhieu sinh vien\b"),
        ("grade-record", r"\bdiem cua\b|\bdiem mon\b|\bgrade(s)? for\b|\baverage\b|\bstatus\b"),
        ("section-record", r"\blop hoc\b|\bclass section\b|\bsection\b|\broster\b"),
    )
    _FOLLOW_UP = re.compile(r"^(con|the con|vay|noi them|chi tiet hon|what about|and|also|more|that|it)\b")

    @classmethod
    def classify(cls, query: str, history: Sequence[Mapping[str, Any]] = ()) -> QueryRoute:
        folded = _fold(query)
        document_hits = tuple(label for label, pattern in cls._DOCUMENT_PATTERNS if re.search(pattern, folded))
        personal_hits = tuple(label for label, pattern in cls._PERSONAL_RECORD_PATTERNS if re.search(pattern, folded))
        database_hits = tuple(label for label, pattern in cls._DATABASE_ACTIONS if re.search(pattern, folded))

        # Explicit document-policy phrases such as "cách tính điểm" are not
        # individual grade records unless the prompt also binds a person.
        identity_bound = any(label in {"personal-record", "assigned-scope", "student-record"} for label in personal_hits)
        if ("live-record" in personal_hits and identity_bound) or (personal_hits and (database_hits or not document_hits)):
            return QueryRoute(DATABASE_ROUTE, "identity-bound or changing record", personal_hits + database_hits)
        if document_hits:
            return QueryRoute(DOCUMENT_ROUTE, "static university document knowledge", document_hits)
        if personal_hits or database_hits:
            return QueryRoute(DATABASE_ROUTE, "structured education data", personal_hits + database_hits)

        if cls._FOLLOW_UP.search(folded) and history:
            previous = history[-1].get("route_type")
            if previous in {DOCUMENT_ROUTE, DATABASE_ROUTE}:
                return QueryRoute(str(previous), "follow-up preserves the previous branch", ("conversation-context",))
        return QueryRoute(DATABASE_ROUTE, "ambiguous request defaults to governed data path", ("safe-default",))


def _source_title(uri: str | None, document_name: str | None, explicit: str | None) -> str:
    if explicit and explicit.strip():
        return explicit.strip()[:240]
    if uri:
        parsed = urlsplit(uri)
        path = parsed.path if parsed.scheme else uri
        name = unquote(PurePosixPath(path).name)
        if name:
            return name[:240]
    if document_name:
        return PurePosixPath(document_name).name[:240]
    return "Vertex AI RAG source"


def _compact_snippet(value: str | None) -> str | None:
    if not value:
        return None
    compact = " ".join(value.split())
    return compact[:MAX_SOURCE_SNIPPET_CHARS] or None


class VertexRagService:
    def __init__(self, config: VertexRagConfig | None = None) -> None:
        self.config = config if config is not None else VertexRagConfig.from_environment()

    @property
    def configured(self) -> bool:
        return self.config is not None

    def readiness(self) -> dict[str, Any]:
        return {
            "ready": self.configured,
            "provider": "vertex_ai_rag_engine",
            "location": self.config.location if self.config else None,
            "corpusConfigured": bool(self.config and self.config.corpus_name),
        }

    @staticmethod
    def _conversation_query(query: str, history: Sequence[Mapping[str, Any]]) -> str:
        recent = [str(item.get("nlq", "")).strip() for item in history[-3:] if item.get("route_type") == DOCUMENT_ROUTE]
        recent = [item for item in recent if item]
        if not recent:
            return query
        context = "\n".join(f"- {item}" for item in recent)
        return f"Previous document questions:\n{context}\n\nCurrent question:\n{query}"

    @staticmethod
    def _extract_sources(response: Any) -> tuple[RagSource, ...]:
        candidates = getattr(response, "candidates", None) or []
        chunks: list[Any] = []
        for candidate in candidates[:1]:
            metadata = getattr(candidate, "grounding_metadata", None)
            chunks.extend(getattr(metadata, "grounding_chunks", None) or [])

        sources: list[RagSource] = []
        seen: set[tuple[str, str]] = set()
        for chunk in chunks:
            context = getattr(chunk, "retrieved_context", None)
            if context is None:
                continue
            uri = getattr(context, "uri", None)
            document_name = getattr(context, "document_name", None)
            explicit_title = getattr(context, "title", None)
            rag_chunk = getattr(context, "rag_chunk", None)
            text = getattr(context, "text", None) or getattr(rag_chunk, "text", None)
            safe_uri = uri.strip()[:2_000] if isinstance(uri, str) and uri.strip() else None
            safe_document = document_name.strip()[:1_000] if isinstance(document_name, str) and document_name.strip() else None
            title = _source_title(safe_uri, safe_document, explicit_title if isinstance(explicit_title, str) else None)
            identity = (safe_uri or "", safe_document or title)
            if identity in seen:
                continue
            seen.add(identity)
            sources.append(
                RagSource(
                    citation=len(sources) + 1,
                    title=title,
                    uri=safe_uri,
                    document_name=safe_document,
                    snippet=_compact_snippet(text if isinstance(text, str) else None),
                )
            )
            if len(sources) >= MAX_RAG_SOURCES:
                break
        return tuple(sources)

    def answer(self, query: str, history: Sequence[Mapping[str, Any]] = ()) -> RagAnswer:
        if self.config is None:
            raise RagNotConfigured("Vertex AI RAG Engine is not configured")
        try:
            from google import genai
            from google.genai import types
        except ImportError as exc:
            raise RagNotConfigured("Google Gen AI SDK is unavailable") from exc

        retrieval_filter = None
        if self.config.vector_distance_threshold is not None:
            retrieval_filter = types.RagRetrievalConfigFilter(
                vector_distance_threshold=self.config.vector_distance_threshold
            )
        tool = types.Tool(
            retrieval=types.Retrieval(
                vertex_rag_store=types.VertexRagStore(
                    rag_resources=[
                        types.VertexRagStoreRagResource(rag_corpus=self.config.corpus_name)
                    ],
                    rag_retrieval_config=types.RagRetrievalConfig(
                        top_k=self.config.top_k,
                        filter=retrieval_filter,
                    ),
                )
            )
        )
        client = genai.Client(
            vertexai=True,
            project=self.config.project_id,
            location=self.config.location,
            http_options=types.HttpOptions(api_version="v1"),
        )
        try:
            response = client.models.generate_content(
                model=self.config.model,
                contents=self._conversation_query(query, history),
                config=types.GenerateContentConfig(
                    system_instruction=(
                        "You answer questions about university documents. Use only facts retrieved "
                        "by the Vertex AI RAG tool. Answer in the user's language. If the retrieved "
                        "documents do not support an answer, say that the documents do not contain "
                        "enough information. Never answer using database records or invent a source."
                    ),
                    tools=[tool],
                    temperature=0.0,
                    max_output_tokens=2_048,
                ),
            )
        except Exception as exc:  # provider details must not cross the browser boundary
            raise RagError("Vertex AI RAG request failed") from exc
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

        answer = getattr(response, "text", None)
        if not isinstance(answer, str) or not answer.strip():
            raise RagGroundingError("Vertex AI RAG returned no answer")
        sources = self._extract_sources(response)
        if not sources:
            raise RagGroundingError("Vertex AI RAG returned no attributable sources")
        return RagAnswer(answer.strip()[:MAX_RAG_ANSWER_CHARS], sources)


__all__ = [
    "DATABASE_ROUTE",
    "DOCUMENT_ROUTE",
    "KnowledgeQueryRouter",
    "QueryRoute",
    "RagAnswer",
    "RagError",
    "RagGroundingError",
    "RagNotConfigured",
    "RagSource",
    "VertexRagConfig",
    "VertexRagService",
]
