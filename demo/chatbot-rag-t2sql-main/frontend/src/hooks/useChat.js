import { useState, useCallback, useRef } from 'react';

/**
 * Custom hook for managing chat functionality
 * 
 * @param {string} threadId - The ID of the conversation thread
 * @returns {Object} An object containing chat state and functions
 * @returns {Array} returns.messages - An array of message objects
 * @returns {boolean} returns.isLoading - The loading state of the chat
 * @returns {string|null} returns.error - An error message, if any occurred
 * @returns {Function} returns.sendMessage - A function to send a new message
 * @returns {Function} returns.clearMessages - A function to clear all messages in the current chat
 */
export const useChat = (threadId) => {
  const [messages, setMessages] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const eventSourceRef = useRef(null);
  const currentMessageIdRef = useRef(null);
  
  // Processing state for visualization
  const [currentProcessingStep, setCurrentProcessingStep] = useState(null);
  const [completedProcessingSteps, setCompletedProcessingSteps] = useState(new Set());
  const [processingLogs, setProcessingLogs] = useState([]);
  const previousStepRef = useRef(null);

  /**
   * Reset processing state
   */
  const resetProcessingState = useCallback(() => {
    setCurrentProcessingStep(null);
    setCompletedProcessingSteps(new Set());
    setProcessingLogs([]);
    previousStepRef.current = null;
  }, []);

  /**
   * Send a message to the chat API
   * @param {string} content - Message content to send
   */
  const sendMessage = useCallback(async (content) => {
    if (!content || !content.trim()) {
      return;
    }

    // Clear any previous errors and reset processing state
    setError(null);
    setIsLoading(true);
    resetProcessingState();
    
    // Add user_query as the first processing step (active)
    setCurrentProcessingStep('user_query');
    previousStepRef.current = 'user_query';
    setProcessingLogs([{ step: 'user_query', status: 'processing', timestamp: Date.now() }]);

    // Add user message to state
    const userMessage = {
      id: `user-${Date.now()}`,
      role: 'user',
      content: content.trim(),
      timestamp: new Date(),
    };

    setMessages((prev) => [...prev, userMessage]);

    // Create assistant message placeholder for streaming
    const assistantMessageId = `assistant-${Date.now()}`;
    currentMessageIdRef.current = assistantMessageId;

    const assistantMessage = {
      id: assistantMessageId,
      role: 'assistant',
      content: '',
      timestamp: new Date(),
      isStreaming: true,
      isLoading: true, // Show typing indicator initially
      logs: [], // Initialize empty logs array for thinking process
    };

    setMessages((prev) => [...prev, assistantMessage]);

    try {
      // Send POST request to backend
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          message: content.trim(),
          thread_id: threadId,
        }),
      });

      // Check if response is OK
      if (!response.ok) {
        const errorData = await response.json().catch(() => ({}));
        throw new Error(
          errorData.message || `HTTP ${response.status}: ${response.statusText}`
        );
      }

      // Check if response is SSE
      const contentType = response.headers.get('content-type');
      if (!contentType || !contentType.includes('text/event-stream')) {
        throw new Error('Expected SSE stream but received different content type');
      }

      // Setup EventSource for SSE streaming
      // Note: EventSource doesn't support POST, so we use fetch + manual parsing
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      // Read stream
      while (true) {
        const { done, value } = await reader.read();
        
        if (done) {
          break;
        }

        // Decode chunk and add to buffer
        buffer += decoder.decode(value, { stream: true });

        // Process complete SSE messages in buffer
        const lines = buffer.split('\n');
        buffer = lines.pop() || ''; // Keep incomplete line in buffer

        let currentEvent = null;
        let currentData = '';

        for (const line of lines) {
          if (line.startsWith('event:')) {
            currentEvent = line.substring(6).trim();
          } else if (line.startsWith('data:')) {
            currentData = line.substring(5).trim();
          } else if (line === '' && currentEvent && currentData) {
            // Complete SSE message received
            try {
              const data = JSON.parse(currentData);

              if (currentEvent === 'log') {
                // Handle log event for thinking process
                if (data.step && data.status) {
                  // Mark previous step as completed when moving to new step
                  if (previousStepRef.current && previousStepRef.current !== data.step) {
                    setCompletedProcessingSteps(prev => new Set([...prev, previousStepRef.current]));
                  }
                  
                  // Always mark user_query as completed when first backend step arrives
                  setCompletedProcessingSteps(prev => new Set([...prev, 'user_query']));
                  
                  // Update current step
                  setCurrentProcessingStep(data.step);
                  previousStepRef.current = data.step;
                  
                  setProcessingLogs((prev) => {
                    // Prevent duplicate log entries
                    const isDuplicate = prev.some(
                      (log) => log.step === data.step && log.status === data.status
                    );
                    if (!isDuplicate) {
                      return [...prev, { 
                        step: data.step, 
                        status: data.status, 
                        timestamp: Date.now(),
                        metadata: data.metadata || {} // Include metadata from backend
                      }];
                    }
                    return prev;
                  });
                  
                  // Update messages for backward compatibility
                  setMessages((prev) =>
                    prev.map((msg) => {
                      if (msg.id === assistantMessageId) {
                        // Prevent duplicate log entries
                        const isDuplicate = msg.logs.some(
                          (log) => log.step === data.step && log.status === data.status
                        );
                        
                        if (!isDuplicate) {
                          return {
                            ...msg,
                            logs: [...msg.logs, { step: data.step, status: data.status }]
                          };
                        }
                      }
                      return msg;
                    })
                  );
                }
              } else if (currentEvent === 'message') {
                // Update assistant message with new token
                if (data.token) {
                  setMessages((prev) =>
                    prev.map((msg) =>
                      msg.id === assistantMessageId
                        ? { ...msg, content: msg.content + data.token, isLoading: false }
                        : msg
                    )
                  );
                }
              } else if (currentEvent === 'done') {
                // Streaming complete - mark current step and all previous steps as completed
                setCompletedProcessingSteps(prev => {
                  const updated = new Set(prev);
                  // Add all steps from logs
                  processingLogs.forEach(log => updated.add(log.step));
                  // Add user_query
                  updated.add('user_query');
                  // Add current step if exists
                  if (previousStepRef.current) {
                    updated.add(previousStepRef.current);
                  }
                  return updated;
                });
                
                // Add final_response as active step
                setCurrentProcessingStep('final_response');
                setProcessingLogs(prev => [...prev, { step: 'final_response', status: 'processing', timestamp: Date.now() }]);
                
                // After a short delay, mark final_response as completed and clear current step
                setTimeout(() => {
                  setCompletedProcessingSteps(prev => new Set([...prev, 'final_response']));
                  setCurrentProcessingStep(null);
                }, 500);
                
                setMessages((prev) =>
                  prev.map((msg) =>
                    msg.id === assistantMessageId
                      ? { ...msg, isStreaming: false }
                      : msg
                  )
                );
                setIsLoading(false);
              } else if (currentEvent === 'error') {
                // Error from backend - preserve logs collected before error
                setMessages((prev) =>
                  prev.map((msg) =>
                    msg.id === assistantMessageId
                      ? { ...msg, isStreaming: false }
                      : msg
                  )
                );
                setIsLoading(false);
                throw new Error(data.error || 'An error occurred');
              }
            } catch (parseError) {
              console.error('Failed to parse SSE data:', parseError);
            }

            // Reset for next message
            currentEvent = null;
            currentData = '';
          }
        }
      }

      // Ensure loading is set to false
      setIsLoading(false);

    } catch (err) {
      console.error('Error sending message:', err);
      
      // Remove the streaming assistant message
      setMessages((prev) =>
        prev.filter((msg) => msg.id !== assistantMessageId)
      );

      // Set error message
      const errorMessage = err.message || 'Could not connect to the server. Please try again.';
      setError(errorMessage);

      // Add error message to chat
      const errorMsg = {
        id: `error-${Date.now()}`,
        role: 'assistant',
        content: `Sorry, an error occurred: ${errorMessage}`,
        timestamp: new Date(),
        isError: true,
      };

      setMessages((prev) => [...prev, errorMsg]);
      setIsLoading(false);
    }
  }, [threadId]);

  /**
   * Clear all messages from the chat
   */
  const clearMessages = useCallback(() => {
    setMessages([]);
    setError(null);
    resetProcessingState();
    
    // Close any active EventSource connection
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
      eventSourceRef.current = null;
    }
  }, [resetProcessingState]);

  return {
    messages,
    isLoading,
    error,
    sendMessage,
    clearMessages,
    // Processing state for visualization
    currentProcessingStep,
    completedProcessingSteps,
    processingLogs,
    resetProcessingState,
  };
};

export default useChat;
