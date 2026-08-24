import React, { useEffect, useReducer, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { ExecutionMode, ScenarioMetadata, ScenarioTurn, SessionIdentity, ToolReadiness } from './types';
import { ScenarioRail } from '../components/ScenarioRail';
import { ChatStage } from '../components/ChatStage';
import { OperationsRail } from '../components/OperationsRail';
import { RotateCcw, Shield, ShieldOff } from 'lucide-react';
import { ApiClient, createApiClient, safeFinalError, validateFinalResultDto, validateRunStatusEvent } from '../api/client';
import { demoReducer, initialDemoState } from '../state/demoReducer';
import '../styles/cockpit.css';

const DEFAULT_IDENTITY: SessionIdentity = { role: 'Student', userId: 40, username: 'Student User 40' };

interface AppProps {
  /** Kept for compatibility with the shell contract; production state comes only from bootstrap. */
  scenarios?: ScenarioMetadata[];
  tools?: ToolReadiness[];
  sessionIdentity?: SessionIdentity;
  readiness?: 'neutral' | 'loading' | 'ready' | 'not-ready';
  apiClient?: ApiClient;
}

export const App: React.FC<AppProps> = ({ sessionIdentity = DEFAULT_IDENTITY, apiClient: customApiClient }) => {
  const clientRef = useRef<ApiClient>(customApiClient ?? createApiClient());
  const [state, dispatch] = useReducer(demoReducer, initialDemoState);
  const unsubscribeSseRef = useRef<(() => void) | null>(null);
  const pendingCreateRef = useRef<AbortController | null>(null);
  const lifecycleTokenRef = useRef(0);
  const activeRunIdRef = useRef<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const runStateRef = useRef(initialDemoState.runState);
  const [draft, setDraft] = useState('');
  const [executionMode, setExecutionMode] = useState<ExecutionMode>('trustedsql');
  const [editingTurnNumber, setEditingTurnNumber] = useState<number | null>(null);
  activeRunIdRef.current = state.activeRunId;
  runStateRef.current = state.runState;

  useEffect(() => {
    let mounted = true;
    dispatch({ type: 'BOOTSTRAP_START' });
    clientRef.current.fetchBootstrap().then((bootstrap) => {
      if (!mounted) return;
      if (!bootstrap.ready) {
        dispatch({ type: 'BOOTSTRAP_ERROR', payload: 'Backend runtime is not ready' });
        return;
      }
      dispatch({ type: 'BOOTSTRAP_SUCCESS', payload: { scenarios: bootstrap.scenarios, tools: bootstrap.tools } });
    }).catch((error: unknown) => {
      if (mounted) dispatch({ type: 'BOOTSTRAP_ERROR', payload: error instanceof Error ? error.message : 'Bootstrap failed' });
    });
    return () => {
      mounted = false;
    };
  }, []);

  useEffect(() => () => {
    lifecycleTokenRef.current += 1;
    pendingCreateRef.current?.abort();
    pendingCreateRef.current = null;
    unsubscribeSseRef.current?.();
    unsubscribeSseRef.current = null;
  }, []);

  const currentScenario = state.scenarios.find((scenario) => scenario.key === state.selectedScenarioKey);
  const isActive = state.runState === 'queued' || state.runState === 'running';

  const closeStream = () => {
    unsubscribeSseRef.current?.();
    unsubscribeSseRef.current = null;
  };

  const invalidateLifecycle = () => {
    lifecycleTokenRef.current += 1;
    pendingCreateRef.current?.abort();
    pendingCreateRef.current = null;
    closeStream();
  };

  const markTelemetryUnavailable = (runId: string, executionState: 'queued' | 'running' | 'unknown', message = 'Execution state unknown; telemetry unavailable') => {
    invalidateLifecycle();
    dispatch({ type: 'TELEMETRY_UNAVAILABLE', payload: { runId, executionState, message } });
  };

  const handleReset = () => {
    invalidateLifecycle();
    conversationIdRef.current = null;
    setDraft('');
    setEditingTurnNumber(null);
    dispatch({ type: 'RESET_STATE' });
  };

  const handleModeSwitch = () => {
    if (isActive || pendingCreateRef.current) return;
    setExecutionMode((current) => current === 'trustedsql' ? 'direct' : 'trustedsql');
  };

  const handleSelectScenario = (scenarioKey: string) => {
    if (isActive || pendingCreateRef.current || !state.scenarios.some((scenario) => scenario.key === scenarioKey)) return;
    dispatch({ type: 'SELECT_SCENARIO', payload: { scenarioKey } });
  };

  const handleRemoveScenario = (scenarioKey: string) => {
    if (isActive || pendingCreateRef.current) return;
    dispatch({ type: 'REMOVE_SCENARIO', payload: { scenarioKey } });
  };

  const handleCopyScenarioTurn = (_scenarioKey: string, turn: ScenarioTurn) => {
    if (turn.replacesTurn !== turn.turnNumber) return;
    const existing = state.chatTurns.find((chatTurn) => chatTurn.turnNumber === turn.turnNumber);
    if (!existing || isActive || pendingCreateRef.current) return;
    invalidateLifecycle();
    setDraft(existing.nlq);
    setEditingTurnNumber(turn.turnNumber);
    dispatch({ type: 'BEGIN_EDIT_TURN', payload: { turnNumber: turn.turnNumber } });
  };

  const handleEditTurn = (turnNumber: number) => {
    if (isActive || pendingCreateRef.current) return;
    const turn = state.chatTurns.find((item) => item.turnNumber === turnNumber);
    if (!turn) return;
    invalidateLifecycle();
    setDraft(turn.nlq);
    setEditingTurnNumber(turnNumber);
    dispatch({ type: 'BEGIN_EDIT_TURN', payload: { turnNumber } });
  };

  const handleSend = async () => {
    const requestedNlq = draft.trim();
    if (state.bootstrapState !== 'ready' || !requestedNlq || isActive || pendingCreateRef.current || state.chatTurns.length >= 20) return;
    const submittedConversationId = conversationIdRef.current;
    if (editingTurnNumber !== null && (
      submittedConversationId === null
      || editingTurnNumber !== state.chatTurns.length + 1
    )) return;
    const replaceTurn = editingTurnNumber ?? undefined;
    const requestedTurns = [...state.chatTurns.map((turn) => turn.nlq), requestedNlq];
    const requestedTurn = replaceTurn ?? requestedTurns.length;
    const lifecycleToken = ++lifecycleTokenRef.current;
    const controller = new AbortController();
    pendingCreateRef.current = controller;
    try {
      const job = await clientRef.current.createRun(requestedNlq, submittedConversationId, controller.signal, executionMode, replaceTurn);
      if (controller.signal.aborted || lifecycleToken !== lifecycleTokenRef.current) return;
      if (
        job.scenarioKey !== 'multiturn'
        || job.turnType !== 'multi'
        || (job.mode ?? 'trustedsql') !== executionMode
        || job.throughTurn !== requestedTurn
        || (replaceTurn === undefined && submittedConversationId !== null && job.conversationId !== submittedConversationId)
        || (replaceTurn !== undefined && job.conversationId === submittedConversationId)
      ) {
        throw new Error('Create run response did not match the active conversation');
      }
      conversationIdRef.current = job.conversationId;
      setEditingTurnNumber(null);
      dispatch({
        type: 'RUN_QUEUED',
        payload: { runId: job.runId, sampleId: job.sampleId, throughTurn: requestedTurn, turns: requestedTurns, mode: job.mode ?? executionMode },
      });
      setDraft('');
      closeStream();
      unsubscribeSseRef.current = clientRef.current.subscribeRunEvents(job.runId, 0, {
        onEvent: ({ eventType, data }) => {
          if (lifecycleToken !== lifecycleTokenRef.current) return;
          flushSync(() => {
            if (eventType === 'module') dispatch({ type: 'MODULE_EVENT', payload: data });
            if (eventType === 'trace') dispatch({ type: 'TRACE_EVENT', payload: data });
            if (eventType === 'revision') dispatch({ type: 'REVISION_EVENT', payload: data });
            if (eventType === 'retract') dispatch({ type: 'RETRACT_EVENT', payload: data });
          });
        },
        onComplete: (finalResult) => {
          if (lifecycleToken !== lifecycleTokenRef.current) return;
          const correlatedResult = validateFinalResultDto(finalResult, { runId: job.runId, sampleId: job.sampleId, throughTurn: job.throughTurn, mode: job.mode ?? executionMode });
          if (!correlatedResult) {
            markTelemetryUnavailable(job.runId, 'unknown');
            return;
          }
          if (correlatedResult.decision === 'ALLOW') dispatch({ type: 'RUN_COMPLETE', payload: { runId: job.runId, finalResult: correlatedResult } });
          if (correlatedResult.decision === 'DENY') dispatch({ type: 'RUN_DENIED', payload: { runId: job.runId, finalResult: correlatedResult } });
          if (correlatedResult.decision === 'ERROR') dispatch({ type: 'RUN_ERROR', payload: { runId: job.runId, error: safeFinalError('ERROR', correlatedResult.error) ?? 'Execution could not be completed' } });
          closeStream();
        },
        onStatus: (status) => {
          if (lifecycleToken !== lifecycleTokenRef.current) return;
          const correlatedStatus = validateRunStatusEvent(status, { runId: job.runId, sampleId: job.sampleId, throughTurn: job.throughTurn, mode: job.mode ?? executionMode });
          if (!correlatedStatus || correlatedStatus.runId !== job.runId) {
            markTelemetryUnavailable(job.runId, 'unknown');
            return;
          }
          status = correlatedStatus;
          if (status.state === 'complete' && status.finalResult?.decision === 'ALLOW') dispatch({ type: 'RUN_COMPLETE', payload: { runId: job.runId, finalResult: status.finalResult } });
          else if (status.state === 'denied' && status.finalResult?.decision === 'DENY') dispatch({ type: 'RUN_DENIED', payload: { runId: job.runId, finalResult: status.finalResult } });
          else if (status.state === 'error' || status.finalResult?.decision === 'ERROR') dispatch({ type: 'RUN_ERROR', payload: { runId: job.runId, error: safeFinalError('ERROR', status.error ?? status.finalResult?.error) ?? 'Execution could not be completed' } });
          else if (status.state === 'cancelled') dispatch({ type: 'RUN_CANCELLED', payload: { runId: job.runId } });
          else if (status.state === 'running') dispatch({ type: 'RUN_RUNNING', payload: { runId: job.runId } });
          if (['complete', 'denied', 'error', 'cancelled'].includes(status.state)) closeStream();
        },
        onTelemetryUnavailable: ({ runId, executionState, message }) => {
          if (lifecycleToken !== lifecycleTokenRef.current) return;
          markTelemetryUnavailable(runId === job.runId ? runId : job.runId, executionState, message);
        },
        onError: () => {
          if (lifecycleToken !== lifecycleTokenRef.current) return;
          dispatch({ type: 'RUN_ERROR', payload: { runId: job.runId, error: 'Execution could not be completed' } });
          closeStream();
        },
      }, { runId: job.runId, sampleId: job.sampleId, throughTurn: job.throughTurn, mode: job.mode ?? executionMode });
      if (job.state === 'running') dispatch({ type: 'RUN_RUNNING', payload: { runId: job.runId } });
    } catch (error: unknown) {
      if (controller.signal.aborted || lifecycleToken !== lifecycleTokenRef.current) return;
      dispatch({ type: 'RUN_REQUEST_ERROR', payload: error instanceof Error ? error.message : 'Execution request failed' });
    } finally {
      if (pendingCreateRef.current === controller) pendingCreateRef.current = null;
    }
  };

  const handleCancel = async () => {
    if (state.runState !== 'queued' || !state.activeRunId) return;
    const runId = state.activeRunId;
    const lifecycleToken = lifecycleTokenRef.current;
    try {
      await clientRef.current.cancelRun(runId);
      if (lifecycleToken !== lifecycleTokenRef.current || activeRunIdRef.current !== runId || runStateRef.current !== 'queued') return;
      closeStream();
      dispatch({ type: 'RUN_CANCELLED', payload: { runId } });
    } catch (error: unknown) {
      if (lifecycleToken !== lifecycleTokenRef.current || activeRunIdRef.current !== runId || runStateRef.current !== 'queued') return;
      dispatch({ type: 'RUN_ERROR', payload: { runId, error: error instanceof Error ? error.message : 'Cancel failed' } });
    }
  };

  return (
    <div className="cockpit-container" data-testid="cockpit-root">
      <header className="cockpit-header" role="banner">
        <div className="brand-group">
          <div className="brand-icon"><Shield size={18} /></div>
          <div>
            <h1 className="brand-title">TrustedSQL + Vertex RAG Cockpit <span className={`brand-badge ${executionMode === 'direct' ? 'direct' : ''}`}>{executionMode === 'trustedsql' ? 'TRUSTEDSQL ON' : 'SECURITY OFF'}</span></h1>
            <div className="brand-sub">{executionMode === 'trustedsql'
              ? 'Chat → Orchestrator · Documents → Vertex AI RAG · Data → TrustedSQL'
              : 'Chat → Orchestrator · Documents → Vertex AI RAG · Data → Direct SQL'}</div>
          </div>
        </div>
        <div className="header-actions">
          <button
            className={`btn-mode-switch ${executionMode}`}
            onClick={handleModeSwitch}
            disabled={isActive}
            aria-pressed={executionMode === 'direct'}
            aria-label="Switch execution mode"
            title="Switch execution mode; chat history is preserved"
          >
            {executionMode === 'trustedsql' ? <Shield size={13} /> : <ShieldOff size={13} />}
            {executionMode === 'trustedsql' ? 'TrustedSQL' : 'Direct SQL'}
          </button>
          <button className="btn-header-reset" onClick={handleReset} aria-label="Reset Cockpit"><RotateCcw size={13} /> Reset</button>
        </div>
      </header>

      <div className="sr-only" role="status" aria-live="polite" data-testid="global-live-region">
        Cockpit status: {state.bootstrapState}. Run state: {state.runState}.{state.error ? ` Warning: ${state.error}` : ''}{state.telemetryError ? ` Warning: ${state.telemetryError}` : ''}
      </div>

      <div className="sr-only"><span data-testid="selected-scenario-id">{currentScenario?.canonicalId ?? 'No scenario loaded'}</span></div>

      <main className="cockpit-stage-grid" role="main">
        <ScenarioRail
          scenarios={state.scenarios}
          selectedScenarioKey={state.selectedScenarioKey}
          disabled={isActive}
          onSelectScenario={handleSelectScenario}
          onRemoveScenario={handleRemoveScenario}
          onCopyScenarioTurn={handleCopyScenarioTurn}
        />
        <ChatStage
          scenario={currentScenario}
          readinessState={state.bootstrapState}
          runState={state.runState}
          chatTurns={state.chatTurns}
          draft={draft}
          onDraftChange={setDraft}
          error={state.error}
          telemetryUnavailable={state.telemetryUnavailable}
          telemetryExecutionState={state.telemetryExecutionState}
          telemetryError={state.telemetryError}
          onSend={handleSend}
          onCancel={handleCancel}
          onEditTurn={handleEditTurn}
          editingTurnNumber={editingTurnNumber}
          mode={executionMode}
          sessionIdentity={sessionIdentity}
        />
        <OperationsRail
          mode={executionMode}
          routeEvidence={state.routeEvidence ?? undefined}
          telemetryEvents={state.telemetryEvents}
          turnRuntimeSnapshots={state.turnRuntimeSnapshots}
          activeTurnNumber={state.activeThroughTurn ?? undefined}
          executionState={state.telemetryExecutionState ?? state.runState}
        />
      </main>
    </div>
  );
};

export default App;
