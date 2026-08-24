import React from 'react';
import { AlertTriangle, CheckCircle2, GitCompare, Rows3, XCircle } from 'lucide-react';
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

const ResultOutput: React.FC<{
  kind: 'actual' | 'expected';
  columns: string[];
  rows: ComparisonCell[][];
  totalRows: number;
}> = ({ kind, columns, rows, totalRows }) => {
  const labels = columns.length
    ? columns
    : Array.from({ length: rows[0]?.length ?? 0 }, (_, index) => `value_${index + 1}`);

  return (
    <section className={`comparison-output ${kind}`} aria-label={kind === 'actual' ? 'Actual runtime output' : 'Expected dataset output'}>
      <div className="comparison-output-header">
        <div>
          <span>{kind === 'actual' ? 'ACTUAL' : 'EXPECTED'}</span>
          <strong>{kind === 'actual' ? 'Runtime output' : 'Dataset output'}</strong>
        </div>
        <small><Rows3 size={11} /> {totalRows} row{totalRows === 1 ? '' : 's'}</small>
      </div>

      <div className="comparison-output-table-wrap">
        <table className="comparison-output-table">
          <thead>
            <tr>{labels.map((column, index) => <th key={`${column}-${index}`} scope="col">{column}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, rowIndex) => (
              <tr key={`${rowIndex}-${JSON.stringify(row)}`}>
                {row.map((cell, cellIndex) => <td key={cellIndex}>{cellText(cell)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
        {!rows.length && <div className="comparison-output-empty">Empty result set</div>}
      </div>
      {totalRows > rows.length && <div className="comparison-output-more">+{totalRows - rows.length} more rows</div>}
    </section>
  );
};

export const ExecutionComparison: React.FC<ExecutionComparisonProps> = ({ comparison }) => {
  if (!comparison.available) {
    return (
      <section className="execution-comparison unavailable" aria-label="Dataset execution comparison unavailable">
        <div className="execution-match-summary unavailable">
          <AlertTriangle size={18} />
          <div>
            <strong>Expected-result comparison unavailable</strong>
            <span>{comparison.datasetId} · Turn {comparison.datasetTurn}</span>
          </div>
        </div>
        <p>{comparison.reason}</p>
      </section>
    );
  }

  const exact = Boolean(comparison.exactMatch);
  const score = comparison.score?.toFixed(2) ?? '—';
  const matchedRows = comparison.matchedRowCount ?? 0;
  const expectedRows = comparison.canonicalExpectedRowCount ?? comparison.expectedRowCount ?? 0;

  return (
    <section className={`execution-comparison ${exact ? 'exact' : 'different'}`} aria-label="Runtime and expected dataset result comparison">
      <div className={`execution-match-summary ${exact ? 'pass' : 'fail'}`}>
        {exact ? <CheckCircle2 size={20} /> : <XCircle size={20} />}
        <div className="execution-match-copy">
          <strong>{exact ? 'Matches expected result' : 'Does not match expected result'}</strong>
          <span>{comparison.datasetId} · Turn {comparison.datasetTurn} · {matchedRows}/{expectedRows} expected rows matched</span>
        </div>
        <div className="execution-metric" aria-label={`${comparison.metric} score ${score}`}>
          <span>{comparison.metric}</span>
          <strong>{score}</strong>
          <small>{exact ? 'MATCH' : 'NO MATCH'}</small>
        </div>
      </div>

      <details className="comparison-detail-details">
        <summary>
          <span><GitCompare size={13} /> View comparison details</span>
          <small>Actual vs expected</small>
        </summary>
        <div className="comparison-detail-content">
          <ResultOutput
            kind="actual"
            columns={comparison.runtimeColumns ?? []}
            rows={comparison.runtimePreviewRows ?? []}
            totalRows={comparison.runtimeRowCount ?? 0}
          />
          <ResultOutput
            kind="expected"
            columns={comparison.expectedColumns ?? []}
            rows={comparison.expectedPreviewRows ?? []}
            totalRows={comparison.expectedRowCount ?? 0}
          />
        </div>
      </details>
    </section>
  );
};
