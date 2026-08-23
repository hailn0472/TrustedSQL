"""
Chat API Routes
Handles chat requests and streams responses from LangGraph agent
"""
import logging
import json
import sqlite3
from flask import Blueprint, request, Response, current_app, jsonify
from langchain_core.messages import HumanMessage

from .. import settings
from ..utils.logger import agent_logger
from ..utils.architecture import ArchitectureVisualizer

logger = logging.getLogger(__name__)

# Create chat blueprint
chat_bp = Blueprint('chat', __name__, url_prefix='/api')

# Node name mapping for user-friendly labels
NODE_LABELS = {
    "route_query": "Analyzing Request",
    "call_sql_agent": "Querying Database",
    "call_rag_agent": "Searching Documents",
    "generate_response": "Generating Answer"
}

# Primary nodes to track for thinking process
PRIMARY_NODES = {"route_query", "call_rag_agent", "call_sql_agent", "generate_response"}


@chat_bp.route('/architecture', methods=['GET'])
def get_architecture():
    """
    Get system architecture information
    
    Returns:
        JSON: Complete architecture information including components, workflow, nodes, tools
    """
    try:
        architecture_info = ArchitectureVisualizer.get_architecture_info()
        return jsonify(architecture_info), 200
    except Exception as e:
        logger.error(f"Error fetching architecture: {e}", exc_info=True)
        return jsonify({"error": "Could not fetch architecture information"}), 500


@chat_bp.route('/architecture/diagram', methods=['GET'])
def get_architecture_diagram():
    """
    Get architecture diagram in different formats
    
    Query Parameters:
        format: 'mermaid' or 'ascii' (default: 'mermaid')
    
    Returns:
        Text: Architecture diagram
    """
    try:
        diagram_format = request.args.get('format', 'mermaid')
        
        if diagram_format == 'ascii':
            diagram = ArchitectureVisualizer.get_ascii_diagram()
            return Response(diagram, mimetype='text/plain'), 200
        else:
            diagram = ArchitectureVisualizer.get_mermaid_diagram()
            return Response(diagram, mimetype='text/plain'), 200
    except Exception as e:
        logger.error(f"Error fetching diagram: {e}", exc_info=True)
        return jsonify({"error": "Could not fetch architecture diagram"}), 500


@chat_bp.route('/threads', methods=['GET'])
def get_threads():
    """
    Get all unique thread IDs from the checkpointer database.
    """
    try:
        conn = sqlite3.connect(settings.CHECKPOINT_DB_PATH)
        cursor = conn.cursor()
        
        # Query for distinct thread_ids
        # Note: The checkpoints table doesn't have a timestamp column,
        # so we can't order by time. We'll just return distinct thread_ids.
        cursor.execute(
            """SELECT DISTINCT thread_id 
               FROM checkpoints"""
        )
        
        threads = cursor.fetchall()
        conn.close()
        
        # Format the response as a list of objects with an 'id' key
        thread_list = [{"id": thread[0], "title": f"Chat {thread[0][:8]}..."} for thread in threads]
        
        return jsonify(thread_list), 200
        
    except Exception as e:
        logger.error(f"Error fetching threads: {e}", exc_info=True)
        return jsonify({"error": "Could not fetch chat history"}), 500


@chat_bp.route('/chat', methods=['POST'])
def chat():
    """
    Chat endpoint that receives messages and streams responses
    
    Request Body:
    {
        "message": str,      # User's message
        "thread_id": str     # Thread ID for conversation history
    }
    
    Response: Server-Sent Events (SSE) stream
    event: message
    data: {"token": "...", "done": false}
    
    event: done
    data: {"done": true}
    
    Returns:
        Response: SSE streaming response with status 200
        or JSON error response with appropriate status code
    """
    try:
        # Extract message and thread_id from request body
        try:
            data = request.get_json()
        except Exception as e:
            logger.error(f"Failed to parse JSON: {str(e)}")
            return jsonify({
                "error": "Invalid request",
                "message": "Request body must be valid JSON"
            }), 400
        
        if not data:
            logger.error("No JSON data in request")
            return jsonify({
                "error": "Invalid request",
                "message": "Request body must be JSON"
            }), 400
        
        message = data.get('message')
        thread_id = data.get('thread_id')
        
        # Validate required fields
        if not message:
            logger.error("Missing 'message' field in request")
            return jsonify({
                "error": "Invalid request",
                "message": "Field 'message' is required"
            }), 400
        
        if not thread_id:
            logger.error("Missing 'thread_id' field in request")
            return jsonify({
                "error": "Invalid request",
                "message": "Field 'thread_id' is required"
            }), 400
        
        logger.info(f"Received chat request - thread_id: {thread_id}, message: {message[:50]}...")
        
        # Get agent from app config
        agent = current_app.config.get('AGENT')
        
        if not agent:
            logger.error("Agent not initialized in app config")
            return jsonify({
                "error": "Server error",
                "message": "Chat agent is not available"
            }), 500
        
        # Create agent input
        agent_input = {
            "messages": [HumanMessage(content=message)]
        }
        
        # Create agent config with thread_id for checkpointing
        agent_config = {
            "configurable": {"thread_id": thread_id}
        }
        
        # Define SSE streaming generator
        def generate():
            """
            Generator function that streams agent response as SSE
            Uses stream API to capture node transitions and message updates
            """
            try:
                # Start logging session
                agent_logger.start_session(thread_id, message)
                
                logger.info(f"Streaming events for thread {thread_id}...")
                
                emitted_nodes = set()
                previous_content = ""
                
                # Use stream with "updates" mode to get node-by-node updates
                for chunk in agent.stream(agent_input, agent_config, stream_mode="updates"):
                    # chunk is a dict with node names as keys
                    for node_name, node_output in chunk.items():
                        # Emit log event when entering a new primary node
                        if node_name in PRIMARY_NODES and node_name not in emitted_nodes:
                            emitted_nodes.add(node_name)
                            
                            # Collect additional metadata from agent_logger session
                            metadata = {}
                            if agent_logger.current_session:
                                # For route_query node: include routing decision
                                if node_name == "route_query":
                                    routing = agent_logger.current_session.get("routing_decision")
                                    if routing:
                                        metadata["routing"] = {
                                            "route": routing.get("route"),
                                            "reasoning": routing.get("reasoning")
                                        }
                                
                                # For call_sql_agent node: include SQL tool details
                                elif node_name == "call_sql_agent":
                                    sql_details = agent_logger.current_session.get("sql_details", [])
                                    if sql_details:
                                        metadata["sql_tools"] = sql_details
                                
                                # For call_rag_agent node: include RAG retrieval details
                                elif node_name == "call_rag_agent":
                                    rag_retrieval = agent_logger.current_session.get("rag_retrieval")
                                    if rag_retrieval:
                                        metadata["rag"] = {
                                            "num_docs": rag_retrieval.get("num_docs"),
                                            "sources": rag_retrieval.get("sources", [])
                                        }
                            
                            log_data = json.dumps({
                                "step": node_name,
                                "status": "processing",
                                "metadata": metadata
                            })
                            yield f"event: log\ndata: {log_data}\n\n"
                            logger.info(f"✅ Emitted log event for node: {node_name}")  # Changed to INFO for visibility
                            
                            # Note: We don't emit separate events for tool nodes (sql_tools, retrieve_context)
                            # because they would immediately override the agent node's active state.
                            # Instead, the WorkflowDiagram will automatically highlight tool nodes
                            # based on the agent node being in the execution path.
                        
                        # Extract and stream message content from generate_response node
                        if node_name == "generate_response" and "messages" in node_output:
                            messages = node_output["messages"]
                            if messages:
                                last_message = messages[-1]
                                if hasattr(last_message, "content"):
                                    current_content = last_message.content
                                    
                                    # Stream the new content that was added
                                    if current_content and len(current_content) > len(previous_content):
                                        new_token = current_content[len(previous_content):]
                                        previous_content = current_content
                                        
                                        # Stream character by character for better UX
                                        for char in new_token:
                                            message_data = json.dumps({
                                                "token": char,
                                                "done": False
                                            })
                                            yield f"event: message\ndata: {message_data}\n\n"
                
                # End logging session
                if previous_content:
                    agent_logger.end_session(previous_content)
                else:
                    agent_logger.end_session()
                
                # Send done event
                done_data = json.dumps({"done": True})
                yield f"event: done\ndata: {done_data}\n\n"
                
                logger.info(f"✓ Successfully streamed response for thread {thread_id}")
                
            except Exception as e:
                agent_logger.log_error(e, f"Chat endpoint - thread {thread_id}")
                agent_logger.end_session()
                
                # Check if it's a rate limit error
                error_str = str(e).lower()
                if "429" in error_str or "quota" in error_str or "rate limit" in error_str:
                    logger.error(f"⚠️ Rate limit exceeded for thread {thread_id}: {e}")
                    error_message = "⚠️ API Rate Limit Exceeded. The system has reached its quota limit. Please try again in a few moments or check your API plan."
                else:
                    logger.error(f"Error during agent streaming: {str(e)}", exc_info=True)
                    error_message = "An error occurred while processing your question. Please try again."
                
                # Send error event
                error_data = json.dumps({
                    "error": error_message,
                    "done": True
                })
                yield f"event: error\ndata: {error_data}\n\n"
        
        # Return SSE response
        return Response(
            generate(),
            mimetype='text/event-stream',
            headers={
                'Cache-Control': 'no-cache',
                'X-Accel-Buffering': 'no',  # Disable buffering in nginx
                'Connection': 'keep-alive'
            }
        )
        
    except Exception as e:
        logger.error(f"Error in chat endpoint: {str(e)}", exc_info=True)
        return jsonify({
            "error": "Server error",
            "message": "An unexpected error occurred"
        }), 500


@chat_bp.errorhandler(Exception)
def handle_chat_error(error):
    """
    Global error handler for chat blueprint
    
    Args:
        error: Exception that was raised
        
    Returns:
        JSON error response
    """
    logger.error(f"Unhandled error in chat blueprint: {str(error)}", exc_info=True)
    
    return jsonify({
        "error": "Server error",
        "message": "An unexpected error occurred",
        "details": str(error) if current_app.config.get('DEBUG') else None
    }), 500
