import { useState, useEffect, useCallback } from 'react';

const API_URL = '/api'; // Base URL for API

export const useChatHistory = () => {
  const [threads, setThreads] = useState([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);

  const fetchThreads = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const response = await fetch(`${API_URL}/threads`);
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      const data = await response.json();
      // Assuming the API returns an array of threads with { id, title }
      setThreads(data || []);
    } catch (err) {
      console.error('Failed to fetch chat history:', err);
      setError('Could not load chat history.');
      setThreads([]); // Clear threads on error
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchThreads();
  }, [fetchThreads]);

  return { threads, isLoading, error, refreshThreads: fetchThreads };
};
