import assert from 'node:assert/strict';
import test from 'node:test';

import React from 'react';
import { createServer, type ViteDevServer } from 'vite';

import type { AgentConfig, Config, ModelProfile, RouteStrategy } from '../src/types';

type ElementNode = {
  type: unknown;
  props: Record<string, unknown>;
};

type GeneratePayload = {
  engine?: string;
  strategy?: string;
  config_id?: number;
  requirement_ids?: number[];
  color_ids?: number[];
  route_metadata?: Record<string, unknown>;
};

type GeneratePageComponent = (props: Record<string, never>) => unknown;

type GenerateApi = {
  generate: {
    start: (payload: GeneratePayload) => Promise<{ batch_id: number; run_ids: number[]; total_runs: number; slides_per_run: number }>;
  };
  batches: {
    get: (batchId: number) => Promise<unknown>;
  };
};

const agentConfig: AgentConfig = {
  api_type: 'codex_native_image',
  endpoint: 'codex://exec',
  model: 'gpt-5.6-luna',
  api_key: '',
  temperature: 1,
  thinking: 'low',
};

const legacyImageConfig: Config = {
  id: 801,
  name: 'Legacy Image 3.0',
  type: 'image',
  designer: agentConfig,
  html_agent: agentConfig,
  is_default: true,
  timeout_minutes: 30,
  max_concurrent_runs: 1,
  route_model_bindings: { image_generator: { profile_id: 701 } },
  created_at: '2026-07-24T00:00:00Z',
  updated_at: '2026-07-24T00:00:00Z',
};

const nativeDirectConfig: Config = {
  ...legacyImageConfig,
  id: 901,
  name: 'Codex Native Image Direct',
  is_default: false,
  route_model_bindings: {
    image_generator: { profile_id: 702 },
    native_image: { adapter: 'codex_native', route: 'image_direct' },
  },
};

const nativeThreeConfig: Config = {
  ...legacyImageConfig,
  id: 902,
  name: 'Codex Native Image 3.0',
  is_default: false,
  route_model_bindings: {
    image_designer: { profile_id: 703 },
    image_generator: { profile_id: 702 },
    image_palette_extractor: { profile_id: 704 },
    native_image: { adapter: 'codex_native', route: 'image_3_0' },
  },
};

const nativeThreeLunaConfig: Config = {
  ...nativeThreeConfig,
  id: 903,
  name: 'Codex Native Image 3.0 Luna Low Director',
  is_default: false,
  route_model_bindings: {
    image_designer: { profile_id: 705 },
    image_generator: { profile_id: 702 },
    image_palette_extractor: { profile_id: 704 },
    native_image: { adapter: 'codex_native', route: 'image_3_0' },
  },
};

const configs = [legacyImageConfig, nativeDirectConfig, nativeThreeConfig, nativeThreeLunaConfig];

const modelProfiles: ModelProfile[] = [
  {
    id: 701,
    role: 'image_generator',
    name: 'Legacy image renderer',
    api_type: 'openai',
    endpoint: 'https://example.test/images',
    model: 'nanobanana2',
    api_key: '',
    temperature: 1,
    thinking: null,
    status: 'active',
  },
  {
    id: 702,
    role: 'image_generator',
    name: 'Codex Native image launcher',
    api_type: 'codex_native_image',
    endpoint: 'codex://exec',
    model: 'gpt-5.6-luna',
    api_key: '',
    temperature: 1,
    thinking: 'low',
    status: 'active',
  },
  {
    id: 703,
    role: 'image_designer',
    name: 'Codex Native 3.0 director',
    api_type: 'codex_exec',
    endpoint: 'codex://exec',
    model: 'gpt-5.6-sol',
    api_key: '',
    temperature: 1,
    thinking: 'low',
    status: 'active',
  },
  {
    id: 704,
    role: 'image_generator',
    name: 'Native 3.0 palette extractor',
    api_type: 'gemini',
    endpoint: 'https://generativelanguage.googleapis.com',
    model: 'gemini-3-flash-preview',
    api_key: '',
    temperature: 1,
    thinking: null,
    status: 'active',
  },
  {
    id: 705,
    role: 'image_designer',
    name: 'Codex Native Image Director Luna Low',
    api_type: 'codex_exec',
    endpoint: 'codex://exec',
    model: 'gpt-5.6-luna',
    api_key: '',
    temperature: 1,
    thinking: 'low',
    status: 'active',
  },
];

let vite: ViteDevServer | null = null;
let GeneratePage: GeneratePageComponent;
let api: GenerateApi;

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
        name: 'nimg-050a-inert-ui-components',
        enforce: 'pre',
        resolveId(id) {
          if (id === 'antd') return '\0nimg-050a-antd';
          if (id === '@ant-design/icons') return '\0nimg-050a-icons';
          if (id === 'react-router-dom') return '\0nimg-050a-router';
          return null;
        },
        load(id) {
          if (id === '\0nimg-050a-icons') {
            return 'export const AppstoreOutlined = "AppstoreOutlined"; export const BellOutlined = "BellOutlined"; export const BgColorsOutlined = "BgColorsOutlined"; export const CheckOutlined = "CheckOutlined"; export const ClockCircleOutlined = "ClockCircleOutlined"; export const FileTextOutlined = "FileTextOutlined"; export const HistoryOutlined = "HistoryOutlined"; export const InfoCircleOutlined = "InfoCircleOutlined"; export const NumberOutlined = "NumberOutlined"; export const SettingOutlined = "SettingOutlined"; export const ThunderboltOutlined = "ThunderboltOutlined";';
          }
          if (id === '\0nimg-050a-router') return 'export const Link = "Link";';
          if (id === '\0nimg-050a-antd') {
            return `
              const component = (name) => { const value = () => null; value.displayName = name; return value; };
              export const Select = component('Select');
              export const Checkbox = Object.assign(component('Checkbox'), { Group: component('Checkbox.Group') });
              export const Button = component('Button');
              export const Alert = component('Alert');
              export const Progress = component('Progress');
              export const Space = component('Space');
              export const Tag = component('Tag');
              export const Spin = component('Spin');
              export const Statistic = component('Statistic');
              export const Divider = component('Divider');
              export const Radio = Object.assign(component('Radio'), { Group: component('Radio.Group'), Button: component('Radio.Button') });
              export const InputNumber = component('InputNumber');
              export const Segmented = component('Segmented');
              export const message = { success() {}, error() {}, info() {} };
            `;
          }
          return null;
        },
      },
    ],
  });
  const pageModule = await vite.ssrLoadModule('/src/pages/GeneratePage.tsx');
  const apiModule = await vite.ssrLoadModule('/src/api.ts');
  GeneratePage = pageModule.default as GeneratePageComponent;
  api = apiModule.api as GenerateApi;
});

test.after(async () => {
  await vite?.close();
});

function elementNodes(node: unknown, results: ElementNode[] = []): ElementNode[] {
  if (Array.isArray(node)) {
    for (const item of node) elementNodes(item, results);
    return results;
  }
  if (!React.isValidElement(node)) return results;
  const element = node as unknown as ElementNode;
  results.push(element);
  elementNodes(element.props.children, results);
  return results;
}

function textContent(node: unknown): string {
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textContent).join('');
  if (!React.isValidElement(node)) return '';
  return textContent((node as unknown as ElementNode).props.children);
}

function control(root: unknown, ariaLabel: string): ElementNode {
  const selected = elementNodes(root).find((element) => element.props['aria-label'] === ariaLabel);
  assert.ok(selected, `Missing ${ariaLabel} control`);
  return selected;
}

function selectOptions(root: unknown, ariaLabel: string): Array<{ label: string; value: number }> {
  const options = control(root, ariaLabel).props.options;
  assert.ok(Array.isArray(options), `${ariaLabel} must expose selectable options`);
  return options as Array<{ label: string; value: number }>;
}

function createGenerateHarness(
  strategy: RouteStrategy,
  options: {
    engine?: 'html' | 'image';
    generationMode?: 'manual' | 'auto';
    requirementIds?: number[];
    colorIds?: number[];
    selectedConfigId?: number;
  } = {},
) {
  const stateValues: unknown[] = [
    1,
    [{ id: 1, title: 'Native Config test deck' }],
    [{ id: 2, title: 'Requirement' }],
    [{ id: 3, title: 'Color' }],
    configs,
    [],
    [],
    [],
    modelProfiles,
    false,
    1,
    1,
    options.requirementIds ?? [2],
    options.colorIds ?? [3],
    options.selectedConfigId ?? legacyImageConfig.id,
    undefined,
    undefined,
    options.generationMode ?? 'manual',
    options.engine ?? 'image',
    strategy,
    'banana',
    'banana',
    1,
    [],
    false,
    null,
  ];
  let hookIndex = 0;
  const reactInternals = (React as unknown as {
    __CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE: { H: unknown };
  }).__CLIENT_INTERNALS_DO_NOT_USE_OR_WARN_USERS_THEY_CANNOT_UPGRADE;
  const hooks = {
    useState<T>(initial: T | (() => T)) {
      const index = hookIndex++;
      if (index >= stateValues.length) stateValues[index] = typeof initial === 'function' ? (initial as () => T)() : initial;
      const setValue = (next: T | ((current: T) => T)) => {
        stateValues[index] = typeof next === 'function'
          ? (next as (current: T) => T)(stateValues[index] as T)
          : next;
      };
      return [stateValues[index] as T, setValue] as const;
    },
    useEffect() {
      hookIndex += 1;
    },
    useCallback<T>(callback: T) {
      hookIndex += 1;
      return callback;
    },
    useMemo<T>(factory: () => T) {
      hookIndex += 1;
      return factory();
    },
    useRef<T>(initial: T) {
      hookIndex += 1;
      return { current: initial };
    },
  };

  return {
    render() {
      const previousDispatcher = reactInternals.H;
      hookIndex = 0;
      reactInternals.H = hooks;
      try {
        return GeneratePage({});
      } finally {
        reactInternals.H = previousDispatcher;
      }
    },
  };
}

async function submit(root: unknown): Promise<GeneratePayload> {
  const submitted: GeneratePayload[] = [];
  const previousStart = api.generate.start;
  const previousBatchGet = api.batches.get;
  api.generate.start = async (payload) => {
    submitted.push(payload);
    return { batch_id: 99, run_ids: [100], total_runs: 1, slides_per_run: 1 };
  };
  api.batches.get = async () => ({ id: 99, status: 'completed' });
  try {
    const generateButton = control(root, 'Generate batch');
    await (generateButton.props.onClick as () => Promise<void>)();
    assert.equal(submitted.length, 1, 'Generate must submit exactly one payload');
    return submitted[0];
  } finally {
    api.generate.start = previousStart;
    api.batches.get = previousBatchGet;
  }
}

function moveToConfirmStep(root: unknown) {
  const confirmStep = elementNodes(root).find((element) => (
    element.type === 'button'
    && String(element.props.className || '').includes('generate-step-button')
    && textContent(element).includes('Confirm')
  ));
  assert.ok(confirmStep, 'Generate must retain a reachable Confirm step');
  (confirmStep.props.onClick as () => void)();
}

function classNodes(root: unknown, className: string): ElementNode[] {
  return elementNodes(root).filter((element) => (
    String(element.props.className || '').split(/\s+/).includes(className)
  ));
}

function switchImageStrategy(root: unknown, strategy: RouteStrategy) {
  const strategyControl = control(root, 'Image strategy');
  (strategyControl.props.onChange as (event: { target: { value: RouteStrategy } }) => void)({
    target: { value: strategy },
  });
}

function assertServerResolvedPayload(payload: GeneratePayload, strategy: RouteStrategy, configId: number) {
  assert.equal(payload.engine, 'image');
  assert.equal(payload.strategy, strategy);
  assert.equal(payload.config_id, configId);

  const containsAuthority = (value: unknown, key: string): boolean => {
    if (Array.isArray(value)) return value.some((item) => containsAuthority(item, key));
    if (!value || typeof value !== 'object') return false;
    return Object.entries(value as Record<string, unknown>).some(([candidate, nested]) => (
      candidate === key || containsAuthority(nested, key)
    ));
  };
  for (const forbiddenAuthority of [
    'adapter',
    'api_type',
    'launcher',
    'model',
    'native_image',
    'native_route',
    'permission',
    'permissions',
    'profile_id',
    'provider_channel',
    'role',
  ]) {
    assert.equal(
      containsAuthority(payload, forbiddenAuthority),
      false,
      `Generate must leave ${forbiddenAuthority} resolution to the server-owned config`,
    );
  }
}

function assertNativeConfigPayload(payload: GeneratePayload, strategy: RouteStrategy, configId: number) {
  assertServerResolvedPayload(payload, strategy, configId);
  const metadata = payload.route_metadata || {};
  for (const clientOwnedField of [
    'image_renderer',
    'image_direct_model_name',
    'image_direct_model_profile_id',
    'image_direct_lane',
  ]) {
    assert.equal(
      metadata[clientOwnedField],
      undefined,
      `Native ${strategy} must leave ${clientOwnedField} to the server-resolved config`,
    );
  }
}

test('Native Direct is selectable from the existing Config dropdown and submits a server-resolved payload', async () => {
  const harness = createGenerateHarness('image_direct');
  const beforeSelection = harness.render();
  const configSelect = control(beforeSelection, 'Select config');
  const options = selectOptions(beforeSelection, 'Select config');
  assert.ok(
    options.some((option) => option.value === nativeDirectConfig.id && option.label.includes(nativeDirectConfig.name)),
    'Native Direct must be a choice in the existing Config dropdown',
  );

  (configSelect.props.onChange as (configId: number) => void)(nativeDirectConfig.id);
  const afterSelection = harness.render();
  assert.equal(control(afterSelection, 'Select config').props.value, nativeDirectConfig.id);
  assert.match(textContent(afterSelection), /ImageDirect \+ Codex Native/);
  assert.match(textContent(afterSelection), /RendererCodex Native/);
  moveToConfirmStep(afterSelection);

  assertNativeConfigPayload(await submit(harness.render()), 'image_direct', nativeDirectConfig.id);
});

test('legacy, Sol, and optional Luna Low Director Native 3.0 configs coexist without Luna replacing the current choice', async () => {
  const harness = createGenerateHarness('image_3_0');
  const beforeSelection = harness.render();
  const configSelect = control(beforeSelection, 'Select config');
  const options = selectOptions(beforeSelection, 'Select config');
  assert.ok(
    options.some((option) => option.value === nativeThreeConfig.id && option.label.includes(nativeThreeConfig.name)),
    'Native 3.0 must be a choice in the existing Config dropdown',
  );
  assert.ok(
    options.some((option) => (
      option.value === nativeThreeLunaConfig.id && option.label.includes(nativeThreeLunaConfig.name)
    )),
    'the optional Luna Low Director config must be a separate Native 3.0 choice',
  );
  assert.ok(
    options.some((option) => option.value === legacyImageConfig.id && option.label.includes(legacyImageConfig.name)),
    'existing legacy Image choices must remain available',
  );
  assert.equal(
    configSelect.props.value,
    legacyImageConfig.id,
    'the non-default Luna variant must not replace the existing selected config',
  );

  (configSelect.props.onChange as (configId: number) => void)(nativeThreeLunaConfig.id);
  const afterSelection = harness.render();
  assert.equal(control(afterSelection, 'Select config').props.value, nativeThreeLunaConfig.id);
  assert.match(textContent(afterSelection), /Image 3\.0 \+ Codex Native/);
  assert.match(textContent(afterSelection), /gpt-5\.6-luna/);
  assert.match(textContent(afterSelection), /RendererCodex Native/);
  moveToConfirmStep(afterSelection);

  const payload = await submit(harness.render());
  assertNativeConfigPayload(payload, 'image_3_0', nativeThreeLunaConfig.id);
  assert.deepEqual(payload.requirement_ids, []);
  assert.deepEqual(payload.color_ids, []);
});

test('a valid Sol Native 3.0 selection remains selected when the optional Luna config is available', () => {
  const root = createGenerateHarness('image_3_0', { selectedConfigId: nativeThreeConfig.id }).render();
  assert.equal(control(root, 'Select config').props.value, nativeThreeConfig.id);
});

test('Generate retains the existing two-route, three-step flow without a Native family or third Direct mode', () => {
  const root = createGenerateHarness('image_direct').render();
  const routeControl = control(root, 'Generation route');
  assert.deepEqual(textContent(routeControl).match(/HTML Route|Image Route/g), ['HTML Route', 'Image Route']);
  const routeControlLabels = elementNodes(root)
    .map((element) => element.props['aria-label'])
    .filter((label): label is string => typeof label === 'string' && label.toLowerCase().includes('route'));
  assert.deepEqual(routeControlLabels, ['Generation route'], 'Generate must not add a top-level Route control');

  const strategyControl = control(root, 'Image strategy');
  const strategyText = textContent(strategyControl);
  assert.equal((strategyText.match(/ImageDirect/g) || []).length, 1, 'the existing Direct family must remain singular');
  assert.doesNotMatch(strategyText, /Native/i, 'Native choices belong in Config, not a new strategy family');
  const strategyValues = elementNodes(strategyControl)
    .filter((element) => element !== strategyControl)
    .map((element) => element.props.value)
    .filter((value): value is string => typeof value === 'string');
  assert.deepEqual(strategyValues, ['image_1_0', 'image_3_0', 'image_3_2', 'image_5_0', 'image_direct']);

  const nativeControlLabels = elementNodes(root)
    .map((element) => element.props['aria-label'])
    .filter((label): label is string => typeof label === 'string' && label.toLowerCase().includes('native'));
  assert.deepEqual(nativeControlLabels, [], 'Generate must not add a Native-family control');

  const workflowSteps = elementNodes(root).filter((element) => (
    element.type === 'button' && String(element.props.className || '').includes('generate-step-button')
  ));
  assert.equal(workflowSteps.length, 3, 'Generate must retain its existing three-step flow');
});

for (const strategy of ['image_1_0', 'image_3_0', 'image_3_2', 'image_direct'] as RouteStrategy[]) {
  test(`${strategy} removes generic Manual Selections while preserving route controls`, () => {
    const root = createGenerateHarness(strategy).render();

    assert.equal(classNodes(root, 'manual-option-grid').length, 0);
    assert.doesNotMatch(textContent(root), /Manual Selections/);
    const autoControl = elementNodes(control(root, 'Generation mode')).find((element) => element.props.value === 'auto');
    assert.ok(autoControl);
    assert.equal(autoControl.props.disabled, true);

    if (strategy === 'image_direct') {
      assert.match(textContent(root), /ImageDirect Model and Config/);
      control(root, 'ImageDirect model family');
      control(root, 'Select config');
    } else {
      assert.match(textContent(root), /Route Configuration/);
      control(root, 'Select config');
    }
  });

  test(`${strategy} clears dirty generic selections and submits empty arrays`, async () => {
    const harness = createGenerateHarness('image_5_0');
    switchImageStrategy(harness.render(), strategy);
    switchImageStrategy(harness.render(), 'image_5_0');
    const clearedGroups = classNodes(harness.render(), 'stacked-options');
    assert.deepEqual(clearedGroups.map((group) => group.props.value), [[], []]);
    switchImageStrategy(harness.render(), strategy);
    let root = harness.render();
    if (strategy === 'image_direct') {
      (control(root, 'Select config').props.onChange as (configId: number) => void)(nativeDirectConfig.id);
      root = harness.render();
    }
    moveToConfirmStep(root);

    const payload = await submit(harness.render());

    assert.deepEqual(payload.requirement_ids, []);
    assert.deepEqual(payload.color_ids, []);
  });
}

test('Image 5.0 and HTML Manual retain generic selections', () => {
  const imageRoot = createGenerateHarness('image_5_0').render();
  assert.equal(classNodes(imageRoot, 'manual-option-grid').length, 1);
  assert.match(textContent(imageRoot), /Manual Selections/);

  const htmlRoot = createGenerateHarness('html_default', { engine: 'html' }).render();
  assert.equal(classNodes(htmlRoot, 'manual-option-grid').length, 1);
  assert.match(textContent(htmlRoot), /Manual Selections/);
});

for (const [engine, strategy] of [
  ['image', 'image_5_0'],
  ['html', 'html_default'],
] as Array<['image' | 'html', RouteStrategy]>) {
  test(`${strategy} Manual preserves selected Requirement and Color payloads`, async () => {
    const harness = createGenerateHarness(strategy, { engine, requirementIds: [2], colorIds: [3] });
    moveToConfirmStep(harness.render());

    const payload = await submit(harness.render());

    assert.deepEqual(payload.requirement_ids, [2]);
    assert.deepEqual(payload.color_ids, [3]);
  });
}

test('Image 5.0 Manual double-empty submits no Custom Instruction or preset color selection', async () => {
  const harness = createGenerateHarness('image_5_0', { requirementIds: [], colorIds: [] });
  moveToConfirmStep(harness.render());

  const payload = await submit(harness.render());

  assert.deepEqual(payload.requirement_ids, []);
  assert.deepEqual(payload.color_ids, []);
});

test('switching self-managed image strategies preserves Candidate Count', () => {
  const harness = createGenerateHarness('image_5_0', { generationMode: 'auto' });
  const autoRoot = harness.render();
  const increaseCandidate = control(autoRoot, 'Increase candidate count');
  (increaseCandidate.props.onClick as () => void)();

  switchImageStrategy(harness.render(), 'image_3_0');
  switchImageStrategy(harness.render(), 'image_5_0');
  const manualRoot = harness.render();
  const generationMode = control(manualRoot, 'Generation mode');
  (generationMode.props.onChange as (event: { target: { value: 'auto' } }) => void)({
    target: { value: 'auto' },
  });

  assert.equal(control(harness.render(), 'Auto candidate count').props.value, 2);
});

test('Image mode guidance uses the three approved literal strings', () => {
  const image50Auto = textContent(createGenerateHarness('image_5_0', { generationMode: 'auto' }).render());
  assert.match(image50Auto, /Uses AI-powered fully automatic layout and design guidance\./);
  for (const strategy of ['image_1_0', 'image_3_0', 'image_3_2', 'image_direct'] as RouteStrategy[]) {
    assert.doesNotMatch(
      textContent(createGenerateHarness(strategy).render()),
      /Uses AI-powered fully automatic layout and design guidance\./,
    );
  }

  const image50Manual = textContent(createGenerateHarness('image_5_0').render());
  assert.match(
    image50Manual,
    /Select optional Requirements and Colors\. Leave both empty to use no Custom Instruction or preset color\./,
  );

  for (const strategy of ['image_3_0', 'image_3_2'] as RouteStrategy[]) {
    assert.match(
      textContent(createGenerateHarness(strategy).render()),
      /This strategy manages its own seed and palette inputs\. Custom Requirements and Colors are not used\./,
    );
  }
});

test('Image 1.0 and ImageDirect keep accurate route-specific Reference Input Maps', () => {
  const image10Map = textContent(classNodes(createGenerateHarness('image_1_0').render(), 'reference-map')[0]);
  assert.match(image10Map, /Conversation Session/);
  assert.match(image10Map, /Per-slide Prompt/);
  assert.match(image10Map, /Generic Requirements and Colors are not used\./);
  assert.match(image10Map, /first content slide.*anchor/i);
  assert.match(image10Map, /later slides.*conversation/i);
  assert.doesNotMatch(image10Map, /Each slide must continue/i);
  assert.doesNotMatch(image10Map, /seed|palette/i);
  const image10Guidance = textContent(createGenerateHarness('image_1_0').render());
  assert.match(image10Guidance, /first content slide.*anchor/i);
  assert.match(image10Guidance, /later slides.*conversation/i);
  assert.doesNotMatch(image10Guidance, /Each slide continues/i);

  const directMap = textContent(classNodes(createGenerateHarness('image_direct').render(), 'reference-map')[0]);
  assert.match(directMap, /Current Slide Content/);
  assert.match(directMap, /Design Director/);
  assert.match(directMap, /Generic Requirements and Colors are not used\./);
  assert.doesNotMatch(directMap, /seed|palette/i);
});

test('Image 3.0 and 3.2 Reference Input Maps preserve their opposite dependency orders', () => {
  const image30Map = textContent(classNodes(createGenerateHarness('image_3_0').render(), 'reference-map')[0]);
  assert.match(image30Map, /Page 2 Content Seed.*Seed Palette.*Cover.*Remaining Pages/);
  assert.match(image30Map, /Cover.*palette values.*seed PNG is not sent/i);
  assert.match(image30Map, /Remaining Pages.*Seed PNG.*Seed XML.*Style DNA/i);

  const image32Map = textContent(classNodes(createGenerateHarness('image_3_2').render(), 'reference-map')[0]);
  assert.match(image32Map, /Cover.*Cover Palette.*Content Seed.*Remaining Pages/);
  assert.match(image32Map, /Content Seed.*cover-derived palette only.*cover PNG is not sent/i);
});
