import React, { Fragment, useEffect, useRef } from 'react';
import { ExecutionMode, ScenarioMetadata, ReadinessState, SessionIdentity } from '../app/types';
import { safeFinalError } from '../api/client';
import { ChatTurn, RunStatus } from '../state/demoReducer';
import { ChatMessageContent } from './ChatMessageContent';
import { ExecutionComparison } from './ExecutionComparison';
import {
  AlertTriangle,
  BookOpen,
  Database,
  ExternalLink,
  FileText,
  MessageCircle,
  Pencil,
  SendHorizontal,
  Shield,
  ShieldAlert,
  ShieldCheck,
  ShieldOff,
  StopCircle,
  Terminal,
  User,
  XCircle,
} from 'lucide-react';

interface ChatStageProps {
  scenario?: ScenarioMetadata;
  readinessState: ReadinessState;
  runState: RunStatus;
  chatTurns: ChatTurn[];
  draft: string;
  onDraftChange: (value: string) => void;
  error: string | null;
  telemetryUnavailable?: boolean;
  telemetryExecutionState?: 'queued' | 'running' | 'unknown' | null;
  telemetryError?: string | null;
  onSend: () => void;
  onCancel: () => void;
  onEditTurn: (turnNumber: number) => void;
  editingTurnNumber?: number | null;
  mode: ExecutionMode;
  sessionIdentity: SessionIdentity;
}

const MODULES = ['C0', 'M1', 'M2', 'M3', 'M4', 'M5', 'M6', 'M7', 'X1'];

export const ChatStage: React.FC<ChatStageProps> = ({
  scenario,
  readinessState,
  runState,
  chatTurns,
  draft,
  onDraftChange,
  error,
  telemetryUnavailable = false,
  telemetryExecutionState = null,
  telemetryError = null,
  onSend,
  onCancel,
  onEditTurn,
  editingTurnNumber = null,
  mode,
  sessionIdentity,
}) => {
  const isReady = readinessState === 'ready';
  const isActive = runState === 'queued' || runState === 'running';
  const isTerminal = ['complete', 'denied', 'error', 'cancelled'].includes(runState);
  const atTurnLimit = chatTurns.length >= 20;
  const sendDisabled = !isReady || isActive || telemetryUnavailable || !draft.trim() || atTurnLimit;
  const terminalHeaderRef = useRef<HTMLHeadingElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const target = messagesEndRef.current;
    if (target && typeof target.scrollIntoView === 'function') {
      target.scrollIntoView({ block: 'end' });
    }
  }, [chatTurns.length, runState]);

  useEffect(() => {
    if (isTerminal) terminalHeaderRef.current?.focus();
  }, [isTerminal]);

  useEffect(() => {
    if (editingTurnNumber === null) return;
    const composer = composerRef.current;
    if (!composer) return;
    composer.focus();
    composer.setSelectionRange(composer.value.length, composer.value.length);
  }, [editingTurnNumber]);

  const submitOnEnter = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      if (!sendDisabled) onSend();
    }
  };

  const testId = (base: string, turnNumber: number) =>
    turnNumber === chatTurns.length ? base : `${base}-${turnNumber}`;

  const renderResult = (turn: ChatTurn) => {
    const result = turn.result;
    const isLatest = turn.turnNumber === chatTurns.length;
    if (result?.decision === 'ALLOW' && result.resultType === 'chat') {
      return (
        <div className="chat-conversation-row bot-row" data-testid={testId('chat-response-row', turn.turnNumber)}>
          <div className="avatar bot-avatar chat-avatar" aria-hidden="true"><MessageCircle size={16} /></div>
          <div className="bot-bubble result-card orchestrator-answer" data-testid={testId('chat-result-card', turn.turnNumber)}>
            <div className="card-header">
              <h3
                tabIndex={-1}
                ref={isLatest ? terminalHeaderRef : undefined}
                className="result-heading"
                data-testid={testId('terminal-result-heading', turn.turnNumber)}
              >
                <MessageCircle size={16} /> Orchestrator response
              </h3>
              <span className="latency-badge">{result.latencyMs ?? 0}ms</span>
            </div>
            <ChatMessageContent className="rag-answer-text">{result.answer ?? ''}</ChatMessageContent>
            <div className="chat-route-untouched">
              <MessageCircle size={12} /> Conversation route · RAG, security modules, and Education DB bypassed
            </div>
          </div>
        </div>
      );
    }
    if (result?.decision === 'ALLOW' && result.resultType === 'rag') {
      return (
        <div className="chat-conversation-row bot-row" data-testid={testId('rag-response-row', turn.turnNumber)}>
          <div className="avatar bot-avatar rag-avatar" aria-hidden="true"><BookOpen size={16} /></div>
          <div className="bot-bubble result-card rag-answer" data-testid={testId('rag-result-card', turn.turnNumber)}>
            <div className="card-header">
              <h3
                tabIndex={-1}
                ref={isLatest ? terminalHeaderRef : undefined}
                className="result-heading"
                data-testid={testId('terminal-result-heading', turn.turnNumber)}
              >
                <BookOpen size={16} /> Orchestrator response · grounded in documents
              </h3>
              <span className="latency-badge">{result.latencyMs ?? 0}ms</span>
            </div>
            <ChatMessageContent className="rag-answer-text">{result.answer ?? ''}</ChatMessageContent>
            <div className="rag-sources" aria-label="Retrieved sources">
              <div className="rag-sources-title"><FileText size={14} /> Sources ({result.sources?.length ?? 0})</div>
              <ol className="rag-source-list">
                {(result.sources ?? []).map((source) => {
                  const clickable = Boolean(source.uri && /^https?:\/\//i.test(source.uri));
                  return (
                    <li key={`${source.citation}-${source.uri ?? source.documentName ?? source.title}`} className="rag-source-row">
                      <details className="rag-source-item">
                        <summary className="rag-source-heading">
                          <span className="rag-citation">[{source.citation}]</span>
                          <span className="rag-source-title">{source.title}</span>
                          <span className="rag-source-expand-label">View source</span>
                        </summary>
                        <div className="rag-source-details">
                          {source.snippet && (
                            <div className="rag-source-snippet">
                              <span>Retrieved passage</span>
                              <p>{source.snippet}</p>
                            </div>
                          )}
                          {source.documentName && source.documentName !== source.uri && (
                            <div className="rag-source-metadata">
                              <span>Document</span>
                              <code>{source.documentName}</code>
                            </div>
                          )}
                          {source.uri && (
                            <div className="rag-source-metadata">
                              <span>Source</span>
                              {clickable ? (
                                <a href={source.uri} target="_blank" rel="noreferrer">
                                  Open original source <ExternalLink size={11} />
                                </a>
                              ) : <code>{source.uri}</code>}
                            </div>
                          )}
                        </div>
                      </details>
                    </li>
                  );
                })}
              </ol>
              <div className="rag-db-untouched"><Database size={12} /> Document route · Education DB untouched</div>
            </div>
          </div>
        </div>
      );
    }
    if (result?.decision === 'ALLOW') {
      return (
        <div className="chat-conversation-row bot-row" data-testid={testId('allow-response-row', turn.turnNumber)}>
          <div className="avatar bot-avatar" aria-hidden="true"><Shield size={16} /></div>
          <div className="bot-bubble result-card allow" data-testid={testId('allow-result-card', turn.turnNumber)}>
            <div className="card-header">
              <h3
                tabIndex={-1}
                ref={isLatest ? terminalHeaderRef : undefined}
                className="result-heading"
                data-testid={testId('terminal-result-heading', turn.turnNumber)}
              >
                {mode === 'trustedsql' ? <ShieldCheck size={16} /> : <ShieldOff size={16} />}
                Orchestrator response
              </h3>
              <span className="latency-badge">{result.latencyMs ?? 0}ms</span>
            </div>
            <ChatMessageContent className="sql-natural-answer">
              {result.answer ?? 'The query completed successfully. Expand the technical evidence below to inspect the result.'}
            </ChatMessageContent>
            {(result.sql || (result.rows && result.columns)) && (
              <details className="sql-evidence-details">
                <summary>
                  <span><Terminal size={12} /> View {mode === 'trustedsql' ? 'TrustedSQL' : 'Direct SQL'} evidence</span>
                  {result.rows && <small>{result.rows.length} row{result.rows.length === 1 ? '' : 's'}</small>}
                </summary>
                <div className="sql-evidence-content">
                  {result.executionComparison && <ExecutionComparison comparison={result.executionComparison} />}
                  <details className="sql-raw-details" data-testid={testId('sql-raw-details', turn.turnNumber)}>
                    <summary>
                      <span><Terminal size={13} /> SQL query &amp; raw results</span>
                      <small>Technical details</small>
                    </summary>
                    <div className="sql-raw-content">
                      {result.sql && (
                        <div className="sql-box" data-testid={testId('sql-box', turn.turnNumber)}>
                          <div className="sql-box-header"><Terminal size={12} /> Executed SQL Query</div>
                          <code>{result.sql}</code>
                        </div>
                      )}
                      {result.rows && result.columns && (
                        <div className="result-table-container" data-testid={testId('result-table-container', turn.turnNumber)}>
                          <table className="result-table">
                            <caption>Query Results ({result.rows.length} rows returned)</caption>
                            <thead><tr>{result.columns.map((column) => <th key={column} scope="col">{column}</th>)}</tr></thead>
                            <tbody>
                              {result.rows.map((row, rowIndex) => (
                                <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{String(cell)}</td>)}</tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  </details>
                </div>
              </details>
            )}
          </div>
        </div>
      );
    }

    if (result?.decision === 'DENY') {
      return (
        <div className="chat-conversation-row bot-row" data-testid={testId('deny-response-row', turn.turnNumber)}>
          <div className="avatar bot-avatar" aria-hidden="true"><Shield size={16} /></div>
          <div className="bot-bubble result-card deny" data-testid={testId('deny-card', turn.turnNumber)}>
            <div className="card-header">
              <h3
                tabIndex={-1}
                ref={isLatest ? terminalHeaderRef : undefined}
                className="result-heading text-deny"
                data-testid={testId('terminal-result-heading', turn.turnNumber)}
              >
                <ShieldAlert size={16} /> Access denied by security policy
              </h3>
            </div>
            <div className="deny-body">
              <div className="deny-reason">{safeFinalError('DENY', result.error) ?? 'Access denied by security policy.'}</div>
              <div className="deny-meta">
                <span><strong>Detector:</strong> {result.detectedAt && MODULES.includes(result.detectedAt) ? result.detectedAt : 'Not reported'}</span>
                <span><strong>Enforcer:</strong> {result.enforcedAt === 'trustedsql' ? 'trustedsql' : 'Not reported'}</span>
                <span><Database size={12} /> <strong>Database Untouched:</strong> Yes</span>
              </div>
            </div>
          </div>
        </div>
      );
    }

    if (turn.error) {
      return (
        <div className="chat-conversation-row bot-row">
          <div className="avatar bot-avatar" aria-hidden="true"><Shield size={16} /></div>
          <div className="bot-bubble result-card error" data-testid={testId('error-card', turn.turnNumber)}>
            <h3
              tabIndex={-1}
              ref={isLatest ? terminalHeaderRef : undefined}
              className="result-heading text-error"
              data-testid={testId('terminal-result-heading', turn.turnNumber)}
            >
              <XCircle size={16} /> Execution error
            </h3>
            <div className="error-body">{safeFinalError('ERROR', turn.error) ?? 'Execution could not be completed.'}</div>
          </div>
        </div>
      );
    }
    return null;
  };

  return (
    <section className="center-chat-stage" aria-label={mode === 'trustedsql' ? 'Live TrustedSQL chat' : 'Live direct SQL chat'}>
      <div className="chat-stage-header">
        <div className="chat-title-meta">
          <div className="chat-main-title">
            <span>Live Multiturn Chat</span>
            <span className={`trustedsql-secured-chip ${mode === 'direct' ? 'direct' : ''}`}>
              {mode === 'trustedsql' ? <ShieldCheck size={12} /> : <ShieldOff size={12} />}
              {mode === 'trustedsql' ? 'TRUSTEDSQL SECURED' : 'DIRECT SQL · SECURITY OFF'}
            </span>
          </div>
          <div className="chat-sub-meta">
            {scenario?.canonicalId ?? 'No scenario selected'} prompt library · {sessionIdentity.role}/User {sessionIdentity.userId} · {chatTurns.length} chat turns
          </div>
        </div>
      </div>

      <div className="chat-messages-container" data-testid="chat-live-region" aria-live="polite">
        {chatTurns.length === 0 && !isActive && (
          <div className="chat-conversation-row bot-row" data-testid="assistant-greeting-row">
            <div className="avatar bot-avatar" aria-hidden="true"><Shield size={16} /></div>
            <div className="bot-bubble greeting-bubble" data-testid="assistant-greeting-bubble">
              <div className="bubble-content">
                Ask about university documents such as syllabi and tuition, or ask a database question. The router selects RAG or the active SQL path automatically.
              </div>
            </div>
          </div>
        )}

        {chatTurns.map((turn) => (
          <Fragment key={turn.turnNumber}>
            <div className="chat-conversation-row user-row" data-testid={`user-prompt-row-${turn.turnNumber}`}>
              <div className="user-bubble" data-testid={testId('user-prompt-bubble', turn.turnNumber)}>
                <div className="bubble-speaker-row">
                  <div className="bubble-speaker">{sessionIdentity.role} (User ID: {sessionIdentity.userId}) · Turn {turn.turnNumber}</div>
                  <button
                    type="button"
                    className="btn-edit-turn"
                    disabled={isActive || telemetryUnavailable}
                    onClick={() => onEditTurn(turn.turnNumber)}
                    aria-label={`Edit Turn ${turn.turnNumber}`}
                    title={`Edit Turn ${turn.turnNumber}`}
                  >
                    <Pencil size={11} /> Edit
                  </button>
                </div>
                <div className="bubble-content">{turn.nlq}</div>
              </div>
              <div className="avatar user-avatar" aria-hidden="true"><User size={16} /></div>
            </div>
            {renderResult(turn)}
          </Fragment>
        ))}

        {isActive && !telemetryUnavailable && (
          <div className="chat-conversation-row bot-row" data-testid="running-status-row">
            <div className="avatar bot-avatar" aria-hidden="true"><Shield size={16} /></div>
            <div className="bot-bubble status-bubble running" data-testid="running-status-bubble">
              <div className="status-spinner" />
              <span>Routing turn {chatTurns.length}, then running the document or {mode === 'trustedsql' ? 'TrustedSQL' : 'Direct SQL'} branch...</span>
            </div>
          </div>
        )}

        {telemetryUnavailable && (
          <div className="chat-conversation-row bot-row">
            <div className="avatar bot-avatar" aria-hidden="true"><Shield size={16} /></div>
            <div className="bot-bubble result-card cancelled" data-testid="telemetry-unavailable-card">
              <h3 className="result-heading text-neutral">
                <AlertTriangle size={16} /> Execution state {telemetryExecutionState === 'unknown' ? 'unknown' : runState}; telemetry unavailable
              </h3>
              <div className="cancelled-body">The runtime status could not be safely reconciled. Reset the chat before sending again.</div>
              {telemetryError && <div className="cancelled-body">{telemetryError}</div>}
            </div>
          </div>
        )}

        {runState === 'error' && error && !chatTurns.some((turn) => turn.error) && (
          <div className="chat-conversation-row bot-row">
            <div className="avatar bot-avatar" aria-hidden="true"><Shield size={16} /></div>
            <div className="bot-bubble result-card error" data-testid="error-card">
              <h3 tabIndex={-1} ref={terminalHeaderRef} className="result-heading text-error" data-testid="terminal-result-heading">
                <XCircle size={16} /> Execution error
              </h3>
              <div className="error-body">{safeFinalError('ERROR', error) ?? 'Execution could not be completed.'}</div>
            </div>
          </div>
        )}

        {runState === 'cancelled' && (
          <div className="chat-conversation-row bot-row">
            <div className="avatar bot-avatar" aria-hidden="true"><Shield size={16} /></div>
            <div className="bot-bubble result-card cancelled" data-testid="cancelled-card">
              <h3 tabIndex={-1} ref={terminalHeaderRef} className="result-heading text-neutral" data-testid="terminal-result-heading">
                <StopCircle size={16} /> Execution cancelled
              </h3>
              <div className="cancelled-body">This queued turn did not complete.</div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-stage-input-area">
        <div className="chat-composer">
          <textarea
            ref={composerRef}
            className="chatbox-input"
            data-testid="chatbox-input"
            aria-label="Chat message"
            placeholder={editingTurnNumber
              ? `Paste the edited prompt to replace Turn ${editingTurnNumber}…`
              : mode === 'trustedsql' ? 'Ask about documents or query data through TrustedSQL…' : 'Ask about documents or query data through Direct SQL…'}
            value={draft}
            maxLength={2_000}
            rows={2}
            disabled={!isReady || isActive || telemetryUnavailable || atTurnLimit}
            onChange={(event) => onDraftChange(event.target.value)}
            onKeyDown={submitOnEnter}
          />
          <div className="input-buttons-group">
            {runState === 'queued' ? (
              <button className="btn-cancel" onClick={onCancel} aria-label="Cancel queued message" disabled={telemetryUnavailable}>
                <StopCircle size={14} /> Cancel
              </button>
            ) : runState === 'running' ? (
              <button className="btn-cancel disabled" disabled aria-label="Cancel running message" title="Cancellation is disabled after execution starts">
                <StopCircle size={14} /> Cancel
              </button>
            ) : (
              <button className="btn-execute" disabled={sendDisabled} onClick={onSend} aria-label={editingTurnNumber ? `Replace Turn ${editingTurnNumber}` : 'Send message'} aria-describedby="send-reason">
                <SendHorizontal size={14} /> {editingTurnNumber ? 'Replace' : 'Send'}
              </button>
            )}
          </div>
        </div>
        <div className="composer-meta">
          <span>{draft.length}/2000</span>
          <span>{atTurnLimit
            ? '20-turn limit reached'
            : editingTurnNumber
              ? `Editing Turn ${editingTurnNumber} · Send replaces the previous Turn ${editingTurnNumber}`
              : 'Enter to send · Shift+Enter for a new line'}</span>
        </div>
        <div className="sr-only" id="send-reason">
          {!isReady ? 'Backend is not ready' : isActive ? 'Execution in progress' : !draft.trim() ? 'Enter a message to send' : 'Ready to send'}
        </div>
      </div>
    </section>
  );
};
