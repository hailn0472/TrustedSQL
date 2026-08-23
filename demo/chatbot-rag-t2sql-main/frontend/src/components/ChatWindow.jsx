import { useState } from 'react';
import Header from './Header';
import MessageArea from './MessageArea';
import InputBar from './InputBar';

/**
 * ChatWindow component - Main chat interface
 * 
 * @param {Object} props
 * @param {string} props.threadId - The ID of the chat thread
 * @param {Array} props.messages - Array of messages
 * @param {boolean} props.isLoading - Loading state
 * @param {string|null} props.error - Error message
 * @param {Function} props.sendMessage - Function to send a message
 * @param {Function} props.clearMessages - Function to clear messages
 */
function ChatWindow({ threadId, messages, isLoading, error, sendMessage, clearMessages }) {
  const [inputValue, setInputValue] = useState('');

  /**
   * Handle form submission
   */
  const handleSubmit = (e) => {
    e.preventDefault();
    
    if (!inputValue.trim() || isLoading) {
      return;
    }

    sendMessage(inputValue);
    setInputValue('');
  };

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <Header />

      {/* Error Display */}
      {error && (
        <div className="bg-red-50 border-l-4 border-red-500 p-4 mx-4 mt-4 rounded">
          <div className="flex items-center">
            <span className="text-red-500 text-xl mr-3">⚠️</span>
            <span className="text-red-700 text-sm">{error}</span>
          </div>
        </div>
      )}

      {/* Messages Area */}
      <MessageArea messages={messages} />

      {/* Input Bar */}
      <InputBar 
        currentMessage={inputValue}
        setCurrentMessage={setInputValue}
        onSubmit={handleSubmit}
        isLoading={isLoading}
      />
    </div>
  );
}

export default ChatWindow;
