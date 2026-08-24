import React, { useState } from 'react';
import { ScenarioMetadata, ScenarioTurn } from '../app/types';
import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  MessageSquareText,
  X,
} from 'lucide-react';

interface ScenarioRailProps {
  scenarios: ScenarioMetadata[];
  selectedScenarioKey: string;
  disabled?: boolean;
  onSelectScenario: (scenarioKey: string) => void;
  onRemoveScenario: (scenarioKey: string) => void;
  onCopyScenarioTurn: (scenarioKey: string, turn: ScenarioTurn) => void;
}

type ScenarioTagKind = 'rag' | 'benign' | 'rbac' | 'pi' | 'mt' | 'other';

const scenarioTag = (canonicalId: string): { kind: ScenarioTagKind; label: string } => {
  const normalized = canonicalId.toUpperCase();
  if (normalized.startsWith('RAG-')) return { kind: 'rag', label: 'RAG' };
  if (normalized.startsWith('ST-BENIGN-')) return { kind: 'benign', label: 'BENIGN' };
  if (normalized.startsWith('ST-RBAC-')) return { kind: 'rbac', label: 'RBAC' };
  if (normalized.startsWith('ST-PI-')) return { kind: 'pi', label: 'PI' };
  if (normalized.startsWith('MT-')) return { kind: 'mt', label: 'MT' };
  return { kind: 'other', label: 'OTHER' };
};

export const ScenarioRail: React.FC<ScenarioRailProps> = ({
  scenarios,
  selectedScenarioKey,
  disabled = false,
  onSelectScenario,
  onRemoveScenario,
  onCopyScenarioTurn,
}) => {
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [copiedTurn, setCopiedTurn] = useState<string | null>(null);

  const fallbackCopy = (text: string): boolean => {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    let copied = false;
    try {
      copied = document.execCommand('copy');
    } finally {
      textarea.remove();
    }
    return copied;
  };

  const copyPrompt = async (scenarioKey: string, turn: ScenarioTurn) => {
    if (!turn.nlq) return;
    const identity = `${scenarioKey}:${turn.optionId ?? `turn-${turn.turnNumber}`}`;
    try {
      let copied = false;
      if (navigator.clipboard?.writeText) {
        try {
          await navigator.clipboard.writeText(turn.nlq);
          copied = true;
        } catch {
          copied = false;
        }
      }
      if (!copied) copied = fallbackCopy(turn.nlq);
      if (!copied) throw new Error('clipboard unavailable');
      setCopiedTurn(identity);
      onCopyScenarioTurn(scenarioKey, turn);
      window.setTimeout(() => setCopiedTurn((current) => current === identity ? null : current), 1_500);
    } catch {
      setCopiedTurn(null);
    }
  };

  const toggleScenario = (scenarioKey: string) => {
    onSelectScenario(scenarioKey);
    setExpandedKeys((current) => {
      const next = new Set(current);
      if (next.has(scenarioKey)) next.delete(scenarioKey);
      else next.add(scenarioKey);
      return next;
    });
  };

  const removeScenario = (scenarioKey: string) => {
    if (disabled) return;
    setExpandedKeys((current) => {
      const next = new Set(current);
      next.delete(scenarioKey);
      return next;
    });
    onRemoveScenario(scenarioKey);
  };

  return (
    <aside className="left-scenario-rail" aria-label="Prompt library">
      <div>
        <h2 className="rail-section-header">
          <span>Prompt Library</span>
          <span className="prompt-library-count">{scenarios.length}</span>
        </h2>

        <div className="prompt-library-cards">
          {scenarios.map((item) => {
            const expanded = expandedKeys.has(item.key);
            const selected = item.key === selectedScenarioKey;
            const tag = scenarioTag(item.canonicalId);
            return (
              <div key={item.key} className={`prompt-library-card ${selected ? 'selected' : ''}`}>
                <div className="prompt-library-card-header">
                  <button
                    type="button"
                    className="prompt-library-toggle"
                    aria-expanded={expanded}
                    aria-controls={`prompt-list-${item.key}`}
                    aria-label={`${item.canonicalId} prompt library`}
                    onClick={() => toggleScenario(item.key)}
                  >
                    <span className="prompt-library-toggle-icon">
                      {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                    </span>
                    <span className="prompt-library-heading">
                      <span className="scenario-card-top">
                        <span className="scenario-id-label" data-testid="scenario-id-label">{item.canonicalId}</span>
                        <span className={`scenario-tag ${tag.kind}`} data-scenario-kind={tag.kind}>
                          {tag.label} · {item.turnCount}
                        </span>
                      </span>
                      <span className="scenario-source">{item.title}</span>
                      <span className="scenario-meta">
                        {item.categoryBadge} · {item.role ?? 'unknown role'}{item.userId === undefined ? '' : ` · User ${item.userId}`}
                      </span>
                    </span>
                  </button>
                  <button
                    type="button"
                    className="btn-remove-scenario"
                    disabled={disabled}
                    aria-label={`Remove ${item.canonicalId} from Prompt Library`}
                    title="Remove from Prompt Library"
                    onClick={() => removeScenario(item.key)}
                  >
                    <X size={14} />
                  </button>
                </div>

                {expanded && (
                  <ol id={`prompt-list-${item.key}`} className="prompt-library-list" data-testid="multiturn-prompt-list">
                    {item.turns.map((turn) => {
                      const optionId = turn.optionId ?? `turn-${turn.turnNumber}`;
                      const copyIdentity = `${item.key}:${optionId}`;
                      const replacesCurrentTurn = turn.replacesTurn === turn.turnNumber;
                      return (
                        <li key={optionId} className={`prompt-library-item ${replacesCurrentTurn ? 'turn-replacement-option' : ''}`}>
                          <div className="prompt-library-item-header">
                            <span>
                              <MessageSquareText size={12} /> Turn {turn.turnNumber}
                              <span className={`turn-classification ${turn.classification.toLowerCase()}`}>
                                {turn.classification}
                              </span>
                              {replacesCurrentTurn && <span className="turn-edit-hint">EDIT SAME TURN</span>}
                            </span>
                            <button
                              type="button"
                              className="btn-copy-prompt"
                              aria-label={replacesCurrentTurn
                                ? `Copy ${item.canonicalId} malicious edit for turn ${turn.turnNumber}`
                                : `Copy ${item.canonicalId} turn ${turn.turnNumber} query`}
                              onClick={() => void copyPrompt(item.key, turn)}
                            >
                              {copiedTurn === copyIdentity ? <Check size={12} /> : <Copy size={12} />}
                              {copiedTurn === copyIdentity ? 'Copied' : replacesCurrentTurn ? 'Copy to edit' : 'Copy'}
                            </button>
                          </div>
                          <p>{turn.nlq}</p>
                        </li>
                      );
                    })}
                  </ol>
                )}
              </div>
            );
          })}
          {!scenarios.length && <div className="prompt-library-empty">Prompt library unavailable</div>}
        </div>
      </div>

    </aside>
  );
};
