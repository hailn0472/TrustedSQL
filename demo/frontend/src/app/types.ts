export type ReadinessState = 'neutral' | 'loading' | 'ready' | 'not-ready';
export type ExecutionMode = 'trustedsql' | 'direct';

export type ScenarioCategory = string;

export interface ToolReadiness {
  id: string;
  name: string;
  ready: boolean;
}

export interface ScenarioTurn {
  turnNumber: number;
  optionId?: string;
  replacesTurn?: number;
  label: string;
  classification: 'BENIGN' | 'MALICIOUS';
  description: string;
  nlq?: string;
}

export type ScenarioRoleFilter = 'all' | 'student' | 'lecturer';

export interface ScenarioMetadata {
  key: ScenarioCategory;
  canonicalId: string;
  title: string;
  categoryBadge: string;
  turnCount: number;
  turnType?: 'single' | 'multi';
  role?: string;
  userId?: number;
  description: string;
  turns: ScenarioTurn[];
}

export interface PromptScenarioSearchItem {
  id: string;
  sourceFile: string;
  role: string;
  userId: number;
  turnCount: number;
}

export interface SessionIdentity {
  role: string;
  userId: number;
  username: string;
}

// ST-08 Route Evidence Types
export type RouteNodeId =
  | 'chat'
  | 'orchestrator'
  | 'context_memory'
  | 'rag'
  | 'policy_engine'
  | 'trustedsql'
  | 'sql_generator'
  | 'education_db';

export type RouteDecision = 'ALLOW' | 'DENY' | 'ERROR';

export type RouteNodeState = 'idle' | 'active' | 'allow' | 'deny' | 'error' | 'untouched';

export interface RouteEvidence {
  version: number | string;
  mode?: ExecutionMode;
  resultType?: 'sql' | 'rag' | 'chat';
  decision: RouteDecision;
  executed: boolean;
  dbTouched: boolean;
  route: RouteNodeId[];
  detectedAt?: string; // M1, M2, M4, M5, M7, etc.
  enforcedAt?: 'trustedsql';
}

export interface GnnGraphNode {
  id: string;
  type:
    | 'Role'
    | 'UserTurn'
    | 'EntityMention'
    | 'SemanticConceptCandidate'
    | 'ScopeCandidate'
    | 'TargetCandidate'
    | 'ReferenceExpression'
    | 'PreviousSemanticState';
  label: string;
  turnNumber?: number;
  current?: boolean;
  confidence?: number;
}

export interface GnnGraphEdge {
  source: string;
  target: string;
  type: string;
  confidence?: number;
  distance?: number;
}

export interface GnnGraphSnapshot {
  graphId: string;
  currentTurn?: number;
  nodeCount: number;
  edgeCount: number;
  nodes: GnnGraphNode[];
  edges: GnnGraphEdge[];
  outputs: Partial<Record<'intent' | 'scope' | 'target' | 'securityTransition', string>>;
  decision: string;
}

export interface NodeStateMap {
  chat: RouteNodeState;
  orchestrator: RouteNodeState;
  context_memory: RouteNodeState;
  rag: RouteNodeState;
  policy_engine: RouteNodeState;
  trustedsql: RouteNodeState;
  sql_generator: RouteNodeState;
  education_db: RouteNodeState;
}

export interface PathStateMap {
  'path-chat-orchestrator': RouteNodeState;
  'path-orchestrator-memory': RouteNodeState;
  'path-orchestrator-rag': RouteNodeState;
  'path-orchestrator-policy': RouteNodeState;
  'path-policy-trustedsql': RouteNodeState;
  'path-trustedsql-db': RouteNodeState;
}
