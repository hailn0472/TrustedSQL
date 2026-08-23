import React from 'react';
import './ProcessStep.css';

/**
 * ProcessStep Component
 * Renders an individual step in the process timeline
 * 
 * @param {Object} props
 * @param {string} props.step - Step identifier (node name)
 * @param {string} props.label - Display label
 * @param {string} props.description - Step description
 * @param {boolean} props.isActive - Whether step is currently active
 * @param {boolean} props.isCompleted - Whether step has completed
 * @param {boolean} props.hasNextStep - Whether there is a next step
 * @param {Object} props.metadata - Additional metadata (tools_count, message_count, etc.)
 */
const ProcessStep = ({
  step,
  label,
  description,
  isActive = false,
  isCompleted = false,
  hasNextStep = false,
  metadata = {}
}) => {
  // Determine step state class
  const getStateClass = () => {
    if (isActive) return 'process-step--active';
    if (isCompleted) return 'process-step--completed';
    return 'process-step--pending';
  };
  
  return (
    <div 
      className={`process-step ${getStateClass()}`}
      data-step={step}
      role="listitem"
      aria-label={`${label} - ${isActive ? 'in progress' : isCompleted ? 'completed' : 'pending'}`}
    >
      {/* Timeline marker */}
      <div className="process-step__timeline">
        {/* Dot */}
        <div className="process-step__dot">
          {isCompleted && !isActive && (
            <span className="process-step__checkmark">✓</span>
          )}
          {isActive && (
            <span className="process-step__pulse"></span>
          )}
        </div>
        
        {/* Connecting line */}
        {hasNextStep && (
          <div className="process-step__line"></div>
        )}
      </div>
      
      {/* Step content */}
      <div className="process-step__content">
        <div className="process-step__header">
          <span className="process-step__label">{label}</span>
          
          {/* Loading indicator for active step */}
          {isActive && (
            <div className="process-step__loading">
              <div className="process-step__loading-dot"></div>
              <div className="process-step__loading-dot"></div>
              <div className="process-step__loading-dot"></div>
            </div>
          )}
        </div>
        
        <div className="process-step__description">{description}</div>
        
        {/* Metadata details - Routing Decision */}
        {metadata.routing && (
          <div className="process-step__metadata">
            <div className="process-step__meta-section">
              <div className="process-step__meta-label">🔀 Route: {metadata.routing.route}</div>
              <div className="process-step__meta-text">{metadata.routing.reasoning}</div>
            </div>
          </div>
        )}
        
        {/* Metadata details - SQL Tools */}
        {metadata.sql_tools && metadata.sql_tools.length > 0 && (
          <div className="process-step__metadata">
            <div className="process-step__meta-section">
              <div className="process-step__meta-label">🔧 SQL Operations:</div>
              {metadata.sql_tools.map((tool, idx) => (
                <div key={idx} className="process-step__meta-item">
                  <span className="process-step__meta-step">{tool.step}</span>
                  {tool.details && <span className="process-step__meta-details">{tool.details}</span>}
                </div>
              ))}
            </div>
          </div>
        )}
        
        {/* Metadata details - RAG Retrieval */}
        {metadata.rag && (
          <div className="process-step__metadata">
            <div className="process-step__meta-section">
              <div className="process-step__meta-label">📚 Retrieved {metadata.rag.num_docs} documents</div>
              {metadata.rag.sources && metadata.rag.sources.length > 0 && (
                <div className="process-step__meta-text">
                  Sources: {metadata.rag.sources.join(', ')}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default ProcessStep;
