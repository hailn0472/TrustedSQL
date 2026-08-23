import React, { useEffect, useMemo, useState } from 'react';
import { ExecutionMode, RouteEvidence } from '../app/types';
import { RoutingMap } from './RoutingMap';
import { TelemetryStream, StreamStatus } from './TelemetryStream';
import { TelemetryItem, RunStatus, TurnRuntimeSnapshot } from '../state/demoReducer';

interface OperationsRailProps {
  routeEvidence?: RouteEvidence;
  telemetryEvents?: TelemetryItem[];
  turnRuntimeSnapshots?: Record<number, TurnRuntimeSnapshot>;
  activeTurnNumber?: number;
  streamStatus?: StreamStatus;
  executionState?: RunStatus | 'unknown';
  telemetryError?: string | null;
  mode: ExecutionMode;
}

export const OperationsRail: React.FC<OperationsRailProps> = ({
  routeEvidence,
  telemetryEvents = [],
  turnRuntimeSnapshots = {},
  activeTurnNumber,
  streamStatus = 'idle',
  executionState = 'idle',
  telemetryError = null,
  mode,
}) => {
  const availableTurns = useMemo(
    () => Object.values(turnRuntimeSnapshots).sort((left, right) => left.turnNumber - right.turnNumber),
    [turnRuntimeSnapshots],
  );
  const [selectedTurnNumber, setSelectedTurnNumber] = useState<number | undefined>(activeTurnNumber);

  useEffect(() => {
    if (activeTurnNumber !== undefined) setSelectedTurnNumber(activeTurnNumber);
  }, [activeTurnNumber]);

  useEffect(() => {
    if (selectedTurnNumber !== undefined && !turnRuntimeSnapshots[selectedTurnNumber]) {
      setSelectedTurnNumber(availableTurns.at(-1)?.turnNumber);
    }
  }, [availableTurns, selectedTurnNumber, turnRuntimeSnapshots]);

  const selectedSnapshot = selectedTurnNumber === undefined
    ? undefined
    : turnRuntimeSnapshots[selectedTurnNumber];
  const mapEvidence = selectedSnapshot?.routeEvidence
    ?? (selectedTurnNumber === activeTurnNumber ? routeEvidence : undefined);
  const mapEvents = selectedSnapshot?.telemetryEvents
    ?? (selectedTurnNumber === activeTurnNumber ? telemetryEvents : []);
  const mapMode = selectedSnapshot?.mode ?? mode;
  const mapRunState = selectedSnapshot?.runState
    ?? (selectedTurnNumber === activeTurnNumber && executionState !== 'unknown' ? executionState : undefined);

  return (
    <aside className="right-operations-rail" aria-label="Operations and Telemetry">
      <RoutingMap
        mode={mapMode}
        evidence={mapEvidence}
        events={mapEvents}
        runState={mapRunState}
        turnOptions={availableTurns}
        selectedTurnNumber={selectedTurnNumber}
        onSelectTurn={setSelectedTurnNumber}
      />
      <TelemetryStream
        mode={mode}
        events={telemetryEvents}
        streamStatus={streamStatus}
        executionState={executionState}
        error={telemetryError}
      />
    </aside>
  );
};
