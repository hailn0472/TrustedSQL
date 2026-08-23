import React, { useEffect, useRef } from 'react';
import { TelemetryItem, RunStatus } from '../state/demoReducer';
import { safeRetractReason, safeRuntimeError } from '../api/client';
import { ExecutionMode } from '../app/types';

export type StreamStatus = 'idle' | 'connecting' | 'open' | 'closed' | 'error' | 'unavailable';

interface TelemetryStreamProps {
  events: TelemetryItem[];
  streamStatus?: StreamStatus;
  executionState?: RunStatus | 'unknown';
  error?: string | null;
  /** Compatibility for callers that have not yet supplied an explicit stream status. */
  isConnecting?: boolean;
  mode?: ExecutionMode;
}

const SAFE_MODULES = new Set(['ROUTER', 'RAG', 'C0', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'X1']);

function safeModule(value: string | undefined): string {
  return value && SAFE_MODULES.has(value) ? value : 'UNKNOWN';
}

function safeDecision(value: string | undefined): string {
  return value === 'ALLOW' || value === 'DENY' || value === 'ERROR' || value === 'QUEUED' || value === 'RUNNING' || value === 'CANCELLED'
    ? value
    : 'STATE UNKNOWN';
}

const MODULE_FALLBACK: Record<string, string> = {
  ROUTER: 'Classifying the request as document knowledge or structured database data',
  RAG: 'Retrieving document chunks, grounding the answer and extracting attributable sources',
  C0: 'Building trusted context from identity, schema, policy and conversation history',
  M1: 'Checking the prompt for injection and instruction-integrity violations',
  M2: 'Resolving semantic intent, scope, target relation and cross-turn risk',
  M3: 'Planning requested tables, columns, predicates and applicable policies',
  M4: 'Validating requested resources against the role access matrix',
  M5: 'Proving row-level scope against the current user and target entities',
  M6: 'Generating PostgreSQL from the resolved request and runtime context',
  M7: 'Parsing SQL and checking SELECT-only, tables, columns and scope predicates',
  X1: 'Executing the final SQL in a bounded read-only database transaction',
};

function moduleDetail(module: string, detail: string | undefined): string {
  return detail?.trim() || MODULE_FALLBACK[module] || 'Processing runtime event';
}

export const TelemetryStream: React.FC<TelemetryStreamProps> = ({
  events,
  streamStatus,
  executionState = 'idle',
  error = null,
  isConnecting = false,
  mode = 'trustedsql',
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const resolvedStreamStatus = streamStatus ?? (isConnecting ? 'connecting' : events.length > 0 ? 'open' : 'idle');

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    if (distanceFromBottom < 60) {
      const prefersReducedMotion =
        typeof window !== 'undefined' &&
        typeof window.matchMedia === 'function' &&
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (typeof el.scrollTo === 'function') {
        el.scrollTo({
          top: el.scrollHeight,
          behavior: prefersReducedMotion ? 'auto' : 'smooth',
        });
      }
    }
  }, [events]);

  const streamLabel = resolvedStreamStatus === 'error'
    ? 'Stream connection error'
    : resolvedStreamStatus === 'unavailable'
      ? 'Telemetry unavailable'
    : resolvedStreamStatus === 'connecting'
      ? 'Connecting SSE...'
      : resolvedStreamStatus === 'open'
        ? 'SSE connected'
        : resolvedStreamStatus === 'closed'
          ? 'SSE closed'
          : 'Idle';

  const isConnected = resolvedStreamStatus === 'open';
  const activeRun = [...events].reverse().find((event) => event.runId)?.runId;
  const shortRun = activeRun ? activeRun.replace(/^run-/, '').slice(0, 8) : 'waiting';

  return (
    <div className="telemetry-stream-panel" data-testid="telemetry-stream-panel">
      <div className="telemetry-header">
        <div className="terminal-window-controls" aria-hidden="true">
          <span className="terminal-window-dot close" />
          <span className="terminal-window-dot minimize" />
          <span className="terminal-window-dot maximize" />
        </div>
        <div className="terminal-title-group">
          <h2 className="ops-panel-title">
            {mode === 'trustedsql' ? 'router@demo: ~/rag-or-trustedsql' : 'router@demo: ~/rag-or-direct-sql'}
          </h2>
        </div>
        <div className="telemetry-status-badge" data-testid="telemetry-status">
          <span className={`terminal-conn-dot ${resolvedStreamStatus}`} />
          {error ? <span className="telemetry-status-err">{streamLabel}</span> : <span className="telemetry-status-idle">{streamLabel}</span>}
        </div>
      </div>
      <div className="telemetry-execution-state" data-testid="telemetry-execution-state">
        <span className="terminal-shell-user">demo</span>
        <span className="terminal-shell-separator">@</span>
        <span className="terminal-shell-host">{mode === 'trustedsql' ? 'trustedsql' : 'direct-sql'}</span>
        <span className="terminal-shell-path">:~$</span>
        <span className="terminal-shell-command">trace --follow --run {shortRun}</span>
        <span className={`state-value ${executionState.toLowerCase()}`}>{executionState.toUpperCase()}</span>
      </div>

      <div
        className="telemetry-log-container"
        ref={containerRef}
        role="log"
        aria-live="polite"
        tabIndex={0}
        aria-label={mode === 'trustedsql' ? 'Security Telemetry Event Log' : 'Direct SQL Telemetry Event Log'}
      >
        {events.length === 0 ? (
          <div className="telemetry-empty-msg">
            {resolvedStreamStatus === 'unavailable'
              ? error ?? 'Execution state unknown; telemetry unavailable.'
              : error
              ? 'SSE connection failed; execution state remains shown above.'
              : resolvedStreamStatus === 'connecting'
              ? 'Connecting to stream...'
              : resolvedStreamStatus === 'open'
              ? 'Connected; waiting for security events...'
              : resolvedStreamStatus === 'closed'
              ? 'Stream closed.'
              : mode === 'trustedsql'
                ? 'Waiting for security events from backend SSE stream...'
                : 'Waiting for SQL generation and execution events...'}
          </div>
        ) : (
          events.map((ev) => {
            const module = safeModule(ev.module);
            const decision = safeDecision(ev.decision);
            const safeError = ev.error ? safeRuntimeError(ev.error) : undefined;

            if (ev.eventType === 'retract') {
              return (
                <div key={ev.id} data-testid={`telemetry-item-${ev.id}`} className="terminal-event retracted">
                  <div className="terminal-line">
                    <span className="term-col-time">{ev.timestamp}</span>
                    <span className="term-col-level level-warn">WARN</span>
                    <span className="term-col-module">{module}</span>
                    <span className="term-col-stage">Evidence retracted</span>
                  </div>
                  <div className="terminal-detail"><span className="terminal-tree">└─</span>{safeRetractReason(ev.reason)}</div>
                </div>
              );
            }

            if (ev.eventType === 'status') {
              return (
                <div key={ev.id} data-testid={`telemetry-item-${ev.id}`} className="terminal-line terminal-system-line status">
                  <span className="terminal-prompt-sym">›</span>
                  <span className="term-col-time">{ev.timestamp}</span>
                  <span className={`term-col-level level-${decision.toLowerCase()}`}>{decision}</span>
                  <span className="term-col-stage">{ev.stage ?? 'Runtime state updated'}</span>
                </div>
              );
            }

            if (ev.eventType === 'trace') {
              const traceProgress = ev.traceStep && ev.traceTotal
                ? `${String(ev.traceStep).padStart(2, '0')}/${String(ev.traceTotal).padStart(2, '0')}`
                : '--/--';
              return (
                <div key={ev.id} data-testid={`telemetry-item-${ev.id}`} className="terminal-line terminal-trace-entry">
                  <span className="term-col-time">{ev.timestamp}</span>
                  <span className="term-col-level level-trace">TRACE</span>
                  <span className="term-col-module">{module}</span>
                  <span className="term-col-stage">
                    <span className="term-trace-progress">[{traceProgress}]</span> {ev.detail ?? ev.stage ?? 'Runtime operation'}
                  </span>
                  <span className="term-col-decision level-running">RUN</span>
                </div>
              );
            }

            const isAllow = decision === 'ALLOW';
            const isDeny = decision === 'DENY';
            const levelClass = isAllow ? 'level-allow' : isDeny ? 'level-deny' : 'level-info';
            const verdictMessage = ev.detail
              ? `${ev.stage ?? 'Module verdict'} · ${moduleDetail(module, ev.detail)}`
              : ev.stage ?? moduleDetail(module, undefined);

            return (
              <div
                key={ev.id}
                data-testid={`telemetry-item-${ev.id}`}
                className={`terminal-event ${isAllow ? 'allow' : isDeny ? 'deny' : 'neutral'}`}
              >
                <div className="terminal-line">
                  <span className="term-col-time">{ev.timestamp}</span>
                  <span className={`term-col-level ${isDeny ? 'level-deny' : safeError ? 'level-error' : 'level-info'}`}>{isDeny ? 'WARN' : safeError ? 'ERROR' : 'INFO'}</span>
                  <span className="term-col-module">{module}</span>
                  <span className="term-col-stage">{verdictMessage}</span>
                  <span className={`term-col-decision ${levelClass}`}>{decision}</span>
                  {ev.revision !== undefined && ev.revision > 1 && <span className="term-col-rev">rev={ev.revision}</span>}
                  {ev.latencyMs !== undefined && <span className="term-col-latency">{Math.round(ev.latencyMs)}ms</span>}
                </div>
              </div>
            );
          })
        )}
        {isConnected && (
          <div className="terminal-live-cursor-line" aria-hidden="true">
            <span className="terminal-prompt-sym">&gt;</span>
            <span className="terminal-cursor-blink">_</span>
          </div>
        )}
      </div>
    </div>
  );
};
