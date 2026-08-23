"""
LangGraph Tools
Defines RAG and SQL tools for the agent
"""
import logging
import time
from langchain_core.tools import tool, Tool
from langchain_community.agent_toolkits import SQLDatabaseToolkit
from langchain_google_genai import ChatGoogleGenerativeAI

from ..services.file_search_service import FileSearchService, FileSearchStoreNotFoundError
from ..services.sql_service import SQLService
from .. import settings
from ..utils.logger import agent_logger

logger = logging.getLogger(__name__)


@tool
def retrieve_context(query: str) -> str:
    """Retrieve information from Google File Search to help answer a query."""
    logger.info(f"Retrieving context for query: {query[:100]}...")
    start_time = time.time()
    
    try:
        # Initialize FileSearchService
        file_search_service = FileSearchService(
            file_search_store_name=settings.FILE_SEARCH_STORE_NAME,
            model=settings.LLM_MODEL
        )
        file_search_service.initialize_client()
        
        # Search using File Search API
        result = file_search_service.search(query, k=4)
        
        # Extract context and citations
        context = result['context']
        citations = result['citations']
        
        # Extract sources and contexts for logging
        sources = [citation['title'] for citation in citations]
        contexts = [citation['preview'] for citation in citations]
        
        # Log RAG retrieval with context previews
        agent_logger.log_rag_retrieval(query, len(citations), sources, contexts)
        
        # Format response for LLM
        if not context:
            logger.warning("No context retrieved from File Search")
            return "No relevant information found in the document store."
        
        # Build formatted response with context and citations
        formatted_response = f"Context:\n{context}\n"
        
        if citations:
            formatted_response += "\nCitations:\n"
            for i, citation in enumerate(citations, 1):
                formatted_response += f"{i}. {citation['title']}\n"
        
        elapsed_time = time.time() - start_time
        logger.info(
            f"Retrieved {len(citations)} chunks from File Search in {elapsed_time:.3f}s"
        )
        
        return formatted_response
        
    except FileSearchStoreNotFoundError as e:
        error_msg = f"File Search Store not found. Please ensure the store is created and FILE_SEARCH_STORE_NAME is correctly configured."
        agent_logger.log_tool_call("retrieve_context", query, error=e)
        logger.error(f"File Search Store error: {e}")
        return f"Error: {error_msg}"
        
    except Exception as e:
        error_msg = "Could not retrieve context from File Search. Please try again or contact support if the issue persists."
        agent_logger.log_tool_call("retrieve_context", query, error=e)
        logger.error(f"Error during context retrieval: {e}")
        return f"Error: {error_msg}"


def create_sql_tools(sql_service: SQLService, llm: ChatGoogleGenerativeAI):
    """
    Creates a toolkit with multiple SQL tools for the agent.

    Uses SQLDatabaseToolkit to create a suite of tools:
    - sql_db_query
    - sql_db_schema
    - sql_db_list_tables
    - sql_db_query_checker

    Args:
        sql_service: An instance of SQLService.
        llm: The language model to be used by the toolkit.

    Returns:
        A list of tools for interacting with the SQL database.
    """
    logger.info("Creating SQL tools...")
    
    try:
        # Get SQLDatabase object from the service
        sql_database = sql_service.get_sql_database()
        
        # Use SQLDatabaseToolkit to get all tools including query checker
        # This should work now with langchain.agents.create_agent
        toolkit = SQLDatabaseToolkit(
            db=sql_database, 
            llm=llm
        )
        
        # Get the tools from the toolkit
        tools = toolkit.get_tools()
        
        # Log tool names
        for tool in tools:
            logger.info(f"  - {tool.name}")
        
        logger.info(f"✓ {len(tools)} SQL tools created successfully")
        return tools
        
    except Exception as e:
        logger.error(f"Failed to create SQL tools: {e}")
        # Fallback to manual tool creation without query checker
        logger.warning("Falling back to manual tool creation without query checker")
        from langchain_community.tools.sql_database.tool import (
            InfoSQLDatabaseTool,
            ListSQLDatabaseTool,
            QuerySQLDatabaseTool,
        )
        
        sql_database = sql_service.get_sql_database()
        tools = [
            QuerySQLDatabaseTool(db=sql_database),
            InfoSQLDatabaseTool(db=sql_database),
            ListSQLDatabaseTool(db=sql_database),
        ]
        
        logger.info(f"✓ {len(tools)} SQL tools created successfully (fallback mode)")
        return tools


def get_tools(sql_service: SQLService):
    """
    Get all tools for the agent
    
    Args:
        sql_service: SQLService instance
        
    Returns:
        List of tools [retrieve_context, sql_tools...]
    """
    logger.info("Initializing all tools...")
    
    tools = []
    
    # Add retrieve_context tool (File Search)
    try:
        tools.append(retrieve_context)
        logger.info("✓ File Search tool (retrieve_context) added")
    except Exception as e:
        logger.warning(f"File Search tool not available: {e}")
    
    # Create SQL tools
    try:
        llm = ChatGoogleGenerativeAI(model=settings.LLM_MODEL)
        sql_tools = create_sql_tools(sql_service, llm)
        tools.extend(sql_tools)
        logger.info(f"✓ {len(sql_tools)} SQL tools added")
    except Exception as e:
        logger.warning(f"SQL tools not available: {e}")
    
    logger.info(f"✓ Initialized {len(tools)} tools total")
    return tools


# Example usage
if __name__ == "__main__":
    from .. import settings
    
    print("Testing tool creation...")
    
    # Test File Search tool (retrieve_context)
    try:
        print(f"✓ File Search Tool: {retrieve_context.name}")
        print(f"  Description: {retrieve_context.description[:100]}...")
        
        # Test with a sample query
        test_query = "What information is available?"
        print(f"\n  Testing with query: {test_query}")
        result = retrieve_context.invoke(test_query)
        print(f"  Result preview: {result[:200]}...")
    except Exception as e:
        print(f"✗ File Search Tool: {e}")
    
    # Test SQL tools creation
    try:
        sql_service = SQLService(settings.DATABASE_URI)
        llm = ChatGoogleGenerativeAI(model=settings.LLM_MODEL)
        sql_tools = create_sql_tools(sql_service, llm)
        print(f"\n✓ SQL Tools: {len(sql_tools)} tools created")
        for tool in sql_tools:
            print(f"  - {tool.name}")
    except Exception as e:
        print(f"\n✗ SQL Tools: {e}")
    
    print("\n✓ Tool creation test completed!")

