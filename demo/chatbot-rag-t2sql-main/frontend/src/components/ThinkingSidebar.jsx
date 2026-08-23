import React from 'react';
import ProcessTimeline from './ProcessTimeline';
import WorkflowDiagram from './WorkflowDiagram';
import ErrorBoundary from './ErrorBoundary';
import './ThinkingSidebar.css';
import './ErrorBoundary.css';

/**
 * ThinkingSidebar Component
 * Container for the thinking process visualization
 * Displays both timeline and workflow diagram
 * 
 * @param {Object} props
 * @param {string | null} props.currentStep - Currently active node
 * @param {Set<string>} props.completedSteps - Set of completed node IDs
 * @param {Array} props.processingLogs - Timeline of processing steps
 * @param {boolean} props.isProcessing - Whether agent is currently processing
 */
const ThinkingSidebar = ({
  currentStep = null,
  completedSteps = new Set(),
  processingLogs = [],
  isProcessing = false
}) => {
  // Build execution path from logs
  const executionPath = processingLogs.map(log => log.step);
  
  // Determine if sidebar should be visible
  const hasContent = processingLogs.length > 0;
  
  return (
    <aside 
      className={`thinking-sidebar ${hasContent ? 'thinking-sidebar--visible' : 'thinking-sidebar--hidden'}`}
      aria-label="Thinking process visualization"
      role="complementary"
    >
      <div className="thinking-sidebar__content">
        {/* Process Timeline */}
        {hasContent && (
          <div className="thinking-sidebar__section">
            <ErrorBoundary fallbackMessage="Unable to display thinking process timeline.">
              <ProcessTimeline
                logs={processingLogs}
                currentStep={currentStep}
                isProcessing={isProcessing}
              />
            </ErrorBoundary>
          </div>
        )}
        
        {/* Workflow Diagram */}
        {hasContent && (
          <div className="thinking-sidebar__section">
            <div className="thinking-sidebar__diagram-header">
              <h3 className="thinking-sidebar__diagram-title">Workflow Diagram</h3>
            </div>
            <ErrorBoundary fallbackMessage="Unable to display workflow diagram. Showing timeline only.">
              <WorkflowDiagram
                currentStep={currentStep}
                completedSteps={completedSteps}
                executionPath={executionPath}
              />
            </ErrorBoundary>
          </div>
        )}
        
        {/* Empty state */}
        {!hasContent && (
          <div className="thinking-sidebar__empty">
            <div className="thinking-sidebar__empty-icon">🤔</div>
            <p className="thinking-sidebar__empty-text">
              Send a message to see the thinking process
            </p>
          </div>
        )}
      </div>
    </aside>
  );
};

export default ThinkingSidebar;
