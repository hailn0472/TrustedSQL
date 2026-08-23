"""
Multi-Agent Architecture Visualization and Documentation
Provides detailed information about the system architecture
"""
from typing import Dict, List, Any


class ArchitectureVisualizer:
    """
    Provides architecture information and visualization for the multi-agent system
    """
    
    @staticmethod
    def get_architecture_info() -> Dict[str, Any]:
        """
        Get complete architecture information
        
        Returns:
            Dictionary containing architecture details
        """
        return {
            "system_name": "Smart Chatbot - Multi-Agent RAG & SQL System",
            "version": "1.0.0",
            "architecture_type": "Multi-Agent with LangGraph",
            "components": ArchitectureVisualizer.get_components(),
            "workflow": ArchitectureVisualizer.get_workflow(),
            "nodes": ArchitectureVisualizer.get_nodes(),
            "tools": ArchitectureVisualizer.get_tools(),
            "data_flow": ArchitectureVisualizer.get_data_flow(),
            "mermaid_diagram": ArchitectureVisualizer.get_mermaid_diagram()
        }
    
    @staticmethod
    def get_components() -> Dict[str, Any]:
        """Get system components"""
        return {
            "frontend": {
                "technology": "React + Vite",
                "port": 5173,
                "features": ["Chat interface", "Thread management", "SSE streaming"]
            },
            "backend": {
                "technology": "Flask + LangGraph",
                "port": 5000,
                "framework": "LangGraph (LangChain)",
                "features": ["Multi-agent orchestration", "RAG", "SQL querying", "Conversation memory"]
            },
            "llm": {
                "provider": "Google Gemini",
                "model": "gemini-1.5-flash",
                "capabilities": ["Text generation", "Structured output", "Tool calling"]
            },
            "file_search": {
                "technology": "Google File Search API",
                "embedding_model": "Automatic (Google-managed)",
                "purpose": "Document retrieval for RAG"
            },
            "database": {
                "technology": "SQL Server",
                "purpose": "Structured data querying"
            },
            "checkpointer": {
                "technology": "SQLite (SqliteSaver)",
                "purpose": "Conversation state persistence"
            }
        }
    
    @staticmethod
    def get_workflow() -> Dict[str, Any]:
        """Get workflow description"""
        return {
            "description": "Multi-agent workflow with intelligent routing",
            "entry_point": "route_query",
            "exit_point": "generate_response",
            "flow": [
                {
                    "step": 1,
                    "node": "route_query",
                    "description": "Analyzes user query and decides routing strategy",
                    "outputs": ["RAG", "SQL", "General"]
                },
                {
                    "step": 2,
                    "node": "call_rag_agent (conditional)",
                    "description": "Invokes RAG sub-agent to retrieve document context",
                    "condition": "If route = RAG",
                    "tools": ["retrieve_context"]
                },
                {
                    "step": 3,
                    "node": "call_sql_agent (conditional)",
                    "description": "Invokes SQL sub-agent to query database",
                    "condition": "If route = SQL",
                    "tools": ["sql_db_list_tables", "sql_db_schema", "sql_db_query", "sql_db_query_checker"]
                },
                {
                    "step": 4,
                    "node": "generate_response",
                    "description": "Generates final response based on context",
                    "inputs": ["User query", "RAG results (if any)", "SQL results (if any)"]
                }
            ]
        }
    
    @staticmethod
    def get_nodes() -> List[Dict[str, Any]]:
        """Get detailed node information"""
        return [
            {
                "name": "route_query",
                "type": "Router Node",
                "purpose": "Intelligent query classification and routing",
                "input": "User message",
                "output": "Routing decision (RAG/SQL/General)",
                "llm_used": True,
                "structured_output": "RouteDecision",
                "logic": [
                    "Analyzes user query intent",
                    "Classifies into RAG (documents), SQL (database), or General",
                    "Uses LLM with structured output for decision",
                    "Adds routing decision to state"
                ]
            },
            {
                "name": "call_rag_agent",
                "type": "Sub-Agent Node",
                "purpose": "Retrieve and synthesize information from documents",
                "input": "User query",
                "output": "RAG results with context",
                "llm_used": True,
                "tools": ["retrieve_context"],
                "logic": [
                    "Creates autonomous RAG sub-agent",
                    "Agent decides when to use retrieve_context tool",
                    "Retrieves relevant documents from vector store",
                    "Synthesizes answer from retrieved context",
                    "Returns results to main workflow"
                ]
            },
            {
                "name": "call_sql_agent",
                "type": "Sub-Agent Node",
                "purpose": "Query database and extract structured data",
                "input": "User query",
                "output": "SQL results with data",
                "llm_used": True,
                "tools": ["sql_db_list_tables", "sql_db_schema", "sql_db_query", "sql_db_query_checker"],
                "logic": [
                    "Creates autonomous SQL sub-agent",
                    "Agent explores database schema",
                    "Generates SQL query",
                    "Validates and executes query",
                    "Formats results for user",
                    "Returns results to main workflow"
                ]
            },
            {
                "name": "generate_response",
                "type": "Response Generator Node",
                "purpose": "Generate final user-facing response",
                "input": "All conversation context + RAG/SQL results",
                "output": "Final AI response",
                "llm_used": True,
                "logic": [
                    "Receives all context from previous nodes",
                    "Synthesizes information into coherent response",
                    "Maintains conversational tone",
                    "Returns final message to user"
                ]
            }
        ]
    
    @staticmethod
    def get_tools() -> List[Dict[str, Any]]:
        """Get tool information"""
        return [
            {
                "name": "retrieve_context",
                "category": "RAG",
                "purpose": "Retrieve relevant documents from File Search Store",
                "input": "Search query (string)",
                "output": "Retrieved documents with citations",
                "used_by": "RAG Agent",
                "implementation": "Google File Search API with automatic semantic search"
            },
            {
                "name": "sql_db_list_tables",
                "category": "SQL",
                "purpose": "List all available tables in database",
                "input": "None",
                "output": "List of table names",
                "used_by": "SQL Agent",
                "implementation": "SQLDatabaseToolkit"
            },
            {
                "name": "sql_db_schema",
                "category": "SQL",
                "purpose": "Get schema information for specific tables",
                "input": "Table names (comma-separated)",
                "output": "Table schemas with columns and types",
                "used_by": "SQL Agent",
                "implementation": "SQLDatabaseToolkit"
            },
            {
                "name": "sql_db_query",
                "category": "SQL",
                "purpose": "Execute SQL query on database",
                "input": "SQL query string",
                "output": "Query results",
                "used_by": "SQL Agent",
                "implementation": "SQLDatabaseToolkit with safety checks"
            },
            {
                "name": "sql_db_query_checker",
                "category": "SQL",
                "purpose": "Validate SQL query for correctness",
                "input": "SQL query string",
                "output": "Validated/corrected query",
                "used_by": "SQL Agent",
                "implementation": "LLM-based query validation"
            }
        ]
    
    @staticmethod
    def get_data_flow() -> Dict[str, Any]:
        """Get data flow information"""
        return {
            "state_management": {
                "type": "AgentState (TypedDict)",
                "fields": {
                    "messages": "List of conversation messages with add_messages reducer"
                },
                "persistence": "SqliteSaver checkpointer for conversation history"
            },
            "message_types": [
                "HumanMessage: User input",
                "AIMessage: Agent responses",
                "SystemMessage: Internal routing/context messages",
                "ToolMessage: Tool execution results"
            ],
            "flow_pattern": "Sequential with conditional branching",
            "state_updates": "Each node returns state updates that are merged into main state"
        }
    
    @staticmethod
    def get_mermaid_diagram() -> str:
        """Get Mermaid diagram for visualization"""
        return """
graph TD
    Start([User Query]) --> RouteQuery[Route Query Node]
    
    RouteQuery -->|Analyze Query| LLM1[LLM: Structured Output]
    LLM1 --> Decision{Routing Decision}
    
    Decision -->|RAG| RAGAgent[RAG Agent Node]
    Decision -->|SQL| SQLAgent[SQL Agent Node]
    Decision -->|General| GenerateResponse[Generate Response Node]
    
    RAGAgent --> RAGTools[Tool: retrieve_context]
    RAGTools --> FileSearch[(Google File Search<br/>Store)]
    FileSearch --> RAGTools
    RAGTools --> RAGAgent
    RAGAgent --> GenerateResponse
    
    SQLAgent --> SQLTools[SQL Tools]
    SQLTools --> ListTables[sql_db_list_tables]
    SQLTools --> GetSchema[sql_db_schema]
    SQLTools --> QueryDB[sql_db_query]
    SQLTools --> CheckQuery[sql_db_query_checker]
    
    ListTables --> Database[(SQL Server<br/>Database)]
    GetSchema --> Database
    QueryDB --> Database
    Database --> SQLAgent
    SQLAgent --> GenerateResponse
    
    GenerateResponse --> LLM2[LLM: Response Generation]
    LLM2 --> End([Final Response])
    
    style Start fill:#e1f5e1
    style End fill:#e1f5e1
    style RouteQuery fill:#fff4e1
    style RAGAgent fill:#e1f0ff
    style SQLAgent fill:#ffe1f0
    style GenerateResponse fill:#f0e1ff
    style Decision fill:#ffe1e1
    style VectorStore fill:#d4edda
    style Database fill:#d4edda
"""
    
    @staticmethod
    def get_ascii_diagram() -> str:
        """Get ASCII diagram for terminal display"""
        return """
╔══════════════════════════════════════════════════════════════════════════════╗
║                    SMART CHATBOT - MULTI-AGENT ARCHITECTURE                  ║
╚══════════════════════════════════════════════════════════════════════════════╝

                              ┌─────────────┐
                              │ User Query  │
                              └──────┬──────┘
                                     │
                                     ▼
                         ┌───────────────────────┐
                         │   ROUTE QUERY NODE    │
                         │  (LLM Classification) │
                         └───────────┬───────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
                    ▼                ▼                ▼
            ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
            │  RAG AGENT   │  │  SQL AGENT   │  │   GENERAL    │
            │   (Sub-Agent)│  │  (Sub-Agent) │  │              │
            └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
                   │                 │                  │
                   │                 │                  │
         ┌─────────▼─────────┐      │                  │
         │  retrieve_context │      │                  │
         │        Tool       │      │                  │
         └─────────┬─────────┘      │                  │
                   │                 │                  │
                   ▼                 ▼                  │
            ┌──────────┐      ┌──────────┐            │
            │  Google  │      │SQL Server│            │
            │   File   │      │ Database │            │
            │  Search  │      └────┬─────┘            │
            └────┬─────┘           │                  │
                 │                 │                  │
                 │    ┌────────────▼──────────┐      │
                 │    │  SQL Tools:           │      │
                 │    │  - list_tables        │      │
                 │    │  - get_schema         │      │
                 │    │  - query              │      │
                 │    │  - query_checker      │      │
                 │    └────────────┬──────────┘      │
                 │                 │                  │
                 └─────────────────┼──────────────────┘
                                   │
                                   ▼
                      ┌────────────────────────┐
                      │ GENERATE RESPONSE NODE │
                      │   (LLM Synthesis)      │
                      └────────────┬───────────┘
                                   │
                                   ▼
                            ┌─────────────┐
                            │Final Response│
                            └─────────────┘

╔══════════════════════════════════════════════════════════════════════════════╗
║  STATE MANAGEMENT: SqliteSaver Checkpointer (Conversation Memory)           ║
║  LLM: Google Gemini 1.5 Flash                                                ║
║  Framework: LangGraph (LangChain)                                            ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""
    
    @staticmethod
    def print_architecture():
        """Print architecture to console"""
        print(ArchitectureVisualizer.get_ascii_diagram())
        
        print("\n" + "=" * 80)
        print("DETAILED COMPONENT INFORMATION")
        print("=" * 80)
        
        components = ArchitectureVisualizer.get_components()
        for comp_name, comp_info in components.items():
            print(f"\n{comp_name.upper()}:")
            for key, value in comp_info.items():
                print(f"  {key}: {value}")
        
        print("\n" + "=" * 80)
        print("NODES")
        print("=" * 80)
        
        nodes = ArchitectureVisualizer.get_nodes()
        for node in nodes:
            print(f"\n{node['name']} ({node['type']}):")
            print(f"  Purpose: {node['purpose']}")
            print(f"  LLM Used: {node['llm_used']}")
            if 'tools' in node:
                print(f"  Tools: {', '.join(node['tools'])}")
        
        print("\n" + "=" * 80)
        print("TOOLS")
        print("=" * 80)
        
        tools = ArchitectureVisualizer.get_tools()
        for tool in tools:
            print(f"\n{tool['name']} ({tool['category']}):")
            print(f"  Purpose: {tool['purpose']}")
            print(f"  Used by: {tool['used_by']}")


# Example usage
if __name__ == "__main__":
    visualizer = ArchitectureVisualizer()
    visualizer.print_architecture()
    
    print("\n\n" + "=" * 80)
    print("MERMAID DIAGRAM (for documentation)")
    print("=" * 80)
    print(visualizer.get_mermaid_diagram())
