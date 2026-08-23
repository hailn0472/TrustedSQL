import React, { useRef, useEffect } from 'react';
import Message from './Message';

/**
 * MessageArea component - Displays the conversation history
 * 
 * @param {Object} props
 * @param {Array} props.messages - Array of message objects
 */
function MessageArea({ messages }) {
  const messagesEndRef = useRef(null);
  const messagesContainerRef = useRef(null);

  /**
   * Automatically scrolls to the bottom when new messages arrive
   */
  useEffect(() => {
    if (messagesEndRef.current) {
      messagesEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [messages]);

  return (
    <div 
      className="bg-white flex-1 overflow-y-auto"
      style={{ scrollBehavior: 'smooth' }}
      ref={messagesContainerRef}
    >
      <div className="max-w-4xl mx-auto p-6">
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full min-h-[400px] text-center">
            <div className="text-6xl mb-4">💬</div>
            <h3 className="text-2xl font-semibold text-gray-800 mb-2">
              Welcome to FARIS!
            </h3>
            <p className="text-gray-600 text-base mb-2">
              Ask about your data or documents
            </p>
            <div className="flex items-center gap-2 text-sm text-gray-500 mt-4">
              <svg className="w-4 h-4 text-amber-500" fill="currentColor" viewBox="0 0 20 20">
                <path d="M11 3a1 1 0 10-2 0v1a1 1 0 102 0V3zM15.657 5.757a1 1 0 00-1.414-1.414l-.707.707a1 1 0 001.414 1.414l.707-.707zM18 10a1 1 0 01-1 1h-1a1 1 0 110-2h1a1 1 0 011 1zM5.05 6.464A1 1 0 106.464 5.05l-.707-.707a1 1 0 00-1.414 1.414l.707.707zM5 10a1 1 0 01-1 1H3a1 1 0 110-2h1a1 1 0 011 1zM8 16v-1h4v1a2 2 0 11-4 0zM12 14c.015-.34.208-.646.477-.859a4 4 0 10-4.954 0c.27.213.462.519.476.859h4.002z" />
              </svg>
              <span>Click icon <strong className="text-amber-600">💡</strong> to see sample questions</span>
            </div>
          </div>
        ) : (
          <div className="flex flex-col space-y-5">
            {messages.map((message) => (
              <Message key={message.id} message={message} />
            ))}
            {/* Scroll anchor */}
            <div ref={messagesEndRef} />
          </div>
        )}
      </div>
    </div>
  );
}

export default MessageArea;
