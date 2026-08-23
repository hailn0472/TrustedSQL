import React from 'react';
import ProcessStep from './ProcessStep';
import { NODE_LABELS } from '../config/workflowConfig';
import './ProcessTimeline.css';

/**
 * ProcessTimeline Component
 * Displays the thinking process as a vertical timeline
 * 
 * @param {Object} props
 * @param {Array} props.logs - Array of log entries with step and status
 * @param {string | null} props.currentStep - Currently active step
 * @param {boolean} props.isProcessing - Whether agent is currently processing
 */
const ProcessTimeline = ({
  logs = [],
  currentStep = null,
  isProcessing = false
}) => {
  // Step configuration with labels and descriptions
  const stepConfig = {
    route_query: {
      label: 'Analyzing Request',
      description: 'Understanding your question and determining the best approach'
    },
    call_sql_agent: {
      label: 'Querying Database',
      description: 'Executing SQL query to retrieve structured data'
    },
    call_rag_agent: {
      label: 'Searching Documents',
      description: 'Searching through knowledge base for relevant information'
    },
    generate_response: {
      label: 'Generating Answer',
      description: 'Composing a comprehensive response based on findings'
    }
  };
  
  // Don't render if no logs
  if (!logs || logs.length === 0) {
    return null;
  }
  
  return (
    <div className="process-timeline">
      <div className="process-timeline__steps" role="list" aria-label="Processing steps">
        {logs.map((log, index) => {
          // Handle missing log data
          if (!log || !log.step) {
            console.warn('ProcessTimeline: Invalid log entry', log);
            return null;
          }
          
          const config = stepConfig[log.step] || {
            label: log.step.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()),
            description: 'Processing...'
          };
          
          const isLastStep = index === logs.length - 1;
          const isActive = isLastStep && isProcessing && currentStep === log.step;
          const isCompleted = !isActive && (index < logs.length - 1 || !isProcessing);
          const hasNextStep = index < logs.length - 1;
          
          return (
            <ProcessStep
              key={`${log.step}-${index}`}
              step={log.step}
              label={config.label}
              description={config.description}
              isActive={isActive}
              isCompleted={isCompleted}
              hasNextStep={hasNextStep}
              metadata={log.metadata || {}}
            />
          );
        })}
      </div>
    </div>
  );
};

export default ProcessTimeline;
