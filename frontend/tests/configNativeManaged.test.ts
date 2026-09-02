import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const configPageSource = readFileSync(new URL('../src/pages/ConfigPage.tsx', import.meta.url), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/^\s*\/\/.*$/gm, '');

const section = (startMarker: string, endMarker: string) => {
  const start = configPageSource.indexOf(startMarker);
  const end = configPageSource.indexOf(endMarker, start + startMarker.length);
  assert.notEqual(start, -1, `Missing ConfigPage section starting ${startMarker}`);
  assert.notEqual(end, -1, `Missing ConfigPage section ending ${endMarker}`);
  return configPageSource.slice(start, end);
};

const handler = (name: string) => {
  const startMarker = `const ${name} =`;
  const start = configPageSource.indexOf(startMarker);
  assert.notEqual(start, -1, `Missing ${name} handler`);
  const nextHandler = configPageSource.indexOf('\n  const ', start + startMarker.length);
  return configPageSource.slice(start, nextHandler === -1 ? undefined : nextHandler);
};

const expectManagedReturnBefore = (source: string, apiCall: string, action: string) => {
  const apiCallIndex = source.indexOf(apiCall);
  assert.notEqual(apiCallIndex, -1, `Missing ${apiCall} call for ${action}`);
  const beforeApiCall = source.slice(0, apiCallIndex);
  assert.match(
    beforeApiCall,
    /if\s*\([\s\S]{0,160}(?:system_managed|isSystemManaged)[\s\S]{0,280}return\s*;/,
    `${action} must return before ${apiCall} for a system-managed Native entry`,
  );
};

const expectManagedControlBlocked = (source: string, recordName: string, action: string) => {
  const managedGate = `(?:isSystemManaged\\(\\s*${recordName}\\s*\\)|${recordName}\\.system_managed)`;
  const disabled = new RegExp(`disabled=\\{[^}]*${managedGate}[^}]*\\}`);
  const unavailable = new RegExp(`!${managedGate}\\s*&&[\\s\\S]{0,220}onClick`);
  assert.ok(
    disabled.test(source) || unavailable.test(source),
    `${action} must be disabled or unavailable for a system-managed Native entry`,
  );
};

test('system-managed Config entries visibly explain the Codex login boundary', () => {
  assert.ok(
    /Managed by Codex login;\s*validate through audited Native preflight/.test(configPageSource),
    'ConfigPage must explain that Native profiles and combinations are managed through Codex login',
  );
  assert.ok(
    /system_managed/.test(configPageSource),
    'ConfigPage must consume the server-owned system-managed marker rather than infer Native status from a label',
  );
});

test('system-managed Native profile actions are unavailable and cannot submit generic Test or update requests', () => {
  const profileColumns = section('const profileColumns =', 'const configColumns =');
  expectManagedControlBlocked(profileColumns, 'record', 'Native profile Edit');
  assert.match(profileColumns, /onClick=\{\(\) => openEditProfile\(record\)\}/, 'Legacy profile Edit must remain wired');

  expectManagedReturnBefore(handler('openEditProfile'), 'setProfileModalOpen(true)', 'Native profile Edit');
  expectManagedReturnBefore(handler('testProfile'), 'api.modelProfiles.test', 'Native profile Test');
  expectManagedReturnBefore(handler('saveProfile'), 'api.modelProfiles.update', 'Native profile Save');

  assert.doesNotMatch(
    profileColumns,
    /api\.modelProfiles\.delete/,
    'The existing workbench has no profile Delete control; Native profile Delete must remain unavailable rather than add a mutation path',
  );
});

test('system-managed Native config Edit and Delete are disabled and cannot submit mutations', () => {
  const configColumns = section('const configColumns =', '\n\n  return (\n    <div className="config-page">');
  const configEditControl = section('<Tooltip title="Edit combination">', '</Tooltip>');
  const configDeleteControl = section('<Popconfirm title="Delete this combination?"', '</Popconfirm>');
  expectManagedControlBlocked(configEditControl, 'record', 'Native config Edit');
  expectManagedControlBlocked(configDeleteControl, 'record', 'Native config Delete');
  assert.match(
    configColumns,
    /<Popconfirm title="Delete this combination\?"[\s\S]{0,360}onConfirm=\{\(\) => deleteConfig\(record(?:\.id)?\)\}/,
    'Config Delete must keep the current handler for legacy rows',
  );
  expectManagedReturnBefore(handler('openEditConfig'), 'setConfigModalOpen(true)', 'Native config Edit');
  expectManagedReturnBefore(handler('saveConfig'), 'api.configs.update', 'Native config Save');
  expectManagedReturnBefore(handler('deleteConfig'), 'api.configs.delete', 'Native config Delete');
});

test('system-managed Native Generation Routes editing and saving are blocked before route-map update', () => {
  const routes = section('key: \'routes\'', 'key: \'system-settings\'');
  const routeEditor = section('aria-label="Route map editor"', '{selectedRouteConfig && (');
  const routeSaveControl = section('<Button type="primary" onClick={saveRouteDraft}', '</Button>');
  expectManagedControlBlocked(routeEditor, 'selectedRouteConfig', 'Native Generation Routes editing');
  expectManagedControlBlocked(routeSaveControl, 'selectedRouteConfig', 'Native Generation Routes Save');
  assert.match(routes, /onChange=\{\(value\) => \{[\s\S]{0,180}setRouteDirty\(true\)/, 'Legacy Generation Routes editing must remain wired');
  assert.match(routes, /onClick=\{saveRouteDraft\}/, 'Legacy Generation Routes Save must remain wired');
  expectManagedReturnBefore(handler('saveRouteDraft'), 'api.configs.update', 'Native Generation Routes Save');
});

test('legacy profile/config actions and the existing Set Default action remain available', () => {
  const configColumns = section('const configColumns =', '\n\n  return (\n    <div className="config-page">');
  const setDefaultControl = section('<Tooltip title="Set as default">', '</Tooltip>');

  assert.match(configPageSource, /onClick=\{testProfile\}/, 'Legacy profile Test must remain available through the existing profile form');
  assert.match(configColumns, /onClick=\{\(\) => openEditConfig\(record\)\}/, 'Legacy config Edit must remain wired');
  assert.match(configColumns, /onConfirm=\{\(\) => deleteConfig\(record(?:\.id)?\)\}/, 'Legacy config Delete must remain wired');
  assert.match(setDefaultControl, /onClick=\{\(\) => setDefaultConfig\(record\.id\)\}/, 'Set Default must remain wired where it already exists');
  assert.doesNotMatch(setDefaultControl, /system_managed|isSystemManaged/, 'Set Default must not be disabled merely because a config is system-managed');
});
