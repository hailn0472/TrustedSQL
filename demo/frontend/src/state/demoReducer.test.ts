import { describe, expect, it } from 'vitest';
import { demoReducer, initialDemoState, ModuleEvidence } from './demoReducer';
import { scenarios, tools } from '../test/fixtures';

const queued = (turns = ['First query']) => ({
  type: 'RUN_QUEUED' as const,
  payload: {
    runId: 'run-1',
    sampleId: 'interactive-multiturn',
    throughTurn: turns.length,
    turns,
  },
});

describe('interactive chat reducer', () => {
  it('boots with only the multiturn prompt library', () => {
    let state = demoReducer(initialDemoState, { type: 'BOOTSTRAP_START' });
    state = demoReducer(state, { type: 'BOOTSTRAP_SUCCESS', payload: { scenarios, tools } });
    expect(state.bootstrapState).toBe('ready');
    expect(state.selectedScenarioKey).toBe('multiturn');
    expect(state.scenarios).toHaveLength(1);
  });

  it('materializes accepted user-authored turns', () => {
    const state = demoReducer(initialDemoState, queued(['First query', 'Follow-up query']));
    expect(state.runState).toBe('queued');
    expect(state.acceptedNlq).toBe('Follow-up query');
    expect(state.chatTurns.map((turn) => turn.nlq)).toEqual(['First query', 'Follow-up query']);
  });

  it('preserves completed prior turns when a follow-up is queued', () => {
    let state = demoReducer(initialDemoState, queued());
    state = demoReducer(state, {
      type: 'RUN_COMPLETE',
      payload: {
        runId: 'run-1',
        finalResult: {
          runId: 'run-1', sampleId: 'interactive-multiturn', turnNumber: 1,
          decision: 'ALLOW', executed: true, dbTouched: true,
          route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'],
        },
      },
    });
    state = demoReducer(state, {
      type: 'RUN_QUEUED',
      payload: { runId: 'run-2', sampleId: 'interactive-multiturn', throughTurn: 2, turns: ['First query', 'Follow-up query'] },
    });
    expect(state.chatTurns[0].result?.decision).toBe('ALLOW');
    expect(state.chatTurns[1].nlq).toBe('Follow-up query');
  });

  it('tracks module, revision and retraction evidence by run/turn/module', () => {
    let state = demoReducer(initialDemoState, queued());
    const event: ModuleEvidence = {
      runId: 'run-1', sampleId: 'interactive-multiturn', turnNumber: 1,
      module: 'M2', streamSequence: 1, stage: 'evaluation', decision: 'ALLOW', revision: 1,
    };
    state = demoReducer(state, { type: 'MODULE_EVENT', payload: event });
    state = demoReducer(state, { type: 'REVISION_EVENT', payload: { ...event, streamSequence: 2, revision: 2, decision: 'DENY' } });
    expect(state.moduleEvidenceMap['run-1:interactive-multiturn:1:M2'].decision).toBe('DENY');
    state = demoReducer(state, {
      type: 'RETRACT_EVENT',
      payload: { runId: 'run-1', sampleId: 'interactive-multiturn', turnNumber: 1, module: 'M2', streamSequence: 3 },
    });
    expect(state.moduleEvidenceMap['run-1:interactive-multiturn:1:M2']).toBeUndefined();
  });

  it('rejects foreign events and terminal results', () => {
    let state = demoReducer(initialDemoState, queued());
    state = demoReducer(state, {
      type: 'MODULE_EVENT',
      payload: { runId: 'foreign', module: 'M1', streamSequence: 1 },
    });
    state = demoReducer(state, {
      type: 'RUN_COMPLETE',
      payload: {
        runId: 'run-1',
        finalResult: {
          runId: 'foreign', sampleId: 'interactive-multiturn', turnNumber: 1,
          decision: 'ALLOW', executed: true, dbTouched: true,
          route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'],
        },
      },
    });
    expect(state.runState).toBe('queued');
    expect(state.moduleEvidenceMap).toEqual({});
    expect(state.finalResult).toBeNull();
  });

  it('stores DENY evidence on the corresponding chat turn', () => {
    let state = demoReducer(initialDemoState, queued());
    state = demoReducer(state, {
      type: 'RUN_DENIED',
      payload: {
        runId: 'run-1',
        finalResult: {
          runId: 'run-1', sampleId: 'interactive-multiturn', turnNumber: 1,
          decision: 'DENY', executed: false, dbTouched: false,
          detectedAt: 'M5', enforcedAt: 'trustedsql',
          route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'],
        },
      },
    });
    expect(state.runState).toBe('denied');
    expect(state.chatTurns[0].result?.decision).toBe('DENY');
    expect(state.routeEvidence?.dbTouched).toBe(false);
  });

  it('marks telemetry unavailable without fabricating an error or result', () => {
    let state = demoReducer(initialDemoState, queued());
    state = demoReducer(state, {
      type: 'TELEMETRY_UNAVAILABLE',
      payload: { runId: 'run-1', executionState: 'unknown', message: 'Telemetry unavailable' },
    });
    expect(state.runState).toBe('queued');
    expect(state.telemetryUnavailable).toBe(true);
    expect(state.finalResult).toBeNull();
    expect(state.error).toBeNull();
  });

  it('resets the complete chat session', () => {
    let state = demoReducer(initialDemoState, queued(['One', 'Two']));
    state = demoReducer(state, { type: 'RESET_STATE' });
    expect(state.chatTurns).toEqual([]);
    expect(state.runState).toBe('idle');
    expect(state.selectedScenarioKey).toBe('');
  });
});
