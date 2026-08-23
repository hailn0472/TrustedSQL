import React from 'react';
import './DiagramResource.css';

/**
 * DiagramResource Component
 * Renders an external resource (database, vector store) in the workflow diagram
 * 
 * @param {Object} props
 * @param {string} props.id - Unique resource identifier
 * @param {string} props.label - Display label
 * @param {'database' | 'vectorstore'} props.type - Resource type
 * @param {{x: number, y: number}} props.position - SVG coordinates
 * @param {boolean} props.isActive - Whether resource is being accessed
 * @param {boolean} props.isCompleted - Whether resource has been accessed
 * @param {string} props.icon - Icon emoji
 */
const DiagramResource = ({
  id,
  label,
  type,
  position,
  isActive = false,
  isCompleted = false,
  icon
}) => {
  // Resource dimensions (smaller than nodes)
  const width = 90;
  const height = 40;
  const borderRadius = 6;
  
  // Calculate centered position
  const x = position.x - width / 2;
  const y = position.y - height / 2;
  
  // Determine resource state class
  const getStateClass = () => {
    if (isActive) return 'diagram-resource--active';
    if (isCompleted) return 'diagram-resource--completed';
    return 'diagram-resource--inactive';
  };
  
  // Determine resource type class
  const getTypeClass = () => {
    return `diagram-resource--${type}`;
  };
  
  // Unified color scheme - gray for default, green for active/completed
  const DEFAULT_COLOR = '#d0d0d0';
  const COMPLETED_COLOR = '#10B981'; // Green for completed
  
  const color = (isActive || isCompleted) ? COMPLETED_COLOR : DEFAULT_COLOR;
  const borderWidth = (isActive || isCompleted) ? 3 : 2;
  
  return (
    <g 
      className={`diagram-resource ${getStateClass()} ${getTypeClass()}`}
      data-resource-id={id}
    >
      {/* Resource background - circular for icon */}
      <circle
        cx={position.x}
        cy={position.y}
        r={25}
        className="diagram-resource__background"
        fill="#ffffff"
      />
      
      {/* Resource border - circular */}
      <circle
        cx={position.x}
        cy={position.y}
        r={25}
        className="diagram-resource__border"
        style={{ 
          stroke: color,
          strokeWidth: borderWidth,
          fill: 'none'
        }}
      />
      
      {/* Icon - centered */}
      <text
        x={position.x}
        y={position.y + 8}
        className="diagram-resource__icon"
        textAnchor="middle"
        fontSize="28"
      >
        {icon}
      </text>
      
      {/* Label below icon */}
      <text
        x={position.x}
        y={position.y + 42}
        className="diagram-resource__label"
        textAnchor="middle"
        fontSize="10"
        fill="#666666"
      >
        {label}
      </text>
      
      {/* Active indicator (glow effect) */}
      {isActive && (
        <circle
          cx={position.x}
          cy={position.y}
          r={28}
          className="diagram-resource__glow"
          style={{ stroke: color, fill: 'none' }}
        />
      )}
    </g>
  );
};

export default DiagramResource;
