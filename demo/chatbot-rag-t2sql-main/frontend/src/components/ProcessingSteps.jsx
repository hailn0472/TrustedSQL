import React from 'react';
import './ProcessingSteps.css';

/**
 * ProcessingSteps Component
 * Displays the thinking process visualization showing agent workflow steps
 * Similar to Perplexity AI's interface with vertical timeline
 * 
 * @param {Object} props
 * @param {Array} props.logs - Array of log entries with step and status
 * @param {boolean} props.isStreaming - Whether the message is currently streaming
 */
const ProcessingSteps = ({ logs = [], isStreaming = false }) => {
  // Don't render if no logs
  if (!logs || logs.length === 0) {
    return null;
  }

  // Step configuration with labels and descriptions
  const stepConfig = {
    'route_query': {
      label: 'Analyzing Request',
      description: 'Understanding your question and determining the best approach'
    },
    'call_sql_agent': {
      label: 'Querying Database',
      description: 'Executing SQL query to retrieve structured data'
    },
    'sql_tools': {
      label: 'Using SQL Tools',
      description: 'Querying database tables and retrieving results'
    },
    'call_rag_agent': {
      label: 'Searching Documents',
      description: 'Searching through knowledge base for relevant information'
    },
    'retrieve_context': {
      label: 'Retrieving Context',
      description: 'Fetching relevant documents from knowledge base'
    },
    'generate_response': {
      label: 'Generating Answer',
      description: 'Composing a comprehensive response based on findings'
    }
  };

  return (
    <div className="processing-steps">
      <div className="processing-timeline">
        {logs.map((log, index) => {
          const config = stepConfig[log.step] || {
            label: log.step.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()),
            description: 'Processing...'
          };

          const isLastStep = index === logs.length - 1;
          const isActive = isLastStep && isStreaming;
          const hasNextStep = index < logs.length - 1;

          return (
            <div 
              key={`${log.step}-${index}`} 
              className="processing-step-item"
            >
              {/* Timeline dot and line */}
              <div className="timeline-marker">
                <div className={`timeline-dot ${isActive ? 'active' : 'completed'}`}></div>
                {hasNextStep && <div className="timeline-line"></div>}
              </div>

              {/* Step content */}
              <div className="step-content">
                <div className="step-header">
                  <span className="step-label">{config.label}</span>
                  {isActive && (
                    <div className="loading-indicator">
                      <div className="loading-dot"></div>
                      <div className="loading-dot"></div>
                      <div className="loading-dot"></div>
                    </div>
                  )}
                </div>
                <div className="step-description">{config.description}</div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default ProcessingSteps;
