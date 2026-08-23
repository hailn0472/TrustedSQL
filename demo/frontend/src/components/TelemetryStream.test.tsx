import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { TelemetryStream } from './TelemetryStream';
import { TelemetryItem } from '../state/demoReducer';

describe('TelemetryStream Component (ST-09)', () => {
  it('renders loading / empty telemetry state when no events exist', () => {
    render(<TelemetryStream events={[]} streamStatus="connecting" />);
    expect(screen.getByRole('log')).toBeInTheDocument();
    expect(screen.getByTestId('telemetry-status')).toHaveTextContent(/connecting/i);
    expect(screen.getByText(/Connecting to stream/i)).toBeInTheDocument();
  });

  it('renders ordered telemetry console lines with module identity, stage, decision, and latency text without card styling', () => {
    const events: TelemetryItem[] = [
      {
        id: 'ev-1',
        eventType: 'module',
        timestamp: '15:25:00',
        runId: 'run-1',
        sampleId: 's-1',
        turnNumber: 1,
        module: 'M1',
        streamSequence: 1,
        stage: 'inbound_sanitization',
        decision: 'ALLOW',
        latencyMs: 8,
      },
      {
        id: 'ev-2',
        eventType: 'revision',
        timestamp: '15:25:01',
        runId: 'run-1',
        sampleId: 's-1',
        turnNumber: 1,
        module: 'M2',
        streamSequence: 2,
        stage: 'policy_check',
        decision: 'DENY',
        revision: 2,
        latencyMs: 14,
      },
    ];

    const { container } = render(<TelemetryStream events={events} streamStatus="open" />);

    const item1 = screen.getByTestId('telemetry-item-ev-1');
    const item2 = screen.getByTestId('telemetry-item-ev-2');

    expect(item1).toHaveClass('terminal-line');
    expect(item2).toHaveClass('terminal-line');

    // Asserts absence of card/chip presentation classes
    expect(container.querySelector('.telemetry-item-header')).not.toBeInTheDocument();
    expect(container.querySelector('.telemetry-decision-chip')).not.toBeInTheDocument();
    expect(container.querySelector('.telemetry-module-badge')).not.toBeInTheDocument();

    expect(screen.getByText('M1')).toBeInTheDocument();
    expect(screen.getByText('Input validation')).toBeInTheDocument();
    expect(screen.getByText('ALLOW')).toBeInTheDocument();
    expect(screen.getByText('8ms')).toBeInTheDocument();

    expect(screen.getByText('M2')).toBeInTheDocument();
    expect(screen.getByText('DENY')).toBeInTheDocument();
    expect(screen.getByText('Rev 2')).toBeInTheDocument();
  });

  it('renders retracted evidence visibly as a console line with retraction text', () => {
    const events: TelemetryItem[] = [
      {
        id: 'ev-3',
        eventType: 'retract',
        timestamp: '15:25:02',
        runId: 'run-1',
        sampleId: 's-1',
        turnNumber: 1,
        module: 'M3',
        streamSequence: 3,
        reason: 'Upstream context cleared',
      },
    ];

    render(<TelemetryStream events={events} streamStatus="open" />);
    expect(screen.getByTestId('telemetry-item-ev-3')).toHaveClass('terminal-line', 'retracted');
    expect(screen.getByText(/RETRACTED: Evidence retracted by the runtime/i)).toBeInTheDocument();
  });

  it('excludes raw prompt, schema, provider payload, or attack tags from telemetry UI', () => {
    const events: TelemetryItem[] = [
      {
        id: 'ev-4',
        eventType: 'module',
        timestamp: '15:25:03',
        runId: 'run-1',
        sampleId: 's-1',
        turnNumber: 1,
        module: 'M4',
        streamSequence: 4,
        stage: 'evaluation',
        decision: 'ALLOW',
        // Raw properties passed from unexpected raw objects
        rawPayload: 'SELECT * FROM secret_table; DROP DATABASE',
        attack_kind: 'sql_injection',
      } as any,
    ];

    render(<TelemetryStream events={events} streamStatus="open" />);

    const logText = screen.getByRole('log').textContent || '';
    expect(logText).not.toContain('secret_table');
    expect(logText).not.toContain('sql_injection');
    expect(logText).not.toContain('attack_kind');
  });

  it('does not infer stream health from events and uses non-green copy for missing/error states', () => {
    const events: TelemetryItem[] = [
      { id: 'queued', eventType: 'status', timestamp: '15:25:04', runId: 'run-1', streamSequence: 0, decision: 'QUEUED', stage: 'Job queued' },
      { id: 'running', eventType: 'status', timestamp: '15:25:05', runId: 'run-1', streamSequence: 1, decision: 'RUNNING', stage: 'Execution running' },
      { id: 'missing', eventType: 'module', timestamp: '15:25:06', runId: 'run-1', streamSequence: 2, module: 'M1', stage: 'unknown server prompt/schema provider payload' },
      { id: 'error', eventType: 'module', timestamp: '15:25:07', runId: 'run-1', streamSequence: 3, module: 'M2', decision: 'ERROR', error: 'traceback provider schema prompt' },
    ];
    render(<TelemetryStream events={events} streamStatus="closed" executionState="error" />);
    expect(screen.getByTestId('telemetry-status')).toHaveTextContent(/SSE closed/i);
    expect(screen.getByTestId('telemetry-execution-state')).toHaveTextContent('ERROR');
    const text = screen.getByRole('log').textContent ?? '';
    expect(text).toContain('QUEUED');
    expect(text).toContain('RUNNING');
    expect(text).toContain('STATE UNKNOWN');
    expect(text).toContain('ERROR');
    expect(text).not.toMatch(/traceback|provider|schema|prompt/i);
  });

  it('has role="log" and polite live region attributes for accessibility, and renders blinking cursor only when open', () => {
    const { rerender } = render(<TelemetryStream events={[]} streamStatus="idle" />);
    const logRegion = screen.getByRole('log');
    expect(logRegion).toHaveAttribute('aria-live', 'polite');
    expect(logRegion).toHaveAttribute('tabIndex', '0');
    expect(screen.queryByText('_')).not.toBeInTheDocument();

    rerender(<TelemetryStream events={[]} streamStatus="open" />);
    expect(screen.getByText('_')).toBeInTheDocument();
    expect(screen.getByText('>')).toBeInTheDocument();
  });
});
