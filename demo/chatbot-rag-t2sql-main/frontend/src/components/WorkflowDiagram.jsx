import React, { useMemo } from 'react';
import { WORKFLOW_GRAPH } from '../config/workflowConfig';
import DiagramNode from './DiagramNode';
import DiagramEdge, { ArrowMarker } from './DiagramEdge';
import DiagramResource from './DiagramResource';
import './WorkflowDiagram.css';

/**
 * WorkflowDiagram Component
 * Renders the complete workflow diagram with nodes, edges, and resources
 * 
 * @param {Object} props
 * @param {string | null} props.currentStep - Currently active node ID
 * @param {Set<string>} props.completedSteps - Set of completed node IDs
 * @param {string[]} props.executionPath - Ordered list of nodes visited
 */
const WorkflowDiagram = ({
  currentStep = null,
  completedSteps = new Set(),
  executionPath = []
}) => {
  // Validate workflow configuration on mount
  React.useEffect(() => {
    const nodeIds = Object.keys(WORKFLOW_GRAPH.nodes);
    if (nodeIds.length === 0) {
      console.error('WorkflowDiagram: No nodes defined in workflow configuration');
    }
  }, []);
  
  // SVG dimensions - adjusted for vertical layout with better centering
  // Increase viewBox to make content appear smaller (zoom out effect)
  const width = 480;
  const height = 820;
  const viewBox = `0 0 ${width} ${height}`;
  
  // Determine which edges are active based on execution path
  const activeEdges = useMemo(() => {
    const active = new Set();
    
    // Build complete path including user_query at start
    const fullPath = ['user_query', ...executionPath];
    
    // Add edges between consecutive nodes in path
    for (let i = 0; i < fullPath.length - 1; i++) {
      const from = fullPath[i];
      const to = fullPath[i + 1];
      
      // Find edge connecting these nodes
      const edge = WORKFLOW_GRAPH.edges.find(
        e => e.from === from && e.to === to
      );
      
      if (edge) {
        active.add(edge.id);
      }
    }
    
    // Add tool loop edges for nodes in execution path
    // When an agent is in path, highlight its tool edges
    fullPath.forEach(nodeId => {
      // Find all edges connected to this node (both directions)
      const connectedEdges = WORKFLOW_GRAPH.edges.filter(
        e => (e.from === nodeId || e.to === nodeId) && e.type === 'tool'
      );
      connectedEdges.forEach(edge => active.add(edge.id));
    });
    
    // If final_response is in path, add edge from generate_response to final_response
    if (fullPath.includes('final_response')) {
      const finalEdge = WORKFLOW_GRAPH.edges.find(
        e => e.from === 'generate_response' && e.to === 'final_response'
      );
      if (finalEdge) {
        active.add(finalEdge.id);
      }
    }
    
    return active;
  }, [executionPath]);
  
  // Determine which resources are active
  const activeResources = useMemo(() => {
    const active = new Set();
    
    // Check if any connected nodes are active or in execution path
    WORKFLOW_GRAPH.resources.forEach(resource => {
      const isConnectedActive = resource.connectedTo.some(
        nodeId => currentStep === nodeId || executionPath.includes(nodeId)
      );
      
      if (isConnectedActive) {
        active.add(resource.id);
      }
    });
    
    return active;
  }, [currentStep, executionPath]);
  
  // Extend completed steps to include tool nodes when their parent agent is completed
  const extendedCompletedSteps = useMemo(() => {
    const extended = new Set(completedSteps);
    
    // If RAG agent is completed, mark retrieve_context as completed
    if (completedSteps.has('call_rag_agent')) {
      extended.add('retrieve_context');
    }
    
    // If SQL agent is completed, mark sql_tools as completed
    if (completedSteps.has('call_sql_agent')) {
      extended.add('sql_tools');
    }
    
    return extended;
  }, [completedSteps]);
  
  // Determine if tool nodes should be active based on their parent agent
  const isToolNodeActive = (toolNodeId) => {
    if (toolNodeId === 'retrieve_context') {
      return currentStep === 'call_rag_agent';
    }
    if (toolNodeId === 'sql_tools') {
      return currentStep === 'call_sql_agent';
    }
    return false;
  };
  
  // Determine which resources are completed
  const completedResources = useMemo(() => {
    const completed = new Set();
    
    // Mark resource as completed if its connected tool node is completed
    WORKFLOW_GRAPH.resources.forEach(resource => {
      const isConnectedCompleted = resource.connectedTo.some(
        nodeId => extendedCompletedSteps.has(nodeId)
      );
      
      if (isConnectedCompleted) {
        completed.add(resource.id);
      }
    });
    
    return completed;
  }, [extendedCompletedSteps]);
  
  return (
    <div className="workflow-diagram">
      <svg
        width="100%"
        height="100%"
        viewBox={viewBox}
        preserveAspectRatio="xMidYMid meet"
        className="workflow-diagram__svg"
        role="img"
        aria-label="Workflow diagram showing agent execution flow"
      >
        {/* Define arrow markers */}
        <ArrowMarker />
        
        {/* Render edges first (so they appear behind nodes) */}
        {WORKFLOW_GRAPH.edges.map(edge => {
          const fromNode = WORKFLOW_GRAPH.nodes[edge.from];
          const toNode = WORKFLOW_GRAPH.nodes[edge.to];
          
          if (!fromNode || !toNode) return null;
          
          return (
            <DiagramEdge
              key={edge.id}
              id={edge.id}
              from={fromNode.position}
              to={toNode.position}
              label={edge.label}
              isActive={activeEdges.has(edge.id)}
              type={edge.type}
            />
          );
        })}
        
        {/* Render resource connection lines */}
        {WORKFLOW_GRAPH.resources.map(resource => {
          return resource.connectedTo.map(nodeId => {
            const node = WORKFLOW_GRAPH.nodes[nodeId];
            if (!node) return null;
            
            const isActive = activeResources.has(resource.id);
            const isCompleted = completedResources.has(resource.id);
            
            // Determine line color - green for active/completed, gray for default
            const strokeColor = (isActive || isCompleted) ? '#10B981' : '#e0e0e0';
            const strokeWidth = (isActive || isCompleted) ? '2' : '1.5';
            
            return (
              <line
                key={`${resource.id}-${nodeId}`}
                x1={resource.position.x}
                y1={resource.position.y - 20}
                x2={node.position.x}
                y2={node.position.y + 25}
                className={`workflow-diagram__resource-line ${isActive || isCompleted ? 'workflow-diagram__resource-line--active' : ''}`}
                stroke={strokeColor}
                strokeWidth={strokeWidth}
                strokeDasharray="3 3"
              />
            );
          });
        })}
        
        {/* Render resources */}
        {WORKFLOW_GRAPH.resources.map(resource => (
          <DiagramResource
            key={resource.id}
            id={resource.id}
            label={resource.label}
            type={resource.type}
            position={resource.position}
            isActive={activeResources.has(resource.id)}
            isCompleted={completedResources.has(resource.id)}
            icon={resource.icon}
          />
        ))}
        
        {/* Render nodes */}
        {Object.values(WORKFLOW_GRAPH.nodes).map(node => {
          // Check if this is a tool node that should be active based on parent agent
          const isActive = currentStep === node.id || isToolNodeActive(node.id);
          
          return (
            <DiagramNode
              key={node.id}
              id={node.id}
              label={node.label}
              type={node.type}
              position={node.position}
              isActive={isActive}
              isCompleted={extendedCompletedSteps.has(node.id)}
              isInPath={executionPath.includes(node.id)}
              icon={node.icon}
              completedColor={node.completedColor}
              activeColor={node.activeColor}
            />
          );
        })}
      </svg>
    </div>
  );
};

export default WorkflowDiagram;
