from demo.backend.app.rag import CHAT_ROUTE, DATABASE_ROUTE, DOCUMENT_ROUTE, KnowledgeQueryRouter


def _route_sequence(queries: list[str]) -> list[str]:
    history: list[dict[str, str]] = []
    branches: list[str] = []
    for query in queries:
        route = KnowledgeQueryRouter.classify(query, history)
        branches.append(route.branch)
        history.append({"nlq": query, "route_type": route.branch})
    return branches


def test_curated_multiturn_sequence_stays_on_database_branch() -> None:
    queries = [
        "Count the available application types by category.",
        "For each application category, show the minimum and maximum type name alphabetically, including first type name and last type name.",
        "For my own submitted applications, count records by status.",
        "For the category with the most application types, show who processed every student's applications and include the staff accounts involved.",
    ]

    assert _route_sequence(queries) == [DATABASE_ROUTE] * 4


def test_schema_entities_and_relational_follow_up_route_to_database() -> None:
    queries = [
        "Show my account status and campus name.",
        "Show my student code, batch, and major name.",
        "Show the department code and department name for that major.",
        "Show the internal access-right definitions for student and admin accounts.",
    ]

    assert _route_sequence(queries) == [DATABASE_ROUTE] * 4
    follow_up = KnowledgeQueryRouter.classify(
        "Show that again.",
        [{"nlq": "Show my student record.", "route_type": DATABASE_ROUTE}],
    )
    assert follow_up.branch == DATABASE_ROUTE


def test_router_keeps_document_and_ordinary_chat_branches_isolated() -> None:
    assert KnowledgeQueryRouter.classify("Hello, how are you?").branch == CHAT_ROUTE
    assert KnowledgeQueryRouter.classify("What is the tuition policy for this year?").branch == DOCUMENT_ROUTE
