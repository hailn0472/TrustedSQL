import React, { useState, useEffect } from 'react';
import ChatLayout from './components/ChatLayout';
import ChatHistorySidebar from './components/ChatHistorySidebar';

function App() {
  const [activeThreadId, setActiveThreadId] = useState(null);

  // Function to generate a simple UUID for new chats
  const generateUUID = () => {
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  };

  useEffect(() => {
    // On initial load, check for a thread ID in local storage
    const storedThreadId = localStorage.getItem('chatbot_thread_id');
    if (storedThreadId) {
      setActiveThreadId(storedThreadId);
    }
  }, []);

  const handleSelectThread = (threadId) => {
    localStorage.setItem('chatbot_thread_id', threadId);
    setActiveThreadId(threadId);
  };

  const handleNewChat = () => {
    const newThreadId = generateUUID();
    localStorage.setItem('chatbot_thread_id', newThreadId);
    setActiveThreadId(newThreadId);
  };

  return (
    <div className="bg-[#F9F9F5] h-screen w-screen overflow-hidden">
      <main className="w-full h-full bg-white">
        {activeThreadId ? (
          <ChatLayout key={activeThreadId} threadId={activeThreadId} />
        ) : (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="text-6xl mb-4">💬</div>
              <h3 className="text-xl font-semibold text-gray-800 mb-2">
                Welcome to FARIS!
              </h3>
              <p className="text-gray-600">
                Select a conversation or start a new one.
              </p>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
