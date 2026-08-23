import React, { useEffect, useRef, useState } from 'react';
import { ExecutionMode, PromptScenarioSearchItem, ScenarioMetadata, ScenarioRoleFilter, ToolReadiness } from '../app/types';
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  LoaderCircle,
  MessageSquareText,
  Plus,
  Search,
  ShieldCheck,
  ShieldOff,
  X,
} from 'lucide-react';

interface ScenarioRailProps {
  scenarios: ScenarioMetadata[];
  selectedScenarioKey: string;
  tools: ToolReadiness[];
  mode: ExecutionMode;
  disabled?: boolean;
  onSelectScenario: (scenarioKey: string) => void;
  onSearchScenarios: (query: string, role: ScenarioRoleFilter) => Promise<PromptScenarioSearchItem[]>;
  onImportScenario: (scenarioId: string) => Promise<ScenarioMetadata>;
  onRemoveScenario: (scenarioKey: string) => void;
}

export const ScenarioRail: React.FC<ScenarioRailProps> = ({
  scenarios,
  selectedScenarioKey,
  tools,
  mode,
  disabled = false,
  onSelectScenario,
  onSearchScenarios,
  onImportScenario,
  onRemoveScenario,
}) => {
  const [expandedKeys, setExpandedKeys] = useState<Set<string>>(new Set());
  const [copiedTurn, setCopiedTurn] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState<ScenarioRoleFilter>('all');
  const [searchResults, setSearchResults] = useState<PromptScenarioSearchItem[]>([]);
  const [searching, setSearching] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [activeSuggestion, setActiveSuggestion] = useState(0);
  const [importingId, setImportingId] = useState<string | null>(null);
  const [libraryMessage, setLibraryMessage] = useState<string | null>(null);
  const [libraryError, setLibraryError] = useState<string | null>(null);
  const searchGeneration = useRef(0);
  const bypassedInDirectMode = (toolId: string) =>
    ['policy_engine', 'trustedsql'].includes(toolId);

  const copyPrompt = async (scenarioKey: string, turnNumber: number, nlq?: string) => {
    if (!nlq) return;
    const identity = `${scenarioKey}:${turnNumber}`;
    try {
      await navigator.clipboard.writeText(nlq);
      setCopiedTurn(identity);
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

  useEffect(() => {
    const query = searchQuery.trim();
    const generation = ++searchGeneration.current;
    if (disabled || !dropdownOpen) {
      setSearchResults([]);
      setSearching(false);
      return;
    }
    setSearching(true);
    setLibraryError(null);
    onSearchScenarios(query, roleFilter).then((matches) => {
      if (generation !== searchGeneration.current) return;
      setSearchResults(matches);
      setActiveSuggestion(0);
    }).catch((error: unknown) => {
      if (generation !== searchGeneration.current) return;
      setSearchResults([]);
      setLibraryError(error instanceof Error ? error.message : 'Dataset scenario search failed');
    }).finally(() => {
      if (generation === searchGeneration.current) setSearching(false);
    });
  }, [disabled, dropdownOpen, onSearchScenarios, roleFilter, searchQuery]);

  useEffect(() => {
    if (!libraryMessage) return;
    const timer = window.setTimeout(() => setLibraryMessage(null), 2_500);
    return () => window.clearTimeout(timer);
  }, [libraryMessage]);

  const handleSearchKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Escape') {
      setDropdownOpen(false);
      return;
    }
    if (!dropdownOpen || !searchResults.length) return;
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      setActiveSuggestion((current) => (current + 1) % searchResults.length);
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      setActiveSuggestion((current) => (current - 1 + searchResults.length) % searchResults.length);
    } else if (event.key === 'Enter') {
      event.preventDefault();
      void importScenario(searchResults[activeSuggestion].id);
    }
  };

  const importScenario = async (scenarioId: string) => {
    if (disabled || importingId) return;
    setImportingId(scenarioId);
    setLibraryMessage(null);
    setLibraryError(null);
    try {
      const imported = await onImportScenario(scenarioId);
      setExpandedKeys((current) => new Set(current).add(imported.key));
      setLibraryMessage(`${imported.canonicalId} added to Prompt Library`);
      setSearchResults([]);
      setDropdownOpen(false);
      setSearchQuery('');
    } catch (error: unknown) {
      setLibraryError(error instanceof Error ? error.message : 'Dataset scenario import failed');
    } finally {
      setImportingId(null);
    }
  };

  const removeScenario = (scenarioKey: string) => {
    if (disabled) return;
    setExpandedKeys((current) => {
      const next = new Set(current);
      next.delete(scenarioKey);
      return next;
    });
    setLibraryMessage(null);
    setLibraryError(null);
    onRemoveScenario(scenarioKey);
  };

  return (
    <aside className="left-scenario-rail" aria-label="Prompt library">
      <div>
        <h2 className="rail-section-header">
          <span>Prompt Library</span>
          <span className="prompt-library-count">{scenarios.length}</span>
        </h2>

        <div
          className="prompt-search-box"
          onBlur={(event) => {
            if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
              setDropdownOpen(false);
            }
          }}
        >
          <div className="prompt-search-toolbar">
            <label htmlFor="prompt-scenario-search">Find a dataset scenario</label>
            <select
              value={roleFilter}
              disabled={disabled}
              aria-label="Filter scenarios by role"
              onChange={(event) => {
                setRoleFilter(event.target.value as ScenarioRoleFilter);
                setDropdownOpen(true);
              }}
            >
              <option value="all">All roles</option>
              <option value="student">Student</option>
              <option value="lecturer">Lecturer</option>
            </select>
          </div>
          <div className="prompt-search-control">
            <Search size={13} />
            <input
              id="prompt-scenario-search"
              role="combobox"
              aria-autocomplete="list"
              aria-expanded={dropdownOpen}
              aria-controls="prompt-search-suggestions"
              aria-activedescendant={dropdownOpen && searchResults[activeSuggestion] ? `prompt-suggestion-${searchResults[activeSuggestion].id}` : undefined}
              value={searchQuery}
              maxLength={120}
              disabled={disabled}
              placeholder="MT-MAL-120 or dataset filename"
              onChange={(event) => {
                setSearchQuery(event.target.value);
                setDropdownOpen(true);
              }}
              onFocus={() => setDropdownOpen(true)}
              onKeyDown={handleSearchKeyDown}
            />
            {searching && <LoaderCircle className="spin prompt-search-spinner" size={13} aria-label="Searching dataset scenarios" />}
          </div>
          <span>Search by scenario ID or dataset filename.</span>

          {dropdownOpen && !searching && (
            <ul id="prompt-search-suggestions" className="prompt-search-results" role="listbox" aria-label="Dataset scenario suggestions">
              {searchResults.map((result, index) => (
                <li key={result.id} role="option" aria-selected={index === activeSuggestion} id={`prompt-suggestion-${result.id}`}>
                <button
                  type="button"
                  className={index === activeSuggestion ? 'active' : ''}
                  disabled={disabled || Boolean(importingId)}
                  onMouseEnter={() => setActiveSuggestion(index)}
                  onClick={() => void importScenario(result.id)}
                  aria-label={`Add ${result.id} to Prompt Library`}
                >
                  <span className="prompt-search-result-copy">
                    <strong>{result.id}</strong>
                    <small>{result.sourceFile} · {result.role}/User {result.userId} · {result.turnCount} turn{result.turnCount === 1 ? '' : 's'}</small>
                  </span>
                  {importingId === result.id ? <LoaderCircle className="spin" size={14} /> : <Plus size={14} />}
                </button>
                </li>
              ))}
              {!searchResults.length && <li className="prompt-search-empty">No matching scenarios</li>}
            </ul>
          )}
        </div>
        {libraryMessage && <div className="prompt-import-status success" role="status">{libraryMessage}</div>}
        {libraryError && <div className="prompt-import-status error" role="alert"><AlertCircle size={12} /> {libraryError}</div>}

        <div className="prompt-library-cards">
          {scenarios.map((item) => {
            const expanded = expandedKeys.has(item.key);
            const selected = item.key === selectedScenarioKey;
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
                        <span className="scenario-tag multiturn">{item.turnType === 'single' ? 'Single' : 'Multi'} · {item.turnCount}</span>
                      </span>
                      <span className="scenario-source">{item.categoryBadge}</span>
                      <span className="scenario-meta">
                        {item.role ?? 'unknown role'}{item.userId === undefined ? '' : ` · User ${item.userId}`}
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
                      const copyIdentity = `${item.key}:${turn.turnNumber}`;
                      return (
                        <li key={turn.turnNumber} className="prompt-library-item">
                          <div className="prompt-library-item-header">
                            <span>
                              <MessageSquareText size={12} /> Turn {turn.turnNumber}
                              <span className={`turn-classification ${turn.classification.toLowerCase()}`}>
                                {turn.classification}
                              </span>
                            </span>
                            <button
                              type="button"
                              className="btn-copy-prompt"
                              aria-label={`Copy ${item.canonicalId} turn ${turn.turnNumber} query`}
                              onClick={() => void copyPrompt(item.key, turn.turnNumber, turn.nlq)}
                            >
                              {copiedTurn === copyIdentity ? <Check size={12} /> : <Copy size={12} />}
                              {copiedTurn === copyIdentity ? 'Copied' : 'Copy'}
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

      <div>
        <h2 className="rail-section-header">Tool Readiness</h2>
        <div className="tool-readiness-box">
          {tools.map((tool) => (
            <div key={tool.id} className="tool-readiness-row">
              <span className="tool-name">{tool.name}</span>
              <span className={`tool-status-tag ${mode === 'direct' && bypassedInDirectMode(tool.id) ? 'bypassed' : tool.ready ? 'ready' : 'neutral'}`}>
                {mode === 'direct' && bypassedInDirectMode(tool.id) ? 'BYPASSED' : tool.ready ? 'ONLINE' : 'NEUTRAL'}
              </span>
            </div>
          ))}
        </div>
      </div>

      <div className="scientific-callout">
        <div className="scientific-callout-title">
          {mode === 'trustedsql' ? <ShieldCheck size={14} /> : <ShieldOff size={14} />}
          <strong>{mode === 'trustedsql' ? 'TrustedSQL mode' : 'Direct SQL mode'}</strong>
        </div>
        {mode === 'trustedsql'
          ? 'Document questions use Vertex AI RAG. Database questions use the complete TrustedSQL security architecture.'
          : 'Conversation Memory remains online for multiturn chat. Policy Engine and TrustedSQL are bypassed before SQL generation and read-only execution.'}
      </div>
    </aside>
  );
};
