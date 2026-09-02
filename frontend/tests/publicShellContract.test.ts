import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const layout = readFileSync(new URL('../src/components/AppLayout.tsx', import.meta.url), 'utf8');
const dataPage = readFileSync(new URL('../src/pages/DataPage.tsx', import.meta.url), 'utf8');
const deckTab = readFileSync(new URL('../src/components/DeckTab.tsx', import.meta.url), 'utf8');
const generatePage = readFileSync(new URL('../src/pages/GeneratePage.tsx', import.meta.url), 'utf8');

const retiredLabels = [
  'Config',
  'Evaluations',
  'RunFail Stats',
  'Prompts',
  'Codex Sessions',
  'System Settings',
];

test('public shell has exactly three business destinations and redirects retired routes to Data', () => {
  const declaredPaths = Array.from(app.matchAll(/<Route path="([^"]+)"/g), ([, path]) => path);
  assert.deepEqual(declaredPaths, [
    '/history/run/:runId/preview',
    '/data',
    '/generate',
    '/history',
    '/history/batch/:batchId',
    '/history/run/:id',
    '/history/:id',
    '*',
  ]);
  assert.match(app, /path="\*" element=\{<Navigate to="\/data" replace \/>\}/);
  for (const retired of [
    'ConfigPage',
    'EvaluationsPage',
    'RunFailStatsPage',
    'PromptsPage',
    'CodexSessionsPage',
    '/config',
    '/evaluations',
    '/runfail',
    '/prompts',
    '/codex-sessions',
    '/system-settings',
  ]) {
    assert.equal(app.includes(retired), false, `retired route reference remains: ${retired}`);
  }
});

test('public navigation has exactly Data, Generate and History plus public branding', () => {
  const menuSection = layout.slice(layout.indexOf('const menuItems'), layout.indexOf('const AppLayout'));
  assert.deepEqual(Array.from(menuSection.matchAll(/key: '([^']+)'/g), ([, key]) => key), [
    '/data',
    '/generate',
    '/history',
  ]);
  for (const destination of ['Data', 'Generate', 'History']) assert.match(menuSection, new RegExp(destination));
  for (const label of retiredLabels) assert.equal(menuSection.includes(label), false, `retired navigation remains: ${label}`);
  assert.match(layout, /Image PPT 3\.0/);
  assert.equal(layout.includes('HTML-PPT-Gen'), false);
  assert.equal(layout.includes('Administrator'), false);
  assert.equal(layout.includes('admin'), false);
});

test('Data makes a Deck-only summary request and renders DeckTab only', () => {
  assert.deepEqual(
    Array.from(dataPage.matchAll(/api\.([a-zA-Z]+)\.([a-zA-Z]+)/g), ([, resource, method]) => `${resource}.${method}`),
    ['decks.list'],
  );
  assert.match(dataPage, /<DeckTab \/>/);
  for (const retired of ['RequirementTab', 'ColorTab', 'Requirements', 'Colors', 'DB:']) {
    assert.equal(dataPage.includes(retired), false, `retired Data surface remains: ${retired}`);
  }
});

test('public Generate requests only public decks/configs/slides and starts the fixed Image 3.0 route', () => {
  const apiCalls = Array.from(
    generatePage.matchAll(/api\.([a-zA-Z]+)\.([a-zA-Z]+)/g),
    ([, resource, method]) => `${resource}.${method}`,
  );
  assert.deepEqual([...new Set(apiCalls)], [
    'decks.list',
    'configs.list',
    'decks.getSlides',
    'generate.start',
  ]);

  assert.match(generatePage, /Image Route \(3\.0\)/);
  assert.match(generatePage, /Reference Input Map/);
  assert.match(generatePage, /PUBLIC_CONFIG_NAMES|PublicConfig/);
  assert.match(generatePage, /Codex Native Image 3\.0/);
  assert.match(generatePage, /Codex Native Image 3\.0 Luna Low Director/);

  for (const retiredControl of [
    'HTML Route',
    'Generation Mode',
    'Image strategy',
    'Auto',
    'Manual',
    'Select requirement',
    'Select color',
    'Select prompt',
    'Select model',
    'Select renderer',
    'Select profile',
    'image_1_0',
    'image_3_2',
    'image_5_0',
    'image_direct',
  ]) {
    assert.equal(generatePage.includes(retiredControl), false, `retired Generate control remains: ${retiredControl}`);
  }
});

test('public Generate gates on two slides and submits the exact six-field payload', () => {
  assert.match(generatePage, /slideCount\s*>=\s*2/);

  const payloadMatch = generatePage.match(/const payload(?:\s*:\s*[^=]+)?\s*=\s*\{([\s\S]*?)\};\s*const result = await api\.generate\.start\(payload\)/);
  assert.ok(payloadMatch, 'fixed generation payload literal is missing');
  const payloadKeys = Array.from(payloadMatch[1].matchAll(/^\s*([a-z_]+):/gm), ([, key]) => key);
  assert.deepEqual(payloadKeys, [
    'deck_id',
    'config_id',
    'engine',
    'strategy',
    'requirement_ids',
    'color_ids',
  ]);
  assert.match(payloadMatch[1], /engine:\s*'image'/);
  assert.match(payloadMatch[1], /strategy:\s*'image_3_0'/);
  assert.match(payloadMatch[1], /requirement_ids:\s*\[\]/);
  assert.match(payloadMatch[1], /color_ids:\s*\[\]/);
  assert.equal(payloadMatch[1].includes('reference_map'), false, 'Reference Map must remain display-only');
});

test('Deck retains CRUD, folders, lifecycle and deterministic split without AutoSplit/settings', () => {
  for (const retained of [
    'api.decks.list',
    'api.decks.create',
    'api.decks.update',
    'api.decks.delete',
    'api.decks.archive',
    'api.decks.restore',
    'api.decks.forceDelete',
    'api.decks.assignFolders',
    "api.folders.list('deck')",
    'DataFolderControls',
    "scope=\"deck\"",
    "entity_type: 'deck'",
    'LifecycleStatus',
    'api.decks.split(id)',
    'Split deck into slides',
  ]) {
    assert.equal(deckTab.includes(retained), true, `required Deck capability missing: ${retained}`);
  }
  for (const retired of [
    'ApiError',
    'AutoSplitSettings',
    'DeckSplitDraft',
    'RobotOutlined',
    'api.autoSplitSettings',
    'createSplitDraft',
    'retrySplitDraft',
    'confirmSplitDraft',
    'deleteSplitDraft',
    'Auto Split',
    "navigate('/config')",
  ]) {
    assert.equal(deckTab.includes(retired), false, `retired AutoSplit/settings path remains: ${retired}`);
  }
});
