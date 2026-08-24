import { ExecutionMode, GnnGraphSnapshot, ReadinessState, RouteEvidence, ScenarioMetadata, ToolReadiness } from '../app/types';
import {
  FinalResultDto,
  ModuleEventDto,
  RetractEventDto,
  TraceEventDto,
  validateFinalResultDto,
} from '../api/client';

export type RunStatus = 'idle' | 'queued' | 'running' | 'complete' | 'denied' | 'error' | 'cancelled';

export interface ModuleEvidence {
  runId: string;
  sampleId?: string;
  turnNumber?: number;
  module: string;
  streamSequence: number;
  stage?: string;
  decision?: string;
  revision?: number;
  latencyMs?: number;
  error?: string;
  detail?: string;
  traceLines?: string[];
  gnnGraph?: GnnGraphSnapshot;
}

export interface TelemetryItem {
  id: string;
  eventType: 'module' | 'revision' | 'trace' | 'retract' | 'status';
  timestamp: string;
  runId: string;
  sampleId?: string;
  turnNumber?: number;
  module?: string;
  streamSequence: number;
  stage?: string;
  decision?: string;
  revision?: number;
  latencyMs?: number;
  reason?: string;
  error?: string;
  detail?: string;
  traceLines?: string[];
  gnnGraph?: GnnGraphSnapshot;
  traceStep?: number;
  traceTotal?: number;
}

export interface ChatTurn {
  turnNumber: number;
  nlq: string;
  result?: FinalResultDto;
  error?: string;
}

export interface TurnRuntimeSnapshot {
  turnNumber: number;
  runId: string;
  mode: ExecutionMode;
  runState: RunStatus;
  routeEvidence?: RouteEvidence;
  telemetryEvents: TelemetryItem[];
  error?: string;
}

export interface DemoState {
  bootstrapState: ReadinessState;
  scenarios: ScenarioMetadata[];
  tools: ToolReadiness[];
  selectedScenarioKey: string;
  selectedTurnNumber: number;
  activeRunId: string | null;
  lastStreamSequence: number;
  runState: RunStatus;
  acceptedNlq: string | null;
  chatTurns: ChatTurn[];
  moduleEvidenceMap: Record<string, ModuleEvidence>;
  telemetryEvents: TelemetryItem[];
  turnRuntimeSnapshots: Record<number, TurnRuntimeSnapshot>;
  finalResult: FinalResultDto | null;
  activeSampleId: string | null;
  activeThroughTurn: number | null;
  routeEvidence: RouteEvidence | null;
  error: string | null;
  telemetryUnavailable: boolean;
  telemetryExecutionState: 'queued' | 'running' | 'unknown' | null;
  telemetryError: string | null;
}

export const initialDemoState: DemoState = {
  bootstrapState: 'neutral',
  scenarios: [],
  tools: [],
  selectedScenarioKey: '',
  selectedTurnNumber: 1,
  activeRunId: null,
  lastStreamSequence: 0,
  runState: 'idle',
  acceptedNlq: null,
  chatTurns: [],
  moduleEvidenceMap: {},
  telemetryEvents: [],
  turnRuntimeSnapshots: {},
  finalResult: null,
  activeSampleId: null,
  activeThroughTurn: null,
  routeEvidence: null,
  error: null,
  telemetryUnavailable: false,
  telemetryExecutionState: null,
  telemetryError: null,
};

export type DemoAction =
  | { type: 'BOOTSTRAP_START' }
  | { type: 'BOOTSTRAP_SUCCESS'; payload: { scenarios: ScenarioMetadata[]; tools: ToolReadiness[] } }
  | { type: 'BOOTSTRAP_ERROR'; payload: string }
  | { type: 'IMPORT_SCENARIOS'; payload: { scenarios: ScenarioMetadata[] } }
  | { type: 'REMOVE_SCENARIO'; payload: { scenarioKey: string } }
  | { type: 'SELECT_SCENARIO'; payload: { scenarioKey: string } }
  | { type: 'SELECT_TURN'; payload: { turnNumber: number } }
  | { type: 'BEGIN_EDIT_TURN'; payload: { turnNumber: number } }
  | { type: 'RUN_QUEUED'; payload: { runId: string; sampleId: string; throughTurn: number; turns: string[]; mode?: ExecutionMode } }
  | { type: 'RUN_RUNNING'; payload: { runId: string } }
  | { type: 'MODULE_EVENT'; payload: ModuleEventDto }
  | { type: 'REVISION_EVENT'; payload: ModuleEventDto }
  | { type: 'TRACE_EVENT'; payload: TraceEventDto }
  | { type: 'RETRACT_EVENT'; payload: RetractEventDto }
  | { type: 'RUN_COMPLETE'; payload: { runId: string; finalResult: FinalResultDto } }
  | { type: 'RUN_DENIED'; payload: { runId: string; finalResult: FinalResultDto } }
  | { type: 'RUN_ERROR'; payload: { runId: string; error: string } }
  | { type: 'TELEMETRY_UNAVAILABLE'; payload: { runId: string; executionState: 'queued' | 'running' | 'unknown'; message: string } }
  | { type: 'RUN_REQUEST_ERROR'; payload: string }
  | { type: 'RUN_CANCELLED'; payload: { runId: string } }
  | { type: 'RESET_STATE' };

function buildEvidenceKey(runId: string, sampleId: string | undefined, turnNumber: number | undefined, module: string): string {
  return `${runId}:${sampleId || 'default'}:${turnNumber || 1}:${module}`;
}

function isTerminal(runState: RunStatus): boolean {
  return runState === 'complete' || runState === 'denied' || runState === 'error' || runState === 'cancelled';
}

function appendTelemetry(events: TelemetryItem[], item: TelemetryItem): TelemetryItem[] {
  return [...events, item].sort((left, right) => left.streamSequence - right.streamSequence);
}

function statusTelemetry(runId: string, decision: string, stage: string, streamSequence: number): TelemetryItem {
  return {
    id: `sys-${runId}-${decision.toLowerCase()}-${streamSequence}`,
    eventType: 'status',
    timestamp: new Date().toLocaleTimeString(),
    runId,
    streamSequence,
    stage,
    decision,
  };
}

function resetRunState(state: DemoState, selectedScenarioKey: string, selectedTurnNumber = 1): DemoState {
  return {
    ...state,
    selectedScenarioKey,
    selectedTurnNumber,
    activeRunId: null,
    lastStreamSequence: 0,
    runState: 'idle',
    acceptedNlq: null,
    chatTurns: [],
    moduleEvidenceMap: {},
    telemetryEvents: [],
    turnRuntimeSnapshots: {},
    finalResult: null,
    activeSampleId: null,
    activeThroughTurn: null,
    routeEvidence: null,
    error: null,
    telemetryUnavailable: false,
    telemetryExecutionState: null,
    telemetryError: null,
  };
}

function makeRouteEvidence(result: FinalResultDto): RouteEvidence {
  return {
    version: 1,
    mode: result.mode ?? 'trustedsql',
    resultType: result.resultType,
    decision: result.decision,
    executed: result.executed,
    dbTouched: result.dbTouched,
    route: result.route,
    detectedAt: result.detectedAt,
    enforcedAt: result.enforcedAt,
  };
}

function syncActiveSnapshot(state: DemoState, changes: Partial<DemoState>): DemoState {
  const next = { ...state, ...changes };
  const turnNumber = next.activeThroughTurn;
  const runId = next.activeRunId;
  if (!turnNumber || !runId) return next;
  const previous = state.turnRuntimeSnapshots[turnNumber];
  const routeEvidence = next.routeEvidence ?? undefined;
  return {
    ...next,
    turnRuntimeSnapshots: {
      ...state.turnRuntimeSnapshots,
      [turnNumber]: {
        turnNumber,
        runId,
        mode: routeEvidence?.mode ?? previous?.mode ?? 'trustedsql',
        runState: next.runState,
        routeEvidence,
        telemetryEvents: next.telemetryEvents,
        error: next.error ?? undefined,
      },
    },
  };
}

export function demoReducer(state: DemoState, action: DemoAction): DemoState {
  switch (action.type) {
    case 'BOOTSTRAP_START':
      return { ...state, bootstrapState: 'loading', scenarios: [], tools: [], error: null };

    case 'BOOTSTRAP_SUCCESS': {
      const scenarios = Array.isArray(action.payload.scenarios) ? action.payload.scenarios : [];
      const tools = Array.isArray(action.payload.tools) ? action.payload.tools : [];
      return {
        ...state,
        bootstrapState: 'ready',
        scenarios,
        tools,
        selectedScenarioKey: scenarios[0]?.key ?? '',
        selectedTurnNumber: 1,
        error: null,
      };
    }

    case 'BOOTSTRAP_ERROR':
      return {
        ...resetRunState(state, ''),
        bootstrapState: 'not-ready',
        scenarios: [],
        tools: [],
        error: action.payload.slice(0, 500),
      };

    case 'IMPORT_SCENARIOS': {
      const imported = action.payload.scenarios;
      if (!imported.length) return state;
      const importedIds = new Set(imported.map((scenario) => scenario.canonicalId));
      const scenarios = [
        ...state.scenarios.filter((scenario) => !importedIds.has(scenario.canonicalId)),
        ...imported,
      ];
      return {
        ...state,
        scenarios,
        selectedScenarioKey: imported[0].key,
        selectedTurnNumber: 1,
      };
    }

    case 'REMOVE_SCENARIO': {
      const removedIndex = state.scenarios.findIndex(
        (scenario) => scenario.key === action.payload.scenarioKey,
      );
      if (removedIndex < 0) return state;
      const scenarios = state.scenarios.filter(
        (scenario) => scenario.key !== action.payload.scenarioKey,
      );
      const selectedScenarioKey = state.selectedScenarioKey === action.payload.scenarioKey
        ? scenarios[Math.min(removedIndex, Math.max(0, scenarios.length - 1))]?.key ?? ''
        : state.selectedScenarioKey;
      return {
        ...state,
        scenarios,
        selectedScenarioKey,
        selectedTurnNumber: 1,
      };
    }

    case 'SELECT_SCENARIO':
      return {
        ...state,
        selectedScenarioKey: action.payload.scenarioKey,
        selectedTurnNumber: 1,
      };

    case 'SELECT_TURN':
      return { ...state, selectedTurnNumber: action.payload.turnNumber };

    case 'BEGIN_EDIT_TURN': {
      const turnNumber = action.payload.turnNumber;
      if (
        state.runState === 'queued'
        || state.runState === 'running'
        || !state.chatTurns.some((turn) => turn.turnNumber === turnNumber)
      ) return state;
      const retainedSnapshots = Object.fromEntries(
        Object.entries(state.turnRuntimeSnapshots).filter(([key]) => Number(key) < turnNumber),
      );
      return {
        ...state,
        selectedTurnNumber: Math.max(1, turnNumber - 1),
        activeRunId: null,
        lastStreamSequence: 0,
        runState: 'idle',
        acceptedNlq: null,
        chatTurns: state.chatTurns.filter((turn) => turn.turnNumber < turnNumber),
        moduleEvidenceMap: {},
        telemetryEvents: [],
        turnRuntimeSnapshots: retainedSnapshots,
        finalResult: null,
        activeSampleId: null,
        activeThroughTurn: null,
        routeEvidence: null,
        error: null,
        telemetryUnavailable: false,
        telemetryExecutionState: null,
        telemetryError: null,
      };
    }

    case 'RUN_QUEUED': {
      const { runId, sampleId, turns } = action.payload;
      const chatTurns = turns.map((nlq, index) => {
        const previous = state.chatTurns[index];
        return previous?.nlq === nlq
          ? previous
          : { turnNumber: index + 1, nlq };
      });
      const queuedEvent: TelemetryItem = {
        id: `sys-${runId}-queued`,
        eventType: 'status',
        timestamp: new Date().toLocaleTimeString(),
        runId,
        streamSequence: 0,
        stage: 'Job queued',
        decision: 'QUEUED',
      };
      return {
        ...state,
        activeRunId: runId,
        selectedTurnNumber: action.payload.throughTurn,
        runState: 'queued',
        acceptedNlq: turns.at(-1) ?? null,
        chatTurns,
        activeSampleId: sampleId,
        activeThroughTurn: action.payload.throughTurn,
        lastStreamSequence: 0,
        moduleEvidenceMap: {},
        telemetryEvents: [queuedEvent],
        turnRuntimeSnapshots: {
          ...state.turnRuntimeSnapshots,
          [action.payload.throughTurn]: {
            turnNumber: action.payload.throughTurn,
            runId,
            mode: action.payload.mode ?? 'trustedsql',
            runState: 'queued',
            telemetryEvents: [queuedEvent],
          },
        },
        finalResult: null,
        routeEvidence: null,
        error: null,
        telemetryUnavailable: false,
        telemetryExecutionState: null,
        telemetryError: null,
      };
    }

    case 'RUN_RUNNING':
      if (action.payload.runId !== state.activeRunId || isTerminal(state.runState)) return state;
      return syncActiveSnapshot(state, {
        runState: 'running',
        telemetryEvents: appendTelemetry(state.telemetryEvents, statusTelemetry(action.payload.runId, 'RUNNING', 'Execution running', state.lastStreamSequence)),
      });

    case 'MODULE_EVENT': {
      const event = action.payload;
      if (event.runId !== state.activeRunId || isTerminal(state.runState) || !Number.isInteger(event.streamSequence) || event.streamSequence <= state.lastStreamSequence) return state;
      const key = buildEvidenceKey(event.runId, event.sampleId, event.turnNumber, event.module);
      if (state.moduleEvidenceMap[key]) return state;
      const evidence: ModuleEvidence = { ...event };
      const item: TelemetryItem = {
        id: `ev-${event.runId}-${event.streamSequence}-${event.module}`,
        eventType: 'module',
        timestamp: new Date().toLocaleTimeString(),
        runId: event.runId,
        sampleId: event.sampleId,
        turnNumber: event.turnNumber,
        module: event.module,
        streamSequence: event.streamSequence,
        stage: event.stage,
        decision: event.decision,
        revision: event.revision,
        latencyMs: event.latencyMs,
        error: event.error,
        detail: event.detail,
        traceLines: event.traceLines,
        gnnGraph: event.gnnGraph,
      };
      return syncActiveSnapshot(state, {
        runState: state.runState === 'queued' ? 'running' : state.runState,
        lastStreamSequence: event.streamSequence,
        moduleEvidenceMap: { ...state.moduleEvidenceMap, [key]: evidence },
        telemetryEvents: appendTelemetry(state.telemetryEvents, item),
      });
    }

    case 'TRACE_EVENT': {
      const event = action.payload;
      if (event.runId !== state.activeRunId || isTerminal(state.runState) || !Number.isInteger(event.streamSequence) || event.streamSequence <= state.lastStreamSequence) return state;
      const item: TelemetryItem = {
        id: `trace-${event.runId}-${event.streamSequence}-${event.module}-${event.traceStep}`,
        eventType: 'trace',
        timestamp: new Date().toLocaleTimeString(),
        runId: event.runId,
        sampleId: event.sampleId,
        turnNumber: event.turnNumber,
        module: event.module,
        streamSequence: event.streamSequence,
        stage: event.stage,
        decision: event.decision,
        detail: event.detail,
        traceStep: event.traceStep,
        traceTotal: event.traceTotal,
      };
      return syncActiveSnapshot(state, {
        runState: state.runState === 'queued' ? 'running' : state.runState,
        lastStreamSequence: event.streamSequence,
        telemetryEvents: appendTelemetry(state.telemetryEvents, item),
      });
    }

    case 'REVISION_EVENT': {
      const event = action.payload;
      if (event.runId !== state.activeRunId || isTerminal(state.runState) || !Number.isInteger(event.streamSequence) || event.streamSequence <= state.lastStreamSequence) return state;
      const key = buildEvidenceKey(event.runId, event.sampleId, event.turnNumber, event.module);
      const existing = state.moduleEvidenceMap[key];
      const incomingRevision = event.revision ?? 0;
      if (!existing || incomingRevision <= (existing.revision ?? 0)) return state;
      const updated: ModuleEvidence = {
        ...existing,
        streamSequence: event.streamSequence,
        stage: event.stage ?? existing.stage,
        decision: event.decision ?? existing.decision,
        revision: incomingRevision,
        latencyMs: event.latencyMs ?? existing.latencyMs,
        error: event.error ?? existing.error,
        detail: event.detail ?? existing.detail,
        traceLines: event.traceLines ?? existing.traceLines,
        gnnGraph: event.gnnGraph ?? existing.gnnGraph,
      };
      const item: TelemetryItem = {
        id: `rev-${event.runId}-${event.streamSequence}-${event.module}`,
        eventType: 'revision',
        timestamp: new Date().toLocaleTimeString(),
        runId: event.runId,
        sampleId: event.sampleId,
        turnNumber: event.turnNumber,
        module: event.module,
        streamSequence: event.streamSequence,
        stage: event.stage ?? existing.stage,
        decision: event.decision ?? existing.decision,
        revision: incomingRevision,
        latencyMs: event.latencyMs,
        error: event.error,
        detail: event.detail ?? existing.detail,
        traceLines: event.traceLines ?? existing.traceLines,
        gnnGraph: event.gnnGraph ?? existing.gnnGraph,
      };
      return syncActiveSnapshot(state, {
        lastStreamSequence: event.streamSequence,
        moduleEvidenceMap: { ...state.moduleEvidenceMap, [key]: updated },
        telemetryEvents: appendTelemetry(state.telemetryEvents, item),
      });
    }

    case 'RETRACT_EVENT': {
      const event = action.payload;
      if (event.runId !== state.activeRunId || isTerminal(state.runState) || !Number.isInteger(event.streamSequence) || event.streamSequence <= state.lastStreamSequence) return state;
      const key = buildEvidenceKey(event.runId, event.sampleId, event.turnNumber, event.module);
      const nextEvidence = { ...state.moduleEvidenceMap };
      delete nextEvidence[key];
      const item: TelemetryItem = {
        id: `ret-${event.runId}-${event.streamSequence}-${event.module}`,
        eventType: 'retract',
        timestamp: new Date().toLocaleTimeString(),
        runId: event.runId,
        sampleId: event.sampleId,
        turnNumber: event.turnNumber,
        module: event.module,
        streamSequence: event.streamSequence,
        revision: event.revision,
        reason: event.reason ?? 'Evidence retracted by the runtime',
      };
      return syncActiveSnapshot(state, {
        lastStreamSequence: event.streamSequence,
        moduleEvidenceMap: nextEvidence,
        telemetryEvents: appendTelemetry(state.telemetryEvents, item),
      });
    }

    case 'RUN_COMPLETE':
    case 'RUN_DENIED': {
      if (action.payload.runId !== state.activeRunId || isTerminal(state.runState)) return state;
      const result = validateFinalResultDto(action.payload.finalResult, {
        runId: state.activeRunId ?? '',
        sampleId: state.activeSampleId ?? undefined,
        throughTurn: state.activeThroughTurn ?? undefined,
      });
      if (!result) return state;
      const denied = action.type === 'RUN_DENIED';
      if (denied && result.decision !== 'DENY') return state;
      if (!denied && result.decision !== 'ALLOW') return state;
      const routeEvidence = makeRouteEvidence(result);
      return syncActiveSnapshot(state, {
        runState: denied ? 'denied' : 'complete',
        finalResult: result,
        chatTurns: state.chatTurns.map((turn) =>
          turn.turnNumber === result.turnNumber
            ? { ...turn, result, error: undefined }
            : turn
        ),
        routeEvidence,
        telemetryEvents: appendTelemetry(state.telemetryEvents, statusTelemetry(action.payload.runId, denied ? 'DENY' : 'ALLOW', denied ? 'Access decision recorded' : 'Execution complete', state.lastStreamSequence)),
        error: null,
        telemetryUnavailable: false,
        telemetryExecutionState: null,
        telemetryError: null,
      });
    }

    case 'RUN_ERROR':
      if (action.payload.runId !== state.activeRunId || isTerminal(state.runState)) return state;
      return syncActiveSnapshot(state, {
        runState: 'error',
        error: action.payload.error.slice(0, 500),
        chatTurns: state.chatTurns.map((turn) =>
          turn.turnNumber === state.activeThroughTurn
            ? { ...turn, error: action.payload.error.slice(0, 500), result: undefined }
            : turn
        ),
        telemetryEvents: appendTelemetry(state.telemetryEvents, statusTelemetry(action.payload.runId, 'ERROR', 'Execution error', state.lastStreamSequence)),
        telemetryUnavailable: false,
        telemetryExecutionState: null,
        telemetryError: null,
      });

    case 'TELEMETRY_UNAVAILABLE':
      if (action.payload.runId !== state.activeRunId || isTerminal(state.runState)) return state;
      return syncActiveSnapshot(state, {
        finalResult: null,
        routeEvidence: null,
        error: null,
        telemetryUnavailable: true,
        telemetryExecutionState: action.payload.executionState,
        telemetryError: action.payload.message.slice(0, 500),
      });

    case 'RUN_REQUEST_ERROR':
      if (state.runState === 'queued' || state.runState === 'running') return state;
      return {
        ...state,
        runState: 'error',
        finalResult: null,
        routeEvidence: null,
        error: action.payload.slice(0, 500),
      };

    case 'RUN_CANCELLED':
      if (action.payload.runId !== state.activeRunId || isTerminal(state.runState)) return state;
      return syncActiveSnapshot(state, {
        runState: 'cancelled',
        error: null,
        telemetryUnavailable: false,
        telemetryExecutionState: null,
        telemetryError: null,
        telemetryEvents: appendTelemetry(state.telemetryEvents, statusTelemetry(action.payload.runId, 'CANCELLED', 'Execution cancelled', state.lastStreamSequence)),
      });

    case 'RESET_STATE':
      return resetRunState(state, state.scenarios[0]?.key ?? '');

    default:
      return state;
  }
}
