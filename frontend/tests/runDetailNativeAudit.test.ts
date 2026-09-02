import assert from 'node:assert/strict';
import test from 'node:test';

import React from 'react';
import { createServer, type ViteDevServer } from 'vite';

import type { CodexAuditInvocationDetail, CodexRunAudit, NativeRunDetail, RunDetail } from '../src/types';
import {
  NATIVE_AUDIT_DETAIL_CREDENTIAL_SENTINELS,
  nativeAuditDetailFixture,
  nativeRunFailureFixture,
  nativeRunSuccessFixture,
} from './nativeRunFixtures';

type ElementNode = {
  type: unknown;
  props: Record<string, unknown>;
};

type AuditPanelComponent = React.FC<{
  audit?: CodexRunAudit | null;
  modelCallMetadata: unknown;
}>;

type RunDetailViewComponent = React.FC<{ runId: number }>;

type AuditCollapse = ElementNode & {
  props: {
    activeKey?: string | string[];
    defaultActiveKey?: string | string[];
    items: Array<{ key: string | number; label: unknown; children: unknown }>;
    onChange?: (keys: string | string[]) => void | Promise<void>;
  };
};

let vite: ViteDevServer | null = null;
let CodexAuditPanel: AuditPanelComponent;
let RunDetailView: RunDetailViewComponent;
let api: typeof import('../src/api').api;

test.before(async () => {
  vite = await createServer({
    root: new URL('../', import.meta.url).pathname,
    configFile: false,
    appType: 'custom',
    logLevel: 'error',
    optimizeDeps: { noDiscovery: true },
    ssr: { noExternal: ['antd', '@ant-design/icons', 'react-router-dom'] },
    plugins: [
      {
        name: 'nimg-050c-inert-run-detail-ui',
        enforce: 'pre',
        resolveId(id) {
          if (id === 'antd') return '\0nimg-050c-antd';
          if (id === '@ant-design/icons') return '\0nimg-050c-icons';
          if (id === 'react-router-dom') return '\0nimg-050c-router';
          return null;
        },
        load(id) {
          if (id === '\0nimg-050c-icons') {
            return 'export const ArrowLeftOutlined = "ArrowLeftOutlined"; export const CopyOutlined = "CopyOutlined"; export const DownloadOutlined = "DownloadOutlined"; export const ExpandOutlined = "ExpandOutlined"; export const FileSearchOutlined = "FileSearchOutlined"; export const ReloadOutlined = "ReloadOutlined"; export const SearchOutlined = "SearchOutlined";';
          }
          if (id === '\0nimg-050c-router') return 'export const useNavigate = () => () => {};';
          if (id === '\0nimg-050c-antd') {
            return `
              export const Descriptions = Object.assign(({ children }) => children, { Item: ({ label, children }) => [label, children] }); export const Card = 'Card'; export const Tabs = 'Tabs';
              export const Tag = 'Tag'; export const Button = 'Button'; export const Image = 'Image';
              export const Alert = ({ message, description, action }) => [message, description, action]; export const Spin = 'Spin'; export const Segmented = 'Segmented';
              export const Slider = 'Slider'; export const Space = 'Space'; export const Collapse = 'Collapse';
              export const Select = 'Select'; export const Modal = 'Modal'; export const Drawer = 'Drawer';
              export const Input = 'Input'; export const Table = ({ columns = [] }) => columns.map((column) => column.title); export const Empty = 'Empty';
              export const message = { success() {}, error() {}, warning() {}, info() {} };
            `;
          }
          return null;
        },
        transform(code, id) {
          if (id.endsWith('/src/components/RunDetail.tsx')) {
            return `${code}\nexport { CodexAuditPanel, RunDetailView };`;
          }
          return null;
        },
      },
    ],
  });
  const module = await vite.ssrLoadModule('/src/components/RunDetail.tsx');
  const apiModule = await vite.ssrLoadModule('/src/api.ts');
  CodexAuditPanel = module.CodexAuditPanel as AuditPanelComponent;
  RunDetailView = module.RunDetailView as RunDetailViewComponent;
  api = apiModule.api as typeof api;
});

test.after(async () => {
  await vite?.close();
});

function reactInternals() {
  return (React as unknown as {
    __CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE: { H: unknown };
  }).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
}

function createHookHarness(initialValues: unknown[] = []) {
  const values = [...initialValues];

  const render = <T,>(component: () => T): T => {
    let hookIndex = 0;
    const internals = reactInternals();
    const previous = internals.H;
    const nextValue = <V,>(initial: V | (() => V)): V => {
      const index = hookIndex++;
      if (index >= values.length) values[index] = typeof initial === 'function' ? (initial as () => V)() : initial;
      return values[index] as V;
    };
    const dispatcher = {
      useState<V>(initial: V | (() => V)) {
        const index = hookIndex;
        const value = nextValue(initial);
        const setValue = (update: V | ((current: V) => V)) => {
          const current = values[index] as V;
          values[index] = typeof update === 'function' ? (update as (current: V) => V)(current) : update;
        };
        return [value, setValue] as const;
      },
      useReducer(reducer: (state: unknown, action: unknown) => unknown, initial: unknown, init?: (value: unknown) => unknown) {
        const index = hookIndex;
        const value = nextValue(init ? () => init(initial) : initial);
        return [value, (action: unknown) => { values[index] = reducer(values[index], action); }] as const;
      },
      useEffect() { hookIndex += 1; },
      useLayoutEffect() { hookIndex += 1; },
      useInsertionEffect() { hookIndex += 1; },
      useMemo(factory: () => unknown) { hookIndex += 1; return factory(); },
      useCallback(callback: unknown) { hookIndex += 1; return callback; },
      useRef(initial: unknown) { return { current: nextValue(initial) }; },
      useContext(context: { _currentValue?: unknown }) { hookIndex += 1; return context._currentValue; },
      useImperativeHandle() { hookIndex += 1; },
      useDebugValue() { hookIndex += 1; },
      useDeferredValue(value: unknown) { hookIndex += 1; return value; },
      useTransition() { hookIndex += 1; return [false, (callback: () => void) => callback()] as const; },
      useId() { return `nimg-050c-${hookIndex++}`; },
      useSyncExternalStore(_subscribe: unknown, getSnapshot: () => unknown) { hookIndex += 1; return getSnapshot(); },
      useOptimistic(value: unknown) { hookIndex += 1; return [value, () => {}] as const; },
      useActionState(action: unknown, initial: unknown) { hookIndex += 1; return [initial, action, false] as const; },
      useEffectEvent(callback: unknown) { hookIndex += 1; return callback; },
      useMemoCache(size: number) { hookIndex += 1; return Array.from({ length: size }); },
      useCacheRefresh() { hookIndex += 1; return () => {}; },
    };
    internals.H = dispatcher;
    try {
      return component();
    } finally {
      internals.H = previous;
    }
  };

  return { render };
}

function isElement(node: unknown): node is ElementNode {
  return React.isValidElement(node);
}

function invokeElement(node: ElementNode): unknown {
  if (typeof node.type !== 'function') return node;
  return createHookHarness().render(() => (node.type as (props: Record<string, unknown>) => unknown)(node.props));
}

function visibleText(node: unknown): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(visibleText).join('');
  if (!isElement(node)) return '';
  if (typeof node.type === 'function') return visibleText(invokeElement(node));
  if (node.type === 'Collapse') {
    const props = node.props as AuditCollapse['props'];
    const activeKeys = new Set(
      (Array.isArray(props.activeKey ?? props.defaultActiveKey)
        ? props.activeKey ?? props.defaultActiveKey
        : [props.activeKey ?? props.defaultActiveKey])
        .filter((key): key is string | number => key !== undefined)
        .map(String),
    );
    return props.items.flatMap((item) => [
      visibleText(item.label),
      activeKeys.has(String(item.key)) ? visibleText(item.children) : '',
    ]).join('');
  }
  return visibleText(node.props.children);
}

function walk(node: unknown, predicate: (element: ElementNode) => boolean, results: ElementNode[] = []): ElementNode[] {
  if (Array.isArray(node)) {
    node.forEach((item) => walk(item, predicate, results));
    return results;
  }
  if (!isElement(node)) return results;
  if (predicate(node)) results.push(node);
  if (typeof node.type === 'function') {
    walk(invokeElement(node), predicate, results);
    return results;
  }
  walk(node.props.children, predicate, results);
  return results;
}

function auditCollapse(tree: unknown, stage: string): AuditCollapse {
  const collapse = walk(tree, (node) => node.type === 'Collapse' && Array.isArray(node.props.items))
    .find((node) => (node.props.items as AuditCollapse['props']['items'])
      .some((item) => visibleText(item.label).includes(stage)));
  assert.ok(collapse, `Missing the ${stage} audit expansion control`);
  return collapse as AuditCollapse;
}

function auditItem(collapse: AuditCollapse, stage: string) {
  const item = collapse.props.items.find((candidate) => visibleText(candidate.label).includes(stage));
  assert.ok(item, `Missing the ${stage} audit invocation`);
  return item;
}

function assertCollapsed(collapse: AuditCollapse) {
  const active = collapse.props.activeKey ?? collapse.props.defaultActiveKey ?? [];
  assert.deepEqual(Array.isArray(active) ? active : [active], [], 'every Codex conversation detail must start collapsed');
  assert.equal(typeof collapse.props.onChange, 'function', 'the existing invocation expander must own the lazy-detail action');
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
}

function detailFor(overrides: Partial<CodexAuditInvocationDetail>): CodexAuditInvocationDetail {
  return {
    ...nativeAuditDetailFixture,
    ...overrides,
    lineage: { ...nativeAuditDetailFixture.lineage, ...overrides.lineage },
    errors: { ...nativeAuditDetailFixture.errors, ...overrides.errors },
    jsonl: { ...nativeAuditDetailFixture.jsonl, ...overrides.jsonl },
    metadata: { ...nativeAuditDetailFixture.metadata, ...overrides.metadata },
  } as CodexAuditInvocationDetail;
}

function nativeThreeRunFixture(): NativeRunDetail {
  const pageInvocation = {
    ...nativeRunSuccessFixture.codex_audit.invocations[0],
    id: 9302,
    run_id: 903,
    run_slide_id: 9031,
    stage_id: 'native-image',
    role: 'image_generator',
  };
  const directorInvocation = {
    ...nativeRunSuccessFixture.codex_audit.invocations[0],
    id: 9301,
    run_id: 903,
    run_slide_id: null,
    stage_id: 'deck-design-director',
    role: 'designer',
    model: 'gpt-5.6-sol',
  };
  return {
    ...nativeRunSuccessFixture,
    id: 903,
    strategy: 'image_3_0',
    slides: [{ ...nativeRunSuccessFixture.slides[0], id: 9031, run_id: 903 }],
    codex_audit: {
      ...nativeRunSuccessFixture.codex_audit,
      run_id: 903,
      invocation_count: 2,
      event_count: 2,
      per_slide_statuses: [{ run_slide_id: 9031, position: 1, status: 'completed', attempt_count: 1 }],
      invocations: [directorInvocation, pageInvocation],
    },
  } as NativeRunDetail;
}

function asLegacyRun(run: NativeRunDetail, overrides: Record<string, unknown>): RunDetail {
  return { ...run, ...overrides } as unknown as RunDetail;
}

test('Native Direct starts with only the safe aggregate/invocation summary, then fetches exactly its expanded invocation detail', { concurrency: false }, async () => {
  const calls: Array<[number, number]> = [];
  const original = api.runs.codexAuditDetail;
  api.runs.codexAuditDetail = async (runId, invocationId) => {
    calls.push([runId, invocationId]);
    return detailFor({ run_id: runId, invocation_id: invocationId });
  };
  try {
    const harness = createHookHarness();
    const renderPanel = () => harness.render(() => (
      CodexAuditPanel({ audit: nativeRunSuccessFixture.codex_audit, modelCallMetadata: nativeRunSuccessFixture.model_call_metadata })
    ));

    let tree = renderPanel();
    const collapse = auditCollapse(tree, 'native-image');
    const item = auditItem(collapse, 'native-image');
    assertCollapsed(collapse);
    assert.match(visibleText(tree), /Aggregate Status/);
    assert.match(visibleText(tree), /Invocation Count/);
    assert.match(visibleText(tree), /Run Slide ID/);
    assert.equal(visibleText(tree).includes(nativeAuditDetailFixture.prompt), false, 'Prompt/input must stay out of the initial summary');
    assert.equal(calls.length, 0, 'no audit-detail request may occur before explicit expansion');

    await collapse.props.onChange?.([String(item.key)]);
    await settle();
    tree = renderPanel();
    assert.deepEqual(calls, [[901, 9101]], 'the Direct business image must fetch only its exact Run/invocation detail');

    const expanded = visibleText(tree);
    for (const required of [
      nativeAuditDetailFixture.prompt,
      nativeAuditDetailFixture.assistant_output,
      'Tool Calls',
      'Imagegen Calls',
      'Raw Event Timeline',
      'Run ID',
      'Invocation ID',
      'NIMG050F_AUDIT_IMAGEGEN_CALL',
      'imagegen-arguments-sha256',
      'raw-jsonl-sha256',
      'observed-jsonl-sha256',
      'canonical-session-sha256',
      '9011',
      '9101',
      'native-image',
    ]) {
      assert.match(expanded, new RegExp(required.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    for (const sentinel of NATIVE_AUDIT_DETAIL_CREDENTIAL_SENTINELS) {
      assert.equal(expanded.includes(sentinel), false, `credential sentinel rendered: ${sentinel}`);
    }
    for (const privateDetailValue of [
      'NIMG050F_AUDIT_THREAD',
      'Session Source Path',
      'Session Archive Path',
      'Canonical Session Source Path',
      'Canonical Session Archive Path',
      '.codex-private',
    ]) {
      assert.equal(expanded.includes(privateDetailValue), false, `private detail value rendered: ${privateDetailValue}`);
    }

    await collapse.props.onChange?.([String(item.key)]);
    assert.deepEqual(calls, [[901, 9101]], 'an already-loaded invocation must not issue a duplicate detail request');

    let rejectDetail: (error: Error) => void = () => {};
    api.runs.codexAuditDetail = async () => new Promise<CodexAuditInvocationDetail>((_resolve, reject) => {
      rejectDetail = (error) => reject(error);
    });
    const errorHarness = createHookHarness();
    const renderErrorPanel = () => errorHarness.render(() => (
      CodexAuditPanel({ audit: nativeRunFailureFixture.codex_audit, modelCallMetadata: nativeRunFailureFixture.model_call_metadata })
    ));
    let errorTree = renderErrorPanel();
    const errorCollapse = auditCollapse(errorTree, 'native-image');
    const errorItem = auditItem(errorCollapse, 'native-image');
    const pending = errorCollapse.props.onChange?.([String(errorItem.key)]);
    errorTree = renderErrorPanel();
    assert.match(visibleText(errorTree), /Loading Run-owned conversation detail/);
    rejectDetail(new Error('NIMG050D_DETAIL_REQUEST_FAILED'));
    await pending;
    await settle();
    errorTree = renderErrorPanel();
    assert.match(visibleText(errorTree), /Conversation detail failed to load/);
    assert.match(visibleText(errorTree), /NIMG050D_DETAIL_REQUEST_FAILED/);
  } finally {
    api.runs.codexAuditDetail = original;
  }
});

test('Native audit event pages are fetched only from explicit timeline and next-page actions', { concurrency: false }, async () => {
  const detailCalls: Array<[number, number]> = [];
  const eventCalls: Array<[number, number, string | undefined]> = [];
  const originalDetail = api.runs.codexAuditDetail;
  const originalEvents = api.runs.codexAuditEvents;
  api.runs.codexAuditDetail = async (runId, invocationId) => {
    detailCalls.push([runId, invocationId]);
    return detailFor({ run_id: runId, invocation_id: invocationId, events: undefined });
  };
  api.runs.codexAuditEvents = async (runId, invocationId, cursor) => {
    eventCalls.push([runId, invocationId, cursor]);
    return {
      run_id: runId,
      invocation_id: invocationId,
      items: [{
        sequence: cursor ? 2 : 1,
        event_type: 'event_msg',
        item_type: 'agent_message',
        is_error: false,
        observed_at: '2026-07-30T00:00:00Z',
        event_timestamp: null,
        payload: { page: cursor ? 'second' : 'first' },
      }],
      next_cursor: cursor ? null : 'NIMG050F_PAGE_2',
    };
  };
  try {
    const harness = createHookHarness();
    const renderPanel = () => harness.render(() => (
      CodexAuditPanel({ audit: nativeRunSuccessFixture.codex_audit, modelCallMetadata: nativeRunSuccessFixture.model_call_metadata })
    ));
    let tree = renderPanel();
    const collapse = auditCollapse(tree, 'native-image');
    const item = auditItem(collapse, 'native-image');
    await collapse.props.onChange?.([String(item.key)]);
    await settle();
    tree = renderPanel();
    assert.deepEqual(detailCalls, [[901, 9101]]);
    assert.deepEqual(eventCalls, [], 'expanding conversation detail must not load its event page');

    const expandedItem = auditItem(auditCollapse(tree, 'native-image'), 'native-image');
    const firstButton = walk(expandedItem.children, (node) => node.type === 'Button' && visibleText(node).includes('Load event timeline'))[0];
    assert.ok(firstButton, 'the explicit event timeline action must be rendered after detail expansion');
    await (firstButton.props.onClick as () => Promise<void>)();
    await settle();
    tree = renderPanel();
    assert.deepEqual(eventCalls, [[901, 9101, undefined]]);

    const nextItem = auditItem(auditCollapse(tree, 'native-image'), 'native-image');
    const nextButton = walk(nextItem.children, (node) => node.type === 'Button' && visibleText(node).includes('Load next page'))[0];
    assert.ok(nextButton, 'a signed continuation must require a distinct next-page action');
    await (nextButton.props.onClick as () => Promise<void>)();
    await settle();
    assert.deepEqual(eventCalls, [[901, 9101, undefined], [901, 9101, 'NIMG050F_PAGE_2']]);
  } finally {
    api.runs.codexAuditDetail = originalDetail;
    api.runs.codexAuditEvents = originalEvents;
  }
});

test('Native Image 3.0 keeps Director and page conversations distinct, traces each image to its page invocation, and renders detail failures', { concurrency: false }, async () => {
  const threeRun = nativeThreeRunFixture();
  const calls: Array<[number, number]> = [];
  const original = api.runs.codexAuditDetail;
  api.runs.codexAuditDetail = async (runId, invocationId) => {
    calls.push([runId, invocationId]);
    if (invocationId === 9301) {
      return detailFor({
        run_id: runId,
        invocation_id: invocationId,
        lineage: {
          run_id: runId,
          run_slide_id: null,
          stage_id: 'deck-design-director',
          attempt: 1,
          invocation_id: 9301,
          session: nativeAuditDetailFixture.lineage.session,
          call: { id: null, arguments_sha256: null },
        },
        prompt: 'NIMG050C Director prompt',
        assistant_output: 'NIMG050C Director XML output',
        imagegen_calls: [],
      });
    }
    return detailFor({
      run_id: runId,
      invocation_id: invocationId,
      lineage: {
        ...nativeAuditDetailFixture.lineage,
        run_id: runId,
        run_slide_id: 9031,
        stage_id: 'native-image',
        invocation_id: 9302,
        call: { id: 'NIMG050C_PAGE_IMAGEGEN_CALL', arguments_sha256: 'NIMG050C_PAGE_ARGS_SHA' },
      },
    });
  };
  try {
    const harness = createHookHarness();
    const renderPanel = () => harness.render(() => (
      CodexAuditPanel({ audit: threeRun.codex_audit, modelCallMetadata: threeRun.model_call_metadata })
    ));
    let tree = renderPanel();
    const collapse = auditCollapse(tree, 'deck-design-director');
    const director = auditItem(collapse, 'deck-design-director');
    const page = auditItem(collapse, 'native-image');
    assertCollapsed(collapse);
    assert.match(visibleText(tree), /deck-design-director/);
    assert.match(visibleText(tree), /native-image/);
    assert.equal(visibleText(tree).includes('NIMG050C Director prompt'), false, 'Director detail must stay collapsed initially');
    assert.equal(calls.length, 0, '3.0 summary must not eagerly load Director or page detail');

    await collapse.props.onChange?.([String(director.key)]);
    await settle();
    tree = renderPanel();
    assert.deepEqual(calls, [[903, 9301]], 'the Director must use its own exact Run/invocation request');
    assert.match(visibleText(tree), /NIMG050C Director prompt/);
    assert.match(visibleText(tree), /NIMG050C Director XML output/);

    const expandedCollapse = auditCollapse(tree, 'deck-design-director');
    await expandedCollapse.props.onChange?.([String(page.key)]);
    await settle();
    tree = renderPanel();
    assert.deepEqual(calls, [[903, 9301], [903, 9302]], 'the business image must trace to the page invocation, not the Director');
    const pageDetail = visibleText(tree);
    assert.equal(pageDetail.includes('NIMG050C_PAGE_THREAD'), false, 'private thread linkage must not render in invocation detail');
    assert.match(pageDetail, /NIMG050C_PAGE_IMAGEGEN_CALL/);
    assert.match(pageDetail, /NIMG050C_PAGE_ARGS_SHA/);
    assert.match(pageDetail, /Raw Event Timeline/);
    assert.match(pageDetail, /thread.started/);
    assert.match(pageDetail, /Run Slide ID/);
    assert.match(pageDetail, /Stage/);
    assert.match(pageDetail, /Attempt/);
    for (const sentinel of NATIVE_AUDIT_DETAIL_CREDENTIAL_SENTINELS) {
      assert.equal(pageDetail.includes(sentinel), false, `credential sentinel rendered: ${sentinel}`);
    }

    const failureHarness = createHookHarness();
    const renderFailure = () => failureHarness.render(() => (
      CodexAuditPanel({ audit: nativeRunFailureFixture.codex_audit, modelCallMetadata: nativeRunFailureFixture.model_call_metadata })
    ));
    tree = renderFailure();
    const failureCollapse = auditCollapse(tree, 'native-image');
    const failureItem = auditItem(failureCollapse, 'native-image');
    api.runs.codexAuditDetail = async (runId, invocationId) => {
      calls.push([runId, invocationId]);
      return detailFor({
        run_id: runId,
        invocation_id: invocationId,
        errors: { invocation_error: 'NIMG050C_DETAIL_FAILURE', metadata_error: null, event_errors: [] },
        assistant_output: null,
      });
    };
    await failureCollapse.props.onChange?.([String(failureItem.key)]);
    await settle();
    const failureDetail = visibleText(renderFailure());
    assert.match(failureDetail, /NIMG050C_DETAIL_FAILURE/);
    assert.match(failureDetail, /Errors/);
    for (const sentinel of NATIVE_AUDIT_DETAIL_CREDENTIAL_SENTINELS) {
      assert.equal(failureDetail.includes(sentinel), false, `credential sentinel rendered: ${sentinel}`);
    }
  } finally {
    api.runs.codexAuditDetail = original;
  }
});

test('Run Detail mounts the audit for Codex-backed Image Runs while preserving HTML and legacy Image behavior', { concurrency: false }, () => {
  const nativeHarness = createHookHarness([nativeRunSuccessFixture]);
  const nativeTree = nativeHarness.render(() => RunDetailView({ runId: nativeRunSuccessFixture.id }));
  assert.match(visibleText(nativeTree), /Codex Audit/, 'Codex-backed Image Runs must mount the existing audit panel');

  const htmlRun = asLegacyRun(nativeRunSuccessFixture, { engine: 'html', strategy: 'codex_html' });
  const htmlHarness = createHookHarness([htmlRun]);
  const htmlTree = htmlHarness.render(() => RunDetailView({ runId: htmlRun.id }));
  assert.match(visibleText(htmlTree), /Codex Audit/, 'the established HTML Codex audit must remain readable');

  const legacyImageRun = asLegacyRun(nativeRunSuccessFixture, { strategy: 'image_3_0', codex_audit: undefined });
  const legacyHarness = createHookHarness([legacyImageRun]);
  const legacyTree = legacyHarness.render(() => RunDetailView({ runId: legacyImageRun.id }));
  assert.equal(visibleText(legacyTree).includes('Codex Audit'), false, 'legacy Image Runs without Codex must remain unchanged');
  assert.match(visibleText(legacyTree), /Generated Outputs/, 'legacy Image Runs must remain readable');
});
