import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { RouteEvidence } from '../app/types';
import { TelemetryItem } from '../state/demoReducer';
import { getFullTrajectory, RoutingMap, validateRouteEvidence } from './RoutingMap';

const sqlAllow: RouteEvidence = {
  version: 1,
  mode: 'trustedsql',
  resultType: 'sql',
  decision: 'ALLOW',
  executed: true,
  dbTouched: true,
  route: ['chat', 'orchestrator', 'context_memory', 'policy_engine', 'trustedsql', 'education_db'],
};

describe('RoutingMap', () => {
  it('shows the complete idle architecture and terminal response nodes', () => {
    render(<RoutingMap />);

    expect(screen.getByTestId('routing-map-status')).toHaveTextContent('Awaiting query');
    expect(screen.getByTestId('node-rag_query')).toBeInTheDocument();
    expect(screen.getByTestId('node-rag_retrieval')).toBeInTheDocument();
    expect(screen.getByTestId('node-rag_grounding')).toBeInTheDocument();
    expect(screen.getByTestId('node-m1')).toBeInTheDocument();
    expect(screen.getByTestId('node-m7')).toBeInTheDocument();
    expect(screen.getByTestId('node-x1')).toBeInTheDocument();
    expect(screen.getByTestId('node-response_composer')).toBeInTheDocument();
    expect(screen.getByTestId('node-chat_response')).toBeInTheDocument();
  });

  it('projects a document result across the three RAG stages and into chat response', () => {
    const ragEvidence: RouteEvidence = {
      version: 1,
      mode: 'trustedsql',
      resultType: 'rag',
      decision: 'ALLOW',
      executed: false,
      dbTouched: false,
      route: ['chat', 'orchestrator', 'context_memory', 'rag'],
    };

    render(<RoutingMap evidence={ragEvidence} />);

    expect(screen.getByTestId('node-rag_query')).toHaveClass('allow');
    expect(screen.getByTestId('node-rag_retrieval')).toHaveClass('allow');
    expect(screen.getByTestId('node-rag_grounding')).toHaveClass('allow');
    expect(screen.getByTestId('node-m1')).toHaveClass('untouched');
    expect(screen.getByTestId('node-chat_response')).toHaveClass('allow');
  });

  it('projects a successful TrustedSQL result through M1-M7, X1, DB, and response composition', () => {
    render(<RoutingMap evidence={sqlAllow} />);

    for (const module of ['m1', 'm2', 'm3', 'm4', 'm5', 'm7', 'x1']) {
      expect(screen.getByTestId(`node-${module}`)).toHaveClass('allow');
    }
    expect(screen.getByTestId('node-sql_generator')).toHaveClass('allow');
    expect(screen.getByTestId('node-education_db')).toHaveClass('allow');
    expect(screen.getByTestId('node-response_composer')).toHaveClass('allow');
    expect(screen.getByTestId('node-chat_response')).toHaveClass('allow');
  });

  it('stops at the real detector, leaves downstream modules untouched, and returns a refusal', () => {
    const denyEvidence: RouteEvidence = {
      version: 1,
      mode: 'trustedsql',
      resultType: 'sql',
      decision: 'DENY',
      executed: false,
      dbTouched: false,
      detectedAt: 'M2',
      enforcedAt: 'trustedsql',
      route: ['chat', 'orchestrator', 'context_memory', 'policy_engine', 'trustedsql'],
    };

    render(<RoutingMap evidence={denyEvidence} />);

    expect(screen.getByTestId('node-m1')).toHaveClass('allow');
    expect(screen.getByTestId('node-m2')).toHaveClass('deny');
    expect(screen.getByTestId('node-m3')).toHaveClass('untouched');
    expect(screen.getByTestId('node-education_db')).toHaveClass('untouched');
    expect(screen.getByTestId('node-chat_response')).toHaveClass('deny');
    expect(screen.getByTestId('detector-badge')).toHaveTextContent('Detector: M2');
  });

  it('uses the compact generator-only lane in direct mode', () => {
    const directEvidence: RouteEvidence = {
      version: 1,
      mode: 'direct',
      resultType: 'sql',
      decision: 'ALLOW',
      executed: true,
      dbTouched: true,
      route: ['chat', 'orchestrator', 'context_memory', 'sql_generator', 'education_db'],
    };

    render(<RoutingMap mode="direct" evidence={directEvidence} />);

    expect(screen.queryByTestId('node-m1')).not.toBeInTheDocument();
    expect(screen.getByTestId('node-sql_generator')).toHaveClass('allow');
    expect(screen.getByText('SECURITY MODULES OFF')).toBeInTheDocument();
    expect(screen.getByTestId('node-chat_response')).toHaveClass('allow');
  });

  it('uses live stage telemetry to animate the exact RAG child module', () => {
    const retrieval: TelemetryItem = {
      id: 'rag-retrieval',
      eventType: 'module',
      timestamp: '12:00:00',
      runId: 'run-1',
      streamSequence: 1,
      module: 'RAG',
      stage: 'vertex_rag_retrieval',
      decision: 'RUNNING',
    };

    render(<RoutingMap events={[retrieval]} runState="running" />);

    expect(screen.getByTestId('node-rag_query')).toHaveClass('allow');
    expect(screen.getByTestId('node-rag_retrieval')).toHaveClass('active');
    expect(screen.getByTestId('node-rag_grounding')).toHaveClass('idle');
    expect(screen.getByTestId('route-particle')).toBeInTheDocument();
  });

  it('keeps the backend route contract separate from visual child modules', () => {
    expect(validateRouteEvidence(sqlAllow)).toEqual({ valid: true });
    expect(getFullTrajectory(sqlAllow)).toEqual(sqlAllow.route);
  });
});
