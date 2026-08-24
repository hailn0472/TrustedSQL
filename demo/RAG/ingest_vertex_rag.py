#!/usr/bin/env python3
"""Upload demo Markdown files and import them into Vertex AI RAG Engine.

This is an explicit provisioning command because it creates billable Google
Cloud resources. The live demo never runs ingestion automatically. GCS batch
import is preferred, but direct local upload is available when the provisioning
identity has Vertex AI permissions without Cloud Storage permissions.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import sys
import tempfile
import threading
from collections.abc import Iterable
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.backend.app.environment import load_env_file  # noqa: E402


def _corpora(response: Any) -> Iterable[Any]:
    """Support both the iterable SDK pager and explicit response containers."""

    if hasattr(response, "__iter__"):
        return response
    items = getattr(response, "rag_corpora", None)
    return items if isinstance(items, Iterable) else ()


def _upload_path(path: Path, relative: str, staging_dir: Path) -> Path:
    """Return a path whose basename is safe for the SDK upload header.

    Vertex accepts UTF-8 RagFile display names, but the current SDK also puts
    the local basename in an ASCII-only HTTP header. Preserve the original
    relative path as the display name and stage only the local upload path
    behind a deterministic ASCII symlink when needed.
    """

    try:
        path.name.encode("ascii")
    except UnicodeEncodeError:
        alias = staging_dir / f"{hashlib.sha256(relative.encode('utf-8')).hexdigest()}{path.suffix}"
        alias.symlink_to(path)
        return alias
    return path


def _resolve_corpus(client: Any, types: Any, args: argparse.Namespace) -> str:
    expected_prefix = f"projects/{args.project}/locations/{args.location}/ragCorpora/"
    if args.corpus_name:
        corpus_id = args.corpus_name.removeprefix(expected_prefix)
        if not args.corpus_name.startswith(expected_prefix) or not corpus_id.isdigit():
            raise SystemExit("--corpus-name must belong to the selected project and location")
        try:
            corpus = client.rag.get_corpus(name=args.corpus_name)
        except Exception as exc:
            raise SystemExit(f"Configured corpus does not exist or is not accessible: {args.corpus_name}") from exc
        resolved_name = getattr(corpus, "name", None)
        if resolved_name != args.corpus_name:
            raise SystemExit("Vertex AI returned a mismatched corpus resource")
        print(f"Reusing configured corpus: {resolved_name}")
        return resolved_name

    try:
        matches = sorted(
            (
                corpus
                for corpus in _corpora(client.rag.list_corpora())
                if getattr(corpus, "display_name", None) == args.display_name
            ),
            key=lambda corpus: str(getattr(corpus, "name", "")),
        )
    except Exception as exc:
        raise SystemExit("Unable to list existing Vertex AI RAG corpora") from exc
    if len(matches) > 1:
        names = ", ".join(str(getattr(corpus, "name", "unknown")) for corpus in matches)
        raise SystemExit(
            f"Multiple corpora use display name {args.display_name!r}: {names}. "
            "Set VERTEX_RAG_CORPUS or pass --corpus-name explicitly."
        )
    if matches:
        corpus_name = getattr(matches[0], "name", None)
        if not isinstance(corpus_name, str) or not corpus_name.startswith(expected_prefix):
            raise SystemExit("Vertex AI returned an invalid corpus resource")
        print(f"Reusing corpus matched by display name: {corpus_name}")
        return corpus_name

    corpus = client.rag.create_corpus(
        rag_corpus=types.RagCorpus(
            display_name=args.display_name,
            rag_vector_db_config=types.RagVectorDbConfig(
                rag_embedding_model_config=types.RagEmbeddingModelConfig(
                    vertex_prediction_endpoint=types.RagEmbeddingModelConfigVertexPredictionEndpoint(
                        endpoint=f"publishers/google/models/{args.embedding_model}"
                    )
                )
            ),
        )
    )
    corpus_name = getattr(corpus, "name", None)
    if not isinstance(corpus_name, str) or not corpus_name.startswith(expected_prefix):
        raise SystemExit("Vertex AI did not return the created corpus resource name")
    print(f"Created corpus: {corpus_name}")
    return corpus_name


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Provision the TrustedSQL demo Vertex AI RAG corpus")
    parser.add_argument("--project", default=os.environ.get("VERTEX_RAG_PROJECT_ID") or os.environ.get("TRUSTEDSQL_VERTEX_PROJECT_ID"))
    parser.add_argument("--location", default=os.environ.get("VERTEX_RAG_LOCATION", "asia-southeast1"))
    parser.add_argument("--bucket", help="Existing GCS bucket name; omit for direct local upload")
    parser.add_argument("--prefix", default="trustedsql-demo-rag")
    parser.add_argument("--source-dir", type=Path, default=Path(__file__).resolve().parent / "md-sources")
    parser.add_argument("--corpus-name", default=os.environ.get("VERTEX_RAG_CORPUS"), help="Reuse a full ragCorpora resource name")
    parser.add_argument("--display-name", default="trustedsql-demo-university-documents")
    parser.add_argument("--embedding-model", default="text-embedding-005")
    parser.add_argument("--chunk-size", type=int, default=512)
    parser.add_argument("--chunk-overlap", type=int, default=100)
    parser.add_argument("--max-embedding-rpm", type=int, default=900)
    parser.add_argument("--workers", type=int, default=4, help="Concurrent direct-upload workers")
    return parser.parse_args()


def main() -> int:
    load_env_file(REPO_ROOT / ".env")
    args = _args()
    if not args.project:
        raise SystemExit("--project or VERTEX_RAG_PROJECT_ID is required")
    source_dir = args.source_dir.expanduser().resolve()
    files = sorted(source_dir.rglob("*.md"))
    if not files:
        raise SystemExit(f"No Markdown documents found under {source_dir}")
    if not 64 <= args.chunk_size <= 2_048 or not 0 <= args.chunk_overlap < args.chunk_size:
        raise SystemExit("chunk size/overlap is invalid")
    if not 1 <= args.workers <= 64:
        raise SystemExit("--workers must be between 1 and 64")

    try:
        import agentplatform
        from agentplatform import types
    except ImportError as exc:
        raise SystemExit(
            "Install the main TrustedSQL requirements before provisioning the corpus"
        ) from exc

    client = agentplatform.Client(project=args.project, location=args.location)
    corpus_name = _resolve_corpus(client, types, args)

    if args.bucket:
        from google.cloud import storage
        from google.genai import types as genai_types

        bucket = storage.Client(project=args.project).bucket(args.bucket)
        if not bucket.exists():
            raise SystemExit(f"GCS bucket does not exist or is not accessible: {args.bucket}")
        for uploaded, path in enumerate(files, start=1):
            relative = path.relative_to(source_dir).as_posix()
            bucket.blob(f"{args.prefix.strip('/')}/{relative}").upload_from_filename(
                path, content_type="text/markdown"
            )
            if uploaded % 100 == 0 or uploaded == len(files):
                print(f"Uploaded {uploaded}/{len(files)} Markdown files to GCS", flush=True)

        client.rag.import_files(
            name=corpus_name,
            import_config=types.ImportRagFilesConfig(
                gcs_source=genai_types.GcsSource(
                    uris=[f"gs://{args.bucket}/{args.prefix.strip('/')}/**"]
                ),
                rag_file_transformation_config=types.RagFileTransformationConfig(
                    rag_file_chunking_config=types.RagFileChunkingConfig(
                        chunk_size=args.chunk_size,
                        chunk_overlap=args.chunk_overlap,
                    )
                ),
                max_embedding_requests_per_min=args.max_embedding_rpm,
            ),
        )
        print("GCS import completed successfully", flush=True)
    else:
        upload_config = types.UploadRagFileConfig(
            rag_file_transformation_config=types.RagFileTransformationConfig(
                rag_file_chunking_config=types.RagFileChunkingConfig(
                    chunk_size=args.chunk_size,
                    chunk_overlap=args.chunk_overlap,
                )
            )
        )
        existing: set[str] = set()
        page_token: str | None = None
        while True:
            response = client.rag.list_files(
                name=corpus_name,
                config=types.ListRagFilesConfig(page_size=1_000, page_token=page_token),
            )
            existing.update(item.display_name for item in response.rag_files if item.display_name)
            page_token = response.next_page_token
            if not page_token:
                break

        pending = [path for path in files if path.relative_to(source_dir).as_posix() not in existing]
        print(
            f"Direct upload resume: {len(existing)} already indexed, {len(pending)} pending",
            flush=True,
        )
        local = threading.local()

        failures: list[tuple[str, str]] = []
        completed = len(existing)
        with tempfile.TemporaryDirectory(prefix="trustedsql-rag-upload-") as temporary:
            staging_dir = Path(temporary)
            upload_paths = {
                path: _upload_path(path, path.relative_to(source_dir).as_posix(), staging_dir)
                for path in pending
            }

            def upload(path: Path) -> str:
                if not hasattr(local, "client"):
                    local.client = agentplatform.Client(project=args.project, location=args.location)
                relative = path.relative_to(source_dir).as_posix()
                local.client.rag.upload_file(
                    corpus_name=corpus_name,
                    path=str(upload_paths[path]),
                    display_name=relative,
                    upload_rag_file_config=upload_config,
                )
                return relative

            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
                futures = {executor.submit(upload, path): path for path in pending}
                for future in concurrent.futures.as_completed(futures):
                    path = futures[future]
                    try:
                        future.result()
                        completed += 1
                    except Exception as exc:  # keep indexing independent files
                        failures.append((path.relative_to(source_dir).as_posix(), str(exc)))
                    processed = completed + len(failures)
                    if processed % 25 == 0 or processed == len(files):
                        print(
                            f"Processed {processed}/{len(files)}; indexed={completed}; failed={len(failures)}",
                            flush=True,
                        )
        if failures:
            for relative, error in failures[:20]:
                print(f"FAILED {relative}: {error}", flush=True)
            raise SystemExit(
                f"Direct upload incomplete: {len(failures)} failed. Re-run the same command to resume."
            )
        print("Direct upload completed successfully", flush=True)
    print(f"VERTEX_RAG_PROJECT_ID={args.project}")
    print(f"VERTEX_RAG_LOCATION={args.location}")
    print(f"VERTEX_RAG_CORPUS={corpus_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
