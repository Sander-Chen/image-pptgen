import assert from 'node:assert/strict';
import test from 'node:test';

import {
  activeFollowUpBatchIds,
  activeFollowUpRunIds,
  generationActionFailureMessage,
  generationActionDestination,
  generationActionLineage,
  preservedBatchRunId,
} from '../src/lib/generationActionNavigation';
import type { GenerationActionResult, Run, RunSlide } from '../src/types';

function actionResult(overrides: Partial<GenerationActionResult> = {}): GenerationActionResult {
  return {
    ok: true,
    action: 'force_regenerate',
    scope: 'run',
    target_id: 7,
    source_batch_id: 3,
    created_batch_id: null,
    created_run_ids: [],
    affected_slide_ids: [],
    launched_run_ids: [],
    skipped: [],
    ...overrides,
  };
}

test('new Batch is the authoritative action destination', () => {
  assert.deepEqual(
    generationActionDestination(actionResult({ created_batch_id: 12, created_run_ids: [40, 41] })),
    { kind: 'batch', id: 12, path: '/history/batch/12', message: 'Opened follow-up Batch #12' },
  );
});

test('one returned Run opens that follow-up Run', () => {
  assert.deepEqual(
    generationActionDestination(actionResult({ created_run_ids: [44] })),
    { kind: 'run', id: 44, path: '/history/run/44', message: 'Opened follow-up Run #44' },
  );
});

test('multiple returned Runs open their source Batch', () => {
  assert.deepEqual(
    generationActionDestination(actionResult({ created_run_ids: [44, 45], source_batch_id: 3 })),
    { kind: 'batch', id: 3, path: '/history/batch/3', message: 'Opened follow-up Batch #3' },
  );
});

test('invalid action response cannot manufacture a success destination', () => {
  let caught: unknown;
  try {
    generationActionDestination(actionResult());
  } catch (error) {
    caught = error;
  }
  assert.match(String(caught), /did not return a follow-up Run or Batch/);
  assert.equal(
    generationActionFailureMessage('force_regenerate', caught),
    'Action started, but its follow-up destination is unavailable. Refresh History before retrying.',
  );
  assert.equal(
    generationActionFailureMessage('retry', new Error('network down')),
    'retry failed: network down',
  );
});

test('active source histories expose each distinct follow-up once', () => {
  const slides = [
    {
      id: 1,
      run_id: 7,
      slide_id: 1,
      position: 1,
      status: 'completed',
      generation_history: [
        { id: 1, action: 'force_regenerate', scope: 'run', created_run_id: 44, status: 'running' },
        { id: 2, action: 'force_regenerate', scope: 'run', created_run_id: 44, status: 'running' },
        { id: 3, action: 'retry', scope: 'slide', created_run_id: 45, status: 'queued' },
      ],
    },
    {
      id: 2,
      run_id: 7,
      slide_id: 2,
      position: 2,
      status: 'completed',
      generation_history: [
        { id: 4, action: 'force_regenerate', scope: 'run', created_run_id: 44, status: 'success' },
        { id: 5, action: 'initial_generation', scope: 'slide', created_run_id: 99, status: 'running' },
      ],
    },
  ] as RunSlide[];

  assert.deepEqual(activeFollowUpRunIds(slides, 7), [44, 45]);
});

test('child lineage is read only from the durable nested lineage object', () => {
  const run = {
    id: 44,
    stage_artifacts: {
      lineage: {
        action: 'force_regenerate',
        scope: 'run',
        force_mode: 'overwrite_current',
        source_target_id: 7,
        source_run_id: 7,
        source_batch_id: 3,
        source_run_slide_ids: [10, 11],
        version_index: 2,
        retention_policy: { mode: 'preserve_source', cleanup: 'manual_future_task' },
      },
    },
  } as Run;

  assert.deepEqual(generationActionLineage(run), {
    action: 'force_regenerate',
    scope: 'run',
    forceMode: 'overwrite_current',
    sourceRunId: 7,
    sourceBatchId: 3,
  });
  assert.equal(generationActionLineage({ id: 7 } as Run), null);
});

test('Batch refresh preserves the selected Run while it remains available', () => {
  const runs = [{ id: 44 }, { id: 45 }] as Run[];
  assert.equal(preservedBatchRunId(45, runs), 45);
  assert.equal(preservedBatchRunId(99, runs), 44);
  assert.equal(preservedBatchRunId(null, []), null);
});

test('source Batch history exposes each active follow-up Batch once', () => {
  assert.deepEqual(activeFollowUpBatchIds([
    { id: 1, action: 'force_regenerate', scope: 'batch', target_id: 3, created_batch_id: 12, created_batch_status: 'running', status: 'queued' },
    { id: 2, action: 'force_regenerate', scope: 'batch', target_id: 3, created_batch_id: 12, created_batch_status: 'running', status: 'running' },
    { id: 3, action: 'force_regenerate', scope: 'batch', target_id: 3, created_batch_id: 13, created_batch_status: 'completed', status: 'queued' },
  ]), [12]);
});
