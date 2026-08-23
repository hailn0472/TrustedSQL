import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import App from './App';
import { ApiClient } from '../api/client';
import { allowResult, denyResult, makeApiClient } from '../test/fixtures';

async function readyApp(apiClient: ApiClient) {
  render(<App apiClient={apiClient} />);
  await waitFor(() => expect(screen.getByTestId('readiness-indicator-pill')).toHaveTextContent('READY'));
}

function enterMessage(value: string) {
  fireEvent.change(screen.getByRole('textbox', { name: /chat message/i }), { target: { value } });
  fireEvent.click(screen.getByRole('button', { name: /send message/i }));
}

const queuedJob = (turn = 1) => ({
  runId: `run-test-${turn}`,
  conversationId: 'conversation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
  state: 'queued' as const,
  scenarioKey: 'multiturn',
  sampleId: 'interactive-multiturn',
  throughTurn: turn,
  turnType: 'multi' as const,
});

describe('live interactive chat flow', () => {
  it('submits user-authored text and renders it only after job acceptance', async () => {
    let resolveJob: ((job: ReturnType<typeof queuedJob>) => void) | undefined;
    const createRun = vi.fn(() => new Promise<ReturnType<typeof queuedJob>>((resolve) => { resolveJob = resolve; }));
    await readyApp(makeApiClient({ createRun }));

    enterMessage('My custom query');
    expect(screen.queryByTestId('user-prompt-bubble')).not.toBeInTheDocument();
    expect(createRun).toHaveBeenCalledWith('My custom query', null, expect.any(AbortSignal));

    resolveJob?.(queuedJob());
    await waitFor(() => expect(screen.getByTestId('user-prompt-bubble')).toHaveTextContent('My custom query'));
    expect(screen.getByRole('textbox', { name: /chat message/i })).toHaveValue('');
  });

  it('sends only the new message with the server-issued conversation identity', async () => {
    let callbacks: Parameters<ApiClient['subscribeRunEvents']>[2] | undefined;
    const createRun = vi.fn()
      .mockResolvedValueOnce(queuedJob(1))
      .mockResolvedValueOnce(queuedJob(2));
    const subscribeRunEvents = vi.fn((_id, _sequence, nextCallbacks) => { callbacks = nextCallbacks; return vi.fn(); });
    await readyApp(makeApiClient({ createRun, subscribeRunEvents }));

    enterMessage('First custom query');
    await waitFor(() => expect(screen.getByTestId('user-prompt-bubble')).toBeInTheDocument());
    await act(async () => callbacks?.onComplete(allowResult));

    enterMessage('Follow-up query');
    await waitFor(() => expect(createRun).toHaveBeenCalledTimes(2));
    expect(createRun).toHaveBeenLastCalledWith('Follow-up query', queuedJob().conversationId, expect.any(AbortSignal));
  });

  it('renders an ALLOW result using backend SQL and rows', async () => {
    const subscribeRunEvents = vi.fn((_id, _sequence, callbacks) => {
      setTimeout(() => callbacks.onComplete(allowResult), 0);
      return vi.fn();
    });
    await readyApp(makeApiClient({ subscribeRunEvents }));
    enterMessage('Show my grades');
    await waitFor(() => expect(screen.getByTestId('result-table-container')).toBeInTheDocument());
    expect(screen.getByTestId('sql-box')).toHaveTextContent('SELECT grade');
    expect(screen.getByRole('table').querySelector('caption')).toHaveTextContent('2 rows returned');
  });

  it('renders DENY with detector/enforcer and no result table', async () => {
    const subscribeRunEvents = vi.fn((_id, _sequence, callbacks) => {
      setTimeout(() => callbacks.onComplete(denyResult), 0);
      return vi.fn();
    });
    await readyApp(makeApiClient({ subscribeRunEvents }));
    enterMessage('Show restricted details');
    await waitFor(() => expect(screen.getByTestId('deny-card')).toBeInTheDocument());
    expect(screen.getByTestId('deny-card')).toHaveTextContent('M5');
    expect(screen.getByTestId('deny-card')).toHaveTextContent('Database Untouched: Yes');
    expect(screen.queryByTestId('result-table-container')).not.toBeInTheDocument();
  });

  it('allows queued cancellation but disables it once a module runs', async () => {
    let callbacks: Parameters<ApiClient['subscribeRunEvents']>[2] | undefined;
    const subscribeRunEvents = vi.fn((_id, _sequence, nextCallbacks) => { callbacks = nextCallbacks; return vi.fn(); });
    await readyApp(makeApiClient({ subscribeRunEvents }));
    enterMessage('Queued query');
    await waitFor(() => expect(screen.getByRole('button', { name: /cancel queued message/i })).toBeInTheDocument());
    await act(async () => callbacks?.onEvent({ eventType: 'module', data: { runId: 'run-test-1', module: 'M1', streamSequence: 1, decision: 'ALLOW' } }));
    expect(screen.getByRole('button', { name: /cancel running message/i })).toBeDisabled();
  });

  it('aborts a pending chat submission on reset', async () => {
    let requestSignal: AbortSignal | undefined;
    let resolveJob: ((job: ReturnType<typeof queuedJob>) => void) | undefined;
    const createRun = vi.fn((_message: string, _conversationId: string | null, signal?: AbortSignal) => {
      requestSignal = signal;
      return new Promise<ReturnType<typeof queuedJob>>((resolve) => { resolveJob = resolve; });
    });
    await readyApp(makeApiClient({ createRun }));
    enterMessage('Pending query');
    fireEvent.click(screen.getByRole('button', { name: /reset/i }));
    resolveJob?.(queuedJob());
    expect(requestSignal?.aborted).toBe(true);
    expect(screen.queryByTestId('user-prompt-bubble')).not.toBeInTheDocument();
  });

  it('rejects a job response that does not match the dynamic chat contract', async () => {
    const createRun = vi.fn().mockResolvedValue({ ...queuedJob(), scenarioKey: 'wrong', throughTurn: 2 });
    await readyApp(makeApiClient({ createRun }));
    enterMessage('Query');
    await waitFor(() => expect(screen.getByTestId('error-card')).toBeInTheDocument());
    expect(screen.queryByTestId('user-prompt-bubble')).not.toBeInTheDocument();
  });

  it('shows telemetry unavailability without fabricating a runtime result', async () => {
    let callbacks: Parameters<ApiClient['subscribeRunEvents']>[2] | undefined;
    const subscribeRunEvents = vi.fn((_id, _sequence, nextCallbacks) => { callbacks = nextCallbacks; return vi.fn(); });
    await readyApp(makeApiClient({ subscribeRunEvents }));
    enterMessage('Query');
    await waitFor(() => expect(screen.getByRole('button', { name: /cancel queued message/i })).toBeInTheDocument());
    await act(async () => callbacks?.onTelemetryUnavailable?.({ runId: 'run-test-1', executionState: 'unknown', message: 'Execution state unknown; telemetry unavailable' }));
    expect(screen.getByTestId('telemetry-unavailable-card')).toBeInTheDocument();
    expect(screen.queryByTestId('allow-result-card')).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /chat message/i })).toBeDisabled();
  });

  it('replays only the latest validated route', async () => {
    const subscribeRunEvents = vi.fn((_id, _sequence, callbacks) => {
      setTimeout(() => callbacks.onComplete(allowResult), 0);
      return vi.fn();
    });
    await readyApp(makeApiClient({ subscribeRunEvents }));
    enterMessage('Query');
    await waitFor(() => expect(screen.getByRole('button', { name: /replay route/i })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /replay route/i }));
    expect(screen.getByRole('button', { name: /replay route/i })).toBeEnabled();
  });
});
