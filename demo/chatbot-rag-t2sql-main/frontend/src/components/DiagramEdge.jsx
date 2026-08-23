import React from 'react';
import './DiagramEdge.css';

/**
 * DiagramEdge Component
 * Renders a connection line between nodes in the workflow diagram
 * 
 * @param {Object} props
 * @param {{x: number, y: number}} props.from - Starting position
 * @param {{x: number, y: number}} props.to - Ending position
 * @param {string} [props.label] - Optional edge label
 * @param {boolean} props.isActive - Whether edge is in active execution path
 * @param {'default' | 'conditional'} props.type - Edge type
 * @param {string} [props.id] - Unique edge identifier
 */
const DiagramEdge = ({
  from,
  to,
  label,
  isActive = false,
  type = 'default',
  id
}) => {
  // Calculate path with better connection points
  const calculatePath = () => {
    let startX = from.x;
    let startY = from.y;
    let endX = to.x;
    let endY = to.y;
    
    const deltaX = endX - startX;
    const deltaY = endY - startY;
    const absDeltaX = Math.abs(deltaX);
    
    // Determine node types based on Y position
    const isFromCircular = from.y < 100 || from.y > 640; // user_query or final_response
    const isToCircular = to.y < 100 || to.y > 640;
    
    // Set start point
    if (isFromCircular) {
      startY = from.y + 45; // Circular node radius - bottom
    } else {
      // For tool type going upward, start from top of node
      if (type === 'tool' && deltaY < 0) {
        startY = from.y - 25; // Top of rectangular node
      } else {
        startY = from.y + 25; // Bottom of rectangular node
      }
    }
    
    // Special handling for edges from route_query
    // Adjust startX to spread them out
    if (id === 'route_to_rag') {
      // Route Query -> RAG Agent: start from left side
      startX = from.x - 40;
    } else if (id === 'route_to_sql') {
      // Route Query -> SQL Agent: start from right side
      startX = from.x + 40;
    } else if (id === 'route_to_general') {
      // Route Query -> Generate Response: start from center
      startX = from.x;
    }
    
    // Special handling for edges to generate_response: make them vertical
    // Adjust startX to match endX for vertical lines
    if (id === 'rag_to_response') {
      // Will be set to to.x - 40 later, so set startX to match
      startX = to.x - 40;
    } else if (id === 'sql_to_response') {
      // Will be set to to.x + 40 later, so set startX to match
      startX = to.x + 40;
    }
    
    // Set end point
    if (isToCircular) {
      endY = to.y - 45; // Circular node radius - top
    } else {
      // For tool type going upward, end at bottom of node
      if (type === 'tool' && deltaY < 0) {
        endY = to.y + 25; // Bottom of rectangular node
      } else {
        endY = to.y - 25; // Top of rectangular node
      }
    }
    
    // Special handling for route edges: make them vertical
    if (id === 'route_to_rag') {
      // Make endX same as startX for vertical line
      endX = startX;  // This will be from.x - 40
    } else if (id === 'route_to_sql') {
      // Make endX same as startX for vertical line
      endX = startX;  // This will be from.x + 40
    }
    
    // Special handling for multiple edges converging to generate_response
    // Adjust endX to spread them out
    if (id === 'rag_to_response') {
      // RAG Agent -> Generate Response: connect to left side
      endX = to.x - 40;
    } else if (id === 'sql_to_response') {
      // SQL Agent -> Generate Response: connect to right side
      endX = to.x + 40;
    } else if (id === 'route_to_general') {
      // Route Query -> Generate Response (General): connect to center
      endX = to.x;
    }
    
    // Special handling for tool loops (bidirectional agent <-> tool)
    if (type === 'tool') {
      const offset = 20;
      
      // Downward: agent -> tool (right side)
      if (deltaY > 0) {
        const x1 = startX + offset;
        const x2 = endX + offset;
        return `M ${x1} ${startY} L ${x2} ${endY}`;
      }
      // Upward: tool -> agent (left side - now straight line)
      else {
        const x1 = startX - offset;
        const x2 = endX - offset;
        return `M ${x1} ${startY} L ${x2} ${endY}`;
      }
    }
    
    // Straight vertical line for aligned nodes
    if (absDeltaX < 10) {
      return `M ${startX} ${startY} L ${endX} ${endY}`;
    }
    
    // Diagonal connections with smooth curves
    const midY = (startY + endY) / 2;
    
    // Control points for bezier curve
    const cp1X = startX;
    const cp1Y = midY;
    const cp2X = endX;
    const cp2Y = midY;
    
    return `M ${startX} ${startY} 
            C ${cp1X} ${cp1Y}, ${cp2X} ${cp2Y}, ${endX} ${endY}`;
  };
  
  // Calculate label position (midpoint of path)
  const calculateLabelPosition = () => {
    const midX = (from.x + to.x) / 2;
    const midY = (from.y + to.y) / 2;
    return { x: midX, y: midY };
  };
  
  const path = calculatePath();
  const labelPos = label ? calculateLabelPosition() : null;
  
  // Determine edge state class
  const getStateClass = () => {
    return isActive ? 'diagram-edge--active' : 'diagram-edge--inactive';
  };
  
  // Determine edge type class
  const getTypeClass = () => {
    return `diagram-edge--${type}`;
  };
  
  return (
    <g 
      className={`diagram-edge ${getStateClass()} ${getTypeClass()}`}
      data-edge-id={id}
    >
      {/* Edge path */}
      <path
        d={path}
        className="diagram-edge__path"
        fill="none"
        strokeWidth="2"
        markerEnd="url(#arrowhead)"
      />
      
      {/* Edge label */}
      {label && labelPos && (
        <g className="diagram-edge__label-group">
          {/* Label background */}
          <rect
            x={labelPos.x - 20}
            y={labelPos.y - 10}
            width="40"
            height="20"
            rx="4"
            ry="4"
            className="diagram-edge__label-bg"
          />
          {/* Label text */}
          <text
            x={labelPos.x}
            y={labelPos.y + 4}
            className="diagram-edge__label-text"
            textAnchor="middle"
            fontSize="10"
          >
            {label}
          </text>
        </g>
      )}
    </g>
  );
};

/**
 * ArrowMarker Component
 * Defines the arrowhead marker for edges
 * Should be included once in the SVG defs section
 */
export const ArrowMarker = () => (
  <defs>
    <marker
      id="arrowhead"
      markerWidth="10"
      markerHeight="10"
      refX="9"
      refY="3"
      orient="auto"
      markerUnits="strokeWidth"
    >
      <path
        d="M0,0 L0,6 L9,3 z"
        fill="#999999"
        className="diagram-edge__arrowhead"
      />
    </marker>
    <marker
      id="arrowhead-active"
      markerWidth="10"
      markerHeight="10"
      refX="9"
      refY="3"
      orient="auto"
      markerUnits="strokeWidth"
    >
      <path
        d="M0,0 L0,6 L9,3 z"
        fill="#10B981"
        className="diagram-edge__arrowhead-active"
      />
    </marker>
  </defs>
);

export default DiagramEdge;
