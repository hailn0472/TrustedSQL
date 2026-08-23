# Interactive TrustedSQL + Vertex AI RAG demo

This directory contains an interactive document-and-database security demo.
Document questions are handled by Vertex AI RAG Engine; structured-data
questions use either the complete TrustedSQL pipeline or the Direct SQL
comparison path selected in the UI.

## Architecture

Conversation Memory is a bidirectional sidecar of the Orchestrator and remains
available to every branch:

```text
Chat -> Orchestrator <-> Conversation Memory
               |-> Vertex AI RAG -> grounded answer + sources
               |-> Policy Engine -> TrustedSQL -> Education DB
               `-> SQL Generator -> Education DB       (Direct SQL mode)
```

Direct SQL mode bypasses the policy and TrustedSQL modules, but it does not
disable Conversation Memory. The routing map stores evidence separately for
each chat turn and can switch between the routing view and the interactive M2
Intent GNN graph.

## Prerequisites

- Python 3.10 or newer.
- Node.js and npm for the React/Vite frontend.
- A reachable PostgreSQL education database containing the schema and demo data.
- A Google Cloud project with Vertex AI access and Application Default
  Credentials, or a service-account JSON credential file.
- A regional Vertex AI RAG corpus when the document branch is required.

Run all commands below from the TrustedSQL repository root unless a command
explicitly changes directory.

## Install

The main TrustedSQL environment includes both runtime and Vertex AI RAG
dependencies; a second RAG virtual environment is not required.

```bash
python -m pip install -e .

cd demo/frontend
npm install
cd ../..
```

Installing from `requirements.txt` is also supported:

```bash
python -m pip install -r requirements.txt
```

## Configure the demo

Create the repository-root environment file:

```bash
cp .env.example .env
```

At minimum, replace the placeholder values for the database, Vertex model
runtime, Google credentials, and RAG corpus:

```dotenv
# TrustedSQL data branch
TRUSTEDSQL_DATABASE_URL="postgresql+psycopg2://user:password@host:5432/database"
TRUSTEDSQL_VERTEX_PROJECT_ID="your-gcp-project-id"
TRUSTEDSQL_VERTEX_LOCATION="global"

# Google authentication: use an absolute path when using a service account
GOOGLE_APPLICATION_CREDENTIALS="/absolute/path/to/service-account.json"

# Vertex AI RAG document branch; location must be regional, not global
VERTEX_RAG_PROJECT_ID="your-gcp-project-id"
VERTEX_RAG_LOCATION="asia-southeast1"
VERTEX_RAG_CORPUS="projects/your-gcp-project-id/locations/asia-southeast1/ragCorpora/your-corpus-id"
VERTEX_RAG_MODEL="gemini-2.5-flash"
VERTEX_RAG_TOP_K="6"
VERTEX_RAG_DISTANCE_THRESHOLD="0.7"
```

`VERTEX_RAG_CORPUS` must belong to the configured project and location. The
backend automatically loads the root `.env` on startup; variables exported by
the shell or service launcher take precedence over values in the file.

If a credential file is not used, remove or comment out
`GOOGLE_APPLICATION_CREDENTIALS` in `.env`, then authenticate through Google
Application Default Credentials before starting the backend:

```bash
gcloud auth application-default login
```

## Configure or provision the Vertex AI RAG corpus

If a corpus already exists, copy its full resource name into
`VERTEX_RAG_CORPUS` and skip this section.

To create a corpus and directly upload `demo/RAG/md-sources/**/*.md`:

```bash
python demo/RAG/ingest_vertex_rag.py \
  --project "your-project-id" \
  --location "asia-southeast1"
```

To stage the documents through an existing Google Cloud Storage bucket:

```bash
python demo/RAG/ingest_vertex_rag.py \
  --project "your-project-id" \
  --location "asia-southeast1" \
  --bucket "your-existing-gcs-bucket"
```

The ingestion command automatically loads the root `.env`. It validates and
reuses `VERTEX_RAG_CORPUS` when configured; otherwise it searches the selected
project/location for exactly one corpus with the requested display name and
creates a corpus only when no match exists. Multiple same-name matches stop the
command and require an explicit `--corpus-name`, so reruns never select a corpus
arbitrarily. Direct uploads also skip already indexed display names. The command
prints the three `VERTEX_RAG_*` values to copy into `.env`. Provisioning is never
triggered by the live demo because it creates external Google Cloud resources.

## Run in development mode

Start the backend from the repository root:

```bash
python -m demo.backend.app.main \
  --provider-config configs/providers/gemini_25_flash.yaml
```

The backend listens on `http://127.0.0.1:8000`.

In a second terminal, start the frontend development server:

```bash
cd demo/frontend
npm run dev -- --host 127.0.0.1 --port 5173
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` and the SSE telemetry stream
to the backend on port 8000.

## Run as a single local server

Build the frontend once, then let the Python backend serve the generated
`demo/frontend/dist` directory together with the API:

```bash
cd demo/frontend
npm run build
cd ../..

python -m demo.backend.app.main \
  --provider-config configs/providers/gemini_25_flash.yaml
```

Open `http://127.0.0.1:8000`.

## Verify readiness

After starting the backend, inspect the bootstrap endpoint:

```bash
curl -s http://127.0.0.1:8000/api/bootstrap | python -m json.tool
```

A fully configured demo reports both top-level readiness and RAG readiness:

```json
{
  "ready": true,
  "rag": {
    "ready": true,
    "provider": "vertex_ai_rag_engine",
    "location": "asia-southeast1",
    "corpusConfigured": true
  }
}
```

If the UI shows Vertex AI RAG Engine as `NEUTRAL`, confirm that the root `.env`
contains matching `VERTEX_RAG_PROJECT_ID`, `VERTEX_RAG_LOCATION`, and
`VERTEX_RAG_CORPUS` values, then restart the backend. TrustedSQL data queries
can remain ready even when the independent RAG branch is not configured.

## Using the cockpit

- Type or paste text into the center chat; prompt-library cards never execute
  automatically.
- Document questions such as syllabus, tuition, or policy questions route to
  Vertex AI RAG and return expandable sources.
- Changing or identity-bound database questions route through the selected
  TrustedSQL or Direct SQL data mode.
- The Prompt Library searches all benchmark datasets by scenario ID or source
  filename and can filter Student/Lecturer scenarios.
- Use the Turn selector in Query Routing Map to inspect historical routing and
  M2 GNN evidence. Chat history is cleared only by Reset.

A RAG response is accepted only when Vertex returns at least one attributable
source in grounding metadata. The UI shows those sources below the answer and
reports that the Education DB was untouched.

`POST /api/runs` accepts only one new message and the opaque conversation ID
returned by the previous run (`null` starts a new conversation):

```json
{"message":"next user query","conversationId":"conversation-..."}
```

The prompt library is sample content, not an automatic runner. Queries execute
only after a user types or pastes text into the chat and presses Send. Prior
turn results are held by the backend as trusted history; they are not accepted
from the browser and are not executed again when a new turn arrives.

The Prompt Library can search all five read-only benchmark datasets by scenario
ID (for example `MT-MAL-120` or `ST-PI-042`) or dataset filename. Selecting a
search result loads only that record's user-query text and adds it as an
expandable library card. Only the `BENIGN`/`MALICIOUS` turn label is exposed for
demo guidance; ground-truth SQL and attack metadata are not sent to the browser,
and the runtime session identity remains Lecturer/User 1.

RAG citations are collapsed to a short source title by default. Expanding one
source reveals only that source's retrieved passage and document reference.

The live demo reads the parent schema, policy, benchmark, and runtime source
read-only. Runtime execution does not modify files under `src/`, `tests/`,
`configs/`, benchmark data, the root `pyproject.toml`, or `_bmad-output/`.

All generated configs and runtime artifacts belong under `demo/runs/`. Parent
resources may be inspected as inputs, but outputs must never be written back to
the parent codebase.
