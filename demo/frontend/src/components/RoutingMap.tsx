import React, { useEffect, useMemo, useRef, useState } from 'react';
import {
  ExecutionMode,
  NodeStateMap,
  RouteEvidence,
  RouteNodeId,
  RouteNodeState,
} from '../app/types';
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

const clampZoom = (scale: number) => Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, scale));

export interface NodeCoordinates {
  cx: number;
  cy: number;
}

export const NODE_COORDINATES: Record<RouteNodeId, NodeCoordinates> = {
  chat: { cx: 58, cy: 63 },
  orchestrator: { cx: 180, cy: 63 },
  context_memory: { cx: 180, cy: 123 },
  rag: { cx: 60, cy: 198 },
  policy_engine: { cx: 297, cy: 178 },
  trustedsql: { cx: 297, cy: 228 },
  sql_generator: { cx: 297, cy: 203 },
  education_db: { cx: 297, cy: 278 },
};

export const LIVE_TRAJECTORY_NODES: readonly RouteNodeId[] = [
  'chat', 'orchestrator', 'context_memory', 'policy_engine', 'trustedsql', 'education_db',
] as const;

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
  if (evidence.resultType === 'rag') {
    if (evidence.decision !== 'ALLOW' || evidence.executed || evidence.dbTouched) {
      return { valid: false, reason: 'RAG branch must complete without database execution' };
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

const idleStates = (): NodeStateMap => ({
  chat: 'idle',
  orchestrator: 'idle',
  context_memory: 'idle',
  rag: 'idle',
  policy_engine: 'idle',
  trustedsql: 'idle',
  sql_generator: 'idle',
  education_db: 'untouched',
});

function terminalStates(evidence: RouteEvidence): NodeStateMap {
  const states = idleStates();
  for (const node of evidence.route) states[node] = 'allow';
  if (evidence.decision === 'DENY') {
    states.trustedsql = 'deny';
    if (evidence.detectedAt === 'M4' || evidence.detectedAt === 'M5') states.policy_engine = 'deny';
  }
  return states;
}

function activeModules(events: TelemetryItem[]): Map<string, string | undefined> {
  const modules = new Map<string, string | undefined>();
  for (const event of events) {
    if (event.eventType === 'retract' && event.module) modules.delete(event.module);
    else if ((event.eventType === 'module' || event.eventType === 'revision') && event.module) {
      modules.set(event.module, event.decision);
    }
  }
  return modules;
}

function liveStates(mode: ExecutionMode, events: TelemetryItem[], runState?: RunStatus): NodeStateMap {
  const states = idleStates();
  if (!runState || runState === 'idle') return states;
  states.chat = 'allow';
  states.orchestrator = runState === 'queued' ? 'active' : 'allow';
  const modules = activeModules(events);
  if (modules.has('ROUTER') || modules.has('C0')) states.context_memory = 'allow';
  if (modules.has('RAG')) {
    states.rag = runState === 'error' ? 'error' : modules.get('RAG') === 'ALLOW' ? 'allow' : 'active';
    return states;
  }
  if (mode === 'direct') {
    if (modules.has('M6')) states.sql_generator = modules.get('M6') === 'ERROR' ? 'error' : 'allow';
  } else {
    if (modules.has('M4') || modules.has('M5')) {
      const denied = modules.get('M4') === 'DENY' || modules.get('M5') === 'DENY';
      states.policy_engine = denied ? 'deny' : 'allow';
    }
    if (modules.has('M6') || modules.has('M7')) {
      const denied = modules.get('M6') === 'DENY' || modules.get('M7') === 'DENY';
      states.trustedsql = denied ? 'deny' : 'allow';
    }
  }
  if (modules.has('X1')) states.education_db = modules.get('X1') === 'ERROR' ? 'error' : 'active';
  return states;
}

function latestNode(states: NodeStateMap): RouteNodeId | null {
  const order: RouteNodeId[] = ['education_db', 'trustedsql', 'sql_generator', 'policy_engine', 'context_memory', 'rag', 'orchestrator', 'chat'];
  return order.find((node) => states[node] === 'active') ?? null;
}

const nodeLabel: Record<RouteNodeId, string> = {
  chat: 'Chat',
  orchestrator: 'Orchestrator',
  context_memory: 'Conversation Memory',
  rag: 'Vertex AI RAG',
  policy_engine: 'Policy Engine',
  trustedsql: 'TrustedSQL',
  sql_generator: 'SQL Generator',
  education_db: 'Education DB',
};

function pathState(states: NodeStateMap, target: RouteNodeId): RouteNodeState {
  const state = states[target];
  return state === 'untouched' ? 'idle' : state;
}

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

  const zoomBy = (delta: number) => {
    setViewport((current) => ({ ...current, scale: clampZoom(current.scale + delta) }));
  };

  const resetViewport = () => {
    dragOrigin.current = null;
    setDragging(false);
    setViewport({ x: 0, y: 0, scale: 1 });
  };

  useEffect(() => {
    resetViewport();
  // Reset the camera when the operator changes evidence snapshots or diagram type.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedTurnNumber, viewMode, gnnGraph?.graphId]);

  const beginPan = (event: React.PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0 || (event.target as Element).closest('.routing-map-controls, .gnn-node')) return;
    dragOrigin.current = {
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      viewX: viewport.x,
      viewY: viewport.y,
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
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
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
  const states = evidence && validation.valid ? terminalStates(evidence) : liveStates(mode, events, runState);
  const currentNode = latestNode(states);
  const particle = currentNode ? NODE_COORDINATES[currentNode] : null;
  const modules = activeModules(events);
  const ragBranch = evidence?.resultType === 'rag' || modules.has('RAG');
  const completed = Boolean(evidence && validation.valid);
  const status = !validation.valid && evidence
    ? `Evidence invalid: ${validation.reason}`
    : evidence?.resultType === 'rag'
      ? 'Document route completed: grounded answer returned with sources; DB untouched'
      : evidence?.decision === 'DENY'
        ? 'Data route enforced: DENY at TrustedSQL boundary'
      : evidence?.decision === 'ALLOW'
          ? `Data route completed through ${mode === 'direct' ? 'Direct SQL' : 'TrustedSQL'}`
          : runState === 'error' && ragBranch
            ? 'Document route failed before a grounded answer could be returned'
          : ragBranch
            ? 'Retrieving and grounding university documents...'
            : runState === 'queued' || runState === 'running'
              ? 'Classifying request, then selecting the document or data branch...'
              : 'No runtime route evidence yet';

  const nodes: Array<{ id: RouteNodeId; x: number; y: number; w: number; text: string }> = [
    { id: 'chat', x: 8, y: 50, w: 100, text: 'Chat Interface' },
    { id: 'orchestrator', x: 125, y: 50, w: 110, text: 'Orchestrator' },
    { id: 'context_memory', x: 120, y: 110, w: 120, text: 'Conversation Memory' },
    { id: 'rag', x: 8, y: 185, w: 104, text: 'Vertex AI RAG' },
    ...(mode === 'trustedsql'
      ? [
          { id: 'policy_engine' as const, x: 242, y: 165, w: 110, text: 'Policy Engine' },
          { id: 'trustedsql' as const, x: 242, y: 215, w: 110, text: 'TrustedSQL' },
        ]
      : [{ id: 'sql_generator' as const, x: 242, y: 190, w: 110, text: 'SQL Generator' }]),
    { id: 'education_db', x: 242, y: 265, w: 110, text: 'Education DB' },
  ];

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
            <select
              aria-label="Select turn evidence"
              value={selectedTurnNumber ?? ''}
              disabled={!turnOptions.length}
              onChange={(event) => onSelectTurn?.(Number(event.target.value))}
            >
              {!turnOptions.length && <option value="">No turns</option>}
              {turnOptions.map((turn) => (
                <option key={turn.turnNumber} value={turn.turnNumber}>
                  Turn {turn.turnNumber} · {turn.runState.toUpperCase()}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span className="sr-only">Select visualization</span>
            <select
              aria-label="Select visualization"
              value={viewMode}
              onChange={(event) => setViewMode(event.target.value as 'route' | 'gnn')}
            >
              <option value="route">Routing map</option>
              <option value="gnn">M2 GNN graph</option>
            </select>
          </label>
        </div>
      </div>

      {viewMode === 'route' && (
        <div className="routing-map-legend">
          <span className="legend-item"><span className="legend-dot active" />Active</span>
          <span className="legend-item"><span className="legend-dot allow" />Allow</span>
          <span className="legend-item"><span className="legend-dot deny" />Deny</span>
          <span className="legend-item"><span className="legend-dot untouched" />Untouched</span>
        </div>
      )}

      {viewMode === 'route' && evidence && validation.valid && (evidence.detectedAt || evidence.enforcedAt) && (
        <div className="detector-meta-bar">
          {evidence.detectedAt && <span className="detector-badge">Detector: {evidence.detectedAt}</span>}
          {evidence.enforcedAt && <span className="enforcer-badge">Enforcer: TrustedSQL</span>}
        </div>
      )}

      <div
        className={`svg-container routing-map-viewport ${dragging ? 'dragging' : ''}`}
        role="region"
        tabIndex={0}
        aria-label={`Interactive ${viewMode === 'route' ? 'query routing map' : 'M2 GNN graph'}. Drag to move; use the controls, mouse wheel, plus and minus keys to zoom.`}
        onPointerDown={beginPan}
        onPointerMove={pan}
        onPointerUp={finishPan}
        onPointerCancel={finishPan}
        onWheel={handleWheel}
        onKeyDown={handleViewportKeyDown}
      >
        <div className="routing-map-controls" aria-label="Routing map view controls">
          <span className="routing-map-pan-hint" title="Drag the map to move"><Move size={12} /></span>
          <button type="button" onClick={() => zoomBy(-ZOOM_STEP)} disabled={viewport.scale <= MIN_ZOOM} aria-label={`Zoom out ${viewMode === 'route' ? 'routing map' : 'GNN graph'}`}><ZoomOut size={13} /></button>
          <span className="routing-map-zoom-value" aria-live="polite">{Math.round(viewport.scale * 100)}%</span>
          <button type="button" onClick={() => zoomBy(ZOOM_STEP)} disabled={viewport.scale >= MAX_ZOOM} aria-label={`Zoom in ${viewMode === 'route' ? 'routing map' : 'GNN graph'}`}><ZoomIn size={13} /></button>
          <button type="button" onClick={resetViewport} disabled={viewport.x === 0 && viewport.y === 0 && viewport.scale === 1} aria-label={`Reset ${viewMode === 'route' ? 'routing map' : 'GNN graph'} view`}><Maximize2 size={12} /></button>
        </div>
        <div
          className="routing-map-canvas"
          data-testid="routing-map-canvas"
          style={{ transform: `translate3d(${viewport.x}px, ${viewport.y}px, 0) scale(${viewport.scale})` }}
        >
        {viewMode === 'route' ? (
        <svg viewBox="0 0 360 304" className="routing-svg" aria-label="Document and database query routing architecture" role="img">
          <title>Document and database query routing architecture</title>
          <desc>Chat enters the Orchestrator. The Orchestrator exchanges context bidirectionally with Conversation Memory, then selects Vertex AI RAG or the active SQL branch.</desc>
          <defs>
            <marker id="route-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
              <path d="M0,0 L7,3.5 L0,7 Z" className="route-arrow" />
            </marker>
          </defs>
          <line x1="108" y1="63" x2="125" y2="63" markerEnd="url(#route-arrow)" className={`route-path ${pathState(states, 'orchestrator')}`} />
          <line x1="174" y1="76" x2="174" y2="110" markerEnd="url(#route-arrow)" className={`route-path memory-link ${pathState(states, 'context_memory')}`} />
          <line x1="186" y1="110" x2="186" y2="76" markerEnd="url(#route-arrow)" className={`route-path memory-link ${pathState(states, 'context_memory')}`} />
          <line x1="145" y1="76" x2="70" y2="185" markerEnd="url(#route-arrow)" className={`route-path ${pathState(states, 'rag')}`} />
          {mode === 'trustedsql' ? (
            <>
              <line x1="215" y1="76" x2="284" y2="165" markerEnd="url(#route-arrow)" className={`route-path ${pathState(states, 'policy_engine')}`} />
              <line x1="297" y1="191" x2="297" y2="215" markerEnd="url(#route-arrow)" className={`route-path ${pathState(states, 'trustedsql')}`} />
              <line x1="297" y1="241" x2="297" y2="265" markerEnd="url(#route-arrow)" className={`route-path ${pathState(states, 'education_db')}`} />
            </>
          ) : (
            <>
              <line x1="215" y1="76" x2="285" y2="190" markerEnd="url(#route-arrow)" className={`route-path ${pathState(states, 'sql_generator')}`} />
              <line x1="297" y1="216" x2="297" y2="265" markerEnd="url(#route-arrow)" className={`route-path ${pathState(states, 'education_db')}`} />
            </>
          )}

          {nodes.map((node) => (
            <g key={node.id} data-testid={`node-${node.id}`} className={`route-node ${states[node.id]}`}>
              <rect x={node.x} y={node.y} width={node.w} height="26" rx="6" />
              <text x={node.x + node.w / 2} y={node.y + 17} textAnchor="middle">{node.text}</text>
            </g>
          ))}
          {particle && <circle data-testid="route-particle" cx={particle.cx} cy={particle.cy} r="4" className="route-particle" />}
        </svg>
        ) : <GnnGraph graph={gnnGraph} />}
        </div>
      </div>

      {viewMode === 'route' && <div className="accessible-node-states-container sr-only">
        {nodes.map((node) => <span key={node.id}>{nodeLabel[node.id]} — {states[node.id]}</span>)}
      </div>}
      {viewMode === 'route' ? <div className="db-touch-status-line">
        <span className="db-status-label">
          DB Execution State: {evidence?.dbTouched ? 'Dispatched & Executed' : ragBranch && (completed || runState === 'running') ? 'Untouched (document route)' : 'No query dispatched'}
        </span>
      </div> : (
        <div className="gnn-output-strip" data-testid="gnn-output-strip">
          {gnnGraph ? (
            <>
              <span>{gnnGraph.nodeCount} nodes · {gnnGraph.edgeCount} encoded edges · {gnnGraph.edges.length} visible relations</span>
              {Object.entries(gnnGraph.outputs).map(([key, value]) => <span key={key}><strong>{key}</strong> {value}</span>)}
            </>
          ) : <span>M2 graph unavailable for this turn</span>}
        </div>
      )}
      <div className={`routing-map-status ${viewMode === 'route' && evidence && !validation.valid ? 'error' : ''}`} data-testid="routing-map-status" role="status" aria-live="polite">
        {viewMode === 'route'
          ? status
          : gnnGraph
            ? `Showing canonical M2 graph relations and exact inference outputs for Turn ${selectedTurnNumber ?? gnnGraph.currentTurn ?? '?'}`
            : mode === 'direct'
              ? 'M2 is bypassed in Direct SQL mode, so this turn has no GNN graph'
              : 'No M2 GNN evidence was emitted for this turn'}
      </div>
    </div>
  );
};
