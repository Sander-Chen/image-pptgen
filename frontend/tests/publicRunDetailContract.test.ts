import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { createServer, type ViteDevServer } from 'vite';

const runDetail = readFileSync(new URL('../src/components/RunDetail.tsx', import.meta.url), 'utf8');
const appCss = readFileSync(new URL('../src/App.css', import.meta.url), 'utf8');
let vite: ViteDevServer | null = null;
let publicPositionTwoSeedEvidence: ((
  run: { stage_artifacts?: unknown },
  seedSlide?: { id: number; position: number; status?: string; final_image_path?: string; stage_artifacts?: unknown },
) => unknown) | undefined;
let findByKey: ((value: unknown, matcher: RegExp) => unknown) | undefined;

test.before(async () => {
  vite = await createServer({
    root: new URL('../', import.meta.url).pathname,
    configFile: false,
    appType: 'custom',
    logLevel: 'error',
    optimizeDeps: { noDiscovery: true },
    ssr: { noExternal: ['antd', '@ant-design/icons', 'react-router-dom'] },
    plugins: [{
      name: 'public-run-detail-contract-mocks',
      enforce: 'pre',
      resolveId(id) {
        if (id === 'antd') return '\0public-run-detail-antd';
        if (id === '@ant-design/icons') return '\0public-run-detail-icons';
        if (id === 'react-router-dom') return '\0public-run-detail-router';
        return null;
      },
      load(id) {
        if (id === '\0public-run-detail-icons') {
          return 'export const ArrowLeftOutlined = "ArrowLeftOutlined"; export const DownloadOutlined = "DownloadOutlined"; export const ReloadOutlined = "ReloadOutlined";';
        }
        if (id === '\0public-run-detail-router') return 'export const useNavigate = () => () => {};';
        if (id === '\0public-run-detail-antd') {
          return `
            export const Descriptions = Object.assign(({ children }) => children, { Item: ({ label, children }) => [label, children] }); export const Card = 'Card';
            export const Collapse = 'Collapse'; export const Empty = 'Empty'; export const Image = 'Image'; export const Spin = 'Spin'; export const Space = 'Space';
            export const Table = 'Table'; export const Tag = 'Tag'; export const Button = 'Button'; export const Alert = 'Alert';
            export const message = { success() {}, error() {}, warning() {}, info() {} };
          `;
        }
        return null;
      },
      transform(code, id) {
        if (id.endsWith('/src/components/RunDetail.tsx')) return `${code}\nexport { findByKey, publicPositionTwoSeedEvidence };`;
        return null;
      },
    }],
  });
  const module = await vite.ssrLoadModule('/src/components/RunDetail.tsx');
  publicPositionTwoSeedEvidence = module.publicPositionTwoSeedEvidence as typeof publicPositionTwoSeedEvidence;
  findByKey = module.findByKey as typeof findByKey;
});

test.after(async () => {
  await vite?.close();
});

test('public Run Detail is an Image 3.0 evidence-only surface', () => {
  for (const required of [
    'Generated Outputs',
    'completed_with_failures',
    'final_image_path',
    'Position 2 Seed',
    'Palette lineage',
    'Request Chain',
    'Codex Audit',
  ]) {
    assert.match(runDetail, new RegExp(required.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
  }

  for (const retired of [
    'Force Run',
    'Force Slide',
    'Retry',
    'Machine QA',
    'Evaluation',
    'ImageDirect',
    'active_version',
    'html_path',
    'clean_html',
    'screenshot_path',
    'route_metadata',
    'model_call_metadata',
  ]) {
    assert.equal(runDetail.includes(retired), false, `retired Run Detail behavior remains: ${retired}`);
  }
});

test('public Run Detail derives slide evidence from the safe final image field', () => {
  assert.match(runDetail, /slide\.final_image_path/);
  assert.match(runDetail, /toArtifactUrl\(slide\.final_image_path\)/);
  assert.doesNotMatch(runDetail, /slide\.active_version/);
});

test('public Codex Audit timeline keeps its Ant Table inside the mobile evidence width', () => {
  assert.match(runDetail, /className="public-audit-events-table"/);
  assert.match(appCss, /\.public-audit-events-table\s+\.ant-table-wrapper\s*\{[\s\S]*?min-width:\s*0/);
  assert.match(appCss, /\.public-audit-events-table\s+\.ant-table-content\s*\{[\s\S]*?overflow-x:\s*auto/);
  assert.match(appCss, /\.public-audit-events-table\s+\.ant-table-cell\s*\{[\s\S]*?overflow-wrap:\s*anywhere/);
});

test('Position 2 Seed evidence prefers the authoritative top-level lineage over nested placeholders', () => {
  const stageArtifacts = {
    request_chain: {
      stages: [{
        id: 'seed-content-generation',
        references: {
          seed_xml: { status: 'placeholder', seed_png_sha256: 'placeholder-seed-sha' },
          palette: { status: 'placeholder', palette_sha256: 'placeholder-palette-sha' },
        },
      }],
    },
    seed_palette_lineage: {
      run_id: 401,
      run_slide_id: 4012,
      deck_position: 2,
      extraction_stage: 'seed_palette_extraction',
      seed_png_sha256: 'authoritative-seed-sha',
      palette_sha256: 'authoritative-palette-sha',
      colors: ['#102030', '#405060'],
      output_path: '/private/should-not-render.png',
    },
  };
  const seedSlide = {
    id: 4012,
    position: 2,
    status: 'completed',
    final_image_path: '/artifacts/runs/401/02.png',
    stage_artifacts: stageArtifacts.request_chain,
  };

  const legacySeed = findByKey?.(seedSlide.stage_artifacts, /seed/i) as Record<string, unknown> | undefined;
  const legacyPalette = findByKey?.(stageArtifacts, /palette/i) as Record<string, unknown> | undefined;
  assert.equal(legacySeed?.seed_png_sha256, 'placeholder-seed-sha');
  assert.equal(legacyPalette?.palette_sha256, 'placeholder-palette-sha');

  assert.equal(typeof publicPositionTwoSeedEvidence, 'function');
  assert.deepEqual(
    publicPositionTwoSeedEvidence?.({ stage_artifacts: stageArtifacts }, seedSlide),
    {
      run_slide_id: 4012,
      deck_position: 2,
      status: 'completed',
      seed_png_sha256: 'authoritative-seed-sha',
      palette_sha256: 'authoritative-palette-sha',
      colors: ['#102030', '#405060'],
    },
  );
  assert.doesNotMatch(runDetail, /findByKey\(seedSlide\?\.stage_artifacts, \/seed\/i\)/);
  assert.doesNotMatch(runDetail, /findByKey\(run\?\.stage_artifacts, \/palette\/i\)/);
});
