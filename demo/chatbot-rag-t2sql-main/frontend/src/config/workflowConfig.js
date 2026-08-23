/**
 * Workflow Graph Configuration
 * Defines the complete structure of the agent workflow including nodes, edges, and resources
 */

/**
 * @typedef {Object} NodeConfig
 * @property {string} id - Unique node identifier
 * @property {string} label - Display label
 * @property {string} description - Detailed description
 * @property {'entry' | 'agent' | 'tool' | 'final'} type - Node type
 * @property {string} icon - Icon emoji
 * @property {string} color - Primary color for node
 * @property {{x: number, y: number}} position - SVG coordinates
 */

/**
 * @typedef {Object} EdgeConfig
 * @property {string} id - Unique edge identifier
 * @property {string} from - Source node ID
 * @property {string} to - Target node ID
 * @property {string} [label] - Optional edge label
 * @property {'default' | 'conditional'} type - Edge type
 * @property {string} [condition] - For conditional edges (e.g., 'rag', 'sql', 'general')
 */

/**
 * @typedef {Object} ResourceConfig
 * @property {string} id - Unique resource identifier
 * @property {string} label - Display label
 * @property {'database' | 'vectorstore'} type - Resource type
 * @property {string} icon - Icon emoji
 * @property {{x: number, y: number}} position - SVG coordinates
 * @property {string[]} connectedTo - Node IDs that use this resource
 */

/**
 * Complete workflow graph definition
 * Matches the actual LangGraph implementation in backend
 */
// Unified color scheme
const DEFAULT_COLOR = '#E5E7EB'; // Light gray for default state
const COMPLETED_COLOR = '#10B981'; // Green for completed state
const ACTIVE_COLOR = '#10B981'; // Green for active/processing state

export const WORKFLOW_GRAPH = {
  nodes: {
    user_query: {
      id: 'user_query',
      label: 'User Query',
      description: 'User input received',
      type: 'entry',
      icon: '',
      completedColor: COMPLETED_COLOR,
      activeColor: ACTIVE_COLOR,
      position: { x: 240, y: 70 }
    },
    route_query: {
      id: 'route_query',
      label: 'Route Query',
      description: 'Analyze Query',
      type: 'decision',
      icon: '',
      completedColor: COMPLETED_COLOR,
      activeColor: ACTIVE_COLOR,
      position: { x: 240, y: 190 }
    },
    call_rag_agent: {
      id: 'call_rag_agent',
      label: 'RAG Agent',
      description: 'Search documents',
      type: 'agent',
      icon: '',
      completedColor: COMPLETED_COLOR,
      activeColor: ACTIVE_COLOR,
      position: { x: 150, y: 330 }
    },
    retrieve_context: {
      id: 'retrieve_context',
      label: 'Retrieve Context',
      description: 'Tool for retrieving context',
      type: 'tool',
      icon: '',
      completedColor: COMPLETED_COLOR,
      activeColor: ACTIVE_COLOR,
      position: { x: 90, y: 470 }
    },
    call_sql_agent: {
      id: 'call_sql_agent',
      label: 'SQL Agent',
      description: 'Query database',
      type: 'agent',
      icon: '',
      completedColor: COMPLETED_COLOR,
      activeColor: ACTIVE_COLOR,
      position: { x: 330, y: 330 }
    },
    sql_tools: {
      id: 'sql_tools',
      label: 'SQL Tools',
      description: 'Database query tools',
      type: 'tool',
      icon: '',
      completedColor: COMPLETED_COLOR,
      activeColor: ACTIVE_COLOR,
      position: { x: 390, y: 470 }
    },
    generate_response: {
      id: 'generate_response',
      label: 'Generate Response',
      description: 'LLM: Response Generation',
      type: 'final',
      icon: '',
      completedColor: COMPLETED_COLOR,
      activeColor: ACTIVE_COLOR,
      position: { x: 240, y: 620 }
    },
    final_response: {
      id: 'final_response',
      label: 'Final Response',
      description: 'Response delivered to user',
      type: 'output',
      icon: '',
      completedColor: COMPLETED_COLOR,
      activeColor: ACTIVE_COLOR,
      position: { x: 240, y: 740 }
    }
  },
  edges: [
    {
      id: 'user_to_route',
      from: 'user_query',
      to: 'route_query',
      label: '',
      type: 'default'
    },
    {
      id: 'route_to_rag',
      from: 'route_query',
      to: 'call_rag_agent',
      label: 'RAG',
      type: 'conditional',
      condition: 'rag'
    },
    {
      id: 'route_to_sql',
      from: 'route_query',
      to: 'call_sql_agent',
      label: 'SQL',
      type: 'conditional',
      condition: 'sql'
    },
    {
      id: 'route_to_general',
      from: 'route_query',
      to: 'generate_response',
      label: 'General',
      type: 'conditional',
      condition: 'general'
    },
    {
      id: 'rag_to_tool',
      from: 'call_rag_agent',
      to: 'retrieve_context',
      label: '',
      type: 'tool'
    },
    {
      id: 'tool_to_rag',
      from: 'retrieve_context',
      to: 'call_rag_agent',
      label: '',
      type: 'tool'
    },
    {
      id: 'sql_to_tools',
      from: 'call_sql_agent',
      to: 'sql_tools',
      label: '',
      type: 'tool'
    },
    {
      id: 'tools_to_sql',
      from: 'sql_tools',
      to: 'call_sql_agent',
      label: '',
      type: 'tool'
    },
    {
      id: 'rag_to_response',
      from: 'call_rag_agent',
      to: 'generate_response',
      type: 'default'
    },
    {
      id: 'sql_to_response',
      from: 'call_sql_agent',
      to: 'generate_response',
      type: 'default'
    },
    {
      id: 'response_to_final',
      from: 'generate_response',
      to: 'final_response',
      label: '',
      type: 'default'
    }
  ],
  resources: [
    {
      id: 'file_search_store',
      label: 'File Search Store',
      type: 'vectorstore',
      icon: '🗄️',
      position: { x: 90, y: 560 },
      connectedTo: ['retrieve_context']
    },
    {
      id: 'sqlserver',
      label: 'SQL Server',
      type: 'database',
      icon: '🗄️',
      position: { x: 390, y: 560 },
      connectedTo: ['sql_tools']
    }
  ]
};

/**
 * Node labels mapping for backend node names
 * Used to display user-friendly labels in the UI
 */
export const NODE_LABELS = {
  route_query: 'Analyzing Request',
  call_sql_agent: 'Querying Database',
  call_rag_agent: 'Searching Documents',
  generate_response: 'Generating Answer'
};

/**
 * Get node configuration by ID
 * @param {string} nodeId - Node identifier
 * @returns {NodeConfig | undefined} Node configuration or undefined if not found
 */
export const getNodeConfig = (nodeId) => {
  return WORKFLOW_GRAPH.nodes[nodeId];
};

/**
 * Get all node IDs in the workflow
 * @returns {string[]} Array of node IDs
 */
export const getAllNodeIds = () => {
  return Object.keys(WORKFLOW_GRAPH.nodes);
};

/**
 * Get edges connected to a specific node
 * @param {string} nodeId - Node identifier
 * @param {'incoming' | 'outgoing' | 'all'} direction - Edge direction
 * @returns {EdgeConfig[]} Array of edge configurations
 */
export const getNodeEdges = (nodeId, direction = 'all') => {
  const edges = WORKFLOW_GRAPH.edges;
  
  if (direction === 'incoming') {
    return edges.filter(edge => edge.to === nodeId);
  } else if (direction === 'outgoing') {
    return edges.filter(edge => edge.from === nodeId);
  }
  
  return edges.filter(edge => edge.from === nodeId || edge.to === nodeId);
};

/**
 * Get resources connected to a specific node
 * @param {string} nodeId - Node identifier
 * @returns {ResourceConfig[]} Array of resource configurations
 */
export const getNodeResources = (nodeId) => {
  return WORKFLOW_GRAPH.resources.filter(resource => 
    resource.connectedTo.includes(nodeId)
  );
};

/**
 * Validate workflow configuration
 * Checks for missing nodes, invalid edges, etc.
 * @returns {{valid: boolean, errors: string[]}} Validation result
 */
export const validateWorkflowConfig = () => {
  const errors = [];
  const nodeIds = getAllNodeIds();
  
  // Check all edges reference valid nodes
  WORKFLOW_GRAPH.edges.forEach(edge => {
    if (!nodeIds.includes(edge.from)) {
      errors.push(`Edge ${edge.id}: 'from' node '${edge.from}' does not exist`);
    }
    if (!nodeIds.includes(edge.to)) {
      errors.push(`Edge ${edge.id}: 'to' node '${edge.to}' does not exist`);
    }
  });
  
  // Check all resources reference valid nodes
  WORKFLOW_GRAPH.resources.forEach(resource => {
    resource.connectedTo.forEach(nodeId => {
      if (!nodeIds.includes(nodeId)) {
        errors.push(`Resource ${resource.id}: connected node '${nodeId}' does not exist`);
      }
    });
  });
  
  return {
    valid: errors.length === 0,
    errors
  };
};
