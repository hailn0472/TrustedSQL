"""
LangGraph Builder
Constructs and compiles the agent graph with all nodes and edges
"""
import logging
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver

from .state import AgentState
from .nodes import route_query, call_rag_agent, call_sql_agent, generate_response
from .. import settings

logger = logging.getLogger(__name__)


def route_decision(state: AgentState) -> str:
    """
    Conditional edge function to decide the routing.

    Reads the routing decision from the state and returns the name of the next node.

    Args:
        state: The current agent state.

    Returns:
        'rag' -> directs to the RAG node.
        'sql' -> directs to the SQL node.
        'general' -> directs to the Generate Response node.
    """
    messages = state["messages"]
    
    # Find the routing decision in messages
    for msg in reversed(messages):
        if "[ROUTING]" in msg.content:
            content = msg.content.upper()
            
            if "RAG" in content and "DECISION: RAG" in content:
                logger.info("Routing to RAG node")
                return "rag"
            elif "SQL" in content and "DECISION: SQL" in content:
                logger.info("Routing to SQL node")
                return "sql"
            elif "GENERAL" in content and "DECISION: GENERAL" in content:
                logger.info("Routing to General (generate response)")
                return "general"
    
    # Default to general if no clear decision
    logger.warning("No clear routing decision found, defaulting to general")
    return "general"


def build_agent(checkpointer):
    """
    Builds and compiles the LangGraph agent.

    Flow:
    1. Initialize StateGraph with AgentState.
    2. Add nodes: route_query, call_rag_agent, call_sql_agent, generate_response.
    3. Set entry point: route_query.
    4. Add conditional edges from route_query.
    5. Add edges from RAG/SQL nodes to generate_response.
    6. Set finish point: generate_response.
    7. Compile with a SqliteSaver checkpointer.
    8. Return the compiled graph.

    Returns:
        The compiled LangGraph agent.
    """
    logger.info("Building LangGraph agent...")
    
    # 1. Initialize StateGraph
    workflow = StateGraph(AgentState)
    
    # 2. Add nodes
    logger.info("Adding nodes to graph...")
    workflow.add_node("route_query", route_query)
    workflow.add_node("call_rag_agent", call_rag_agent)
    workflow.add_node("call_sql_agent", call_sql_agent)
    workflow.add_node("generate_response", generate_response)
    
    # 3. Set entry point
    workflow.set_entry_point("route_query")
    
    # 4. Add conditional edges from route_query
    logger.info("Adding conditional edges...")
    workflow.add_conditional_edges(
        "route_query",
        route_decision,
        {
            "rag": "call_rag_agent",
            "sql": "call_sql_agent",
            "general": "generate_response"
        }
    )
    
    # 5. Add edges from RAG/SQL nodes to generate_response
    workflow.add_edge("call_rag_agent", "generate_response")
    workflow.add_edge("call_sql_agent", "generate_response")
    
    # 6. Set finish point
    workflow.add_edge("generate_response", END)
    
    # 7. Compile with checkpointer
    logger.info("Compiling graph with checkpointer...")
    
    
    # Compile the graph
    app = workflow.compile(checkpointer=checkpointer)
    
    logger.info("✓ LangGraph agent built successfully!")
    
    return app


def visualize_graph(app):
    """
    Visualize the graph structure (for debugging)
    
    Args:
        app: Compiled LangGraph app
    """
    try:
        # Try to get graph visualization
        graph_image = app.get_graph().draw_mermaid()
        print("\nGraph Structure (Mermaid):")
        print(graph_image)
    except Exception as e:
        logger.warning(f"Could not visualize graph: {e}")


# Example usage
if __name__ == "__main__":
    print("=" * 60)
    print("Building LangGraph Agent")
    print("=" * 60)
    
    try:
        # Build agent
        with SqliteSaver.from_conn_string(settings.CHECKPOINT_DB_PATH) as checkpointer:
            app = build_agent(checkpointer)
        
        print("\n✓ Agent built successfully!")
        print(f"  - Nodes: route_query, call_rag_agent, call_sql_agent, generate_response")
        print(f"  - Entry point: route_query")
        print(f"  - Checkpointer: {settings.CHECKPOINT_DB_PATH}")
        
        # Try to visualize
        visualize_graph(app)
        
        # Test invoke
        print("\n" + "=" * 60)
        print("Testing Agent Invocation")
        print("=" * 60)
        
        from langchain_core.messages import HumanMessage
        
        test_input = {
            "messages": [HumanMessage(content="Xin chào!")]
        }
        
        config_dict = {
            "configurable": {"thread_id": "test-thread-1"}
        }
        
        print("\nInvoking agent with test message...")
        result = app.invoke(test_input, config_dict)
        
        print("\n✓ Agent invocation successful!")
        print(f"  - Messages in state: {len(result['messages'])}")
        print(f"  - Final response: {result['messages'][-1].content[:100]}...")
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()

