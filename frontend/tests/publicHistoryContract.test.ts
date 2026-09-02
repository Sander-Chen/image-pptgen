import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const history = readFileSync(new URL('../src/pages/HistoryPage.tsx', import.meta.url), 'utf8');
const batch = readFileSync(new URL('../src/pages/BatchOverviewPage.tsx', import.meta.url), 'utf8');

const forbiddenHistorySurface = [
  'generationActions',
  'bulkActions',
  'artifactVersions',
  'Force',
  'Retry',
  'MQA',
  'Evaluation',
  'HTML',
  'ImageDirect',
  'activeFollowUpBatchIds',
  'generation_history',
  'PresentationPreview',
];

test('public history is a read-only Image 3.0 surface', () => {
  for (const source of [history, batch]) {
    for (const retired of forbiddenHistorySurface) {
      assert.equal(source.includes(retired), false, `retired history surface remains: ${retired}`);
    }
  }
});

test('public history uses only allowlisted read/download API calls', () => {
  for (const source of [history, batch]) {
    const calls = Array.from(source.matchAll(/api\.([a-zA-Z]+)\.([a-zA-Z]+)/g), ([, resource, method]) => `${resource}.${method}`);
    for (const call of calls) {
      assert.deepEqual(
        [
          'batches.list',
          'batches.get',
          'batches.download',
          'runs.get',
          'runs.download',
        ].includes(call),
        true,
        `non-public history API call remains: ${call}`,
      );
    }
  }
});

test('public history keeps status/progress, run detail, and PNG artifact access', () => {
  assert.match(history, /batchProgress/);
  assert.match(history, /statusColorMap/);
  assert.match(history, /progress/);
  assert.match(history, /\/history\/run\/\$\{run\.id\}/);
  assert.match(history, /batches\.download/);
  assert.match(history, /runs\.download/);
  assert.match(batch, /final_image_path/);
  assert.match(batch, /toArtifactUrl/);
  assert.match(batch, /\/history\/run\/\$\{selectedRun\.id\}/);
});

test('public history labels the fixed route and does not render unknown DTO fields', () => {
  for (const source of [history, batch]) {
    assert.match(source, /Image Route \(3\.0\)/);
    assert.match(source, /Public(Batch|Run)/);
  }
  assert.doesNotMatch(history, /record\.requirements|record\.colors|record\.route_metadata/);
  assert.doesNotMatch(batch, /batch\.generation_mode|batch\.requirements|batch\.colors|run\.route_metadata/);
});
