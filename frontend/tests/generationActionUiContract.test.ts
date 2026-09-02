import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const runDetail = readFileSync(new URL('../src/components/RunDetail.tsx', import.meta.url), 'utf8');
const history = readFileSync(new URL('../src/pages/HistoryPage.tsx', import.meta.url), 'utf8');
const batch = readFileSync(new URL('../src/pages/BatchOverviewPage.tsx', import.meta.url), 'utf8');
const deterministicOracleHelper = readFileSync(
  new URL('../../test_inventory/tests/oracle/ui-interactions/helpers/oracleEvidence.ts', import.meta.url),
  'utf8',
);
const liveForceOracle = readFileSync(
  new URL('../../test_inventory/tests/oracle/ui-interactions/generation-action-force-live.spec.ts', import.meta.url),
  'utf8',
);

function section(source: string, start: string, end: string): string {
  const startIndex = source.indexOf(start);
  const endIndex = source.indexOf(end, startIndex + start.length);
  assert.notEqual(startIndex, -1, `missing start marker: ${start}`);
  assert.notEqual(endIndex, -1, `missing end marker: ${end}`);
  return source.slice(startIndex, endIndex);
}

test('all user-triggered Generation Action surfaces navigate using one response contract', () => {
  for (const [name, source] of [['Run Detail', runDetail], ['History', history], ['Batch Overview', batch]] as const) {
    assert.match(source, /generationActionDestination/,
      `${name} must use the shared response-to-destination contract`);
    assert.match(source, /navigate\(destination\.path\)/,
      `${name} must navigate to the returned follow-up object`);
    assert.doesNotMatch(source, /created \$\{result\.created_run_ids\.length\}/,
      `${name} must not claim an existing follow-up was newly created`);
    assert.match(source, /generationActionFailureMessage/,
      `${name} must distinguish a response-contract failure from an action failure`);
  }
});

test('Run Detail keeps source slide state separate and renders durable follow-up context', () => {
  const operationState = section(runDetail, 'const operationStateForSlide', 'const slideStatusTag');
  assert.doesNotMatch(operationState, /generation_history|actionKey/);
  assert.match(operationState, /slide\.status/);
  assert.match(runDetail, /Active follow-up executions/);
  assert.match(runDetail, /Follow-up execution from Run/);
  assert.match(runDetail, /TERMINAL\.has\(run\.status\) && activeFollowUpIds\.length === 0/);
});

test('Batch Overview polls nonterminal state and preserves the selected Run', () => {
  const polling = section(batch, 'const pollBatch = useCallback', 'useEffect(() => {\n    queueMicrotask');
  assert.match(batch, /preservedBatchRunId/);
  assert.match(batch, /batchLoadEpochRef/);
  assert.match(batch, /batchPollInFlightRef/);
  assert.match(polling, /selectedRunIdRef\.current = nextRunId/);
  assert.match(polling, /setSelectedRun\(nextSelectedRun\)/);
  assert.match(batch, /void pollBatch\(\)/);
  assert.doesNotMatch(
    section(batch, 'useEffect(() => {\n    if (!batch?.status', 'const selectRun'),
    /void loadBatch\(true\)/,
  );
  assert.match(batch, /window\.setInterval/);
  assert.match(batch, /terminalStatuses\.has\(batch\.status\)/);
  assert.match(batch, /Active follow-up batches/);
  assert.match(batch, /terminalStatuses\.has\(batch\.status\) && activeFollowUpBatchIdsForSource\.length === 0/);
});

test('route changes cannot render a previous Run or Batch under a new URL', () => {
  assert.match(history, /<RunDetailView key=\{id\} runId=\{Number\(id\)\}/);
  assert.match(batch, /batch\.id !== numericBatchId/);
});

test('real-provider evidence has a standalone owner outside deterministic oracle cleanup', () => {
  assert.match(deterministicOracleHelper, /fs\.rmSync\(dir, \{ recursive: true, force: true \}\)/);
  assert.match(liveForceOracle, /ANTD-GENERATION-ACTION-FORCE-LIVE-001/);
  assert.match(liveForceOracle, /payload-assertions\.json/);
  assert.match(liveForceOracle, /accessibility-snapshot\.json/);
  assert.match(liveForceOracle, /tracing\.stop\(\{ path: path\.join\(evidenceDir, 'trace\.zip'\) \}\)/);
  assert.doesNotMatch(
    liveForceOracle,
    /ANTD-GENERATION-ACTION-FORCE-ROUTE-TERMINAL-001\/live-provider/,
  );
});
