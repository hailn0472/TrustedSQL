import React from 'react';
import { AlertTriangle, ArrowRight, CheckCircle2, Equal, GitCompare, Rows3, XCircle } from 'lucide-react';
import { ComparisonCell, ExecutionComparisonDto } from '../api/client';

interface ExecutionComparisonProps {
  comparison: ExecutionComparisonDto;
}

const cellText = (cell: ComparisonCell): string => {
  if (Array.isArray(cell)) return JSON.stringify(cell);
  if (typeof cell === 'number' && !Number.isInteger(cell)) {
    return cell.toLocaleString(undefined, { maximumFractionDigits: 6 });
  }
  return String(cell);
};

const ResultPreview: React.FC<{
  kind: 'actual' | 'expected';
  columns: string[];
  rows: ComparisonCell[][];
  totalRows: number;
}> = ({ kind, columns, rows, totalRows }) => {
  const labels = columns.length ? columns : Array.from({ length: rows[0]?.length ?? 0 }, (_, index) => `value_${index + 1}`);
  const template = { gridTemplateColumns: `repeat(${Math.max(1, labels.length)}, minmax(0, 1fr))` };
  return (
    <div className={`result-preview ${kind}`}>
      <div className="result-preview-header">
        <div>
          <span className="result-preview-kicker">{kind === 'actual' ? 'ACTUAL' : 'EXPECTED'}</span>
          <strong>{kind === 'actual' ? 'Runtime result' : 'Dataset result'}</strong>
        </div>
        <span className="result-preview-count"><Rows3 size={11} /> {totalRows} row{totalRows === 1 ? '' : 's'}</span>
      </div>
      <div className="result-preview-table">
        <div className="result-preview-columns" style={template}>
          {labels.map((column, index) => <code key={`${column}-${index}`} title={column}>{column}</code>)}
        </div>
        {rows.map((row, rowIndex) => (
          <div className="result-preview-row" style={template} key={`${rowIndex}-${JSON.stringify(row)}`}>
            {row.map((cell, cellIndex) => <strong key={cellIndex} title={String(cell)}>{cellText(cell)}</strong>)}
          </div>
        ))}
        {!rows.length && <div className="result-preview-empty">Empty result set</div>}
      </div>
      {totalRows > rows.length && <div className="result-preview-more">+{totalRows - rows.length} more canonical rows</div>}
    </div>
  );
};

const DifferenceRows: React.FC<{ title: string; rows: ComparisonCell[][]; kind: 'missing' | 'unexpected' }> = ({ title, rows, kind }) => (
  <div className={`result-difference-list ${kind}`}>
    <span>{title}</span>
    {rows.length ? rows.map((row, index) => (
      <code key={`${index}-${JSON.stringify(row)}`}>{row.map(cellText).join(' · ')}</code>
    )) : <small>None</small>}
  </div>
);

export const ExecutionComparison: React.FC<ExecutionComparisonProps> = ({ comparison }) => {
  if (!comparison.available) {
    return (
      <section className="execution-comparison unavailable" aria-label="Dataset execution comparison unavailable">
        <div className="execution-comparison-header">
          <div>
            <div className="execution-comparison-title"><AlertTriangle size={14} /> Dataset comparison unavailable</div>
            <div className="execution-comparison-dataset">{comparison.datasetId} · Turn {comparison.datasetTurn}</div>
          </div>
        </div>
        <p>{comparison.reason}</p>
      </section>
    );
  }

  const exact = Boolean(comparison.exactMatch);
  const runtimeColumns = comparison.runtimeColumns ?? [];
  const expectedColumns = comparison.expectedColumns ?? [];
  const runtimeRows = comparison.runtimePreviewRows ?? [];
  const expectedRows = comparison.expectedPreviewRows ?? [];
  const columnPairs = Array.from({ length: Math.max(runtimeColumns.length, expectedColumns.length) }, (_, index) => ({
    actual: runtimeColumns[index],
    expected: expectedColumns[index],
  }));
  const aliasesDiffer = columnPairs.some((pair) => pair.actual !== pair.expected);

  return (
    <section className={`execution-comparison ${exact ? 'exact' : 'different'}`} aria-label="Runtime and expected dataset result comparison">
      <div className="execution-comparison-header">
        <div>
          <div className="execution-comparison-title"><GitCompare size={15} /> Runtime vs expected result</div>
          <div className="execution-comparison-dataset">{comparison.datasetId} · Turn {comparison.datasetTurn}</div>
        </div>
        <div className={`execution-score ${exact ? 'pass' : 'fail'}`}>
          <span>{comparison.metric}</span>
          <strong>{comparison.score?.toFixed(2)}</strong>
          <small>{exact ? 'EXACT MATCH' : 'DIFFERENT'}</small>
        </div>
      </div>

      <div className="result-comparison-hero">
        <ResultPreview kind="actual" columns={runtimeColumns} rows={runtimeRows} totalRows={comparison.runtimeRowCount ?? 0} />
        <div className={`result-comparison-symbol ${exact ? 'equal' : 'different'}`} aria-label={exact ? 'Results are equal' : 'Results are different'}>
          {exact ? <Equal size={25} /> : <XCircle size={24} />}
          <strong>{exact ? 'SAME' : 'DIFF'}</strong>
        </div>
        <ResultPreview kind="expected" columns={expectedColumns} rows={expectedRows} totalRows={comparison.expectedRowCount ?? 0} />
      </div>

      <div className={`execution-verdict ${exact ? 'pass' : 'fail'}`}>
        {exact ? <CheckCircle2 size={16} /> : <XCircle size={16} />}
        <div>
          <strong>{exact ? 'The returned values match the expected values.' : 'The returned values do not match the expected values.'}</strong>
          <span>{comparison.matchedRowCount}/{comparison.canonicalExpectedRowCount} canonical expected rows matched · Value overlap {Math.round((comparison.softF1 ?? 0) * 100)}%</span>
        </div>
      </div>

      <div className="column-alignment-panel">
        <div className="column-alignment-title">
          <span>Column alignment by position</span>
          {aliasesDiffer && <small>Labels differ, but ST-EX compares the ordered row values.</small>}
        </div>
        <div className="column-alignment-list">
          {columnPairs.map((pair, index) => (
            <div key={`${index}-${pair.actual}-${pair.expected}`} className={pair.actual === pair.expected ? 'same' : 'alias'}>
              <code>{pair.actual ?? '—'}</code>
              <ArrowRight size={12} />
              <code>{pair.expected ?? '—'}</code>
              <span>{pair.actual === pair.expected ? 'same label' : 'alias differs'}</span>
            </div>
          ))}
        </div>
      </div>

      {!exact && (
        <div className="result-difference-grid">
          <DifferenceRows title="Expected but missing" rows={comparison.missingRows ?? []} kind="missing" />
          <DifferenceRows title="Returned but unexpected" rows={comparison.unexpectedRows ?? []} kind="unexpected" />
        </div>
      )}

      <div className="comparison-rule-note">
        {comparison.rule}. Row order and duplicate multiplicity are ignored; value order inside each row is preserved.
      </div>
    </section>
  );
};
