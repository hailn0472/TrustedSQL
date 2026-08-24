import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ExecutionMode, RouteEvidence, RouteNodeId, RouteNodeState } from '../app/types';
import { TelemetryItem, RunStatus } from '../state/demoReducer';
import { GitBranch, Maximize2, Move, Network, ZoomIn, ZoomOut } from 'lucide-react';
import { GnnGraph } from './GnnGraph';

const ALLOWED_DETECTORS = ['C0', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'X1'];
const MIN_ZOOM = 0.65;
const MAX_ZOOM = 2.5;
const ZOOM_STEP = 0.2;

interface MapViewport {
  x: number;
  y: number;
  scale: number;
}

interface DragOrigin {
  pointerId: number;
  clientX: number;
  clientY: number;
  viewX: number;
  viewY: number;
}

type VisualNodeId =
  | 'user_query'
  | 'orchestrator'
  | 'memory'
  | 'chat_generation'
  | 'rag_query'
  | 'rag_retrieval'
  | 'rag_grounding'
  | 'm1'
  | 'm2'
  | 'm3'
  | 'm4'
  | 'm5'
  | 'm6'
  | 'm7'
  | 'x1'
  | 'education_db'
  | 'response_composer'
  | 'chat_response';

type VisualStateMap = Record<VisualNodeId, RouteNodeState>;

interface VisualNode {
  id: VisualNodeId;
  code: string;
  title: string;
  subtitle: string;
  x: number;
  y: number;
  w: number;
  h: number;
  testId?: string;
  emphasis?: 'input' | 'core' | 'output' | 'database';
}

interface VisualEdge {
  id: string;
  d: string;
  state: RouteNodeState;
  dashed?: boolean;
  testId?: string;
}

const TRUSTEDSQL_SEQUENCE: VisualNodeId[] = ['m1', 'm2', 'm3', 'm4', 'm5', 'm6', 'm7', 'x1'];
const TOOL_NODES: VisualNodeId[] = [
  'chat_generation', 'rag_query', 'rag_retrieval', 'rag_grounding',
  ...TRUSTEDSQL_SEQUENCE, 'education_db',
];

const VISUAL_NODE_LABELS: Record<VisualNodeId, string> = {
  user_query: 'User Query',
  orchestrator: 'Orchestrator',
  memory: 'C0 Conversation Memory',
  chat_generation: 'Chat Generation',
  rag_query: 'RAG Query Context',
  rag_retrieval: 'Vertex AI Corpus Retrieval',
  rag_grounding: 'RAG Grounding and Citations',
  m1: 'M1 Prompt Integrity',
  m2: 'M2 Intent Risk Guard',
  m3: 'M3 Access Planner',
  m4: 'M4 Schema Authorization',
  m5: 'M5 Row Scope',
  m6: 'M6 SQL Generator',
  m7: 'M7 SQL Conformance',
  x1: 'X1 Read-only Executor',
  education_db: 'Education Database',
  response_composer: 'Response Composer',
  chat_response: 'Chat Response',
};

const clampZoom = (scale: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));

export interface RoutingMapProps {
  evidence?: RouteEvidence;
  events?: TelemetryItem[];
  runState?: RunStatus;
  mode?: ExecutionMode;
  turnOptions?: Array<{ turnNumber: number; runState: RunStatus }>;
  selectedTurnNumber?: number;
  onSelectTurn?: (turnNumber: number) => void;
}

function expectedRoute(evidence: RouteEvidence): RouteNodeId[] {
  if (evidence.resultType === 'chat') return ['chat', 'orchestrator', 'context_memory'];
  if (evidence.resultType === 'rag') return ['chat', 'orchestrator', 'context_memory', 'rag'];
  if (evidence.mode === 'direct') {
    return evidence.decision === 'ALLOW' && evidence.executed
      ? ['chat', 'orchestrator', 'context_memory', 'sql_generator', 'education_db']
      : ['chat', 'orchestrator', 'context_memory', 'sql_generator'];
  }
  const common: RouteNodeId[] = ['chat', 'orchestrator', 'context_memory', 'policy_engine'];
  if (evidence.decision === 'ALLOW' && evidence.executed) return [...common, 'trustedsql', 'education_db'];
  if (evidence.decision === 'DENY') return [...common, 'trustedsql'];
  return common;
}

export function validateRouteEvidence(evidence?: RouteEvidence): { valid: boolean; reason?: string } {
  if (!evidence) return { valid: false, reason: 'No runtime route evidence yet' };
  if (evidence.decision === 'ERROR') return { valid: false, reason: 'Runtime error reported in evidence' };
  if (JSON.stringify(evidence.route) !== JSON.stringify(expectedRoute(evidence))) {
    return { valid: false, reason: 'Route does not match the selected execution branch' };
  }
  if (evidence.resultType === 'rag' || evidence.resultType === 'chat') {
    if (evidence.decision !== 'ALLOW' || evidence.executed || evidence.dbTouched) {
      return { valid: false, reason: `${evidence.resultType === 'chat' ? 'Chat' : 'RAG'} branch must complete without database execution` };
    }
    return { valid: true };
  }
  if (evidence.decision === 'ALLOW' && (!evidence.executed || !evidence.dbTouched)) {
    return { valid: false, reason: 'SQL ALLOW must report database execution' };
  }
  if (evidence.decision === 'DENY') {
    if (evidence.executed || evidence.dbTouched) return { valid: false, reason: 'DENY cannot touch the database' };
    if (!evidence.detectedAt || !ALLOWED_DETECTORS.includes(evidence.detectedAt) || evidence.enforcedAt !== 'trustedsql') {
      return { valid: false, reason: 'DENY evidence is missing its TrustedSQL detector/enforcer' };
    }
  }
  return { valid: true };
}

export function getFullTrajectory(evidence: RouteEvidence): RouteNodeId[] {
  return [...evidence.route];
}

function emptyVisualStates(): VisualStateMap {
  return {
    user_query: 'idle', orchestrator: 'idle', memory: 'idle', chat_generation: 'idle',
    rag_query: 'idle', rag_retrieval: 'idle', rag_grounding: 'idle',
    m1: 'idle', m2: 'idle', m3: 'idle', m4: 'idle', m5: 'idle', m6: 'idle', m7: 'idle', x1: 'idle',
    education_db: 'untouched', response_composer: 'idle', chat_response: 'idle',
  };
}

function markUnusedTools(states: VisualStateMap): void {
  for (const node of TOOL_NODES) states[node] = 'untouched';
}

function terminalVisualStates(evidence: RouteEvidence): VisualStateMap {
  const states = emptyVisualStates();
  markUnusedTools(states);
  states.user_query = 'allow';
  states.orchestrator = 'allow';
  states.memory = 'allow';

  if (evidence.resultType === 'chat') {
    states.chat_generation = 'allow';
    states.response_composer = 'allow';
    states.chat_response = 'allow';
    return states;
  }
  if (evidence.resultType === 'rag') {
    states.rag_query = 'allow';
    states.rag_retrieval = 'allow';
    states.rag_grounding = 'allow';
    states.response_composer = 'allow';
    states.chat_response = 'allow';
    return states;
  }
  if (evidence.mode === 'direct') {
    states.m6 = evidence.decision === 'ERROR' ? 'error' : 'allow';
    if (evidence.decision === 'ALLOW') {
      states.x1 = 'allow';
      states.education_db = 'allow';
      states.response_composer = 'allow';
      states.chat_response = 'allow';
    }
    return states;
  }
  if (evidence.decision === 'ALLOW') {
    for (const module of TRUSTEDSQL_SEQUENCE) states[module] = 'allow';
    states.education_db = 'allow';
    states.response_composer = 'allow';
    states.chat_response = 'allow';
    return states;
  }

  const detectorNode = evidence.detectedAt?.toLowerCase() as VisualNodeId | undefined;
  if (evidence.detectedAt === 'C0') {
    states.memory = 'deny';
  } else {
    const detectorIndex = detectorNode ? TRUSTEDSQL_SEQUENCE.indexOf(detectorNode) : -1;
    TRUSTEDSQL_SEQUENCE.forEach((module, index) => {
      if (detectorIndex >= 0 && index < detectorIndex) states[module] = 'allow';
      if (index === detectorIndex) states[module] = 'deny';
    });
  }
  states.response_composer = 'allow';
  states.chat_response = 'deny';
  return states;
}

function eventState(decision?: string): RouteNodeState {
  if (decision === 'ALLOW') return 'allow';
  if (decision === 'DENY') return 'deny';
  if (decision === 'ERROR') return 'error';
  return 'active';
}

function applyEvent(states: VisualStateMap, event: TelemetryItem): void {
  if (event.eventType !== 'module' && event.eventType !== 'revision') return;
  const state = eventState(event.decision);
  const module = event.module?.toUpperCase();
  if (module === 'ROUTER') {
    states.orchestrator = state;
    return;
  }
  if (module === 'C0') {
    states.memory = state;
    return;
  }
  if (module && /^M[1-7]$/.test(module)) {
    states[module.toLowerCase() as VisualNodeId] = state;
    return;
  }
  if (module === 'X1') {
    states.x1 = state;
    if (state === 'active' || state === 'allow') states.education_db = 'active';
    if (state === 'error') states.education_db = 'error';
    return;
  }
  if (module === 'RAG') {
    states.rag_query = 'allow';
    if (event.stage === 'vertex_rag_retrieval') {
      states.rag_retrieval = state;
    } else if (event.stage === 'vertex_rag_grounding') {
      states.rag_retrieval = 'allow';
      states.rag_grounding = state;
    } else {
      states.rag_retrieval = state;
    }
    return;
  }
  if (module === 'ORCHESTRATOR') {
    if (event.stage === 'orchestrator_chat_generation') {
      states.chat_generation = state;
      if (state === 'allow') states.chat_response = 'active';
    } else if (event.stage === 'orchestrator_response_synthesis') {
      states.response_composer = state;
      if (state === 'allow') states.chat_response = 'active';
    } else {
      states.orchestrator = state;
    }
  }
}

function latestActiveNode(states: VisualStateMap): VisualNodeId | null {
  const order: VisualNodeId[] = [
    'chat_response', 'response_composer', 'education_db', 'x1', 'm7', 'm6', 'm5', 'm4', 'm3', 'm2', 'm1',
    'rag_grounding', 'rag_retrieval', 'rag_query', 'chat_generation', 'memory', 'orchestrator', 'user_query',
  ];
  return order.find((node) => states[node] === 'active') ?? null;
}

function liveVisualStates(events: TelemetryItem[], runState?: RunStatus): VisualStateMap {
  const states = emptyVisualStates();
  if (!runState || runState === 'idle') return states;
  states.user_query = 'allow';
  states.orchestrator = runState === 'queued' ? 'active' : 'allow';
  for (const event of events) applyEvent(states, event);
  if (runState === 'error') {
    const current = latestActiveNode(states);
    if (current) states[current] = 'error';
    states.chat_response = 'error';
  }
  return states;
}

function groupState(states: VisualStateMap, nodes: VisualNodeId[]): RouteNodeState {
  const values = nodes.map((node) => states[node]);
  if (values.includes('deny')) return 'deny';
  if (values.includes('error')) return 'error';
  if (values.includes('active')) return 'active';
  if (values.includes('allow')) return 'allow';
  if (values.every((state) => state === 'untouched')) return 'untouched';
  return 'idle';
}

function routeSummary(evidence: RouteEvidence | undefined, validation: { valid: boolean; reason?: string }, runState?: RunStatus): { label: string; state: RouteNodeState } {
  if (evidence && validation.valid) {
    const branch = evidence.resultType === 'chat'
      ? 'Conversation'
      : evidence.resultType === 'rag'
        ? 'Vertex AI RAG'
        : evidence.mode === 'direct'
          ? 'Generator only'
          : 'TrustedSQL';
    return { label: `${branch} · ${evidence.decision}`, state: evidence.decision === 'DENY' ? 'deny' : 'allow' };
  }
  if (evidence && !validation.valid) return { label: 'Route evidence unavailable', state: 'error' };
  if (runState === 'queued' || runState === 'running') return { label: 'Routing in progress', state: 'active' };
  if (runState === 'error') return { label: 'Execution error', state: 'error' };
  return { label: 'Awaiting query', state: 'idle' };
}

const RouteNode: React.FC<{ node: VisualNode; state: RouteNodeState }> = ({ node, state }) => (
  <g
    data-testid={node.testId ?? `node-${node.id}`}
    className={`route-node ${state} ${node.emphasis ? `route-node-${node.emphasis}` : ''}`}
    aria-label={`${VISUAL_NODE_LABELS[node.id]}: ${state}`}
  >
    <title>{VISUAL_NODE_LABELS[node.id]} — {state}</title>
    <rect x={node.x} y={node.y} width={node.w} height={node.h} rx="8" />
    <text className="route-node-code" x={node.x + 9} y={node.y + 12}>{node.code}</text>
    <text className="route-node-title" x={node.x + 9} y={node.y + 24}>{node.title}</text>
    <text className="route-node-subtitle" x={node.x + 9} y={node.y + node.h - 7}>{node.subtitle}</text>
  </g>
);

export const RoutingMap: React.FC<RoutingMapProps> = ({
  evidence,
  events = [],
  runState,
  mode = 'trustedsql',
  turnOptions = [],
  selectedTurnNumber,
  onSelectTurn,
}) => {
  const [viewport, setViewport] = useState<MapViewport>({ x: 0, y: 0, scale: 1 });
  const [dragging, setDragging] = useState(false);
  const [viewMode, setViewMode] = useState<'route' | 'gnn'>('route');
  const dragOrigin = useRef<DragOrigin | null>(null);
  const gnnGraph = useMemo(
    () => [...events].reverse().find((event) => event.module === 'M2' && event.gnnGraph)?.gnnGraph,
    [events],
  );

  const zoomBy = (delta: number) => setViewport((current) => ({ ...current, scale: clampZoom(current.scale + delta) }));
  const resetViewport = () => {
    dragOrigin.current = null;
    setDragging(false);
    setViewport({ x: 0, y: 0, scale: 1 });
  };

  useEffect(() => {
    resetViewport();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTurnNumber, viewMode, gnnGraph?.graphId]);

  const beginPan = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || (event.target as Element).closest('.routing-map-controls, .gnn-node')) return;
    dragOrigin.current = {
      pointerId: event.pointerId, clientX: event.clientX, clientY: event.clientY,
      viewX: viewport.x, viewY: viewport.y,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDragging(true);
  };
  const pan = (event: React.PointerEvent<HTMLDivElement>) => {
    const origin = dragOrigin.current;
    if (!origin || origin.pointerId !== event.pointerId) return;
    setViewport((current) => ({
      ...current,
      x: origin.viewX + event.clientX - origin.clientX,
      y: origin.viewY + event.clientY - origin.clientY,
    }));
  };
  const finishPan = (event: React.PointerEvent<HTMLDivElement>) => {
    if (dragOrigin.current?.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    dragOrigin.current = null;
    setDragging(false);
  };
  const handleWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    zoomBy(event.deltaY < 0 ? ZOOM_STEP : -ZOOM_STEP);
  };
  const handleViewportKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const panStep = 18;
    if (event.key === '+' || event.key === '=') {
      event.preventDefault();
      zoomBy(ZOOM_STEP);
    } else if (event.key === '-') {
      event.preventDefault();
      zoomBy(-ZOOM_STEP);
    } else if (event.key === '0') {
      event.preventDefault();
      resetViewport();
    } else if (['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault();
      setViewport((current) => ({
        ...current,
        x: current.x + (event.key === 'ArrowLeft' ? -panStep : event.key === 'ArrowRight' ? panStep : 0),
        y: current.y + (event.key === 'ArrowUp' ? -panStep : event.key === 'ArrowDown' ? panStep : 0),
      }));
    }
  };

  const validation = validateRouteEvidence(evidence);
  const states = evidence && validation.valid ? terminalVisualStates(evidence) : liveVisualStates(events, runState);
  const summary = routeSummary(evidence, validation, runState);
  const isTrustedMode = mode === 'trustedsql';
  const composerY = isTrustedMode ? 652 : 520;
  const responseY = isTrustedMode ? 724 : 592;
  const viewBoxHeight = isTrustedMode ? 790 : 660;

  const coreNodes: VisualNode[] = [
    { id: 'user_query', code: 'INPUT', title: 'User Query', subtitle: 'prompt / follow-up', x: 12, y: 28, w: 112, h: 50, testId: 'node-chat', emphasis: 'input' },
    { id: 'orchestrator', code: 'ROUTER', title: 'Orchestrator', subtitle: 'intent + route', x: 168, y: 24, w: 120, h: 58, testId: 'node-orchestrator', emphasis: 'core' },
    { id: 'memory', code: 'C0', title: 'Conversation Memory', subtitle: 'history + role context', x: 332, y: 28, w: 112, h: 50, testId: 'node-context_memory', emphasis: 'core' },
    { id: 'chat_generation', code: 'CHAT', title: 'Chat Generation', subtitle: 'ordinary conversation', x: 23, y: 174, w: 98, h: 50 },
    { id: 'rag_query', code: 'R1', title: 'Query Context', subtitle: 'history-aware request', x: 157, y: 170, w: 114, h: 48 },
    { id: 'rag_retrieval', code: 'R2', title: 'Corpus Retrieval', subtitle: 'Vertex AI RAG Engine', x: 157, y: 240, w: 114, h: 48 },
    { id: 'rag_grounding', code: 'R3', title: 'Grounding + Sources', subtitle: 'answer + citations', x: 157, y: 310, w: 114, h: 48 },
  ];
  const trustedNodes: VisualNode[] = isTrustedMode
    ? [
        { id: 'm1', code: 'M1', title: 'Prompt Integrity', subtitle: 'injection signals', x: 309, y: 166, w: 122, h: 40 },
        { id: 'm2', code: 'M2', title: 'Intent Risk Guard', subtitle: 'heuristic + GNN', x: 309, y: 212, w: 122, h: 40 },
        { id: 'm3', code: 'M3', title: 'Access Planner', subtitle: 'entities + resources', x: 309, y: 258, w: 122, h: 40 },
        { id: 'm4', code: 'M4', title: 'Schema Authorization', subtitle: 'tables + columns', x: 309, y: 304, w: 122, h: 40 },
        { id: 'm5', code: 'M5', title: 'Row Scope', subtitle: 'ownership filters', x: 309, y: 350, w: 122, h: 40 },
        { id: 'm6', code: 'M6', title: 'SQL Generator', subtitle: 'natural language → SQL', x: 309, y: 396, w: 122, h: 40, testId: 'node-sql_generator' },
        { id: 'm7', code: 'M7', title: 'SQL Conformance', subtitle: 'validate generated SQL', x: 309, y: 442, w: 122, h: 40 },
        { id: 'x1', code: 'X1', title: 'Read-only Executor', subtitle: 'bounded execution', x: 309, y: 488, w: 122, h: 40 },
      ]
    : [
        { id: 'm6', code: 'M6', title: 'SQL Generator', subtitle: 'natural language → SQL', x: 309, y: 178, w: 122, h: 44, testId: 'node-sql_generator' },
        { id: 'x1', code: 'X1', title: 'Read-only Executor', subtitle: 'execute generated SQL', x: 309, y: 252, w: 122, h: 44 },
      ];
  const educationDb: VisualNode = isTrustedMode
    ? { id: 'education_db', code: 'DATA', title: 'Education DB', subtitle: 'PostgreSQL', x: 309, y: 564, w: 122, h: 46, testId: 'node-education_db', emphasis: 'database' }
    : { id: 'education_db', code: 'DATA', title: 'Education DB', subtitle: 'PostgreSQL', x: 309, y: 368, w: 122, h: 46, testId: 'node-education_db', emphasis: 'database' };
  const outputNodes: VisualNode[] = [
    { id: 'response_composer', code: 'ORCH', title: 'Response Composer', subtitle: 'paraphrase grounded result', x: 144, y: composerY, w: 168, h: 52, emphasis: 'core' },
    { id: 'chat_response', code: 'OUTPUT', title: 'Chat Response', subtitle: 'answer / refusal + evidence', x: 144, y: responseY, w: 168, h: 50, emphasis: 'output' },
  ];
  const nodes = [...coreNodes, ...trustedNodes, educationDb, ...outputNodes];

  const trustedEntryY = isTrustedMode ? 166 : 178;
  const trustedExitY = isTrustedMode ? 528 : 296;
  const dbTop = educationDb.y;
  const dbBottom = educationDb.y + educationDb.h;
  const edges: VisualEdge[] = [
    { id: 'input-orchestrator', d: 'M124 53 H168', state: states.orchestrator, testId: 'path-chat-orchestrator' },
    { id: 'orchestrator-memory', d: 'M288 43 H332', state: states.memory, testId: 'path-orchestrator-memory' },
    { id: 'memory-orchestrator', d: 'M332 65 H288', state: states.memory, dashed: true },
    { id: 'orchestrator-chat', d: 'M218 82 V110 H72 V174', state: states.chat_generation },
    { id: 'orchestrator-rag', d: 'M228 82 V170', state: states.rag_query, testId: 'path-orchestrator-rag' },
    { id: 'orchestrator-sql', d: `M238 82 V110 H370 V${trustedEntryY}`, state: states[isTrustedMode ? 'm1' : 'm6'], testId: 'path-orchestrator-policy' },
    { id: 'rag-query-retrieval', d: 'M214 218 V240', state: states.rag_retrieval },
    { id: 'rag-retrieval-grounding', d: 'M214 288 V310', state: states.rag_grounding },
    { id: 'chat-composer', d: `M72 224 V${composerY - 26} H186 V${composerY}`, state: states.chat_generation },
    { id: 'rag-composer', d: `M214 358 V${composerY}`, state: states.rag_grounding },
  ];
  if (isTrustedMode) {
    trustedNodes.slice(0, -1).forEach((node, index) => {
      const next = trustedNodes[index + 1];
      edges.push({
        id: `${node.id}-${next.id}`,
        d: `M370 ${node.y + node.h} V${next.y}`,
        state: states[next.id],
        testId: node.id === 'm5' ? 'path-policy-trustedsql' : undefined,
      });
    });
  } else {
    edges.push({ id: 'm6-x1', d: 'M370 222 V252', state: states.x1, testId: 'path-policy-trustedsql' });
  }
  edges.push(
    { id: 'executor-db', d: `M370 ${trustedExitY} V${dbTop}`, state: states.education_db, testId: 'path-trustedsql-db' },
    { id: 'db-composer', d: `M370 ${dbBottom} V${composerY - 26} H270 V${composerY}`, state: states.education_db },
    { id: 'composer-response', d: `M228 ${composerY + 52} V${responseY}`, state: states.chat_response },
  );
  if (isTrustedMode) {
    edges.push({
      id: 'deny-response', d: `M444 330 H450 V${composerY + 26} H312`,
      state: evidence?.decision === 'DENY' && validation.valid ? 'deny' : 'untouched',
      dashed: true, testId: 'path-trustedsql-response',
    });
  }

  const currentNode = latestActiveNode(states);
  const activeVisualNode = currentNode ? nodes.find((node) => node.id === currentNode) : undefined;
  const particle = activeVisualNode
    ? { cx: activeVisualNode.x + activeVisualNode.w / 2, cy: activeVisualNode.y + activeVisualNode.h / 2 }
    : null;
  const ragState = groupState(states, ['rag_query', 'rag_retrieval', 'rag_grounding']);
  const policyState = groupState(states, isTrustedMode ? ['m1', 'm2', 'm3', 'm4', 'm5'] : ['m6']);
  const trustedState = groupState(states, isTrustedMode ? TRUSTEDSQL_SEQUENCE : ['m6', 'x1']);

  return (
    <div className="routing-map-card" data-testid="routing-map-card">
      <div className="routing-map-header">
        <h2 className="routing-map-title">
          {viewMode === 'route' ? <GitBranch size={15} /> : <Network size={15} />}
          {viewMode === 'route' ? 'Query Routing Map' : 'M2 Intent GNN'}
        </h2>
        <div className="routing-map-selectors">
          <label>
            <span className="sr-only">Select turn evidence</span>
            <select aria-label="Select turn evidence" value={selectedTurnNumber ?? ''} disabled={!turnOptions.length} onChange={(event) => onSelectTurn?.(Number(event.target.value))}>
              {!turnOptions.length && <option value="">No turns</option>}
              {turnOptions.map((turn) => <option key={turn.turnNumber} value={turn.turnNumber}>Turn {turn.turnNumber} · {turn.runState.toUpperCase()}</option>)}
            </select>
          </label>
          <label>
            <span className="sr-only">Select visualization</span>
            <select aria-label="Select visualization" value={viewMode} onChange={(event) => setViewMode(event.target.value as 'route' | 'gnn')}>
              <option value="route">Routing map</option>
              <option value="gnn">M2 GNN graph</option>
            </select>
          </label>
        </div>
      </div>

      {viewMode === 'route' && (
        <div className="routing-map-meta-row">
          <div className="routing-map-legend">
            <span className="legend-item"><span className="legend-dot active" />Active</span>
            <span className="legend-item"><span className="legend-dot allow" />Allow</span>
            <span className="legend-item"><span className="legend-dot deny" />Deny</span>
            <span className="legend-item"><span className="legend-dot untouched" />Untouched</span>
          </div>
          <div className={`routing-map-status ${summary.state}`} data-testid="routing-map-status"><span className="routing-map-status-dot" />{summary.label}</div>
        </div>
      )}
      {viewMode === 'route' && evidence && validation.valid && (evidence.detectedAt || evidence.enforcedAt) && (
        <div className="detector-meta-bar" data-testid="detector-meta-bar">
          {evidence.detectedAt && <span className="detector-badge" data-testid="detector-badge">Detector: {evidence.detectedAt}</span>}
          {evidence.enforcedAt && <span className="enforcer-badge" data-testid="enforcer-badge">Enforcer: TrustedSQL</span>}
        </div>
      )}

      <div
        className={`svg-container routing-map-viewport ${dragging ? 'dragging' : ''}`}
        role="region" tabIndex={0}
        aria-label={`Interactive ${viewMode === 'route' ? 'query routing map' : 'M2 GNN graph'}. Drag to move; use the controls, mouse wheel, plus and minus keys to zoom.`}
        onPointerDown={beginPan} onPointerMove={pan} onPointerUp={finishPan} onPointerCancel={finishPan}
        onWheel={handleWheel} onKeyDown={handleViewportKeyDown}
      >
        <div className="routing-map-controls" aria-label="Routing map view controls">
          <span className="routing-map-pan-hint" title="Drag the map to move"><Move size={12} /></span>
          <button type="button" onClick={() => zoomBy(-ZOOM_STEP)} disabled={viewport.scale <= MIN_ZOOM} aria-label={`Zoom out ${viewMode === 'route' ? 'routing map' : 'GNN graph'}`}><ZoomOut size={13} /></button>
          <span className="routing-map-zoom-value" aria-live="polite">{Math.round(viewport.scale * 100)}%</span>
          <button type="button" onClick={() => zoomBy(ZOOM_STEP)} disabled={viewport.scale >= MAX_ZOOM} aria-label={`Zoom in ${viewMode === 'route' ? 'routing map' : 'GNN graph'}`}><ZoomIn size={13} /></button>
          <button type="button" onClick={resetViewport} disabled={viewport.x === 0 && viewport.y === 0 && viewport.scale === 1} aria-label={`Reset ${viewMode === 'route' ? 'routing map' : 'GNN graph'} view`}><Maximize2 size={12} /></button>
        </div>
        <div className="routing-map-canvas" data-testid="routing-map-canvas" style={{ transform: `translate3d(${viewport.x}px, ${viewport.y}px, 0) scale(${viewport.scale})` }}>
          {viewMode === 'route' ? (
            <svg viewBox={`0 0 456 ${viewBoxHeight}`} className="routing-svg" aria-label="Chat, document RAG, and TrustedSQL query routing architecture" role="img">
              <title>Chat, document RAG, and TrustedSQL query routing architecture</title>
              <desc>A user query enters the Orchestrator and Conversation Memory. It branches to chat generation, Vertex AI RAG, or the active data pipeline. Every successful result is composed into a normal chat response.</desc>
              <defs>
                <marker id="route-arrow-head" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 Z" className="route-arrow" /></marker>
                <filter id="route-node-shadow" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="2" stdDeviation="2" floodColor="#0f172a" floodOpacity="0.08" /></filter>
              </defs>

              <g className={`route-group conversation-group ${groupState(states, ['chat_generation'])}`}>
                <rect className="route-group-frame" x="12" y="130" width="120" height="122" rx="12" />
                <text className="route-group-kicker" x="24" y="149">CONVERSATION</text>
                <text className="route-group-title" x="24" y="162">Normal chat</text>
              </g>
              <g data-testid="node-rag" className={`route-group rag-group ${ragState}`}>
                <rect className="route-group-frame" x="144" y="130" width="140" height="254" rx="12" />
                <text className="route-group-kicker" x="157" y="149">DOCUMENTS</text>
                <text className="route-group-title" x="157" y="162">Vertex AI RAG</text>
              </g>
              <g data-testid="node-trustedsql" className={`route-group trustedsql-group ${trustedState}`}>
                <rect className="route-group-frame" x="296" y="130" width="148" height={isTrustedMode ? 420 : 220} rx="12" />
                <text className="route-group-kicker" x="309" y="149">STRUCTURED DATA</text>
                <text className="route-group-title" x="309" y="162">{isTrustedMode ? 'TrustedSQL pipeline' : 'Generator only'}</text>
              </g>
              {isTrustedMode && (
                <g data-testid="node-policy_engine" className={`route-policy-bracket ${policyState}`}>
                  <path d="M302 166 H299 V390 H302" />
                  <text x="299" y="278" transform="rotate(-90 299 278)">SECURITY</text>
                </g>
              )}
              {!isTrustedMode && (
                <g className="security-off-badge"><rect x="309" y="311" width="122" height="24" rx="6" /><text x="370" y="327" textAnchor="middle">SECURITY MODULES OFF</text></g>
              )}

              <g className="route-edges">
                {edges.map((edge) => (
                  <path key={edge.id} data-testid={edge.testId} d={edge.d} fill="none" markerEnd="url(#route-arrow-head)" className={`route-path ${edge.state} ${edge.dashed ? 'route-path-dashed' : ''}`} />
                ))}
              </g>
              {nodes.map((node) => <RouteNode key={node.id} node={node} state={states[node.id]} />)}
              {particle && <circle data-testid="route-particle" cx={particle.cx} cy={particle.cy} r="4" className="route-particle" />}
            </svg>
          ) : <GnnGraph graph={gnnGraph} />}
        </div>
      </div>

      {viewMode === 'route' && (
        <div className="accessible-node-states-container sr-only">
          {nodes.map((node) => <span key={node.id} data-testid={`accessible-state-${node.id}`}>{VISUAL_NODE_LABELS[node.id]} — {states[node.id]}</span>)}
        </div>
      )}
      {viewMode === 'gnn' && (
        <div className="gnn-output-strip" data-testid="gnn-output-strip">
          {gnnGraph ? (
            <>
              <span>{gnnGraph.nodeCount} nodes · {gnnGraph.edgeCount} encoded edges · {gnnGraph.edges.length} visible relations</span>
              {Object.entries(gnnGraph.outputs).map(([key, value]) => <span key={key}><strong>{key}</strong> {value}</span>)}
            </>
          ) : <span>M2 graph unavailable for this turn</span>}
        </div>
      )}
    </div>
  );
};
