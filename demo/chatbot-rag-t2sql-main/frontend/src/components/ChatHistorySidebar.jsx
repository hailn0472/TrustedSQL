import React from 'react';
import { useChatHistory } from '../hooks/useChatHistory';
import './ChatHistorySidebar.css';

const ChatHistorySidebar = ({ activeThreadId, onSelectThread, onNewChat }) => {
  const { threads, isLoading, error } = useChatHistory();

  return (
    <aside className="chat-history-sidebar">
      <div className="sidebar-header">
        <h2>Chat History</h2>
        <button className="new-chat-button" onClick={onNewChat}>
          + New Chat
        </button>
      </div>
      {isLoading && <div className="sidebar-loading">Loading...</div>}
      {error && <div className="sidebar-error">{error}</div>}
      <ul className="chat-history-list">
        {threads.map((thread) => (
          <li
            key={thread.id}
            className={`history-item ${thread.id === activeThreadId ? 'active' : ''}`}
            onClick={() => onSelectThread(thread.id)}>
            <a href="#">{thread.title || 'New Chat'}</a>
          </li>
        ))}
      </ul>
    </aside>
  );
};

export default ChatHistorySidebar;
