import type { GenerationActionResult, GenerationHistoryItem, Run, RunSlide } from '../types';

export type GenerationActionDestination = {
  kind: 'run' | 'batch';
  id: number;
  path: string;
  message: string;
};

export type GenerationActionLineage = {
  action: string;
  scope: string;
  forceMode: string | null;
  sourceRunId: number;
  sourceBatchId: number | null;
};

const ACTIVE_HISTORY_STATUSES = new Set(['pending', 'queued', 'running']);
const FOLLOW_UP_ACTIONS = new Set(['retry', 'auto_retry', 'force_regenerate']);

export class GenerationActionDestinationError extends Error {
  constructor() {
    super('Generation action did not return a follow-up Run or Batch');
    this.name = 'GenerationActionDestinationError';
  }
}

function positiveInteger(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null;
}

export function generationActionDestination(result: GenerationActionResult): GenerationActionDestination {
  const createdBatchId = positiveInteger(result.created_batch_id);
  if (createdBatchId) {
    return {
      kind: 'batch',
      id: createdBatchId,
      path: `/history/batch/${createdBatchId}`,
      message: `Opened follow-up Batch #${createdBatchId}`,
    };
  }

  const runIds = [...new Set(result.created_run_ids.map(positiveInteger).filter((id): id is number => id !== null))];
  if (runIds.length === 1) {
    const runId = runIds[0];
    return {
      kind: 'run',
      id: runId,
      path: `/history/run/${runId}`,
      message: `Opened follow-up Run #${runId}`,
    };
  }

  const sourceBatchId = positiveInteger(result.source_batch_id);
  if (runIds.length > 1 && sourceBatchId) {
    return {
      kind: 'batch',
      id: sourceBatchId,
      path: `/history/batch/${sourceBatchId}`,
      message: `Opened follow-up Batch #${sourceBatchId}`,
    };
  }

  throw new GenerationActionDestinationError();
}

export function generationActionFailureMessage(action: string, error: unknown): string {
  if (error instanceof GenerationActionDestinationError) {
    return 'Action started, but its follow-up destination is unavailable. Refresh History before retrying.';
  }
  return `${action.replaceAll('_', ' ')} failed: ${error instanceof Error ? error.message : String(error)}`;
}

export function activeFollowUpRunIds(slides: RunSlide[], currentRunId: number): number[] {
  const ids = new Set<number>();
  for (const slide of slides) {
    for (const item of slide.generation_history || []) {
      const createdRunId = positiveInteger(item.created_run_id);
      if (
        createdRunId
        && createdRunId !== currentRunId
        && FOLLOW_UP_ACTIONS.has(item.action)
        && ACTIVE_HISTORY_STATUSES.has(item.status)
      ) {
        ids.add(createdRunId);
      }
    }
  }
  return [...ids].sort((left, right) => left - right);
}

export function activeFollowUpBatchIds(history: GenerationHistoryItem[]): number[] {
  const ids = new Set<number>();
  for (const item of history) {
    const createdBatchId = positiveInteger(item.created_batch_id);
    if (
      createdBatchId
      && item.action === 'force_regenerate'
      && item.scope === 'batch'
      && ACTIVE_HISTORY_STATUSES.has(item.created_batch_status || item.status)
    ) {
      ids.add(createdBatchId);
    }
  }
  return [...ids].sort((left, right) => left - right);
}

export function generationActionLineage(run: Pick<Run, 'stage_artifacts'>): GenerationActionLineage | null {
  const stageArtifacts = run.stage_artifacts;
  if (!stageArtifacts || typeof stageArtifacts !== 'object') return null;
  const value = stageArtifacts.lineage;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const lineage = value as Record<string, unknown>;
  const sourceRunId = positiveInteger(lineage.source_run_id);
  const action = typeof lineage.action === 'string' ? lineage.action : '';
  const scope = typeof lineage.scope === 'string' ? lineage.scope : '';
  if (!sourceRunId || !FOLLOW_UP_ACTIONS.has(action) || !scope) return null;

  return {
    action,
    scope,
    forceMode: typeof lineage.force_mode === 'string' ? lineage.force_mode : null,
    sourceRunId,
    sourceBatchId: positiveInteger(lineage.source_batch_id),
  };
}

export function preservedBatchRunId(currentRunId: number | null, runs: Array<Pick<Run, 'id'>>): number | null {
  if (currentRunId && runs.some((run) => run.id === currentRunId)) return currentRunId;
  return runs[0]?.id ?? null;
}
