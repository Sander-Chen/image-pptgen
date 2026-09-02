import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import React from 'react';
import { createServer, type ViteDevServer } from 'vite';

type ElementNode = {
  type: unknown;
  props: Record<string, unknown>;
};

type PageComponent = React.FC;

const SESSION_A = '019f8d16-9519-74f1-9e4d-d0cd55eb1d81';
const SESSION_B = '019f8d16-9519-74f1-9e4d-d0cd55eb1d82';

const source = {
  bytes: 475_106_486,
  mtime_ns: 1,
  sha256: 'a'.repeat(64),
  projection_version: 'effective_v1',
};

const summary = {
  schema_version: 'codex_session_reader_v1',
  session_id: SESSION_A,
  level: 'L1',
  source,
  items: [{
    kind: 'summary',
    message_count: 2,
    tool_count: 1,
    indexed_events: 3,
    core_conclusion: {
      kind: 'message',
      sequence: 8,
      role: 'assistant',
      phase: 'final',
      timestamp: '2026-07-29T10:01:00Z',
      text: 'The latest assistant turn completed the bounded projection.',
      truncated: false,
    },
    metadata: { cli_version: '0.1.0' },
  }],
  next_cursor: null,
  truncated: false,
  filters: { effective_only: true },
};

const coverage = {
  ...summary,
  level: 'L2',
  items: [
    {
      kind: 'message',
      role: 'assistant',
      sequence: 7,
      turn_id: 'turn-7',
      phase: 'analysis',
      tool_name: null,
      timestamp: '2026-07-29T10:00:00Z',
      range_start: 80,
      range_end: 160,
      truncated: false,
      output_chars: 0,
      preview: 'bounded message preview',
      preview_source: 'text',
    },
    {
      kind: 'tool',
      role: null,
      sequence: 8,
      turn_id: 'turn-8',
      phase: null,
      tool_name: 'exec',
      timestamp: '2026-07-29T10:00:01Z',
      range_start: 161,
      range_end: 240,
      truncated: true,
      output_chars: 18,
      preview: 'bounded tool output preview',
      preview_source: 'output',
    },
    {
      kind: 'message',
      role: 'user',
      sequence: 9,
      turn_id: 'turn-9',
      phase: 'commentary',
      tool_name: null,
      timestamp: '2026-07-29T10:00:02Z',
      range_start: 241,
      range_end: 280,
      truncated: false,
      output_chars: 0,
      preview: '',
      preview_source: 'none',
      preview_reason: 'no_persisted_fragment',
    },
  ],
  next_cursor: 'cursor-next',
  truncated: true,
};

const detail = {
  ...coverage,
  level: 'L3',
  items: [{
    ...coverage.items[0],
    text: 'selected effective detail',
    raw_cursor: 'signed l4 raw cursor',
  }],
  next_cursor: null,
  truncated: false,
};

const toolDetail = {
  ...coverage,
  level: 'L3',
  items: [{
    ...coverage.items[1],
    input: 'bounded tool input',
    output: 'bounded tool output',
    raw_cursor: 'signed tool l4 raw cursor',
  }],
  next_cursor: null,
  truncated: true,
};

const raw = {
  ...detail,
  level: 'L4',
  items: [{
    kind: 'raw',
    range_start: 80,
    range_end: 160,
    sha256: 'b'.repeat(64),
    text: 'bounded raw range only',
    truncated: false,
  }],
};

let vite: ViteDevServer | null = null;
let CodexSessionsPage: PageComponent;
let pageModule: Record<string, unknown>;
let api: Record<string, Record<string, (...args: unknown[]) => Promise<unknown>>>;

test.before(async () => {
  vite = await createServer({
    root: new URL('../', import.meta.url).pathname,
    configFile: false,
    appType: 'custom',
    logLevel: 'error',
    optimizeDeps: { noDiscovery: true },
    ssr: { noExternal: ['antd', 'react-router-dom'] },
    plugins: [
      {
        name: 'codex-session-reader-inert-ui',
        enforce: 'pre',
        resolveId(id) {
          if (id === 'antd') return '\0codex-session-reader-antd';
          if (id === 'react-router-dom') return '\0codex-session-reader-router';
          if (id.endsWith('/CodexSessionsPage.css')) return '\0codex-session-reader-page-css';
          return null;
        },
        load(id) {
          if (id === '\0codex-session-reader-router') return 'export const useNavigate = () => () => {};';
          if (id === '\0codex-session-reader-page-css') return 'export default {};';
          if (id === '\0codex-session-reader-antd') {
            return `
              import { createElement } from 'react';
              const primitive = (name) => (props) => createElement(name, props, props.children);
              export const Alert = primitive('Alert'); export const Button = primitive('Button');
              export const Card = primitive('Card'); export const Descriptions = Object.assign(primitive('Descriptions'), { Item: primitive('Descriptions.Item') });
              export const Drawer = primitive('Drawer'); export const Empty = primitive('Empty');
              export const Form = Object.assign(primitive('Form'), { Item: primitive('Form.Item'), useForm: () => [{}] });
              export const Input = primitive('Input'); export const List = Object.assign(primitive('List'), { Item: primitive('List.Item') });
              export const Segmented = primitive('Segmented'); export const Space = primitive('Space'); export const Spin = primitive('Spin');
              export const Tag = primitive('Tag'); export const Typography = { Text: primitive('Typography.Text'), Paragraph: primitive('Typography.Paragraph') };
            `;
          }
          return null;
        },
      },
    ],
  });
  pageModule = await vite.ssrLoadModule('/src/pages/CodexSessionsPage.tsx') as Record<string, unknown>;
  CodexSessionsPage = pageModule.default as PageComponent;
  api = (await vite.ssrLoadModule('/src/api.ts') as { api: typeof api }).api;
});

test.after(async () => {
  await vite?.close();
});

function reactInternals() {
  return (React as unknown as {
    __CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE: { H: unknown };
  }).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
}

function createHookHarness() {
  const values: unknown[] = [];
  return {
    render<T>(component: () => T): T {
      let hookIndex = 0;
      const internals = reactInternals();
      const previous = internals.H;
      const nextValue = <V,>(initial: V | (() => V)): V => {
        const index = hookIndex++;
        if (index >= values.length) values[index] = typeof initial === 'function' ? (initial as () => V)() : initial;
        return values[index] as V;
      };
      internals.H = {
        useState<V>(initial: V | (() => V)) {
          const index = hookIndex;
          const value = nextValue(initial);
          return [value, (update: V | ((current: V) => V)) => {
            const current = values[index] as V;
            values[index] = typeof update === 'function' ? (update as (current: V) => V)(current) : update;
          }] as const;
        },
        useReducer(reducer: (state: unknown, action: unknown) => unknown, initial: unknown, init?: (value: unknown) => unknown) {
          const index = hookIndex;
          const value = nextValue(init ? () => init(initial) : initial);
          return [value, (action: unknown) => { values[index] = reducer(values[index], action); }] as const;
        },
        useEffect() { hookIndex += 1; }, useLayoutEffect() { hookIndex += 1; }, useInsertionEffect() { hookIndex += 1; },
        useMemo(factory: () => unknown) { hookIndex += 1; return factory(); }, useCallback(callback: unknown) { hookIndex += 1; return callback; },
        useRef(initial: unknown) { return { current: nextValue(initial) }; }, useContext(context: { _currentValue?: unknown }) { hookIndex += 1; return context._currentValue; },
        useImperativeHandle() { hookIndex += 1; }, useDebugValue() { hookIndex += 1; }, useDeferredValue(value: unknown) { hookIndex += 1; return value; },
        useTransition() { hookIndex += 1; return [false, (callback: () => void) => callback()] as const; }, useId() { return `csr-${hookIndex++}`; },
        useSyncExternalStore(_subscribe: unknown, getSnapshot: () => unknown) { hookIndex += 1; return getSnapshot(); }, useOptimistic(value: unknown) { hookIndex += 1; return [value, () => {}] as const; },
        useActionState(action: unknown, initial: unknown) { hookIndex += 1; return [initial, action, false] as const; }, useEffectEvent(callback: unknown) { hookIndex += 1; return callback; },
        useMemoCache(size: number) { hookIndex += 1; return Array.from({ length: size }); }, useCacheRefresh() { hookIndex += 1; return () => {}; },
      };
      try {
        return component();
      } finally {
        internals.H = previous;
      }
    },
  };
}

function isElement(node: unknown): node is ElementNode {
  return React.isValidElement(node);
}

function invokeElement(node: ElementNode): unknown {
  if (typeof node.type !== 'function') return node;
  return createHookHarness().render(() => (node.type as (props: Record<string, unknown>) => unknown)(node.props));
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

function textContent(node: unknown): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textContent).join('');
  if (!isElement(node)) return '';
  if (typeof node.type === 'function') return textContent(invokeElement(node));
  return textContent(node.props.children);
}

function findOne(tree: unknown, predicate: (element: ElementNode) => boolean, description: string): ElementNode {
  const found = walk(tree, predicate)[0];
  assert.ok(found, `Missing ${description}`);
  return found;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

async function settle() {
  await Promise.resolve();
  await Promise.resolve();
  await new Promise<void>((resolve) => setImmediate(resolve));
}

test('session IDs keep the exact UUID validation boundary in the page component', async () => {
  const pageSource = await readFile(new URL('../src/pages/CodexSessionsPage.tsx', import.meta.url), 'utf8');
  assert.ok(pageSource.includes('const UUID_PATTERN = /^[0-9a-f]'));
  assert.match(pageSource, /UUID_PATTERN\.test\(value\.trim\(\)\)/);
  assert.match(pageSource, /!isCodexSessionId\(nextSessionId\)/);
});

test('API helpers use the frozen layered endpoints and do not expose a path parameter', async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    return jsonResponse(summary);
  };
  try {
    await api.codexSessions.summary(SESSION_A);
    await api.codexSessions.index(SESSION_A, 'cursor next');
    await api.codexSessions.detail(SESSION_A, 7);
    await api.codexSessions.raw(SESSION_A, 'raw cursor');
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.deepEqual(calls, [
    `/api/codex-sessions/${SESSION_A}/summary`,
    `/api/codex-sessions/${SESSION_A}/index?cursor=cursor+next`,
    `/api/codex-sessions/${SESSION_A}/detail?sequence=7`,
    `/api/codex-sessions/${SESSION_A}/raw?cursor=raw+cursor`,
  ]);
  assert.equal(calls.some((path) => /[?&](path|file|root|offset|turn_id|role|kind|phase|tool_name)=/.test(path)), false);
});

test('the page starts with L1 only and fetches L2/L3/L4 only from explicit actions', async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  const responses = [summary, coverage, { ...coverage, next_cursor: null }, detail, raw];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    return jsonResponse(responses.shift());
  };
  try {
    const harness = createHookHarness();
    let tree = harness.render(() => CodexSessionsPage({}));
    assert.equal(calls.length, 0, 'the initial render must not prefetch any payload');

    const form = findOne(tree, (node) => node.type === 'Form', 'session ID form');
    await (form.props.onFinish as (values: { sessionId: string }) => Promise<void>)({ sessionId: SESSION_A });
    assert.deepEqual(calls, [`/api/codex-sessions/${SESSION_A}/summary`]);

    tree = harness.render(() => CodexSessionsPage({}));
    const conclusion = findOne(tree, (node) => node.type === 'Alert' && node.props.message === 'L1 core conclusion', 'nested L1 core conclusion');
    assert.match(String(conclusion.props.description), /latest assistant turn/i);
    const summaryCounts = walk(tree, (node) => node.type === 'Descriptions.Item' && ['Messages', 'Tools', 'Indexed events'].includes(String(node.props.label)));
    assert.deepEqual(summaryCounts.map((node) => textContent(node)), ['2', '1', '3'], 'L1 must render summary counts without projecting summary items as events');
    assert.equal(textContent(tree).includes('No display text'), false, 'L1 summary items must not enter the generic effective-event list');
    const segmented = findOne(tree, (node) => node.type === 'Segmented', 'level selector');
    await (segmented.props.onChange as (level: string) => Promise<void>)('L2');
    assert.deepEqual(calls, [
      `/api/codex-sessions/${SESSION_A}/summary`,
      `/api/codex-sessions/${SESSION_A}/index`,
    ]);

    tree = harness.render(() => CodexSessionsPage({}));
    assert.equal(textContent(tree).includes('range 80–160'), true, 'real range_start/range_end must render');
    assert.equal(textContent(tree).includes('bounded message preview'), true, 'L2 must render the bounded message preview');
    assert.equal(textContent(tree).includes('Message text preview'), true, 'L2 must label the message preview source');
    assert.equal(textContent(tree).includes('bounded tool output preview'), true, 'L2 must render the bounded tool preview');
    assert.equal(textContent(tree).includes('Tool output preview'), true, 'L2 must label the tool preview source');
    assert.equal(textContent(tree).includes('No persisted text fragment'), true, 'L2 must explain a structural empty preview honestly');
    assert.equal(textContent(tree).includes('No display text'), false, 'L2 must not replace stored structural emptiness with a generic display fallback');
    assert.equal(walk(tree, (node) => node.props['aria-label'] === 'View raw range').length, 0, 'L4 must stay unavailable before an L3 raw cursor exists');
    assert.equal(calls.length, 2, 'L4 must not issue a raw request before an L3 signed cursor exists');
    const l3Segment = findOne(tree, (node) => node.type === 'Segmented', 'L3 level selector');
    await (l3Segment.props.onChange as (level: string) => Promise<void>)('L3');
    assert.equal(calls.length, 2, 'selecting L3 alone must not infer an event or issue a detail request');
    tree = harness.render(() => CodexSessionsPage({}));
    const selectionGuidance = findOne(tree, (node) => node.type === 'Alert' && node.props.role === 'alert', 'L3 selection guidance');
    assert.match(String(selectionGuidance.props.message), /choose an L2 event/i);
    const next = findOne(tree, (node) => node.props['aria-label'] === 'Load next page', 'next-page control');
    await (next.props.onClick as () => Promise<void>)();
    assert.equal(calls.at(-1), `/api/codex-sessions/${SESSION_A}/index?cursor=cursor-next`);

    tree = harness.render(() => CodexSessionsPage({}));
    const detailAction = findOne(tree, (node) => node.props['aria-label'] === 'Load exact selected event', 'L3 action');
    await (detailAction.props.onClick as () => Promise<void>)();
    await settle();
    assert.equal(calls.at(-1), `/api/codex-sessions/${SESSION_A}/detail?sequence=7`);

    tree = harness.render(() => CodexSessionsPage({}));
    assert.equal(textContent(tree).includes('selected effective detail'), true, 'the selected L3 item must be shown before raw access');
    assert.equal(textContent(tree).includes('Message text'), true, 'L3 message content must use an explicit content label');
    assert.equal(walk(tree, (node) => node.props['aria-label'] === 'Load next page').length, 0, 'L3 must not expose pagination controls');
    assert.equal(textContent(tree).includes('Raw text stays closed until this explicit action.'), true, 'only a signed L3 raw cursor may expose the raw action');
    const rawAction = findOne(tree, (node) => node.props['aria-label'] === 'View raw range', 'explicit raw action');
    await (rawAction.props.onClick as () => Promise<void>)();
    assert.equal(calls.at(-1), `/api/codex-sessions/${SESSION_A}/raw?cursor=signed+l4+raw+cursor`);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('a selected tool event requests its sequence and renders input and output as separate fields', async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  const responses = [summary, coverage, toolDetail];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    return jsonResponse(responses.shift());
  };
  try {
    const harness = createHookHarness();
    let tree = harness.render(() => CodexSessionsPage({}));
    const form = findOne(tree, (node) => node.type === 'Form', 'session ID form');
    await (form.props.onFinish as (values: { sessionId: string }) => Promise<void>)({ sessionId: SESSION_A });
    tree = harness.render(() => CodexSessionsPage({}));
    const segmented = findOne(tree, (node) => node.type === 'Segmented', 'level selector');
    await (segmented.props.onChange as (level: string) => Promise<void>)('L2');
    tree = harness.render(() => CodexSessionsPage({}));
    const detailActions = walk(tree, (node) => typeof node.type === 'function' && node.props['aria-label'] === 'Load exact selected event');
    assert.equal(detailActions.length, 3, 'each L2 effective event must remain individually selectable');
    await (detailActions[1].props.onClick as () => Promise<void>)();
    await settle();
    assert.equal(calls.at(-1), `/api/codex-sessions/${SESSION_A}/detail?sequence=8`);

    tree = harness.render(() => CodexSessionsPage({}));
    assert.equal(textContent(tree).includes('Tool input'), true);
    assert.equal(textContent(tree).includes('bounded tool input'), true);
    assert.equal(textContent(tree).includes('Tool output'), true);
    assert.equal(textContent(tree).includes('bounded tool output'), true);
    assert.equal(walk(tree, (node) => node.props['aria-label'] === 'Load previous page').length, 0, 'L3 must not expose pagination controls');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('changing session discards old payload/cursors, while displayed errors remain announced and retryable', async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  const replacementSummary = {
    ...summary,
    session_id: SESSION_B,
    items: [{ ...summary.items[0], core_conclusion: { ...summary.items[0].core_conclusion, text: 'replacement session only' } }],
  };
  const replacementCoverage = { ...coverage, session_id: SESSION_B };
  const replacementDetail = { ...detail, session_id: SESSION_B };
  const responses = [summary, coverage, replacementSummary, replacementCoverage, replacementDetail, { error: 'source changed' }, replacementSummary];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    const body = responses.shift();
    return calls.length === 6 ? jsonResponse(body, 409) : jsonResponse(body);
  };
  try {
    const harness = createHookHarness();
    let tree = harness.render(() => CodexSessionsPage({}));
    let form = findOne(tree, (node) => node.type === 'Form', 'session ID form');
    await (form.props.onFinish as (values: { sessionId: string }) => Promise<void>)({ sessionId: SESSION_A });
    tree = harness.render(() => CodexSessionsPage({}));
    const segmented = findOne(tree, (node) => node.type === 'Segmented', 'level selector');
    await (segmented.props.onChange as (level: string) => Promise<void>)('L2');

    tree = harness.render(() => CodexSessionsPage({}));
    form = findOne(tree, (node) => node.type === 'Form', 'session ID form');
    await (form.props.onFinish as (values: { sessionId: string }) => Promise<void>)({ sessionId: SESSION_B });
    tree = harness.render(() => CodexSessionsPage({}));
    assert.equal(textContent(tree).includes('first coverage row'), false, 'old page data must not survive a session change');

    const replacementSegmented = findOne(tree, (node) => node.type === 'Segmented', 'level selector');
    await (replacementSegmented.props.onChange as (level: string) => Promise<void>)('L2');
    tree = harness.render(() => CodexSessionsPage({}));
    const detailAction = findOne(tree, (node) => node.props['aria-label'] === 'Load exact selected event', 'L3 action');
    await (detailAction.props.onClick as () => Promise<void>)();
    await settle();
    tree = harness.render(() => CodexSessionsPage({}));
    const rawAction = findOne(tree, (node) => node.props['aria-label'] === 'View raw range', 'explicit raw action');
    await (rawAction.props.onClick as () => Promise<void>)();
    await settle();
    tree = harness.render(() => CodexSessionsPage({}));
    const alert = findOne(tree, (node) => node.type === 'Alert' && node.props.role === 'alert', 'announced source-change alert');
    assert.match(String(alert.props.message), /source changed/i);
    assert.ok(isElement(alert.props.action), 'the error alert must provide a retry action');
    assert.equal((alert.props.action as ElementNode).props['aria-label'], 'Retry current request');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('a new or invalid session clears retry so it cannot replay a previous session request', async () => {
  const originalFetch = globalThis.fetch;
  const calls: string[] = [];
  globalThis.fetch = async (input) => {
    calls.push(String(input));
    return jsonResponse(summary);
  };
  try {
    const harness = createHookHarness();
    let tree = harness.render(() => CodexSessionsPage({}));
    let form = findOne(tree, (node) => node.type === 'Form', 'session ID form');
    await (form.props.onFinish as (values: { sessionId: string }) => Promise<void>)({ sessionId: SESSION_A });
    tree = harness.render(() => CodexSessionsPage({}));
    form = findOne(tree, (node) => node.type === 'Form', 'session ID form');
    await (form.props.onFinish as (values: { sessionId: string }) => Promise<void>)({ sessionId: '../private/session.jsonl' });
    tree = harness.render(() => CodexSessionsPage({}));
    const alert = findOne(tree, (node) => node.type === 'Alert' && node.props.role === 'alert', 'invalid-session alert');
    assert.ok(isElement(alert.props.action), 'the invalid-session alert must provide its declared retry action');
    await ((alert.props.action as ElementNode).props.onClick as () => Promise<void>)();
    assert.deepEqual(calls, [`/api/codex-sessions/${SESSION_A}/summary`], 'retry must not replay the previous session request');
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('the page makes loading, empty, truncated, and disabled states distinguishable', async () => {
  const originalFetch = globalThis.fetch;
  let releaseLoading: ((response: Response) => void) | null = null;
  globalThis.fetch = () => new Promise<Response>((resolve) => { releaseLoading = resolve; });
  try {
    const harness = createHookHarness();
    let tree = harness.render(() => CodexSessionsPage({}));
    const form = findOne(tree, (node) => node.type === 'Form', 'session ID form');
    const pending = (form.props.onFinish as (values: { sessionId: string }) => Promise<void>)({ sessionId: SESSION_A });
    tree = harness.render(() => CodexSessionsPage({}));
    findOne(tree, (node) => node.type === 'Spin', 'loading indicator');
    const pendingLoad = findOne(tree, (node) => node.type === 'Button' && node.props.htmlType === 'submit', 'pending load control');
    assert.equal(pendingLoad.props.disabled, true, 'the load control must be directly disabled while its request is pending');
    assert.ok(releaseLoading, 'the summary request must be pending before its loading state is asserted');
    releaseLoading(jsonResponse(summary));
    await pending;

    tree = harness.render(() => CodexSessionsPage({}));
    const settledLoad = findOne(tree, (node) => node.type === 'Button' && node.props.htmlType === 'submit', 'settled load control');
    assert.equal(settledLoad.props.disabled, false, 'the load control must be directly re-enabled after its request settles');
    findOne(tree, (node) => node.type === 'Empty', 'empty bounded-page state');
  } finally {
    globalThis.fetch = originalFetch;
  }

  const truncatedFetch = globalThis.fetch;
  globalThis.fetch = async () => jsonResponse(coverage);
  try {
    const harness = createHookHarness();
    let tree = harness.render(() => CodexSessionsPage({}));
    const form = findOne(tree, (node) => node.type === 'Form', 'session ID form');
    await (form.props.onFinish as (values: { sessionId: string }) => Promise<void>)({ sessionId: SESSION_A });
    tree = harness.render(() => CodexSessionsPage({}));
    const segmented = findOne(tree, (node) => node.type === 'Segmented', 'level selector');
    await (segmented.props.onChange as (level: string) => Promise<void>)('L2');
    tree = harness.render(() => CodexSessionsPage({}));
    assert.equal(textContent(tree).includes('Truncated page'), true);
  } finally {
    globalThis.fetch = truncatedFetch;
  }

  const disabledFetch = globalThis.fetch;
  globalThis.fetch = async () => jsonResponse({ error: 'disabled' }, 403);
  try {
    const harness = createHookHarness();
    const tree = harness.render(() => CodexSessionsPage({}));
    const form = findOne(tree, (node) => node.type === 'Form', 'session ID form');
    await (form.props.onFinish as (values: { sessionId: string }) => Promise<void>)({ sessionId: SESSION_A });
    const errorTree = harness.render(() => CodexSessionsPage({}));
    const alert = findOne(errorTree, (node) => node.type === 'Alert' && node.props.role === 'alert', 'disabled-reader alert');
    assert.match(String(alert.props.message), /disabled/i);
  } finally {
    globalThis.fetch = disabledFetch;
  }
});

test('the implementation keeps one current payload and encodes the responsive accessibility contracts', async () => {
  const [pageSource, typeSource, css] = await Promise.all([
    readFile(new URL('../src/pages/CodexSessionsPage.tsx', import.meta.url), 'utf8'),
    readFile(new URL('../src/types.ts', import.meta.url), 'utf8'),
    readFile(new URL('../src/pages/CodexSessionsPage.css', import.meta.url), 'utf8'),
  ]);
  assert.doesNotMatch(pageSource, /\b(allItems|allPages|pagePayloads)\b/, 'the page must not retain a growing payload collection');
  assert.doesNotMatch(pageSource, /currentPayload\.core_conclusion/);
  assert.match(pageSource, /range_start/);
  assert.match(pageSource, /raw_cursor/);
  for (const field of ['sequence', 'preview', 'preview_source', 'preview_reason']) {
    assert.match(typeSource, new RegExp(field));
    assert.match(pageSource, new RegExp(field));
  }
  assert.doesNotMatch(pageSource, /detailSelectors|detailFilters/);
  assert.match(pageSource, /level !== 'L2'/);
  assert.match(pageSource, /aria-live=["']polite["']/);
  assert.match(pageSource, /role=["']alert["']/);
  assert.match(css, /min-height:\s*44px/);
  assert.match(css, /:focus-visible/);
  assert.match(css, /overflow-wrap:\s*anywhere/);
  assert.match(css, /\.codex-sessions-page\s*\{\s*display:\s*grid;\s*grid-template-columns:\s*minmax\(0,\s*1fr\);/);
  assert.match(css, /\.codex-sessions-page > \.ant-card\s*\{\s*min-width:\s*0;/);
  assert.match(css, /\.codex-sessions-result-card \.ant-card-body\s*\{\s*display:\s*grid;\s*grid-template-columns:\s*minmax\(0,\s*1fr\);/);
  assert.match(css, /@media\s*\(max-width:\s*767px\)/);
});
