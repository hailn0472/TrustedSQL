import { ExecutionMode, GnnGraphSnapshot, PromptScenarioSearchItem, ScenarioMetadata, ScenarioRoleFilter, ToolReadiness, RouteNodeId, RouteDecision } from '../app/types';

const REQUIRED_MODULES = ['C0', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'X1'] as const;
const REQUIRED_MODULE_SET = new Set<string>(REQUIRED_MODULES);
const EVENT_MODULE_SET = new Set<string>([...REQUIRED_MODULES, 'ROUTER', 'RAG']);
const KNOWN_SCENARIO_KEYS = ['multiturn'] as const;
const ROUTE_NODES = ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'sql_generator', 'education_db'] as const;
const MAX_TEXT = 500;
const MAX_CHAT_QUERY = 2_000;
const MAX_CHAT_TURNS = 20;
const MAX_SQL = 4_000;
const MAX_ROWS = 500;
const MAX_COLUMNS = 100;
const CONVERSATION_ID = /^conversation-[0-9a-f]{32}$/;
const DATASET_SCENARIO_ID = /^(?:MT-(?:BEN|MAL)|ST-(?:BENIGN|PI|RBAC))-[0-9]{3}$/;

type JsonRecord = Record<string, unknown>;

export interface BootstrapCatalogItem {
  key: string;
  canonical_id: string;
  title: string;
  description: string;
  turn_count: number;
  turn_type: 'multi';
  role: 'lecturer';
  user_id: 1;
  turns: Array<{
    turn_number: number;
    label: string;
    classification: 'BENIGN' | 'MALICIOUS';
    description: string;
    nlq: string;
  }>;
}

export interface BootstrapResponseDto {
  ready: boolean;
  readiness: string;
  catalog: BootstrapCatalogItem[];
  architecture: string[];
  ragReady: boolean;
}

export type TelemetryExecutionState = 'queued' | 'running' | 'unknown';

export interface RunJobDto {
  runId: string;
  conversationId: string;
  state: 'queued' | 'running' | 'complete' | 'denied' | 'error' | 'cancelled';
  scenarioKey: string;
  sampleId: string;
  throughTurn: number;
  turnType: 'multi';
  mode?: ExecutionMode;
}

export interface ModuleEventDto {
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

export interface RagSourceDto {
  citation: number;
  title: string;
  uri?: string;
  documentName?: string;
  snippet?: string;
}

export interface TraceEventDto {
  runId: string;
  sampleId?: string;
  turnNumber?: number;
  module: string;
  streamSequence: number;
  stage?: string;
  decision: 'RUNNING';
  detail: string;
  traceStep: number;
  traceTotal: number;
}

export interface RetractEventDto {
  runId: string;
  sampleId?: string;
  turnNumber?: number;
  module: string;
  streamSequence: number;
  revision?: number;
  reason?: string;
}

export interface FinalResultDto {
  runId?: string;
  sampleId?: string;
  turnNumber?: number;
  decision: RouteDecision;
  detectedAt?: string;
  enforcedAt?: 'trustedsql';
  executed: boolean;
  dbTouched: boolean;
  columns?: string[];
  rows?: Array<Array<string | number | boolean | null>>;
  sql?: string;
  latencyMs?: number;
  error?: string;
  route: RouteNodeId[];
  mode?: ExecutionMode;
  resultType?: 'sql' | 'rag';
  answer?: string;
  sources?: RagSourceDto[];
}

export interface RunStateDto extends RunJobDto {
  events: Array<ModuleEventDto | TraceEventDto | RetractEventDto>;
  finalResult?: FinalResultDto;
  error?: string;
}

export interface RunStatusEventDto {
  runId: string;
  state: RunJobDto['state'];
  finalResult?: FinalResultDto;
  error?: string;
}

export interface RunCorrelationContext {
  runId: string;
  sampleId?: string;
  throughTurn?: number;
  mode?: ExecutionMode;
}

const isRecord = (value: unknown): value is JsonRecord => Boolean(value) && typeof value === 'object' && !Array.isArray(value);
const boundedText = (value: unknown, max = MAX_TEXT): string | undefined =>
  typeof value === 'string' && value.length <= max ? value : undefined;
const boundedRequiredText = (value: unknown, max = MAX_TEXT): string | null => boundedText(value, max) ?? null;
const SAFE_STAGE_LABELS: Record<string, string> = {
  queued: 'Job queued',
  running: 'Execution running',
  complete: 'Execution complete',
  denied: 'Access decision recorded',
  error: 'Execution error',
  cancelled: 'Execution cancelled',
  inbound_sanitization: 'Input validation',
  policy_check: 'Policy evaluation',
  evaluation: 'Security evaluation',
  query_execution: 'Query execution',
  direct_sql_generator: 'Direct SQL generation',
  direct_sql_executor: 'Direct database execution',
  runtime_context_builder: 'Build trusted runtime context',
  prompt_integrity_guard: 'Inspect prompt integrity',
  m2_intent_risk_guard: 'Resolve intent and cross-turn risk',
  policy_grounded_resource_planner: 'Plan policy-scoped resources',
  table_column_access_validator: 'Validate table and column access',
  row_scope_proof_verifier: 'Verify row-level scope',
  policy_aware_sql_generator: 'Generate policy-aware SQL',
  sql_conformance_validator: 'Validate generated SQL',
  readonly_sql_executor: 'Execute read-only SQL',
  intent_router: 'Classify document or database request',
  orchestrator: 'Orchestrate document or database route',
  direct_context_memory: 'Hydrate conversation memory',
  vertex_rag_retrieval: 'Retrieve from Vertex AI RAG Engine',
  vertex_rag_grounding: 'Validate grounding and citations',
};
const SAFE_MODULE_ERROR = 'Runtime reported an issue';
const SAFE_RETRACT_REASON = 'Evidence retracted by the runtime';

export function safeStageLabel(value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== 'string') return 'Module activity';
  return SAFE_STAGE_LABELS[value.toLowerCase()] ?? 'Module activity';
}

export function safeRuntimeError(value: unknown): string | undefined {
  return value === undefined || value === null ? undefined : SAFE_MODULE_ERROR;
}

export function safeRetractReason(_value: unknown): string {
  return SAFE_RETRACT_REASON;
}

export function safeFinalError(decision: RouteDecision, value: unknown): string | undefined {
  if (value === undefined || value === null) return undefined;
  if (decision === 'DENY') return 'Access denied by security policy';
  if (decision === 'ERROR') return 'Execution could not be completed';
  return 'Runtime reported an issue';
}

const isPositiveInteger = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value > 0;
const isNonNegativeInteger = (value: unknown): value is number => typeof value === 'number' && Number.isInteger(value) && value >= 0;
const isFiniteNumber = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const isRouteNode = (value: unknown): value is RouteNodeId => typeof value === 'string' && (ROUTE_NODES as readonly string[]).includes(value);

function safeError(message: string): Error {
  return new Error(message.slice(0, MAX_TEXT));
}

function architectureModules(value: unknown): string[] | null {
  const modules = Array.isArray(value) ? value : isRecord(value) && Array.isArray(value.modules) ? value.modules : null;
  if (!modules || modules.some((module) => typeof module !== 'string')) return null;
  const normalized = modules as string[];
  if (
    normalized.length !== REQUIRED_MODULES.length ||
    new Set(normalized).size !== REQUIRED_MODULES.length ||
    REQUIRED_MODULES.some((module) => !normalized.includes(module))
  ) {
    return null;
  }
  return normalized;
}

function normalizeCatalogItem(value: unknown, fallbackKey?: string): BootstrapCatalogItem | null {
  if (!isRecord(value)) return null;
  const key = boundedRequiredText(value.key ?? fallbackKey);
  const canonicalId = boundedRequiredText(value.canonical_id ?? value.canonicalId);
  const title = boundedRequiredText(value.title);
  const description = boundedRequiredText(value.description);
  const turnType = value.turn_type === 'multi' || value.turnType === 'multi' ? 'multi' : null;
  const role = value.role === 'lecturer' ? 'lecturer' : null;
  const userId = value.user_id === 1 || value.userId === 1 ? 1 : null;
  const rawTurns = Array.isArray(value.turns) ? value.turns : null;
  if (!key || !(KNOWN_SCENARIO_KEYS as readonly string[]).includes(key) || !canonicalId || !title || !description || !turnType || !role || userId === null || !rawTurns || rawTurns.length === 0) return null;

  const turns = rawTurns.map((rawTurn): BootstrapCatalogItem['turns'][number] | null => {
    if (!isRecord(rawTurn)) return null;
    const number = rawTurn.turn_id ?? rawTurn.turn_number ?? rawTurn.turnNumber;
    const nlq = boundedRequiredText(rawTurn.nlq, MAX_CHAT_QUERY);
    const classification = rawTurn.turn_label === 'BENIGN' || rawTurn.turn_label === 'MALICIOUS'
      ? rawTurn.turn_label
      : null;
    if (!isPositiveInteger(number) || !nlq || !classification) return null;
    return {
      turn_number: number,
      // The backend display label is deliberately ignored: only neutral demo-owned labels enter UI state.
      label: `Turn ${number}`,
      classification,
      description: nlq,
      nlq,
    };
  });
  if (turns.some((turn): turn is null => turn === null)) return null;
  const normalizedTurns = turns as BootstrapCatalogItem['turns'];
  if (normalizedTurns.some((turn, index) => turn.turn_number !== index + 1)) return null;
  const declaredTurnCount = value.turn_count ?? value.turnCount;
  if (declaredTurnCount !== undefined && declaredTurnCount !== normalizedTurns.length) return null;
  if (normalizedTurns.length < 2) return null;

  return {
    key,
    canonical_id: canonicalId,
    title,
    description,
    turn_count: normalizedTurns.length,
    turn_type: turnType,
    role,
    user_id: userId,
    turns: normalizedTurns,
  };
}

export function validateBootstrapResponse(data: unknown): BootstrapResponseDto | null {
  if (!isRecord(data) || typeof data.ready !== 'boolean') return null;
  const architecture = architectureModules(data.architecture);
  if (!architecture) return null;
  if (!isRecord(data.readiness) || typeof data.readiness.ready !== 'boolean' || data.readiness.ready !== data.ready) return null;
  const readiness = data.readiness.ready ? 'ready' : 'not-ready';
  const ragReady = isRecord(data.rag) && typeof data.rag.ready === 'boolean' ? data.rag.ready : false;
  const rawCatalog = Array.isArray(data.catalog)
    ? data.catalog.map((item) => ({ item, key: undefined }))
    : isRecord(data.catalog)
      ? Object.entries(data.catalog).map(([key, item]) => ({ item, key }))
      : null;
  if (!rawCatalog) return null;
  const catalog = rawCatalog.map(({ item, key }) => normalizeCatalogItem(item, key));
  if (catalog.some((item): item is null => item === null) || catalog.length !== 1 || catalog[0]?.key !== 'multiturn') return null;
  return {
    ready: data.ready,
    readiness,
    catalog: catalog as BootstrapCatalogItem[],
    architecture,
    ragReady,
  };
}

const VALID_STATES = ['queued', 'running', 'complete', 'denied', 'error', 'cancelled'] as const;
const GNN_NODE_TYPES = new Set([
  'Role',
  'UserTurn',
  'EntityMention',
  'SemanticConceptCandidate',
  'ScopeCandidate',
  'TargetCandidate',
  'ReferenceExpression',
  'PreviousSemanticState',
]);

function normalizeGnnGraph(data: unknown): GnnGraphSnapshot | null {
  if (!isRecord(data)) return null;
  const graphId = boundedRequiredText(data.graphId, MAX_TEXT);
  if (!graphId || !isNonNegativeInteger(data.nodeCount) || !isNonNegativeInteger(data.edgeCount)) return null;
  if (!Array.isArray(data.nodes) || !Array.isArray(data.edges) || data.nodes.length > 120 || data.edges.length > 240) return null;
  const currentTurn = data.currentTurn === undefined || data.currentTurn === null
    ? undefined
    : isPositiveInteger(data.currentTurn) && data.currentTurn <= MAX_CHAT_TURNS
      ? data.currentTurn
      : null;
  if (currentTurn === null) return null;

  const nodes = [] as GnnGraphSnapshot['nodes'];
  const nodeIds = new Set<string>();
  for (const raw of data.nodes) {
    if (!isRecord(raw)) return null;
    const id = boundedRequiredText(raw.id, 160);
    const type = boundedRequiredText(raw.type, 64);
    const label = boundedRequiredText(raw.label, 120);
    const turnNumber = raw.turnNumber === undefined
      ? undefined
      : isPositiveInteger(raw.turnNumber) && raw.turnNumber <= MAX_CHAT_TURNS
        ? raw.turnNumber
        : null;
    const confidence = raw.confidence === undefined
      ? undefined
      : isFiniteNumber(raw.confidence) && raw.confidence >= 0 && raw.confidence <= 1
        ? raw.confidence
        : null;
    if (!id || !type || !GNN_NODE_TYPES.has(type) || !label || turnNumber === null || confidence === null || nodeIds.has(id)) return null;
    nodeIds.add(id);
    nodes.push({
      id,
      type: type as GnnGraphSnapshot['nodes'][number]['type'],
      label,
      turnNumber,
      current: raw.current === true ? true : undefined,
      confidence,
    });
  }

  const edges = [] as GnnGraphSnapshot['edges'];
  for (const raw of data.edges) {
    if (!isRecord(raw)) return null;
    const source = boundedRequiredText(raw.source, 160);
    const target = boundedRequiredText(raw.target, 160);
    const type = boundedRequiredText(raw.type, 80);
    const confidence = raw.confidence === undefined
      ? undefined
      : isFiniteNumber(raw.confidence) && raw.confidence >= 0 && raw.confidence <= 1
        ? raw.confidence
        : null;
    const distance = raw.distance === undefined
      ? undefined
      : isNonNegativeInteger(raw.distance) && raw.distance <= MAX_CHAT_TURNS
        ? raw.distance
        : null;
    if (!source || !target || !type || !nodeIds.has(source) || !nodeIds.has(target) || confidence === null || distance === null) return null;
    edges.push({ source, target, type, confidence, distance });
  }

  const outputs: GnnGraphSnapshot['outputs'] = {};
  if (!isRecord(data.outputs)) return null;
  for (const key of ['intent', 'scope', 'target', 'securityTransition'] as const) {
    if (data.outputs[key] === undefined) continue;
    const value = boundedText(data.outputs[key], 80);
    if (!value) return null;
    outputs[key] = value;
  }
  const decision = boundedRequiredText(data.decision, 16);
  if (!decision || !['ALLOW', 'DENY', 'ERROR'].includes(decision)) return null;
  return {
    graphId,
    currentTurn,
    nodeCount: data.nodeCount,
    edgeCount: data.edgeCount,
    nodes,
    edges,
    outputs,
    decision,
  };
}

function isRunState(value: unknown): value is RunJobDto['state'] {
  return typeof value === 'string' && (VALID_STATES as readonly string[]).includes(value);
}

export function validateRunJobDto(data: unknown): RunJobDto | null {
  if (!isRecord(data) || !boundedText(data.runId) || typeof data.conversationId !== 'string' || !CONVERSATION_ID.test(data.conversationId) || !isRunState(data.state) || !boundedText(data.scenarioKey) || !KNOWN_SCENARIO_KEYS.includes(data.scenarioKey as typeof KNOWN_SCENARIO_KEYS[number]) || !isPositiveInteger(data.throughTurn)) return null;
  if (data.sampleId !== 'interactive-multiturn' || data.turnType !== 'multi' || data.throughTurn > MAX_CHAT_TURNS) return null;
  const mode: ExecutionMode | null = data.mode === undefined || data.mode === 'trustedsql'
    ? 'trustedsql'
    : data.mode === 'direct'
      ? 'direct'
      : null;
  if (!mode) return null;
  return {
    runId: data.runId as string,
    conversationId: data.conversationId,
    state: data.state,
    scenarioKey: data.scenarioKey as string,
    sampleId: (data.sampleId as string | undefined) ?? '',
    throughTurn: data.throughTurn,
    turnType: data.turnType,
    mode,
  };
}

function normalizeModuleEvent(data: unknown, expectedType: 'module' | 'revision' = 'module'): ModuleEventDto | null {
  if (!isRecord(data) || !boundedText(data.runId) || !isPositiveInteger(data.streamSequence)) return null;
  const module = boundedText(data.module ?? data.moduleId);
  if (!module || !EVENT_MODULE_SET.has(module)) return null;
  const sampleId = data.sampleId === undefined ? undefined : boundedText(data.sampleId);
  const turnValue = data.turnNumber ?? data.turnId;
  const turnNumber = turnValue === undefined ? undefined : isPositiveInteger(turnValue) ? turnValue : null;
  if (turnNumber === null || (sampleId === null)) return null;
  const revision = data.revision === undefined ? undefined : isNonNegativeInteger(data.revision) ? data.revision : null;
  if (revision === null || (expectedType === 'revision' && revision === undefined)) return null;
  const decisionValue = boundedText(data.decision)?.toUpperCase();
  if (data.decision !== undefined && !decisionValue) return null;
  const decision = decisionValue && ['ALLOW', 'DENY', 'ERROR', 'QUEUED', 'RUNNING'].includes(decisionValue) ? decisionValue : undefined;
  if (data.decision !== undefined && !decision) return null;
  const latencyMs = data.latencyMs === undefined ? undefined : isFiniteNumber(data.latencyMs) && data.latencyMs >= 0 ? data.latencyMs : null;
  if (latencyMs === null) return null;
  const detail = data.detail === undefined ? undefined : boundedText(data.detail, MAX_TEXT);
  if (data.detail !== undefined && detail === undefined) return null;
  const traceLines = data.traceLines === undefined
    ? undefined
    : Array.isArray(data.traceLines) && data.traceLines.length <= 6
      ? data.traceLines.map((line) => boundedText(line, 160))
      : null;
  if (traceLines === null || traceLines?.some((line) => line === undefined)) return null;
  const gnnGraph = data.gnnGraph === undefined ? undefined : normalizeGnnGraph(data.gnnGraph);
  if (data.gnnGraph !== undefined && !gnnGraph) return null;
  return {
    runId: data.runId as string,
    sampleId,
    turnNumber,
    module,
    streamSequence: data.streamSequence,
    stage: safeStageLabel(data.stage),
    decision,
    revision,
    latencyMs,
    error: safeRuntimeError(data.error),
    detail,
    traceLines: traceLines as string[] | undefined,
    gnnGraph: gnnGraph ?? undefined,
  };
}

export function validateModuleEventDto(data: unknown): ModuleEventDto | null {
  return normalizeModuleEvent(data);
}

export function validateRevisionEventDto(data: unknown): ModuleEventDto | null {
  return normalizeModuleEvent(data, 'revision');
}

export function validateTraceEventDto(data: unknown): TraceEventDto | null {
  if (!isRecord(data) || !boundedText(data.runId) || !isPositiveInteger(data.streamSequence)) return null;
  const module = boundedText(data.module ?? data.moduleId);
  const detail = boundedText(data.detail, 160);
  const sampleId = data.sampleId === undefined ? undefined : boundedText(data.sampleId);
  const turnValue = data.turnNumber ?? data.turnId;
  const turnNumber = turnValue === undefined ? undefined : isPositiveInteger(turnValue) ? turnValue : null;
  if (!module || !EVENT_MODULE_SET.has(module) || !detail || sampleId === null || turnNumber === null) return null;
  if (!isPositiveInteger(data.traceStep) || !isPositiveInteger(data.traceTotal) || data.traceTotal > 6 || data.traceStep > data.traceTotal) return null;
  return {
    runId: data.runId as string,
    sampleId,
    turnNumber,
    module,
    streamSequence: data.streamSequence,
    stage: safeStageLabel(data.stage),
    decision: 'RUNNING',
    detail,
    traceStep: data.traceStep,
    traceTotal: data.traceTotal,
  };
}

export function validateRetractEventDto(data: unknown): RetractEventDto | null {
  if (!isRecord(data) || !boundedText(data.runId) || !isPositiveInteger(data.streamSequence)) return null;
  const module = boundedText(data.module ?? data.moduleId);
  if (!module || !REQUIRED_MODULE_SET.has(module)) return null;
  const sampleId = data.sampleId === undefined ? undefined : boundedText(data.sampleId);
  const turnValue = data.turnNumber ?? data.turnId;
  const turnNumber = turnValue === undefined ? undefined : isPositiveInteger(turnValue) ? turnValue : null;
  const revision = data.revision === undefined ? undefined : isNonNegativeInteger(data.revision) ? data.revision : null;
  if (sampleId === null || turnNumber === null || revision === null) return null;
  return {
    runId: data.runId as string,
    sampleId,
    turnNumber,
    module,
    streamSequence: data.streamSequence,
    revision,
    reason: safeRetractReason(data.reason),
  };
}

function expectedRoute(decision: RouteDecision, executed: boolean, mode: ExecutionMode, resultType: 'sql' | 'rag'): RouteNodeId[] {
  if (resultType === 'rag') return ['chat', 'orchestrator', 'context_memory', 'rag'];
  if (mode === 'direct') {
    return decision === 'ALLOW' && executed
      ? ['chat', 'orchestrator', 'context_memory', 'sql_generator', 'education_db']
      : ['chat', 'orchestrator', 'context_memory', 'sql_generator'];
  }
  const common: RouteNodeId[] = ['chat', 'orchestrator', 'context_memory', 'policy_engine'];
  if (decision === 'ALLOW' && executed) return [...common, 'trustedsql', 'education_db'];
  if (decision === 'DENY') return [...common, 'trustedsql'];
  return common;
}

function validRows(columns: unknown, rows: unknown): { columns?: string[]; rows?: FinalResultDto['rows'] } | null {
  if (columns === undefined && rows === undefined) return {};
  if (!Array.isArray(columns) || !Array.isArray(rows) || columns.length > MAX_COLUMNS || rows.length > MAX_ROWS) return null;
  if (columns.some((column) => !boundedText(column, 100))) return null;
  const normalizedColumns = columns as string[];
  const normalizedRows: Array<Array<string | number | boolean | null>> = [];
  for (const row of rows) {
    let cells: unknown[];
    if (Array.isArray(row)) {
      if (row.length !== normalizedColumns.length) return null;
      cells = row;
    } else if (isRecord(row)) {
      const keys = Object.keys(row);
      if (keys.length !== normalizedColumns.length || keys.some((key) => !normalizedColumns.includes(key))) return null;
      cells = normalizedColumns.map((column) => row[column]);
    } else {
      return null;
    }
    const normalizedRow: Array<string | number | boolean | null> = [];
    for (const cell of cells) {
      if (cell !== null && typeof cell !== 'string' && typeof cell !== 'number' && typeof cell !== 'boolean') return null;
      normalizedRow.push(cell);
    }
    normalizedRows.push(normalizedRow);
  }
  return { columns: normalizedColumns, rows: normalizedRows };
}

export function validateFinalResultDto(data: unknown, expected?: RunCorrelationContext): FinalResultDto | null {
  if (!isRecord(data) || !['ALLOW', 'DENY', 'ERROR'].includes(data.decision as string)) return null;
  const runId = boundedText(data.runId);
  const sampleId = boundedText(data.sampleId);
  const turnValue = data.turnNumber ?? data.turnId;
  const turnNumber = turnValue === undefined ? null : isPositiveInteger(turnValue) ? turnValue : null;
  if (!runId || !sampleId || turnNumber === null) return null;
  if (data.turnId !== undefined && data.turnId !== 1 && (!expected || data.turnId !== expected.throughTurn)) {
    return null;
  }
  if (expected && (runId !== expected.runId || (expected.sampleId !== undefined && sampleId !== expected.sampleId) || (expected.throughTurn !== undefined && turnNumber !== expected.throughTurn))) return null;
  const decision = data.decision as RouteDecision;
  const mode: ExecutionMode | null = data.mode === undefined || data.mode === 'trustedsql'
    ? 'trustedsql'
    : data.mode === 'direct'
      ? 'direct'
      : null;
  if (!mode || (expected?.mode && mode !== expected.mode)) return null;
  const resultType = data.resultType === 'rag'
    ? 'rag'
    : data.resultType === undefined || data.resultType === 'sql'
      ? 'sql'
      : null;
  if (!resultType) return null;
  if (typeof data.executed !== 'boolean' || typeof data.dbTouched !== 'boolean') return null;
  if (resultType === 'rag') {
    if (decision !== 'ALLOW' || data.executed || data.dbTouched) return null;
  } else {
    if (decision === 'ALLOW' && (!data.executed || !data.dbTouched)) return null;
    if (decision !== 'ALLOW' && (data.executed || data.dbTouched)) return null;
  }
  const route = Array.isArray(data.route) && data.route.every(isRouteNode) ? (data.route as RouteNodeId[]) : null;
  if (!route || JSON.stringify(route) !== JSON.stringify(expectedRoute(decision, data.executed, mode, resultType))) return null;
  const detectedAt = data.detectedAt === undefined || data.detectedAt === null ? undefined : typeof data.detectedAt === 'string' && REQUIRED_MODULE_SET.has(data.detectedAt) ? data.detectedAt : null;
  const enforcedAt = data.enforcedAt === undefined || data.enforcedAt === null ? undefined : data.enforcedAt === 'trustedsql' ? 'trustedsql' : null;
  if (detectedAt === null || enforcedAt === null) return null;
  if (decision === 'DENY' && (resultType !== 'sql' || !detectedAt || enforcedAt !== 'trustedsql')) return null;
  if (decision === 'ALLOW' && (detectedAt !== undefined || enforcedAt !== undefined)) return null;
  if (mode === 'direct' && (decision === 'DENY' || enforcedAt !== undefined)) return null;
  const table = validRows(data.columns, data.rows);
  if (!table) return null;
  const sqlValue = data.sql ?? data.finalSql ?? data.rawSql;
  const sql = sqlValue === undefined || sqlValue === null ? undefined : boundedText(sqlValue, MAX_SQL);
  if (sqlValue !== undefined && sqlValue !== null && sql === undefined) return null;
  const latencyMs = data.latencyMs === undefined || data.latencyMs === null ? undefined : isFiniteNumber(data.latencyMs) && data.latencyMs >= 0 ? data.latencyMs : null;
  if (latencyMs === null) return null;
  const error = data.error === undefined || data.error === null ? undefined : safeFinalError(decision, data.error);
  if (data.error !== undefined && data.error !== null && error === undefined) return null;
  const answer = data.answer === undefined || data.answer === null ? undefined : boundedText(data.answer, 12_000);
  if (data.answer !== undefined && data.answer !== null && answer === undefined) return null;
  let sources: RagSourceDto[] | undefined;
  if (data.sources !== undefined && data.sources !== null) {
    if (!Array.isArray(data.sources) || data.sources.length === 0 || data.sources.length > 8) return null;
    const parsed: RagSourceDto[] = [];
    for (const raw of data.sources) {
      if (!isRecord(raw) || !isPositiveInteger(raw.citation) || raw.citation !== parsed.length + 1) return null;
      const title = boundedRequiredText(raw.title, 240);
      const uri = raw.uri === undefined || raw.uri === null ? undefined : boundedText(raw.uri, 2_000);
      const documentName = raw.documentName === undefined || raw.documentName === null ? undefined : boundedText(raw.documentName, 1_000);
      const snippet = raw.snippet === undefined || raw.snippet === null ? undefined : boundedText(raw.snippet, 360);
      if (!title || (raw.uri != null && !uri) || (raw.documentName != null && !documentName) || (raw.snippet != null && !snippet)) return null;
      parsed.push({ citation: raw.citation, title, uri, documentName, snippet });
    }
    sources = parsed;
  }
  if (resultType === 'rag' && (!answer || !sources?.length || sql || table.columns || table.rows)) return null;
  if (resultType === 'sql' && (answer !== undefined || sources !== undefined)) return null;
  return {
    runId,
    sampleId,
    turnNumber,
    decision,
    detectedAt,
    enforcedAt,
    executed: data.executed,
    dbTouched: data.dbTouched,
    columns: table.columns,
    rows: table.rows,
    sql,
    latencyMs,
    error,
    route,
    mode,
    resultType,
    answer,
    sources,
  };
}

export function validateRunStateDto(data: unknown): RunStateDto | null {
  const job = validateRunJobDto(data);
  if (!job || !isRecord(data)) return null;
  if (!Array.isArray(data.events)) return null;
  const events: Array<ModuleEventDto | TraceEventDto | RetractEventDto> = [];
  for (const rawEvent of data.events) {
    const eventType = isRecord(rawEvent) ? rawEvent.eventType : undefined;
    const event = eventType === 'trace' ? validateTraceEventDto(rawEvent) : eventType === 'revision' ? validateRevisionEventDto(rawEvent) : eventType === 'retract' ? validateRetractEventDto(rawEvent) : eventType === 'module' || eventType === undefined ? validateModuleEventDto(rawEvent) : null;
    if (!event) return null;
    events.push(event);
  }
  const parsedFinalResult: FinalResultDto | undefined = data.finalResult === null || data.finalResult === undefined ? undefined : validateFinalResultDto(data.finalResult, { runId: job.runId, sampleId: job.sampleId, throughTurn: job.throughTurn, mode: job.mode }) || undefined;
  if (data.finalResult !== null && data.finalResult !== undefined && !parsedFinalResult) return null;
  const status = validateRunStatusEvent({ runId: job.runId, state: job.state, finalResult: parsedFinalResult, error: data.error }, { runId: job.runId, sampleId: job.sampleId, throughTurn: job.throughTurn, mode: job.mode });
  if (!status) return null;
  return { ...job, events, finalResult: status.finalResult, error: status.error };
}

export function validateRunStatusEvent(data: unknown, expected?: RunCorrelationContext): RunStatusEventDto | null {
  if (!isRecord(data) || !boundedText(data.runId) || !isRunState(data.state)) return null;
  const outerRunId = data.runId as string;
  if (expected && outerRunId !== expected.runId) return null;
  const parsedFinalResult: FinalResultDto | undefined = data.finalResult === null || data.finalResult === undefined ? undefined : validateFinalResultDto(data.finalResult, { runId: outerRunId, sampleId: expected?.sampleId, throughTurn: expected?.throughTurn, mode: expected?.mode }) || undefined;
  if (data.finalResult !== null && data.finalResult !== undefined && !parsedFinalResult) return null;
  const error = data.error === undefined || data.error === null ? undefined : data.state === 'error' ? safeFinalError('ERROR', data.error) : safeRuntimeError(data.error);
  if (data.error !== undefined && data.error !== null && error === undefined) return null;
  if (data.state === 'complete' && parsedFinalResult?.decision !== 'ALLOW') return null;
  if (data.state === 'denied' && parsedFinalResult?.decision !== 'DENY') return null;
  if ((data.state === 'queued' || data.state === 'running') && parsedFinalResult) return null;
  if ((data.state === 'error' || data.state === 'cancelled') && parsedFinalResult?.decision === 'ALLOW') return null;
  return { runId: outerRunId, state: data.state, finalResult: parsedFinalResult, error };
}

export function mapCategoryBadge(_key: string): string {
  return 'Multiturn_Malicious_records.json';
}

export function mapBootstrapToScenarios(catalog: BootstrapCatalogItem[]): ScenarioMetadata[] {
  return catalog.map((item) => ({
    key: item.key as ScenarioMetadata['key'],
    canonicalId: item.canonical_id,
    title: item.title,
    categoryBadge: mapCategoryBadge(item.key),
    turnCount: item.turn_count,
    turnType: item.turn_type,
    role: item.role,
    userId: item.user_id,
    description: item.description,
    turns: item.turns.map((turn) => ({
      turnNumber: turn.turn_number,
      label: `Turn ${turn.turn_number}`,
      classification: turn.classification,
      description: turn.description,
      nlq: turn.nlq,
    })),
  }));
}

function validatePromptSearchResponse(data: unknown): PromptScenarioSearchItem[] | null {
  if (!isRecord(data) || !Array.isArray(data.matches) || data.matches.length > 20) return null;
  const matches = data.matches.map((raw): PromptScenarioSearchItem | null => {
    if (!isRecord(raw)) return null;
    const id = boundedRequiredText(raw.id, 120);
    const sourceFile = boundedRequiredText(raw.source_file, 240);
    const role = boundedRequiredText(raw.role, 40);
    if (!id || !DATASET_SCENARIO_ID.test(id) || !sourceFile || !role || !isPositiveInteger(raw.user_id) || !isPositiveInteger(raw.turn_count) || raw.turn_count > MAX_CHAT_TURNS) return null;
    return { id, sourceFile, role, userId: raw.user_id, turnCount: raw.turn_count };
  });
  return matches.some((item) => item === null) ? null : matches as PromptScenarioSearchItem[];
}

function validateDatasetScenario(data: unknown): ScenarioMetadata | null {
  if (!isRecord(data)) return null;
  const canonicalId = boundedRequiredText(data.canonical_id, 120);
  const key = boundedRequiredText(data.key, 160);
  const title = boundedRequiredText(data.title, 240);
  const description = boundedRequiredText(data.description, MAX_TEXT);
  const sourceFile = boundedRequiredText(data.source_file, 240);
  const role = boundedRequiredText(data.role, 40);
  const turnType = data.turn_type === 'single' || data.turn_type === 'multi' ? data.turn_type : null;
  if (!canonicalId || !DATASET_SCENARIO_ID.test(canonicalId) || key !== `dataset-${canonicalId.toLowerCase()}` || !title || !description || !sourceFile || !role || !isPositiveInteger(data.user_id) || !turnType || !isPositiveInteger(data.turn_count) || data.turn_count > MAX_CHAT_TURNS || !Array.isArray(data.turns) || data.turns.length !== data.turn_count) return null;
  const turns = data.turns.map((raw, index) => {
    if (!isRecord(raw) || raw.turn_id !== index + 1) return null;
    const nlq = boundedRequiredText(raw.nlq, MAX_CHAT_QUERY);
    const classification = raw.turn_label === 'BENIGN' || raw.turn_label === 'MALICIOUS'
      ? raw.turn_label
      : null;
    return nlq && classification
      ? { turnNumber: index + 1, label: `Turn ${index + 1}`, classification, description: 'Dataset user query', nlq }
      : null;
  });
  if (turns.some((turn) => turn === null)) return null;
  return {
    key,
    canonicalId,
    title,
    categoryBadge: sourceFile,
    turnCount: data.turn_count,
    turnType,
    role,
    userId: data.user_id,
    description,
    turns: turns as ScenarioMetadata['turns'],
  };
}

export interface ApiClientCallbacks {
  onEvent: (event:
    | { eventType: 'module' | 'revision'; data: ModuleEventDto }
    | { eventType: 'trace'; data: TraceEventDto }
    | { eventType: 'retract'; data: RetractEventDto }
  ) => void;
  onComplete: (result: FinalResultDto) => void;
  onError: (error: string) => void;
  onTelemetryUnavailable?: (status: { runId: string; executionState: TelemetryExecutionState; message: string }) => void;
  onStatus?: (status: RunStatusEventDto) => void;
  onOpen?: () => void;
  onClose?: (reason: 'terminal' | 'manual' | 'error' | 'unavailable') => void;
}

export interface ApiClient {
  fetchBootstrap: () => Promise<{ ready: boolean; scenarios: ScenarioMetadata[]; tools: ToolReadiness[] }>;
  searchPromptScenarios: (query: string, role?: ScenarioRoleFilter, signal?: AbortSignal) => Promise<PromptScenarioSearchItem[]>;
  fetchPromptScenario: (scenarioId: string, signal?: AbortSignal) => Promise<ScenarioMetadata>;
  createRun: (message: string, conversationId: string | null, signal?: AbortSignal, mode?: ExecutionMode) => Promise<RunJobDto>;
  getRun: (runId: string) => Promise<RunStateDto>;
  cancelRun: (runId: string) => Promise<{ success: boolean }>;
  subscribeRunEvents: (runId: string, lastSequence: number, callbacks: ApiClientCallbacks, expected?: RunCorrelationContext) => () => void;
}

async function jsonResponse(response: Response, operation: string): Promise<unknown> {
  if (!response.ok) throw safeError(`${operation} failed (${response.status})`);
  try {
    return await response.json();
  } catch {
    throw safeError(`${operation} returned invalid JSON`);
  }
}

export function createApiClient(baseUrl = '/api'): ApiClient {
  const fetchRunState = async (runId: string): Promise<RunStateDto> => {
    const response = await fetch(`${baseUrl}/runs/${encodeURIComponent(runId)}`, { headers: { Accept: 'application/json' } });
    const state = validateRunStateDto(await jsonResponse(response, 'Get run'));
    if (!state || state.runId !== runId) throw safeError('Invalid run state response');
    return state;
  };

  return {
    async fetchBootstrap() {
      const response = await fetch(`${baseUrl}/bootstrap`, { headers: { Accept: 'application/json' } });
      const validated = validateBootstrapResponse(await jsonResponse(response, 'Bootstrap request'));
      if (!validated) throw safeError('Invalid or non-conforming architecture/bootstrap response');
      const tools: ToolReadiness[] = [
        { id: 'context_memory', name: 'Conversation Memory', ready: validated.ready },
        { id: 'rag_context', name: 'Vertex AI RAG Engine', ready: validated.ragReady },
        { id: 'policy_engine', name: 'Policy Engine', ready: validated.ready },
        { id: 'trustedsql', name: 'TrustedSQL Engine', ready: validated.ready },
        { id: 'education_db', name: 'Education DB', ready: validated.ready },
      ];
      return { ready: validated.ready, scenarios: mapBootstrapToScenarios(validated.catalog), tools };
    },

    async searchPromptScenarios(query: string, role: ScenarioRoleFilter = 'all', signal?: AbortSignal) {
      const normalized = query.trim();
      if (normalized.length > 120) throw safeError('Prompt search query is invalid');
      if (!['all', 'student', 'lecturer'].includes(role)) throw safeError('Prompt search role is invalid');
      const response = await fetch(`${baseUrl}/prompt-library/search?q=${encodeURIComponent(normalized)}&role=${encodeURIComponent(role)}&limit=12`, {
        headers: { Accept: 'application/json' },
        signal,
      });
      const matches = validatePromptSearchResponse(await jsonResponse(response, 'Prompt search'));
      if (!matches) throw safeError('Invalid prompt search response');
      return matches;
    },

    async fetchPromptScenario(scenarioId: string, signal?: AbortSignal) {
      if (!DATASET_SCENARIO_ID.test(scenarioId)) throw safeError('Dataset scenario identity is invalid');
      const response = await fetch(`${baseUrl}/prompt-library/scenarios/${encodeURIComponent(scenarioId)}`, {
        headers: { Accept: 'application/json' },
        signal,
      });
      const scenario = validateDatasetScenario(await jsonResponse(response, 'Load prompt scenario'));
      if (!scenario) throw safeError('Invalid dataset scenario response');
      return scenario;
    },

    async createRun(message: string, conversationId: string | null, signal?: AbortSignal, mode: ExecutionMode = 'trustedsql') {
      if (typeof message !== 'string' || !message.trim() || message.length > MAX_CHAT_QUERY) {
        throw safeError('Chat message is empty or exceeds the bounded limit');
      }
      if (conversationId !== null && !CONVERSATION_ID.test(conversationId)) {
        throw safeError('Conversation identity is invalid');
      }
      if (mode !== 'trustedsql' && mode !== 'direct') throw safeError('Execution mode is invalid');
      const response = await fetch(`${baseUrl}/runs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify({ message: message.trim(), conversationId, mode }),
        signal,
      });
      if (response.status !== 202) throw safeError(`Create run failed (${response.status})`);
      const job = validateRunJobDto(await jsonResponse(response, 'Create run'));
      if (!job) throw safeError('Invalid run job response');
      return job;
    },

    async getRun(runId: string) {
      return fetchRunState(runId);
    },

    async cancelRun(runId: string) {
      const response = await fetch(`${baseUrl}/runs/${encodeURIComponent(runId)}/cancel`, { method: 'POST', headers: { Accept: 'application/json' } });
      const body = await jsonResponse(response, 'Cancel run');
      if (!isRecord(body) || (body.success !== true && validateRunJobDto(body)?.state !== 'cancelled')) throw safeError('Invalid cancel response');
      return { success: true };
    },

    subscribeRunEvents(runId: string, lastSequence: number, callbacks: ApiClientCallbacks, expected?: RunCorrelationContext) {
      let closed = false;
      let source: EventSource | null = null;
      let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
      let sequence = isNonNegativeInteger(lastSequence) ? lastSequence : 0;
      let retries = 0;
      const seenRevisions = new Map<string, number>();
      const runContext: RunCorrelationContext = { runId, ...expected };

      const closeSource = () => {
        if (source) {
          source.close();
          source = null;
        }
      };
      const finish = (reason: 'terminal' | 'manual' | 'error' | 'unavailable' = 'manual') => {
        if (closed) return;
        closed = true;
        closeSource();
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = null;
        callbacks.onClose?.(reason);
      };
      const reportTelemetryUnavailable = () => {
        if (closed) return;
        callbacks.onTelemetryUnavailable?.({
          runId,
          executionState: 'unknown',
          message: 'Execution state unknown; telemetry unavailable',
        });
        finish('unavailable');
      };
      const dispatchEvent = (eventType: 'module' | 'revision' | 'trace' | 'retract', raw: unknown): boolean => {
        const event = eventType === 'trace'
          ? validateTraceEventDto(raw)
          : eventType === 'retract'
          ? validateRetractEventDto(raw)
          : eventType === 'revision'
            ? validateRevisionEventDto(raw)
            : validateModuleEventDto(raw);
        if (!event || event.runId !== runId || event.streamSequence <= sequence) return false;
        sequence = event.streamSequence;
        retries = 0;
        if (eventType === 'module' && 'revision' in event && event.revision !== undefined) {
          const identity = `${event.runId}:${event.sampleId ?? 'default'}:${event.turnNumber ?? 1}:${event.module}`;
          seenRevisions.set(identity, event.revision);
        }
        if (eventType === 'revision') {
          const revisionEvent = event as ModuleEventDto;
          const identity = `${event.runId}:${event.sampleId ?? 'default'}:${event.turnNumber ?? 1}:${event.module}`;
          const revision = revisionEvent.revision ?? 0;
          if (revision <= (seenRevisions.get(identity) ?? -1)) return true;
          seenRevisions.set(identity, revision);
        }
        if (eventType === 'trace') callbacks.onEvent({ eventType, data: event as TraceEventDto });
        else if (eventType === 'retract') callbacks.onEvent({ eventType, data: event as RetractEventDto });
        else callbacks.onEvent({ eventType, data: event as ModuleEventDto });
        return true;
      };
      const handleStatus = (raw: unknown): boolean => {
        const status = validateRunStatusEvent(raw, runContext);
        if (!status || status.runId !== runId) {
          reportTelemetryUnavailable();
          return false;
        }
        retries = 0;
        if (callbacks.onStatus) callbacks.onStatus(status);
        else if (status.finalResult && (status.state === 'complete' || status.state === 'denied')) callbacks.onComplete(status.finalResult);
        else if (status.state === 'error') callbacks.onError(status.error ?? 'Runtime execution error');
        if (['complete', 'denied', 'error', 'cancelled'].includes(status.state)) finish('terminal');
        return true;
      };
      const reconcileAfterTransportFailure = async () => {
        let state: RunStateDto;
        try {
          state = await fetchRunState(runId);
        } catch {
          if (closed) return;
          callbacks.onTelemetryUnavailable?.({
            runId,
            executionState: 'unknown',
            message: 'Execution state unknown; telemetry unavailable',
          });
          finish('unavailable');
          return;
        }
        if (closed) return;
        const status = validateRunStatusEvent({
          runId: state.runId,
          state: state.state,
          finalResult: state.finalResult,
          error: state.error,
        }, runContext);
        if (status && ['complete', 'denied', 'error', 'cancelled'].includes(status.state)) {
          handleStatus(status);
          return;
        }
        callbacks.onTelemetryUnavailable?.({
          runId,
          executionState: state.state === 'queued' || state.state === 'running' ? state.state : 'unknown',
          message: `Execution state ${state.state}; telemetry unavailable`,
        });
        finish('unavailable');
      };
      const parse = (event: Event) => {
        const message = event as MessageEvent;
        try {
          const raw = JSON.parse(message.data);
          const type = message.type;
          if (type === 'module' || type === 'revision' || type === 'trace' || type === 'retract') dispatchEvent(type, raw);
          else if (type === 'status') handleStatus(raw);
          else if (type === 'complete') {
            const result = validateFinalResultDto(raw, runContext);
            if (result && result.decision === 'ALLOW') {
              retries = 0;
              callbacks.onComplete(result);
              finish('terminal');
            } else {
              reportTelemetryUnavailable();
            }
          }
        } catch {
          if (message.type === 'status' || message.type === 'complete') reportTelemetryUnavailable();
          // Malformed or unknown server records never enter UI state.
        }
      };
      const connect = () => {
        if (closed) return;
        closeSource();
        source = new EventSource(`${baseUrl}/runs/${encodeURIComponent(runId)}/events?after=${sequence}`);
        source.onopen = () => {
          retries = 0;
          callbacks.onOpen?.();
        };
        (['module', 'revision', 'trace', 'retract', 'status', 'complete'] as const).forEach((type) => source?.addEventListener(type, parse));
        source.onerror = () => {
          if (closed) return;
          closeSource();
          if (retries >= 3) {
            void reconcileAfterTransportFailure();
            return;
          }
          retries += 1;
          reconnectTimer = setTimeout(connect, 250 * retries);
        };
      };
      connect();
      return finish;
    },
  };
}
