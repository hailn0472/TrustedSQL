import React from 'react';
import { useChat } from '../hooks/useChat';
import ProcessTimeline from './ProcessTimeline';
import WorkflowDiagram from './WorkflowDiagram';
import ChatWindow from './ChatWindow';
import ErrorBoundary from './ErrorBoundary';
import './ChatLayout.css';
import './ErrorBoundary.css';

/**
 * ChatLayout Component
 * Three-column layout: Thinking Process (left), Chat (center), Diagram (right)
 * 
 * @param {Object} props
 * @param {string} props.threadId - The ID of the chat thread
 */
const ChatLayout = ({ threadId }) => {
  // Get chat state including processing visualization data
  const {
    messages,
    isLoading,
    error,
    sendMessage,
    clearMessages,
    currentProcessingStep,
    completedProcessingSteps,
    processingLogs
  } = useChat(threadId);
  
  // Build execution path from logs
  const executionPath = processingLogs.map(log => log.step);
  const hasContent = processingLogs.length > 0;
  
  return (
    <div className="chat-layout">
      {/* Left Sidebar - Thinking Process - Always visible */}
      <aside 
        className="chat-layout__thinking-sidebar chat-layout__thinking-sidebar--visible"
        aria-label="Thinking process timeline"
        role="complementary"
      >
        <div className="chat-layout__sidebar-content">
          <div className="chat-layout__sidebar-header">
            <h3 className="chat-layout__sidebar-title">Thinking Process</h3>
            {isLoading && (
              <span className="chat-layout__sidebar-badge">Processing...</span>
            )}
          </div>
          {hasContent ? (
            <ErrorBoundary fallbackMessage="Unable to display thinking process.">
              <ProcessTimeline
                logs={processingLogs}
                currentStep={currentProcessingStep}
                isProcessing={isLoading}
              />
            </ErrorBoundary>
          ) : (
            <div className="chat-layout__sidebar-empty">
              <div className="chat-layout__empty-icon">🤔</div>
              <p className="chat-layout__empty-text">
                Send a message to see the thinking process
              </p>
            </div>
          )}
        </div>
      </aside>
      
      {/* Center - Main Chat Area */}
      <div className="chat-layout__main">
        <ChatWindow
          threadId={threadId}
          messages={messages}
          isLoading={isLoading}
          error={error}
          sendMessage={sendMessage}
          clearMessages={clearMessages}
        />
      </div>
      
      {/* Right Sidebar - Workflow Diagram - Always visible */}
      <aside 
        className="chat-layout__diagram-sidebar chat-layout__diagram-sidebar--visible"
        aria-label="Workflow diagram visualization"
        role="complementary"
      >
        <div className="chat-layout__sidebar-content">
          <div className="chat-layout__sidebar-header">
            <h3 className="chat-layout__sidebar-title">System Architecture</h3>
          </div>
          <ErrorBoundary fallbackMessage="Unable to display workflow diagram.">
            <WorkflowDiagram
              currentStep={currentProcessingStep}
              completedSteps={completedProcessingSteps}
              executionPath={executionPath}
            />
          </ErrorBoundary>
        </div>
      </aside>
    </div>
  );
};

export default ChatLayout;
