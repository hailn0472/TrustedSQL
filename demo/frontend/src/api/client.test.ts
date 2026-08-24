import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createApiClient,
  mapBootstrapToScenarios,
  validateBootstrapResponse,
  validateFinalResultDto,
  validateModuleEventDto,
  validateRunStatusEvent,
  validateRunStateDto,
} from './client';

const architecture = { label: 'TrustedSQL-only demo', modules: ['C0', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'X1'] };
const conversationId = 'conversation-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const catalogItem = {
  key: 'multiturn', canonical_id: 'MT-MAL-420', title: 'Prompt library', description: 'Reference queries', role: 'lecturer', user_id: 1, turn_type: 'multi',
  turns: [{ turn_id: 1, nlq: 'Select grades', display_label: 'BENIGN' }, { turn_id: 2, nlq: 'Continue the query', display_label: 'BENIGN' }],
};
const validBootstrap = { ready: true, readiness: { ready: true }, catalog: { multiturn: catalogItem }, architecture };
const allow = {
  runId: 'run-1', sampleId: 'interactive-multiturn', turnId: 1,
  decision: 'ALLOW', executed: true, dbTouched: true,
  route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'], columns: ['grade'], rows: [['A']], sql: 'SELECT grade',
};

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  listeners = new Map<string, (event: Event) => void>();
  close = vi.fn();
  onerror: (() => void) | null = null;
  onopen: (() => void) | null = null;
  constructor(public url: string) { FakeEventSource.instances.push(this); }
  addEventListener(type: string, listener: (event: Event) => void) { this.listeners.set(type, listener); }
  emit(type: string, data: unknown) { this.listeners.get(type)?.({ type, data: JSON.stringify(data) } as MessageEvent); }
  fail() { this.onerror?.(); }
}

describe('ST-09 typed API client', () => {
  let mockFetch: ReturnType<typeof vi.fn>;
  beforeEach(() => { mockFetch = vi.fn(); vi.stubGlobal('fetch', mockFetch); vi.stubGlobal('EventSource', FakeEventSource); FakeEventSource.instances = []; });
  afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });

  it('validates exact architecture and maps backend catalog while ignoring raw labels', () => {
    const parsed = validateBootstrapResponse(validBootstrap);
    expect(parsed).not.toBeNull();
    expect(parsed?.architecture).toEqual(architecture.modules);
    expect(parsed?.catalog[0]).not.toHaveProperty('attack_kind');
    expect(parsed?.catalog[0].turns[0].label).toBe('Turn 1');
    expect(mapBootstrapToScenarios(parsed!.catalog)[0]).toMatchObject({ categoryBadge: 'Prompt Library', canonicalId: 'MT-MAL-420', turnType: 'multi', role: 'lecturer', userId: 1 });
    expect(validateBootstrapResponse({ ...validBootstrap, architecture: { modules: ['C0'] } })).toBeNull();
    expect(validateBootstrapResponse({ ...validBootstrap, catalog: 'invalid' })).toBeNull();
    expect(validateBootstrapResponse({ ...validBootstrap, ready: true, readiness: { ready: false } })).toBeNull();
    expect(validateBootstrapResponse({ ...validBootstrap, ready: false, readiness: { ready: true } })).toBeNull();
    expect(validateBootstrapResponse({ ...validBootstrap, readiness: { message: 'unknown' } })).toBeNull();
    expect(validateBootstrapResponse({ ...validBootstrap, readiness: undefined })).toBeNull();
    expect(validateBootstrapResponse({ ...validBootstrap, readiness: 'ready' })).toBeNull();
    expect(validateBootstrapResponse({ ...validBootstrap, readiness: { ready: 'true' } })).toBeNull();
  });

  it('rejects malformed untrusted run/event/final payloads', () => {
    expect(validateModuleEventDto({ runId: 'r', moduleId: 'M1', streamSequence: '1' })).toBeNull();
    expect(validateModuleEventDto({ runId: 'r', moduleId: 'unknown', streamSequence: 1 })).toBeNull();
    expect(validateFinalResultDto({ ...allow, rows: [['A', { secret: true }]] })).toBeNull();
    expect(validateFinalResultDto({ ...allow, route: ['chat'] })).toBeNull();
    expect(validateFinalResultDto({ ...allow, detectedAt: 'attacker-controlled-detector' })).toBeNull();
    const denied = validateFinalResultDto({
      runId: 'run-1', sampleId: 'interactive-multiturn', turnId: 1,
      decision: 'DENY', executed: false, dbTouched: false, detectedAt: 'M5', enforcedAt: 'trustedsql',
      route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'], error: 'traceback prompt schema provider',
    });
    expect(denied?.error).toBe('Access denied by security policy');
    expect(validateRunStateDto({ runId: 'r', state: 'complete', scenarioKey: 'multiturn', sampleId: 'interactive-multiturn', throughTurn: 1, turnType: 'multi', events: [{ runId: 'r', moduleId: 'M1', streamSequence: 1, eventType: 'unknown' }], finalResult: allow })).toBeNull();
  });

  it('counts Unicode code points consistently with the Python RAG boundary', () => {
    const ragResult = {
      runId: 'run-1', sampleId: 'interactive-multiturn', turnId: 1,
      decision: 'ALLOW', executed: false, dbTouched: false,
      route: ['chat', 'orchestrator', 'context_memory', 'rag'],
      mode: 'trustedsql', resultType: 'rag', answer: 'Grounded answer',
      sources: [{ citation: 1, title: 'tuition.md', uri: 'tuition.md', snippet: `${'x'.repeat(359)}😀` }],
    };
    expect(ragResult.sources[0].snippet.length).toBe(361);
    expect(Array.from(ragResult.sources[0].snippet)).toHaveLength(360);
    expect(validateFinalResultDto(ragResult)).not.toBeNull();
    expect(validateFinalResultDto({
      ...ragResult,
      sources: [{ ...ragResult.sources[0], snippet: `${ragResult.sources[0].snippet}x` }],
    })).toBeNull();
  });

  it('uses same-origin bootstrap and strict exact POST body', async () => {
    mockFetch.mockResolvedValueOnce({ ok: true, status: 200, json: async () => validBootstrap });
    const client = createApiClient('/api');
    const bootstrap = await client.fetchBootstrap();
    expect(mockFetch).toHaveBeenCalledWith('/api/bootstrap', { headers: { Accept: 'application/json' } });
    expect(bootstrap.scenarios[0].turns[0].nlq).toBe('Select grades');

    mockFetch.mockResolvedValueOnce({ ok: true, status: 202, json: async () => ({ runId: 'r', conversationId, state: 'queued', scenarioKey: 'multiturn', sampleId: 'interactive-multiturn', throughTurn: 1, turnType: 'multi' }) });
    await client.createRun('Select grades', null);
    expect(mockFetch).toHaveBeenLastCalledWith('/api/runs', expect.objectContaining({ method: 'POST', body: JSON.stringify({ message: 'Select grades', conversationId: null }) }));
  });

  it('redacts HTTP/JSON failures into bounded safe errors', async () => {
    mockFetch.mockResolvedValueOnce({ ok: false, status: 500, json: async () => ({ traceback: 'private traceback' }) });
    await expect(createApiClient().fetchBootstrap()).rejects.toThrow('Bootstrap request failed (500)');
  });

  it('preserves module/revision/retract types, resumes after last sequence, and cleans up', () => {
    vi.useFakeTimers();
    const events: string[] = [];
    const client = createApiClient('/api');
    const unsubscribe = client.subscribeRunEvents('run-1', 0, {
      onEvent: ({ eventType }) => events.push(eventType), onComplete: vi.fn(), onError: vi.fn(),
    });
    const first = FakeEventSource.instances[0];
    expect(first.url).toContain('after=0');
    first.emit('module', { runId: 'run-1', moduleId: 'M1', streamSequence: 1, decision: 'allow' });
    first.emit('module', { runId: 'other', moduleId: 'M2', streamSequence: 2 });
    first.emit('revision', { runId: 'run-1', moduleId: 'M1', streamSequence: 2, revision: 1, decision: 'deny' });
    first.emit('retract', { runId: 'run-1', moduleId: 'M1', streamSequence: 3 });
    expect(events).toEqual(['module', 'revision', 'retract']);
    first.fail();
    vi.advanceTimersByTime(250);
    expect(FakeEventSource.instances[1].url).toContain('after=3');
    unsubscribe();
    expect(first.close).toHaveBeenCalled();
    expect(FakeEventSource.instances[1].close).toHaveBeenCalled();
  });

  it('advances the resume cursor past stale revisions and resets retries after a successful open', () => {
    vi.useFakeTimers();
    const onEvent = vi.fn();
    createApiClient('/api').subscribeRunEvents('run-1', 0, { onEvent, onComplete: vi.fn(), onError: vi.fn() });
    const first = FakeEventSource.instances[0];
    first.onopen?.();
    first.emit('module', { runId: 'run-1', moduleId: 'M1', streamSequence: 1, revision: 2 });
    first.emit('revision', { runId: 'run-1', moduleId: 'M1', streamSequence: 2, revision: 1 });
    first.fail();
    vi.advanceTimersByTime(250);
    expect(FakeEventSource.instances[1].url).toContain('after=2');
    FakeEventSource.instances[1].onopen?.();
    FakeEventSource.instances[1].fail();
    vi.advanceTimersByTime(250);
    expect(FakeEventSource.instances[2].url).toContain('after=2');
    expect(onEvent).toHaveBeenCalledTimes(1);
  });

  it('handles terminal status only when its final result is validated', () => {
    const onComplete = vi.fn();
    const onStatus = vi.fn();
    const client = createApiClient();
    const unsubscribe = client.subscribeRunEvents('run-1', 0, { onEvent: vi.fn(), onComplete, onError: vi.fn(), onStatus });
    FakeEventSource.instances[0].emit('status', { runId: 'run-1', state: 'complete', finalResult: allow });
    expect(onStatus).toHaveBeenCalledWith(expect.objectContaining({ state: 'complete' }));
    expect(onComplete).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('requires terminal status decisions to correlate with their state', () => {
    expect(validateRunStatusEvent({ runId: 'run-1', state: 'complete', finalResult: { ...allow, decision: 'DENY', executed: false, dbTouched: false, detectedAt: 'M5', enforcedAt: 'trustedsql', route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'] } })).toBeNull();
    expect(validateRunStatusEvent({ runId: 'run-1', state: 'denied', finalResult: allow })).toBeNull();
    expect(validateRunStatusEvent({ runId: 'run-1', state: 'queued', finalResult: allow })).toBeNull();
    expect(validateRunStatusEvent({ runId: 'run-1', state: 'running', finalResult: allow })).toBeNull();
    expect(validateRunStatusEvent({ runId: 'run-1', state: 'error', finalResult: allow })).toBeNull();
    expect(validateRunStatusEvent({ runId: 'run-1', state: 'cancelled', finalResult: allow })).toBeNull();
  });

  it('rejects a getRun response belonging to a different requested run', async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ runId: 'foreign-run', state: 'running', scenarioKey: 'multiturn', sampleId: 'interactive-multiturn', throughTurn: 1, turnType: 'multi', events: [] }),
    });

    await expect(createApiClient('/api').getRun('run-1')).rejects.toThrow('Invalid run state response');
  });

  it('requires final-result identity and rejects a foreign nested result', () => {
    expect(validateFinalResultDto({ ...allow, runId: undefined })).toBeNull();
    expect(validateFinalResultDto({ ...allow, sampleId: undefined })).toBeNull();
    expect(validateFinalResultDto({ ...allow, turnId: 2 })).toBeNull();
    expect(validateRunStatusEvent({
      runId: 'run-1', state: 'complete', finalResult: { ...allow, runId: 'foreign-run' },
    })).toBeNull();
    expect(validateRunStatusEvent(
      { runId: 'run-1', state: 'complete', finalResult: allow },
      { runId: 'run-1', sampleId: 'other-sample', throughTurn: 1 },
    )).toBeNull();
    expect(validateRunStateDto({
      runId: 'run-1', state: 'complete', scenarioKey: 'multiturn', sampleId: 'interactive-multiturn', throughTurn: 1, turnType: 'multi', events: [],
      finalResult: { ...allow, runId: 'foreign-run' },
    })).toBeNull();
  });

  it('normalizes backend object rows before dispatching terminal status', async () => {
    const onStatus = vi.fn();
    createApiClient().subscribeRunEvents('run-1', 0, {
      onEvent: vi.fn(), onComplete: vi.fn(), onError: vi.fn(), onStatus,
    });
    const source = FakeEventSource.instances[0];

    source.emit('status', {
      runId: 'run-1',
      state: 'complete',
      finalResult: {
        ...allow,
        columns: ['fullname'],
        rows: [{ fullname: 'Ngo Duc Kien' }],
      },
    });

    expect(onStatus).toHaveBeenCalledWith(expect.objectContaining({
      state: 'complete',
      finalResult: expect.objectContaining({ rows: [['Ngo Duc Kien']] }),
    }));
    expect(source.close).toHaveBeenCalled();
  });

  it('reconciles an exhausted SSE transport to a terminal runtime status without reporting execution error', async () => {
    vi.useFakeTimers();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ runId: 'run-1', state: 'complete', scenarioKey: 'multiturn', sampleId: 'interactive-multiturn', throughTurn: 1, turnType: 'multi', events: [], finalResult: allow }),
    });
    const onStatus = vi.fn();
    const onError = vi.fn();
    const onTelemetryUnavailable = vi.fn();
    createApiClient('/api').subscribeRunEvents('run-1', 0, {
      onEvent: vi.fn(), onComplete: vi.fn(), onError, onStatus, onTelemetryUnavailable,
    });

    for (const delay of [250, 500, 750]) {
      FakeEventSource.instances.at(-1)?.fail();
      await vi.advanceTimersByTimeAsync(delay);
    }
    FakeEventSource.instances.at(-1)?.fail();
    await vi.runAllTimersAsync();

    expect(mockFetch).toHaveBeenCalledWith('/api/runs/run-1', { headers: { Accept: 'application/json' } });
    expect(onStatus).toHaveBeenCalledWith(expect.objectContaining({ runId: 'run-1', state: 'complete' }));
    expect(onTelemetryUnavailable).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it('reports unknown execution state and telemetry unavailability when reconciliation fails', async () => {
    vi.useFakeTimers();
    mockFetch.mockRejectedValueOnce(new Error('status unavailable'));
    const onError = vi.fn();
    const onTelemetryUnavailable = vi.fn();
    createApiClient('/api').subscribeRunEvents('run-1', 0, {
      onEvent: vi.fn(), onComplete: vi.fn(), onError, onTelemetryUnavailable,
    });

    for (const delay of [250, 500, 750]) {
      FakeEventSource.instances.at(-1)?.fail();
      await vi.advanceTimersByTimeAsync(delay);
    }
    FakeEventSource.instances.at(-1)?.fail();
    await vi.runAllTimersAsync();

    expect(onTelemetryUnavailable).toHaveBeenCalledWith(expect.objectContaining({
      runId: 'run-1', executionState: 'unknown',
    }));
    expect(onTelemetryUnavailable.mock.calls[0][0].message).toMatch(/execution state unknown.*telemetry unavailable/i);
    expect(onError).not.toHaveBeenCalled();
  });

  it('reports a reconciled nonterminal state as telemetry unavailable without terminalizing the run', async () => {
    vi.useFakeTimers();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ runId: 'run-1', state: 'running', scenarioKey: 'multiturn', sampleId: 'interactive-multiturn', throughTurn: 1, turnType: 'multi', events: [] }),
    });
    const onStatus = vi.fn();
    const onError = vi.fn();
    const onTelemetryUnavailable = vi.fn();
    createApiClient('/api').subscribeRunEvents('run-1', 0, {
      onEvent: vi.fn(), onComplete: vi.fn(), onError, onStatus, onTelemetryUnavailable,
    });

    for (const delay of [250, 500, 750]) {
      FakeEventSource.instances.at(-1)?.fail();
      await vi.advanceTimersByTimeAsync(delay);
    }
    FakeEventSource.instances.at(-1)?.fail();
    await vi.runAllTimersAsync();

    expect(onTelemetryUnavailable).toHaveBeenCalledWith({
      runId: 'run-1', executionState: 'running', message: 'Execution state running; telemetry unavailable',
    });
    expect(onStatus).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });

  it('reports an invalid terminal response as telemetry unavailable after retry exhaustion', async () => {
    vi.useFakeTimers();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        runId: 'run-1', state: 'complete', scenarioKey: 'multiturn', sampleId: 'interactive-multiturn', throughTurn: 1, turnType: 'multi', events: [],
        finalResult: { ...allow, decision: 'DENY', executed: false, dbTouched: false, detectedAt: 'M5', enforcedAt: 'trustedsql', route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'] },
      }),
    });
    const onStatus = vi.fn();
    const onTelemetryUnavailable = vi.fn();
    createApiClient('/api').subscribeRunEvents('run-1', 0, {
      onEvent: vi.fn(), onComplete: vi.fn(), onError: vi.fn(), onStatus, onTelemetryUnavailable,
    });

    for (const delay of [250, 500, 750]) {
      FakeEventSource.instances.at(-1)?.fail();
      await vi.advanceTimersByTimeAsync(delay);
    }
    FakeEventSource.instances.at(-1)?.fail();
    await vi.runAllTimersAsync();

    expect(onStatus).not.toHaveBeenCalled();
    expect(onTelemetryUnavailable).toHaveBeenCalledWith(expect.objectContaining({ runId: 'run-1', executionState: 'unknown' }));
    expect(onTelemetryUnavailable.mock.calls[0][0].message).toMatch(/unknown.*telemetry unavailable/i);
  });

  it('rejects a DENY and a foreign result on the SSE complete event as telemetry unavailable', () => {
    const onComplete = vi.fn();
    const onTelemetryUnavailable = vi.fn();
    const unsubscribe = createApiClient().subscribeRunEvents('run-1', 0, {
      onEvent: vi.fn(), onComplete, onError: vi.fn(), onTelemetryUnavailable,
    }, { runId: 'run-1', sampleId: 'interactive-multiturn', throughTurn: 1 });
    const source = FakeEventSource.instances[0];
    source.emit('complete', { ...allow, decision: 'DENY', executed: false, dbTouched: false, detectedAt: 'M5', enforcedAt: 'trustedsql', route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'] });
    expect(onComplete).not.toHaveBeenCalled();
    expect(onTelemetryUnavailable).toHaveBeenCalledWith(expect.objectContaining({ runId: 'run-1', executionState: 'unknown' }));
    expect(source.close).toHaveBeenCalled();
    unsubscribe();

    const foreignUnavailable = vi.fn();
    createApiClient().subscribeRunEvents('run-1', 0, {
      onEvent: vi.fn(), onComplete: vi.fn(), onError: vi.fn(), onTelemetryUnavailable: foreignUnavailable,
    }, { runId: 'run-1', sampleId: 'interactive-multiturn', throughTurn: 1 });
    FakeEventSource.instances[1].emit('complete', { ...allow, runId: 'foreign-run' });
    expect(foreignUnavailable).toHaveBeenCalledWith(expect.objectContaining({ executionState: 'unknown' }));
  });
});
