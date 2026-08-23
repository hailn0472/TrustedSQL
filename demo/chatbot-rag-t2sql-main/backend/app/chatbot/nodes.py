"""
LangGraph Nodes
Implements the core logic for each node in the agent graph
"""
import logging
import re
import time
from datetime import datetime
from typing import Literal
from flask import request,has_request_context
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent

from .state import AgentState, RouteDecision
from .prompts import (
    format_routing_prompt,
    SQL_AGENT_SYSTEM_PROMPT,
    RESPONSE_GENERATION_SYSTEM_PROMPT
)
from .. import settings
from ..utils.logger import agent_logger, log_node_execution

logger = logging.getLogger(__name__)

'''
# Initialize LLM with retry configuration
llm = ChatGoogleGenerativeAI(
    model=settings.LLM_MODEL,
    temperature=settings.LLM_TEMPERATURE,
    google_api_key=settings.GOOGLE_API_KEY,
    max_retries=0,  # Disable retry on rate limit
    timeout=30  # 30 second timeout
)

# Initialize LLM for SQL (with temperature=0 for deterministic output)
llm_sql = ChatGoogleGenerativeAI(
    model=settings.LLM_MODEL,
    temperature=0,  # Deterministic for SQL generation
    google_api_key=settings.GOOGLE_API_KEY,
    max_retries=0,  # Disable retry on rate limit
    timeout=30  # 30 second timeout
)
'''
def get_dynamic_llm(temperature=None):
    """
    Tự động lấy Key từ Header (Auto-run) hoặc Settings (Mặc định)
    """
    current_api_key = settings.GOOGLE_API_KEY
    
    if has_request_context():
        header_key = request.headers.get('x-gemini-api-key')
        if header_key:
            current_api_key = header_key

    return ChatGoogleGenerativeAI(
        model=settings.LLM_MODEL,
        temperature=temperature if temperature is not None else settings.LLM_TEMPERATURE,
        google_api_key=current_api_key,
        max_retries=0,
        timeout=30
    )
@log_node_execution("route_query")
def route_query(state: AgentState) -> AgentState:
    """
    First node - analyzes the question and decides on routing.

    Flow:
    1. Get the last message from the state.
    2. Call the LLM with structured output (RouteDecision).
    3. Add the routing decision to the state.
    4. Return the state.

    Args:
        state: The current agent state.

    Returns:
        The updated state with the routing decision.
    """
    try:
        llm = get_dynamic_llm()
        messages = state["messages"]
        last_message = messages[-1]
        
        logger.info(f"Analyzing query: {last_message.content[:100]}...")
        
        # Use prompt from prompts.py
        routing_prompt = format_routing_prompt(last_message.content)
        
        # Get structured output from LLM
        start_time = time.time()
        structured_llm = llm.with_structured_output(RouteDecision)
        decision = structured_llm.invoke(routing_prompt)
        llm_duration = time.time() - start_time
        
        # Log LLM call
        agent_logger.log_llm_call(
            model=settings.LLM_MODEL,
            prompt_length=len(routing_prompt),
            response_length=len(str(decision)),
            duration=llm_duration
        )
        
        # Log routing decision
        agent_logger.log_routing_decision(decision.route, decision.reasoning)
        
        logger.info(f"Routing decision: {decision.route}")
        logger.info(f"Reasoning: {decision.reasoning}")
        
        # Add decision to state as a system message
        decision_message = SystemMessage(
            content=f"[ROUTING] Decision: {decision.route} | Reason: {decision.reasoning}"
        )
        
        return {"messages": [decision_message]}
    
    except Exception as e:
        # Check if it's a rate limit error (429)
        error_str = str(e).lower()
        if "429" in error_str or "quota" in error_str or "rate limit" in error_str:
            agent_logger.log_error(e, "route_query node - RATE LIMIT")
            logger.error(f"⚠️ Rate limit exceeded: {e}")
            error_message = AIMessage(
                content="⚠️ API Rate Limit Exceeded. The system has reached its quota limit. Please try again in a few moments or check your API plan."
            )
        else:
            agent_logger.log_error(e, "route_query node")
            logger.error(f"Error in route_query: {e}")
            error_message = AIMessage(
                content=f"Sorry, I encountered an error while analyzing the question: {str(e)}"
            )
        return {"messages": [error_message]}


@log_node_execution("call_rag_agent")
def call_rag_agent(state: AgentState) -> AgentState:
    """
    Node that retrieves context from File Search API.

    This node directly calls the retrieve_context tool to get relevant information
    from the File Search Store and stores it in the state for use by generate_response.

    Args:
        state: The current agent state.

    Returns:
        The updated state with rag_context containing retrieved information and citations.
    """
    try:
        messages = state["messages"]
        user_query = None
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                user_query = msg.content
                break
        
        if not user_query:
            raise ValueError("No user query found in messages")

        logger.info(f"Retrieving context from File Search for: {user_query[:100]}...")

        # Import tool here to avoid circular dependencies
        from .tools import retrieve_context

        # Call retrieve_context tool directly
        start_time = time.time()
        context_result = retrieve_context.invoke(user_query)
        retrieval_duration = time.time() - start_time

        logger.info(f"Context retrieved in {retrieval_duration:.3f}s")
        logger.info(f"Context preview: {context_result[:200]}...")

        # Store context in state for generate_response to use
        # Also add a system message for logging/debugging
        rag_message = SystemMessage(
            content=f"[RAG CONTEXT RETRIEVED]\nQuery: {user_query[:60]}...\nContext length: {len(context_result)} chars"
        )
        
        return {
            "messages": [rag_message],
            "rag_context": context_result
        }

    except Exception as e:
        agent_logger.log_error(e, "call_rag_agent node")
        logger.error(f"Error in call_rag_agent: {e}", exc_info=True)
        error_message = AIMessage(
            content=f"Sorry, I encountered an error while retrieving information: {str(e)}"
        )
        return {
            "messages": [error_message],
            "rag_context": None
        }


@log_node_execution("call_sql_agent")
def call_sql_agent(state: AgentState) -> AgentState:
    """
    Node that invokes a sub-agent to interact with the SQL database.

    This node creates a new agent using LangChain's `create_agent` function,
    equipped with a specialized toolkit for SQL operations. This sub-agent
    can autonomously decide which SQL tools to use (e.g., list tables,
    check schema, execute queries) to answer the user's question.

    Args:
        state: The current agent state.

    Returns:
        The updated state with the SQL agent's final answer.
    """
    try:
        llm_sql = get_dynamic_llm(temperature=0)
        messages = state["messages"]
        user_query = None
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                user_query = msg.content
                break
        
        if not user_query:
            raise ValueError("No user query found in messages")

        logger.info(f"Invoking SQL agent for: {user_query[:100]}...")
        agent_logger.log_sql_generation("Starting SQL Agent", f"Query: {user_query[:60]}")

        # Import services and tools here to avoid circular dependencies
        from ..services.sql_service import SQLService
        from .tools import create_sql_tools
        from .prompts import SQL_AGENT_SYSTEM_PROMPT

        # Initialize services and tools
        sql_service = SQLService(settings.DATABASE_URI)
        sql_tools = create_sql_tools(sql_service, llm_sql)
        db = sql_service.get_sql_database()

        # Create the SQL agent
        # Format the system prompt with the dialect and top_k value
        system_prompt = SQL_AGENT_SYSTEM_PROMPT.format(
            dialect=db.dialect,
            top_k=5
        )

        # Create the SQL agent
        sql_agent_runnable = create_agent(
            model=llm_sql,
            tools=sql_tools,
            system_prompt=system_prompt
        )

        # Invoke the agent
        start_time = time.time()
        response = sql_agent_runnable.invoke({
            "messages": [HumanMessage(content=user_query)]
        })
        agent_duration = time.time() - start_time

        # Extract and log tool calls from messages
        tool_count = 0
        
        for msg in response['messages']:
            # Log tool calls
            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                for tool_call in msg.tool_calls:
                    tool_name = tool_call.get('name', 'unknown')
                    tool_input = str(tool_call.get('args', {}))[:80]
                    agent_logger.log_sql_generation(f"Tool: {tool_name}", tool_input)
                    tool_count += 1
            
            # Log tool results
            if hasattr(msg, 'name') and msg.name:
                tool_name = msg.name
                tool_output = str(msg.content)[:150]
                agent_logger.log_sql_generation(f"Result: {tool_name}", tool_output)
                
                # Special handling for SQL query results
                if tool_name == "sql_db_query" and msg.content:
                    # Try to extract and log SQL query details
                    if not msg.content.startswith("Error"):
                        agent_logger.log_sql_generation("Query executed successfully", f"Results: {tool_output}")
        
        # Log tool count to session
        if agent_logger.current_session and tool_count > 0:
            for _ in range(tool_count):
                agent_logger.current_session["tools_called"].append({
                    "tool": "sql_tools",
                    "timestamp": datetime.now().isoformat(),
                    "success": True
                })

        # Extract the final response and add it to the state
        final_response = response['messages'][-1].content
        logger.info(f"SQL agent final response: {final_response[:200]}...")
        logger.info(f"SQL agent execution time: {agent_duration:.3f}s")
        
        # Log final SQL agent result
        agent_logger.log_sql_generation("SQL Agent Completed", f"{agent_duration:.2f}s | Response: {final_response[:80]}")

        sql_message = SystemMessage(
            content=f"[SQL RESULTS]\n{final_response}"
        )
        
        return {"messages": [sql_message]}

    except Exception as e:
        agent_logger.log_error(e, "call_sql_agent node")
        logger.error(f"Error in call_sql_agent: {e}", exc_info=True)
        error_message = AIMessage(
            content=f"Sorry, I encountered an error while processing your database question: {str(e)}"
        )
        return {"messages": [error_message]}


@log_node_execution("generate_response")
def generate_response(state: AgentState) -> AgentState:
    """
    Final node - generates a response for the user.

    Flow:
    1. Get the entire conversation history from the state.
    2. Check if rag_context is available and include it in the prompt.
    3. Call the LLM to generate a response (can be streamed).
    4. Add the response to the state messages.
    5. Return the state.

    Args:
        state: The current agent state.

    Returns:
        The updated state with the final response.
    """
    try:
        llm = get_dynamic_llm()
        messages = state["messages"]
        rag_context = state.get("rag_context")
        
        # Use system prompt from prompts.py
        system_message = SystemMessage(content=RESPONSE_GENERATION_SYSTEM_PROMPT)
        
        # If RAG context is available, add it to the prompt
        if rag_context:
            logger.info(f"Including RAG context in response generation ({len(rag_context)} chars)")
            
            # Extract user query from messages
            user_query = None
            for msg in reversed(messages):
                if isinstance(msg, HumanMessage):
                    user_query = msg.content
                    break
            
            # Create enhanced prompt with RAG context
            from .prompts import format_response_with_rag_prompt
            enhanced_prompt = format_response_with_rag_prompt(
                query=user_query or "the question",
                rag_context=rag_context
            )
            
            # Add enhanced prompt as a system message
            context_message = SystemMessage(content=enhanced_prompt)
            llm_messages = [system_message, context_message] + messages
        else:
            # No RAG context, use standard prompt
            llm_messages = [system_message] + messages
        
        # Generate response
        start_time = time.time()
        response = llm.invoke(llm_messages)
        llm_duration = time.time() - start_time
        
        # Log LLM call
        total_prompt_length = sum(len(str(m.content)) for m in llm_messages)
        agent_logger.log_llm_call(
            model=settings.LLM_MODEL,
            prompt_length=total_prompt_length,
            response_length=len(response.content),
            duration=llm_duration
        )
        
        logger.info(f"Generated response: {response.content[:100]}...")
        
        return {"messages": [response]}
    
    except Exception as e:
        # Check if it's a rate limit error (429)
        error_str = str(e).lower()
        if "429" in error_str or "quota" in error_str or "rate limit" in error_str:
            agent_logger.log_error(e, "generate_response node - RATE LIMIT")
            logger.error(f"⚠️ Rate limit exceeded: {e}")
            error_message = AIMessage(
                content="⚠️ API Rate Limit Exceeded. The system has reached its quota limit. Please try again in a few moments or check your API plan."
            )
        else:
            agent_logger.log_error(e, "generate_response node")
            logger.error(f"Error in generate_response: {e}")
            error_message = AIMessage(
                content=f"Sorry, I encountered an error while generating the response: {str(e)}"
            )
        return {"messages": [error_message]}


# Example usage
if __name__ == "__main__":
    from langchain_core.messages import HumanMessage
    
    print("Testing nodes...")
    
    # Test route_query
    test_state = AgentState(
        messages=[HumanMessage(content="What is Smart Chatbot?")]
    )
    
    result = route_query(test_state)
    print(f"\n✓ Route Query: {result['messages'][-1].content}")
    
    print("\n✓ All nodes implemented successfully!")

