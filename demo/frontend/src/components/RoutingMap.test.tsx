import { render, screen, act } from '@testing-library/react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { RoutingMap, getFullTrajectory } from './RoutingMap';
import { RouteEvidence } from '../app/types';

describe('RoutingMap Component (Task 8)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('idle hides particle and shows no-evidence status', () => {
    render(<RoutingMap evidence={undefined} />);

    expect(screen.getByTestId('routing-map-status')).toHaveTextContent(/no runtime route evidence yet/i);
    expect(screen.queryByTestId('route-particle')).not.toBeInTheDocument();
  });

  it('contains stable node/path IDs and no baseline/bypass content', () => {
    const { container } = render(<RoutingMap evidence={undefined} />);

    expect(screen.getByTestId('node-chat')).toBeInTheDocument();
    expect(screen.getByTestId('node-orchestrator')).toBeInTheDocument();
    expect(screen.getByTestId('node-context_memory')).toBeInTheDocument();
    expect(screen.getByTestId('node-rag')).toBeInTheDocument();
    expect(screen.getByTestId('node-policy_engine')).toBeInTheDocument();
    expect(screen.getByTestId('node-trustedsql')).toBeInTheDocument();
    expect(screen.getByTestId('node-education_db')).toBeInTheDocument();

    expect(screen.getByTestId('path-chat-orchestrator')).toBeInTheDocument();
    expect(screen.getByTestId('path-orchestrator-memory')).toBeInTheDocument();
    expect(screen.getByTestId('path-orchestrator-rag')).toBeInTheDocument();
    expect(screen.getByTestId('path-orchestrator-policy')).toBeInTheDocument();
    expect(screen.getByTestId('path-policy-trustedsql')).toBeInTheDocument();
    expect(screen.getByTestId('path-trustedsql-db')).toBeInTheDocument();

    const text = container.textContent || '';
    expect(text).not.toMatch(/baseline/i);
    expect(text).not.toMatch(/bypass/i);
  });

  it('trajectory visits memory and RAG before policy and trustedsql', () => {
    const allowEvidence: RouteEvidence = {
      version: 1,
      decision: 'ALLOW',
      executed: true,
      dbTouched: true,
      route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'],
    };

    const trajectory = getFullTrajectory(allowEvidence);
    expect(trajectory).toEqual([
      'chat',
      'orchestrator',
      'context_memory',
      'orchestrator',
      'rag',
      'orchestrator',
      'policy_engine',
      'trustedsql',
      'education_db',
    ]);
  });

  it('truthful ALLOW turns policy/trustedsql/db green and reaches DB', () => {
    const allowEvidence: RouteEvidence = {
      version: 1,
      decision: 'ALLOW',
      executed: true,
      dbTouched: true,
      route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'],
    };

    render(<RoutingMap evidence={allowEvidence} />);

    // Fast-forward animation timers
    act(() => {
      vi.runAllTimers();
    });

    expect(screen.getByTestId('routing-map-status')).toHaveTextContent(/route completed: allow/i);
    expect(screen.getByTestId('node-policy_engine')).toHaveClass('allow');
    expect(screen.getByTestId('node-trustedsql')).toHaveClass('allow');
    expect(screen.getByTestId('node-education_db')).toHaveClass('allow');
    expect(screen.getByTestId('path-trustedsql-db')).toHaveClass('allow');
  });

  it('truthful DENY for representative M1 stops red at TrustedSQL, leaves DB untouched, and displays actual detector', () => {
    const denyEvidence: RouteEvidence = {
      version: 2,
      decision: 'DENY',
      executed: false,
      dbTouched: false,
      detectedAt: 'M1',
      enforcedAt: 'trustedsql',
      route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'],
    };

    render(<RoutingMap evidence={denyEvidence} />);

    act(() => {
      vi.runAllTimers();
    });

    expect(screen.getByTestId('routing-map-status')).toHaveTextContent(/route enforced: deny/i);
    expect(screen.getByTestId('detector-badge')).toHaveTextContent('Detector: M1');
    expect(screen.getByTestId('enforcer-badge')).toHaveTextContent('Enforcer: TrustedSQL');
    expect(screen.getByTestId('node-trustedsql')).toHaveClass('deny');
    expect(screen.getByTestId('node-education_db')).toHaveClass('untouched');
    expect(screen.getByTestId('db-status-label')).toHaveTextContent('No query dispatched');
  });

  // Additional detector cases: C0, M2, M3, M4, M5, M6, M7, X1
  it.each(['C0', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'X1'])('truthful DENY for detector %s validates and displays detector correctly', (detector) => {
    const denyEvidence: RouteEvidence = {
      version: `deny-${detector}`,
      decision: 'DENY',
      executed: false,
      dbTouched: false,
      detectedAt: detector,
      enforcedAt: 'trustedsql',
      route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'],
    };

    render(<RoutingMap evidence={denyEvidence} />);

    act(() => {
      vi.runAllTimers();
    });

    expect(screen.getByTestId('detector-badge')).toHaveTextContent(`Detector: ${detector}`);
    expect(screen.getByTestId('node-trustedsql')).toHaveClass('deny');
    expect(screen.getByTestId('node-education_db')).toHaveClass('untouched');
  });

  // Strict ALLOW contradiction tests (Round 2 review regressions)
  it.each([
    ['ALLOW with detectedAt set', { version: 1, decision: 'ALLOW', executed: true, dbTouched: true, detectedAt: 'M1', route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'] }],
    ['ALLOW with enforcedAt set', { version: 2, decision: 'ALLOW', executed: true, dbTouched: true, enforcedAt: 'trustedsql', route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'] }],
    ['ALLOW with truthy string executed', { version: 3, decision: 'ALLOW', executed: 'true' as any, dbTouched: true, route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'] }],
    ['ALLOW with truthy number dbTouched', { version: 4, decision: 'ALLOW', executed: true, dbTouched: 1 as any, route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'] }],
    ['fabricated Policy Check detector', { version: 5, decision: 'DENY', executed: false, dbTouched: false, detectedAt: 'Policy Check', enforcedAt: 'trustedsql', route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'] }],
  ])('rejects non-strict / contradictory ALLOW / DENY evidence: %s', (_, evidence) => {
    render(<RoutingMap evidence={evidence as any} />);

    act(() => {
      vi.runAllTimers();
    });

    expect(screen.getByTestId('routing-map-status')).toHaveTextContent(/evidence invalid or malformed/i);
    expect(screen.getByTestId('node-education_db')).toHaveClass('error');
    expect(screen.getByTestId('db-status-label')).not.toHaveTextContent('Dispatched & Executed');
  });

  // Metadata badge verification for ALLOW vs DENY (Round 3 review finding)
  it('does not render detector or enforcer badges for valid ALLOW evidence where fields are absent', () => {
    const allowEvidence: RouteEvidence = {
      version: 'allow-no-badge',
      decision: 'ALLOW',
      executed: true,
      dbTouched: true,
      route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'],
    };

    render(<RoutingMap evidence={allowEvidence} />);

    act(() => {
      vi.runAllTimers();
    });

    expect(screen.queryByTestId('detector-badge')).not.toBeInTheDocument();
    expect(screen.queryByTestId('enforcer-badge')).not.toBeInTheDocument();
    expect(screen.queryByTestId('detector-meta-bar')).not.toBeInTheDocument();
  });

  // Accessible per-node state labels in DOM (screen reader accessible via sr-only instead of display:none)
  it('renders accessible node state labels within screen-reader accessible sr-only element', () => {
    const allowEvidence: RouteEvidence = {
      version: 'accessible-sr-only-test',
      decision: 'ALLOW',
      executed: true,
      dbTouched: true,
      route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'],
    };

    render(<RoutingMap evidence={allowEvidence} />);

    act(() => {
      vi.runAllTimers();
    });

    const accessibleStateNode = screen.getByTestId('accessible-state-trustedsql');
    expect(accessibleStateNode).toBeInTheDocument();
    expect(accessibleStateNode).toHaveTextContent('TrustedSQL Engine — Allow');
    expect(accessibleStateNode.parentElement).toHaveClass('sr-only');
    expect(accessibleStateNode.parentElement).not.toHaveStyle('display: none');
  });

  // Invalid/malformed route evidence cases
  it.each([
    ['wrong route order', { version: 1, decision: 'ALLOW', executed: true, dbTouched: true, route: ['chat', 'orchestrator', 'policy_engine', 'trustedsql', 'education_db'] }],
    ['missing memory/rag', { version: 2, decision: 'ALLOW', executed: true, dbTouched: true, route: ['chat', 'policy_engine', 'education_db'] }],
    ['invalid detector', { version: 3, decision: 'DENY', executed: false, dbTouched: false, detectedAt: 'M99', enforcedAt: 'trustedsql', route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'] }],
    ['missing enforcedAt', { version: 4, decision: 'DENY', executed: false, dbTouched: false, detectedAt: 'M1', route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'] }],
    ['wrong enforcedAt', { version: 5, decision: 'DENY', executed: false, dbTouched: false, detectedAt: 'M1', enforcedAt: 'baseline' as any, route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'] }],
    ['invalid ALLOW metadata', { version: 6, decision: 'ALLOW', executed: true, dbTouched: false, route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'] }],
    ['explicit ERROR state', { version: 7, decision: 'ERROR', executed: false, dbTouched: false, route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql'] }],
  ])('fails closed for invalid/malformed evidence: %s', (_, evidence) => {
    render(<RoutingMap evidence={evidence as RouteEvidence} />);

    act(() => {
      vi.runAllTimers();
    });

    expect(screen.getByTestId('routing-map-status')).toHaveTextContent(/evidence invalid or malformed/i);
    expect(screen.getByTestId('node-education_db')).toHaveClass('error');
    expect(screen.getByTestId('db-status-label')).not.toHaveTextContent('Dispatched & Executed');
  });

  // Particle movement and per-node accessible state labels
  it('updates particle cx/cy coordinates and renders per-node accessible state badges during travel', () => {
    const allowEvidence: RouteEvidence = {
      version: 'particle-test',
      decision: 'ALLOW',
      executed: true,
      dbTouched: true,
      route: ['chat', 'orchestrator', 'context_memory', 'rag', 'policy_engine', 'trustedsql', 'education_db'],
    };

    render(<RoutingMap evidence={allowEvidence} />);

    // Step 0: chat
    const particle = screen.getByTestId('route-particle');
    expect(particle).toHaveAttribute('cx', '160');
    expect(particle).toHaveAttribute('cy', '23');

    // Advance 1 step: orchestrator
    act(() => {
      vi.advanceTimersByTime(150);
    });
    expect(particle).toHaveAttribute('cx', '160');
    expect(particle).toHaveAttribute('cy', '88');

    // Fast-forward to end
    act(() => {
      vi.runAllTimers();
    });
  });

  it('proves X1 live telemetry without final evidence never gives DB/path class allow and never claims route completed', () => {
    const evX1: any = { id: 'x1', eventType: 'module', streamSequence: 5, module: 'X1', decision: 'ALLOW' };
    render(<RoutingMap events={[evX1]} runState="running" evidence={undefined} />);

    // Education DB must be active, NOT allow
    expect(screen.getByTestId('node-education_db')).not.toHaveClass('allow');
    expect(screen.getByTestId('node-education_db')).toHaveClass('active');
    expect(screen.getByTestId('path-trustedsql-db')).not.toHaveClass('allow');
    expect(screen.getByTestId('path-trustedsql-db')).toHaveClass('active');

    // Status must NOT claim route completed
    expect(screen.getByTestId('routing-map-status')).not.toHaveTextContent(/route completed/i);
    expect(screen.getByTestId('routing-map-status')).toHaveTextContent(/awaiting final validation/i);
    expect(screen.getByTestId('db-status-label')).not.toHaveTextContent('Dispatched & Executed');
  });

  it('animates event-driven choreographic trajectory through Orchestrator between Memory and RAG', () => {
    const { rerender } = render(<RoutingMap events={[]} runState="queued" />);
    expect(screen.getByTestId('node-chat')).toHaveClass('active');

    // C0 arrives -> moves to context_memory (step 2)
    const evC0: any = { id: '1', eventType: 'module', streamSequence: 1, module: 'C0', decision: 'ALLOW' };
    rerender(<RoutingMap events={[evC0]} runState="running" />);

    act(() => {
      vi.runAllTimers();
    });
    expect(screen.getByTestId('node-context_memory')).toHaveClass('allow');
    expect(screen.getByTestId('route-particle')).toHaveAttribute('cx', '70');
    expect(screen.getByTestId('route-particle')).toHaveAttribute('cy', '138');

    // Next, M1 arrives -> triggers transition to RAG (target step 4) through orchestrator (step 3)
    const evM1: any = { id: '2', eventType: 'module', streamSequence: 2, module: 'M1', decision: 'ALLOW' };
    rerender(<RoutingMap events={[evC0, evM1]} runState="running" />);

    // Sub-step 3: Orchestrator return
    expect(screen.getByTestId('route-particle')).toHaveAttribute('cx', '160');
    expect(screen.getByTestId('route-particle')).toHaveAttribute('cy', '88');

    // Advance timer for next live sub-step
    act(() => {
      vi.advanceTimersByTime(80);
    });

    // Sub-step 4: RAG
    expect(screen.getByTestId('route-particle')).toHaveAttribute('cx', '250');
    expect(screen.getByTestId('route-particle')).toHaveAttribute('cy', '138');
    expect(screen.getByTestId('node-rag')).toHaveClass('allow');
  });
});
