import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from './App';
import { ApiClient } from '../api/client';
import { makeApiClient } from '../test/fixtures';

async function renderReady(apiClient = makeApiClient()) {
  render(<App apiClient={apiClient} />);
  await waitFor(() => expect(screen.getByTestId('readiness-indicator-pill')).toHaveTextContent('Readiness: READY'));
  return apiClient;
}

describe('Live chat bootstrap and prompt library', () => {
  it('loads only the multiturn prompt library', async () => {
    await renderReady();
    expect(screen.getAllByTestId('scenario-id-label')).toHaveLength(1);
    expect(screen.getByTestId('scenario-id-label')).toHaveTextContent('MT-MAL-420');
    expect(screen.queryByText(/ST-BENIGN|ST-PI|ST-RBAC/)).not.toBeInTheDocument();
  });

  it('shows no static prompts and disables chat after bootstrap failure', async () => {
    const apiClient = makeApiClient({ fetchBootstrap: vi.fn().mockRejectedValue(new Error('Backend offline (503)')) as ApiClient['fetchBootstrap'] });
    render(<App apiClient={apiClient} />);
    await waitFor(() => expect(screen.getByTestId('readiness-indicator-pill')).toHaveTextContent('NOT-READY'));
    expect(screen.queryByTestId('scenario-id-label')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /chat message/i })).toBeDisabled();
    expect(screen.getByTestId('global-live-region')).toHaveTextContent('Backend offline (503)');
  });

  it('renders a real editable chatbox with a natural greeting', async () => {
    await renderReady();
    const chatbox = screen.getByRole('textbox', { name: /chat message/i });
    expect(chatbox).toBeEnabled();
    fireEvent.change(chatbox, { target: { value: 'My own database question' } });
    expect(chatbox).toHaveValue('My own database question');
    expect(screen.getByTestId('assistant-greeting-bubble')).toHaveTextContent(/type any database question/i);
  });

  it('expands six reference queries from the arrow dropdown', async () => {
    await renderReady();
    const toggle = screen.getByRole('button', { name: /multiturn prompt library/i });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByTestId('multiturn-prompt-list').querySelectorAll('li')).toHaveLength(6);
    expect(screen.getByRole('button', { name: /copy turn 1 query/i })).toBeInTheDocument();
  });

  it('keeps route, telemetry and controls accessible', async () => {
    await renderReady();
    expect(screen.getByRole('banner')).toBeInTheDocument();
    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(screen.getByRole('log')).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByRole('button', { name: /send message/i })).toBeDisabled();
    expect(screen.getByRole('button', { name: /replay route/i })).toBeDisabled();
  });

  it('reset clears typed text and conversation state', async () => {
    await renderReady();
    const chatbox = screen.getByRole('textbox', { name: /chat message/i });
    fireEvent.change(chatbox, { target: { value: 'Temporary question' } });
    fireEvent.click(screen.getByRole('button', { name: /reset/i }));
    expect(chatbox).toHaveValue('');
    expect(screen.getByTestId('selected-scenario-id')).toHaveTextContent('MT-MAL-420');
  });
});
