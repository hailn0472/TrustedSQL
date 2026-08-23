import React, { useState, useRef, useEffect } from 'react';
import QuickQuestions from './QuickQuestions';

/**
 * InputBar component - Modern input interface with visual affordances
 * 
 * @param {Object} props
 * @param {string} props.currentMessage - Current input value
 * @param {Function} props.setCurrentMessage - Function to update input value
 * @param {Function} props.onSubmit - Function to handle form submission
 * @param {boolean} props.isLoading - Whether a message is being sent
 */
function InputBar({ currentMessage, setCurrentMessage, onSubmit, isLoading }) {
  const [showExamples, setShowExamples] = useState(false);
  const dropdownRef = useRef(null);

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      onSubmit(e);
    }
  };

  const handleQuestionSelect = (question) => {
    setCurrentMessage(question);
    setShowExamples(false);
  };

  // Close dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setShowExamples(false);
      }
    };

    if (showExamples) {
      document.addEventListener('mousedown', handleClickOutside);
    }

    return () => {
      document.removeEventListener('mousedown', handleClickOutside);
    };
  }, [showExamples]);

  return (
    <div className="p-4 bg-white border-t border-gray-100">
      <form onSubmit={onSubmit} className="max-w-4xl mx-auto relative">
        <div className="rounded-full bg-gray-50 shadow-sm border border-gray-200 p-3 flex items-center hover:shadow-md transition-shadow duration-200">
          {/* Emoji button */}
          <button
            type="button"
            className="rounded-full p-2 hover:bg-gray-200 transition-all duration-200 flex-shrink-0"
            aria-label="Add emoji"
          >
            <span className="text-xl">😊</span>
          </button>

          {/* Text input */}
          <input
            type="text"
            value={currentMessage}
            onChange={(e) => setCurrentMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Enter your question..."
            disabled={isLoading}
            className="flex-grow px-4 py-2 bg-transparent focus:outline-none text-gray-800 placeholder-gray-400"
          />

          {/* Examples button */}
          <div className="relative" ref={dropdownRef}>
            <button
              type="button"
              onClick={() => setShowExamples(!showExamples)}
              className="rounded-full p-2 hover:bg-gray-200 transition-all duration-200 flex-shrink-0 relative group"
              aria-label="Example questions"
              title="Câu hỏi mẫu"
            >
              <svg className="w-5 h-5 text-amber-500 group-hover:text-amber-600" fill="currentColor" viewBox="0 0 20 20">
                <path d="M11 3a1 1 0 10-2 0v1a1 1 0 102 0V3zM15.657 5.757a1 1 0 00-1.414-1.414l-.707.707a1 1 0 001.414 1.414l.707-.707zM18 10a1 1 0 01-1 1h-1a1 1 0 110-2h1a1 1 0 011 1zM5.05 6.464A1 1 0 106.464 5.05l-.707-.707a1 1 0 00-1.414 1.414l.707.707zM5 10a1 1 0 01-1 1H3a1 1 0 110-2h1a1 1 0 011 1zM8 16v-1h4v1a2 2 0 11-4 0zM12 14c.015-.34.208-.646.477-.859a4 4 0 10-4.954 0c.27.213.462.519.476.859h4.002z" />
              </svg>
              <span className="absolute -top-0.5 -right-0.5 bg-teal-500 text-white text-[8px] font-bold rounded-full w-3.5 h-3.5 flex items-center justify-center shadow-sm">
                E
              </span>
            </button>

            {/* Examples Dropdown */}
            {showExamples && (
              <div className="absolute bottom-full right-0 mb-2 bg-white rounded-lg shadow-xl border border-gray-200 z-50">
                <QuickQuestions onQuestionClick={handleQuestionSelect} />
              </div>
            )}
          </div>

          {/* Send button */}
          <button
            type="submit"
            disabled={isLoading || !currentMessage.trim()}
            className="group bg-gradient-to-r from-teal-500 to-teal-400 rounded-full p-3 ml-2 hover:shadow-lg transition-all duration-200 disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
            aria-label="Send message"
          >
            <svg 
              className="w-5 h-5 text-white rotate-45 group-hover:scale-110 transition-transform duration-200" 
              fill="currentColor" 
              viewBox="0 0 24 24"
            >
              <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
            </svg>
          </button>
        </div>
      </form>
    </div>
  );
}

export default InputBar;
