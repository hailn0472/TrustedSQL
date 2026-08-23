"""
Enhanced Logging System for Multi-Agent Chatbot
Provides detailed logging for all agent activities, tool calls, and system operations
"""
import logging
import json
import time
from datetime import datetime
from functools import wraps
from typing import Any, Dict, Optional
from pathlib import Path


class AgentLogger:
    """
    Centralized logger for tracking all agent activities
    """
    
    def __init__(self, log_file: str = "app.log", log_level: int = logging.INFO):
        """
        Initialize the agent logger
        
        Args:
            log_file: Path to log file
            log_level: Logging level (default: INFO)
        """
        self.logger = logging.getLogger("AgentLogger")
        self.logger.setLevel(log_level)
        
        # Prevent duplicate logs by clearing existing handlers
        self.logger.handlers.clear()
        self.logger.propagate = False
        
        # Create formatters
        # Detailed formatter for file
        detailed_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(name)-20s | %(funcName)-25s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        
        # Colorful formatter for console (more readable)
        console_formatter = logging.Formatter(
            '%(asctime)s | %(levelname)-8s | %(message)s',
            datefmt='%H:%M:%S'
        )
        
        # File handler - detailed logs
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed_formatter)
        
        # Console handler - show all important logs with clean format
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(console_formatter)
        
        # Add handlers
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        
        # Activity tracking
        self.current_session = None
        self.session_start_time = None
    
    def start_session(self, thread_id: str, user_message: str):
        """Start a new chat session"""
        self.current_session = {
            "thread_id": thread_id,
            "start_time": datetime.now().isoformat(),
            "user_message": user_message,
            "nodes_executed": [],
            "tools_called": [],
            "errors": [],
            "routing_decision": None,  # Store routing decision
            "rag_retrieval": None,  # Store RAG retrieval details
            "sql_details": []  # Store SQL tool call details
        }
        self.session_start_time = time.time()
        
        self.logger.info("=" * 80)
        self.logger.info(f"🚀 NEW SESSION | Thread: {thread_id[:12]}...")
        self.logger.info(f"📝 Query: {user_message[:70]}{'...' if len(user_message) > 70 else ''}")
        self.logger.info("=" * 80)
    
    def log_node_entry(self, node_name: str, state: Dict[str, Any]):
        """Log when entering a node"""
        self.logger.info(f"📍 NODE: {node_name} | Messages: {len(state.get('messages', []))}")
        
        if self.current_session:
            self.current_session["nodes_executed"].append({
                "node": node_name,
                "timestamp": datetime.now().isoformat(),
                "message_count": len(state.get('messages', []))
            })
    
    def log_node_exit(self, node_name: str, state: Dict[str, Any], duration: float):
        """Log when exiting a node"""
        self.logger.info(f"✅ DONE: {node_name} | Duration: {duration:.3f}s | Messages: {len(state.get('messages', []))}")
    
    def log_routing_decision(self, decision: str, reasoning: str):
        """Log routing decision"""
        self.logger.info(f"🔀 ROUTE: {decision} | {reasoning[:60]}{'...' if len(reasoning) > 60 else ''}")
        
        # Store routing decision in session
        if self.current_session:
            self.current_session["routing_decision"] = {
                "route": decision,
                "reasoning": reasoning,
                "timestamp": datetime.now().isoformat()
            }
    
    def log_tool_call(self, tool_name: str, tool_input: Any, tool_output: Any = None, error: Exception = None):
        """Log tool invocation"""
        input_str = str(tool_input)[:50] + ('...' if len(str(tool_input)) > 50 else '')
        
        if error:
            self.logger.error(f"🔧 TOOL: {tool_name} | ❌ Error: {str(error)[:50]}")
            if self.current_session:
                self.current_session["errors"].append({
                    "tool": tool_name,
                    "error": str(error),
                    "timestamp": datetime.now().isoformat()
                })
        elif tool_output:
            output_str = str(tool_output)[:50] + ('...' if len(str(tool_output)) > 50 else '')
            self.logger.info(f"🔧 TOOL: {tool_name} | Input: {input_str}")
        else:
            self.logger.info(f"🔧 TOOL: {tool_name} | Input: {input_str}")
        
        if self.current_session:
            self.current_session["tools_called"].append({
                "tool": tool_name,
                "timestamp": datetime.now().isoformat(),
                "success": error is None
            })
    
    def log_llm_call(self, model: str, prompt_length: int, response_length: int, duration: float):
        """Log LLM API call"""
        self.logger.info(f"🤖 LLM: {model} | Prompt: {prompt_length}ch | Response: {response_length}ch | {duration:.2f}s")
    
    def log_rag_retrieval(self, query: str, num_docs: int, sources: list, contexts: list = None):
        """Log RAG document retrieval with context preview"""
        sources_str = ', '.join(sources[:3]) + ('...' if len(sources) > 3 else '')
        self.logger.info(f"📚 RAG: Retrieved {num_docs} docs | Sources: {sources_str}")
        
        # Log context previews
        if contexts:
            self.logger.info(f"   📄 Context Previews:")
            for i, context in enumerate(contexts[:2], 1):  # Show first 2 contexts
                preview = context[:100].replace('\n', ' ') + ('...' if len(context) > 100 else '')
                self.logger.info(f"      {i}. {preview}")
        
        # Store RAG retrieval details in session
        if self.current_session:
            self.current_session["rag_retrieval"] = {
                "query": query,
                "num_docs": num_docs,
                "sources": sources[:5],  # Store first 5 sources
                "timestamp": datetime.now().isoformat()
            }
    
    def log_sql_generation(self, step: str, details: str):
        """Log SQL generation process steps"""
        self.logger.info(f"   🔧 {step}: {details[:80]}{'...' if len(details) > 80 else ''}")
        
        # Store SQL tool details in session
        if self.current_session and ("Tool:" in step or "Result:" in step or "Query executed" in step):
            self.current_session["sql_details"].append({
                "step": step,
                "details": details,
                "timestamp": datetime.now().isoformat()
            })
    
    def log_sql_query(self, query: str, results_count: int, execution_time: float, results_preview: str = None):
        """Log SQL query execution with results preview"""
        # Clean and format query
        query_clean = ' '.join(query.split())  # Remove extra whitespace
        self.logger.info(f"💾 SQL Query:")
        self.logger.info(f"   {query_clean}")
        self.logger.info(f"   ↳ {results_count} rows returned | {execution_time:.3f}s")
        
        # Log results preview
        if results_preview:
            self.logger.info(f"   📊 Results Preview:")
            for line in results_preview.split('\n')[:3]:  # Show first 3 lines
                if line.strip():
                    self.logger.info(f"      {line}")
    
    def log_error(self, error: Exception, context: str = ""):
        """Log error with context"""
        error_msg = str(error)[:80] + ('...' if len(str(error)) > 80 else '')
        if context:
            self.logger.error(f"❌ ERROR in {context}: {error_msg}")
        else:
            self.logger.error(f"❌ ERROR: {error_msg}")
        
        if self.current_session:
            self.current_session["errors"].append({
                "context": context,
                "error": str(error),
                "type": type(error).__name__,
                "timestamp": datetime.now().isoformat()
            })
    
    def end_session(self, final_response: str = None):
        """End the current session"""
        if not self.current_session:
            return
        
        duration = time.time() - self.session_start_time if self.session_start_time else 0
        
        nodes_count = len(self.current_session['nodes_executed'])
        tools_count = len(self.current_session['tools_called'])
        errors_count = len(self.current_session['errors'])
        
        self.logger.info(f"🏁 COMPLETED | {duration:.2f}s | Nodes: {nodes_count} | Tools: {tools_count} | Errors: {errors_count}")
        
        if final_response:
            response_preview = final_response[:70].replace('\n', ' ') + ('...' if len(final_response) > 70 else '')
            self.logger.info(f"💬 Response: {response_preview}")
        
        self.logger.info("=" * 80)
        
        # Save session summary
        self.current_session["end_time"] = datetime.now().isoformat()
        self.current_session["duration_seconds"] = duration
        self.current_session["final_response"] = final_response
        
        # Reset session
        session_data = self.current_session
        self.current_session = None
        self.session_start_time = None
        
        return session_data
    
    def get_session_summary(self) -> Optional[Dict]:
        """Get current session summary"""
        return self.current_session


# Global logger instance
agent_logger = AgentLogger()


def log_node_execution(node_name: str):
    """
    Decorator to log node execution
    
    Usage:
        @log_node_execution("route_query")
        def route_query(state: AgentState) -> AgentState:
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(state, *args, **kwargs):
            agent_logger.log_node_entry(node_name, state)
            start_time = time.time()
            
            try:
                result = func(state, *args, **kwargs)
                duration = time.time() - start_time
                agent_logger.log_node_exit(node_name, result, duration)
                return result
            except Exception as e:
                duration = time.time() - start_time
                agent_logger.log_error(e, f"Node: {node_name}")
                agent_logger.log_node_exit(node_name, state, duration)
                raise
        
        return wrapper
    return decorator


def log_tool_execution(tool_name: str):
    """
    Decorator to log tool execution
    
    Usage:
        @log_tool_execution("retrieve_context")
        def retrieve_context(query: str) -> str:
            ...
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            tool_input = {"args": args, "kwargs": kwargs}
            
            try:
                result = func(*args, **kwargs)
                agent_logger.log_tool_call(tool_name, tool_input, result)
                return result
            except Exception as e:
                agent_logger.log_tool_call(tool_name, tool_input, error=e)
                raise
        
        return wrapper
    return decorator


# Example usage
if __name__ == "__main__":
    # Test the logger
    logger = AgentLogger()
    
    logger.start_session("test-thread-123", "What is Smart Chatbot?")
    logger.log_node_entry("route_query", {"messages": []})
    logger.log_routing_decision("RAG", "Question is about documentation")
    logger.log_tool_call("retrieve_context", "What is Smart Chatbot?", "Retrieved 4 documents")
    logger.log_node_exit("route_query", {"messages": []}, 0.5)
    logger.end_session("Smart Chatbot is an AI assistant...")
    
    print("\n✓ Logger test completed!")
