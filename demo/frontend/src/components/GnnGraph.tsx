import React, { useEffect, useMemo, useState } from 'react';
import { GnnGraphNode, GnnGraphSnapshot } from '../app/types';

interface GnnGraphProps {
  graph?: GnnGraphSnapshot;
}

interface PositionedNode extends GnnGraphNode {
  x: number;
  y: number;
}

const COLUMN_BY_TYPE: Record<GnnGraphNode['type'], number> = {
  Role: 0,
  UserTurn: 1,
  PreviousSemanticState: 1,
  EntityMention: 2,
  ReferenceExpression: 2,
  SemanticConceptCandidate: 3,
  ScopeCandidate: 3,
  TargetCandidate: 3,
};

const TYPE_LABELS: Record<GnnGraphNode['type'], string> = {
  Role: 'ROLE',
  UserTurn: 'TURN',
  PreviousSemanticState: 'STATE',
  EntityMention: 'MENTION',
  ReferenceExpression: 'REFERENCE',
  SemanticConceptCandidate: 'CONCEPT',
  ScopeCandidate: 'SCOPE',
  TargetCandidate: 'TARGET',
};

function compactLabel(label: string): string {
  return label.length <= 18 ? label : `${label.slice(0, 17)}…`;
}

function layoutNodes(graph: GnnGraphSnapshot): { nodes: PositionedNode[]; height: number } {
  const columns = [0, 1, 2, 3].map((column) => graph.nodes
    .filter((node) => COLUMN_BY_TYPE[node.type] === column)
    .sort((left, right) => (left.turnNumber ?? 99) - (right.turnNumber ?? 99) || left.id.localeCompare(right.id)));
  const height = Math.max(292, Math.max(...columns.map((nodes) => nodes.length), 1) * 43 + 42);
  const x = [16, 142, 268, 394];
  const nodes = columns.flatMap((column, columnIndex) => {
    const contentHeight = Math.max(0, (column.length - 1) * 43);
    const startY = Math.max(22, (height - contentHeight) / 2 - 15);
    return column.map((node, rowIndex) => ({ ...node, x: x[columnIndex], y: startY + rowIndex * 43 }));
  });
  return { nodes, height };
}

export const GnnGraph: React.FC<GnnGraphProps> = ({ graph }) => {
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  useEffect(() => setSelectedNodeId(undefined), [graph?.graphId]);
  const layout = useMemo(() => graph ? layoutNodes(graph) : undefined, [graph]);

  if (!graph || !layout) {
    return (
      <div className="gnn-empty" data-testid="gnn-empty">
        <strong>No M2 graph for this turn</strong>
        <span>The M2 Intent GNN only runs on the TrustedSQL data route.</span>
      </div>
    );
  }

  const positioned = new Map(layout.nodes.map((node) => [node.id, node]));
  const selected = selectedNodeId ? positioned.get(selectedNodeId) : undefined;

  return (
    <div className="gnn-graph-content" data-testid="gnn-graph">
      <svg
        viewBox={`0 0 520 ${layout.height}`}
        className="routing-svg gnn-svg"
        role="img"
        aria-label={`Interactive M2 intent graph for turn ${graph.currentTurn ?? '?'}`}
      >
        <title>M2 runtime intent graph</title>
        <desc>Actual role, conversation turn, mention, concept, scope, target, and reference nodes encoded by M2 for the selected turn. Canonical relations are drawn once; reverse encoder relations are omitted visually.</desc>
        <defs>
          <marker id="gnn-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
            <path d="M0,0 L7,3.5 L0,7 Z" className="gnn-arrow" />
          </marker>
        </defs>
        {graph.edges.map((edge, index) => {
          const source = positioned.get(edge.source);
          const target = positioned.get(edge.target);
          if (!source || !target) return null;
          return (
            <g key={`${edge.source}-${edge.target}-${edge.type}-${index}`} className="gnn-edge">
              <line
                x1={source.x + 104}
                y1={source.y + 15}
                x2={target.x}
                y2={target.y + 15}
                markerEnd="url(#gnn-arrow)"
              />
              <title>{edge.type}{edge.distance !== undefined ? ` · distance ${edge.distance}` : ''}</title>
            </g>
          );
        })}
        {layout.nodes.map((node) => (
          <g
            key={node.id}
            className={`gnn-node type-${node.type} ${node.current ? 'current' : ''} ${selectedNodeId === node.id ? 'selected' : ''}`}
            data-testid={`gnn-node-${node.id}`}
            role="button"
            tabIndex={0}
            aria-label={`${TYPE_LABELS[node.type]} ${node.label}`}
            onPointerDown={(event) => event.stopPropagation()}
            onClick={() => setSelectedNodeId(node.id)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                setSelectedNodeId(node.id);
              }
            }}
          >
            <rect x={node.x} y={node.y} width="104" height="30" rx="7" />
            <text x={node.x + 7} y={node.y + 10} className="gnn-node-type">{TYPE_LABELS[node.type]}</text>
            <text x={node.x + 52} y={node.y + 23} textAnchor="middle" className="gnn-node-label">{compactLabel(node.label)}</text>
            <title>{node.type}: {node.label}{node.confidence !== undefined ? ` · confidence ${node.confidence}` : ''}</title>
          </g>
        ))}
      </svg>
      {selected && (
        <div className="gnn-node-inspector" role="status">
          <strong>{selected.type}</strong>
          <span>{selected.label}</span>
          {selected.turnNumber && <span>Turn {selected.turnNumber}</span>}
          {selected.confidence !== undefined && <span>confidence {selected.confidence}</span>}
        </div>
      )}
    </div>
  );
};
