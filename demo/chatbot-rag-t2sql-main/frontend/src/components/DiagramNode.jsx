import React from 'react';
import './DiagramNode.css';

/**
 * DiagramNode Component
 * Renders an individual node in the workflow diagram
 * 
 * @param {Object} props
 * @param {string} props.id - Unique node identifier
 * @param {string} props.label - Display label
 * @param {'entry' | 'agent' | 'tool' | 'final'} props.type - Node type
 * @param {{x: number, y: number}} props.position - SVG coordinates
 * @param {boolean} props.isActive - Whether node is currently active
 * @param {boolean} props.isCompleted - Whether node has completed
 * @param {boolean} props.isInPath - Whether node is in execution path
 * @param {string} props.icon - Icon emoji
 * @param {string} props.color - Primary color for node
 */
const DiagramNode = ({
  id,
  label,
  type,
  position,
  isActive = false,
  isCompleted = false,
  isInPath = false,
  icon,
  completedColor,
  activeColor
}) => {
  // Check if this is a circular node (entry or output)
  const isCircular = type === 'entry' || type === 'output';
  
  // Node dimensions - adjusted for better UI
  const width = type === 'tool' ? 110 : 130;
  const height = 50;
  const borderRadius = 8;
  const circleRadius = 45; // Increased from 30 to 40 to fit text better
  
  // Calculate centered position
  const x = position.x - width / 2;
  const y = position.y - height / 2;
  
  // Determine colors based on state
  const backgroundColor = isActive ? activeColor : '#ffffff';
  // Show green border when completed OR active
  const borderColor = (isCompleted || isActive) ? completedColor : '#d0d0d0';
  const borderWidth = (isCompleted || isActive) ? 3 : 2;
  const textColor = isActive ? '#ffffff' : '#666666';
  
  // Determine node state class
  const getStateClass = () => {
    if (isActive) return 'diagram-node--active';
    if (isCompleted) return 'diagram-node--completed';
    if (isInPath) return 'diagram-node--in-path';
    return 'diagram-node--inactive';
  };
  
  // Determine node type class
  const getTypeClass = () => {
    return `diagram-node--${type}`;
  };
  
  if (isCircular) {
    // Render circular node for entry/output
    return (
      <g 
        className={`diagram-node ${getStateClass()} ${getTypeClass()}`}
        data-node-id={id}
        role="img"
        aria-label={`${label} node - ${isActive ? 'active' : isCompleted ? 'completed' : 'inactive'}`}
      >
        {/* Circle background */}
        <circle
          cx={position.x}
          cy={position.y}
          r={circleRadius}
          className="diagram-node__background"
          style={{ fill: backgroundColor }}
        />
        
        {/* Circle border */}
        <circle
          cx={position.x}
          cy={position.y}
          r={circleRadius}
          className="diagram-node__border"
          style={{ 
            stroke: borderColor,
            strokeWidth: borderWidth,
            fill: 'none'
          }}
        />
        
        {/* Label - centered */}
        <text
          x={position.x}
          y={position.y + 5}
          className="diagram-node__label"
          textAnchor="middle"
          fill={textColor}
        >
          {label}
        </text>
        
        {/* Active indicator (pulsing ring) */}
        {isActive && (
          <circle
            cx={position.x}
            cy={position.y}
            r={circleRadius + 4}
            className="diagram-node__active-ring"
            style={{ stroke: activeColor, fill: 'none' }}
          />
        )}
      </g>
    );
  }
  
  // Render rectangular node for other types
  return (
    <g 
      className={`diagram-node ${getStateClass()} ${getTypeClass()}`}
      data-node-id={id}
      role="img"
      aria-label={`${label} node - ${isActive ? 'active' : isCompleted ? 'completed' : 'inactive'}`}
    >
      {/* Node background */}
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx={borderRadius}
        ry={borderRadius}
        className="diagram-node__background"
        style={{ fill: backgroundColor }}
      />
      
      {/* Node border */}
      <rect
        x={x}
        y={y}
        width={width}
        height={height}
        rx={borderRadius}
        ry={borderRadius}
        className="diagram-node__border"
        style={{ 
          stroke: borderColor,
          strokeWidth: borderWidth
        }}
      />
      
      {/* Label - centered, multi-line support */}
      <text
        x={position.x}
        y={position.y + 5}
        className="diagram-node__label"
        textAnchor="middle"
        fill={textColor}
      >
        {label}
      </text>
      
      {/* Active indicator (pulsing ring) */}
      {isActive && (
        <rect
          x={x - 4}
          y={y - 4}
          width={width + 8}
          height={height + 8}
          rx={borderRadius + 2}
          ry={borderRadius + 2}
          className="diagram-node__active-ring"
          style={{ stroke: activeColor }}
        />
      )}
    </g>
  );
};

export default DiagramNode;
