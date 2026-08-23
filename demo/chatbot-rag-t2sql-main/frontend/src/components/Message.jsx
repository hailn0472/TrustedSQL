import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import TypingIndicator from './TypingIndicator';

/**
 * Message component displays a single chat message bubble
 * 
 * @param {Object} props
 * @param {Object} props.message - Message object
 * @param {string} props.message.id - Unique message ID
 * @param {string} props.message.role - Message role ('user' or 'assistant')
 * @param {string} props.message.content - Message text content
 * @param {Date} props.message.timestamp - Message timestamp
 * @param {boolean} [props.message.isStreaming] - Whether message is currently streaming
 * @param {boolean} [props.message.isLoading] - Whether message is loading
 */
function Message({ message }) {
  const { role, content, timestamp, isStreaming, isLoading } = message;
  
  // Determine if this is a user message based on role
  const isUserMessage = role === 'user';
  
  // Format timestamp to readable format
  const formatTimestamp = (date) => {
    if (!date) return '';
    
    const messageDate = new Date(date);
    const now = new Date();
    const diffInSeconds = Math.floor((now - messageDate) / 1000);
    
    // If less than 1 minute ago, show "Just now"
    if (diffInSeconds < 60) {
      return 'Just now';
    }
    
    // If today, show time only
    if (messageDate.toDateString() === now.toDateString()) {
      return messageDate.toLocaleTimeString('en-US', { 
        hour: '2-digit', 
        minute: '2-digit' 
      });
    }
    
    // Otherwise show date and time
    return messageDate.toLocaleString('en-US', {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit'
    });
  };

  return (
    <div className={`flex ${isUserMessage ? 'justify-end' : 'justify-start'}`}>
      <div 
        className={`
          py-3 px-5 rounded-lg max-w-[70%]
          ${isUserMessage 
            ? 'bg-gradient-to-br from-[#5E507F] to-[#4A3F71] text-white rounded-br-none shadow-md' 
            : 'bg-gray-50 text-gray-800 border border-gray-200 rounded-bl-none shadow-sm'
          }
        `}
      >
        <div className="message-content prose prose-sm max-w-none">
          {isLoading ? (
            <TypingIndicator />
          ) : (
            <>
              {isUserMessage ? (
                // User messages: plain text
                <span>{content}</span>
              ) : (
                // Assistant messages: render markdown
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    // Custom styling for markdown elements
                    p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
                    strong: ({ children }) => <strong className="font-semibold text-gray-900">{children}</strong>,
                    ol: ({ children }) => <ol className="list-decimal list-inside space-y-1 my-2">{children}</ol>,
                    ul: ({ children }) => <ul className="list-disc list-inside space-y-1 my-2">{children}</ul>,
                    li: ({ children }) => <li className="ml-2">{children}</li>,
                    code: ({ inline, children }) => 
                      inline ? (
                        <code className="bg-gray-200 px-1.5 py-0.5 rounded text-sm font-mono">{children}</code>
                      ) : (
                        <code className="block bg-gray-200 p-2 rounded text-sm font-mono my-2 overflow-x-auto">{children}</code>
                      ),
                    h1: ({ children }) => <h1 className="text-xl font-bold mb-2 mt-3">{children}</h1>,
                    h2: ({ children }) => <h2 className="text-lg font-bold mb-2 mt-3">{children}</h2>,
                    h3: ({ children }) => <h3 className="text-base font-bold mb-1 mt-2">{children}</h3>,
                    blockquote: ({ children }) => (
                      <blockquote className="border-l-4 border-gray-300 pl-3 italic my-2">{children}</blockquote>
                    ),
                  }}
                >
                  {content}
                </ReactMarkdown>
              )}
              {isStreaming && <span className="inline-block ml-1 animate-pulse">▋</span>}
            </>
          )}
        </div>
        {timestamp && !isLoading && (
          <div className={`text-xs mt-1 ${isUserMessage ? 'text-white/70' : 'text-gray-500'}`}>
            {formatTimestamp(timestamp)}
          </div>
        )}
      </div>
    </div>
  );
}

export default Message;
