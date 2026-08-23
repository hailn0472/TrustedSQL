"""
LangGraph Agent State Definitions
Defines the state structure and routing decision model for the agent
"""
from typing import TypedDict, Annotated, Literal
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class AgentState(TypedDict):
    """
    The state of the agent that is passed between nodes.

    Attributes:
        messages: A list of conversation messages (HumanMessage, AIMessage, ToolMessage).
                  Uses the add_messages reducer to append new messages.
        rag_context: Optional context retrieved from File Search API, including citations.
    """
    messages: Annotated[list, add_messages]
    # add_messages is a reducer function that automatically appends messages to the list.
    # It intelligently handles merging messages.
    rag_context: str | None  # Context from File Search API with citations


class RouteDecision(BaseModel):
    """
    Structured output from the LLM for the routing decision.

    The LLM will analyze the question and return one of three routes:
    - RAG: For questions about information in documents.
    - SQL: For questions that require querying the database.
    - General: For general questions that don't require data retrieval.
    """
    route: Literal["RAG", "SQL", "General"] = Field(
        description=(
            "The type of query to determine how to proceed:\n"
            "- RAG: For questions about information in documents (unstructured data).\n"
            "- SQL: For questions that require querying a database (structured data).\n"
            "- General: For general questions, greetings, or questions that don't require data retrieval."
        )
    )
    
    reasoning: str = Field(
        description="The reason for choosing this route. A brief explanation of the routing decision."
    )


# Example usage and documentation
if __name__ == "__main__":
    from langchain_core.messages import HumanMessage, AIMessage
    
    # Example 1: Creating initial state
    initial_state = AgentState(
        messages=[
            HumanMessage(content="Hello!")
        ]
    )
    print("Initial state:", initial_state)
    
    # Example 2: RouteDecision for RAG
    rag_decision = RouteDecision(
        route="RAG",
        reasoning="The question is about information in the FAQ documents."
    )
    print("\nRAG Decision:", rag_decision.model_dump())
    
    # Example 3: RouteDecision for SQL
    sql_decision = RouteDecision(
        route="SQL",
        reasoning="The question requires querying data from the products table."
    )
    print("\nSQL Decision:", sql_decision.model_dump())
    
    # Example 4: RouteDecision for General
    general_decision = RouteDecision(
        route="General",
        reasoning="It's a greeting and doesn't require data retrieval."
    )
    print("\nGeneral Decision:", general_decision.model_dump())
    
    # Example 5: How add_messages works
    print("\n--- Demonstrating add_messages reducer ---")
    state1 = AgentState(messages=[HumanMessage(content="Hello")])
    print("State 1:", [m.content for m in state1["messages"]])
    
    # When we update state with new messages, add_messages appends them
    state2 = AgentState(
        messages=state1["messages"] + [AIMessage(content="Hi there!")]
    )
    print("State 2:", [m.content for m in state2["messages"]])
    
    print("\n✓ State definitions are working correctly!")

