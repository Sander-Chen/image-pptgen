import { Fragment, useMemo, useState } from 'react';
import type { ReactNode } from 'react';
import {
  Alert,
  Badge,
  Button,
  Collapse,
  DatePicker,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  InputNumber,
  Layout,
  Menu,
  Modal,
  Progress,
  Radio,
  Segmented,
  Select,
  Space,
  Slider,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import {
  ApiOutlined,
  AppstoreOutlined,
  ArrowLeftOutlined,
  BarChartOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  HistoryOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RollbackOutlined,
  SearchOutlined,
  SettingOutlined,
  ThunderboltOutlined,
  UserOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { HashRouter, Navigate, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';

const { Content, Sider } = Layout;
const { Text } = Typography;
const { RangePicker } = DatePicker;

type ForceScope = 'Batch' | 'Run' | 'Slide';
type ForceMode = 'overwrite' | 'new';
type AttemptStatus = 'success' | 'failed' | 'running' | 'legacy';
type VersionStatus = 'active' | 'available';
type RunType = 'image' | 'html';

interface PendingForce {
  scope: ForceScope;
  target: string;
}

interface SlideRow {
  key: string;
  position: number;
  title: string;
  status: 'completed' | 'failed' | 'missing' | 'pending';
  hasArtifact: boolean;
  activeVersion: string;
  latestIssue?: string;
}

interface VersionRow {
  key: string;
  version: string;
  status: VersionStatus;
  createdAt: string;
  artifact: string;
  prompt: string;
  model: string;
  note: string;
}

interface HistoryAttempt {
  key: string;
  time: string;
  operation: string;
  scope: string;
  status: AttemptStatus;
  version: string;
  summary: string;
}

interface XmlRow {
  key: string;
  node: string;
  field: string;
  value: string;
}

const menuItems = [
  { key: '/data', icon: <DatabaseOutlined />, label: 'Data' },
  { key: '/generate', icon: <ThunderboltOutlined />, label: 'Generate' },
  { key: '/history', icon: <HistoryOutlined />, label: 'History' },
  { key: '/runfail', icon: <BarChartOutlined />, label: 'RunFail Stats' },
  { key: '/prompts', icon: <FileTextOutlined />, label: 'Prompts' },
  { key: '/config', icon: <SettingOutlined />, label: 'Config' },
];

const slides: SlideRow[] = [
  { key: 's1', position: 1, title: 'Opening Context', status: 'completed', hasArtifact: true, activeVersion: 'v3' },
  { key: 's2', position: 2, title: 'Market Signals', status: 'completed', hasArtifact: true, activeVersion: 'v3' },
  { key: 's3', position: 3, title: 'Timeline', status: 'completed', hasArtifact: true, activeVersion: 'v2' },
  { key: 's4', position: 4, title: 'Architecture', status: 'completed', hasArtifact: true, activeVersion: 'v3' },
  { key: 's5', position: 5, title: 'Tradeoffs', status: 'completed', hasArtifact: true, activeVersion: 'v2' },
  { key: 's6', position: 6, title: 'Cost Guardrails', status: 'completed', hasArtifact: true, activeVersion: 'v3' },
  { key: 's7', position: 7, title: 'Image Route', status: 'completed', hasArtifact: true, activeVersion: 'v4' },
  { key: 's8', position: 8, title: 'Evidence Deep Dive', status: 'completed', hasArtifact: true, activeVersion: 'v5' },
  { key: 's9', position: 9, title: 'Recovery Queue', status: 'missing', hasArtifact: false, activeVersion: '-', latestIssue: 'No displayable active artifact' },
  { key: 's10', position: 10, title: 'Next Phase', status: 'pending', hasArtifact: false, activeVersion: '-', latestIssue: 'Waiting for Run-level retry' },
];

const htmlSlides: SlideRow[] = [
  { key: 'h1', position: 1, title: 'Rendered HTML Cover', status: 'completed', hasArtifact: true, activeVersion: 'v2' },
  { key: 'h2', position: 2, title: 'Clean HTML Layout', status: 'completed', hasArtifact: true, activeVersion: 'v2' },
  { key: 'h3', position: 3, title: 'Captured PNG Review', status: 'completed', hasArtifact: true, activeVersion: 'v1' },
  { key: 'h4', position: 4, title: 'Raw Response Trace', status: 'completed', hasArtifact: true, activeVersion: 'v1' },
  { key: 'h5', position: 5, title: 'Screenshot Timeout', status: 'failed', hasArtifact: false, activeVersion: '-', latestIssue: 'Playwright capture timed out before PNG was written' },
  { key: 'h6', position: 6, title: 'Original Markdown', status: 'completed', hasArtifact: true, activeVersion: 'v1' },
];

const richBlueprintXml = `<slide>
  <background color="#F5FAFD"/>
  <header title="日内多维能源负荷观测" subtitle="2026-05-20 智慧园区小时级监测指标" color="#0B243B"/>
  <layout type="grid" columns="12" rows="8" gap="20">
    <section id="insight" col="1" row="2" colspan="5" rowspan="5" backgroundColor="#FFFFFF" borderColor="#4B9CD3">
      <textBox title="可视化分析指引" textColor="#0B243B" fontSize="24">
        <item>总负荷、温度、占用率、电价变化曲线并列观察</item>
        <item>高峰时段使用强调色标注，避免只依赖颜色</item>
      </textBox>
      <metric label="Peak load" value="1240kW" x="36" y="220" color="#C91F37"/>
    </section>
    <section id="chart" col="6" row="2" colspan="7" rowspan="5">
      <chart type="multi-line" dataSource="hourlyLoadData" xAxis="hour" yAxis="load">
        <series name="totalLoad" color="#0B243B"/>
        <series name="temperature" color="#4B9CD3"/>
        <annotation x="18:00" label="evening peak" />
      </chart>
      <legend position="bottom" itemGap="12"/>
    </section>
  </layout>
  <style>
    <font family="Inter" weight="600"/>
    <card radius="8" shadow="subtle"/>
    <safeArea left="72" top="48" right="72" bottom="48"/>
  </style>
  <media>
    <icon name="zap" x="72" y="620" size="28"/>
    <imageRef id="active-version-preview" src="slide-08-v5.png" fit="cover"/>
  </media>
  <unknownExperimentalNode role="stress-test" tokens="layout,color,chart,list,media"/>
</slide>`;

const versions: VersionRow[] = [
  {
    key: 'v5',
    version: 'v5',
    status: 'active',
    createdAt: '2026-06-01 10:58',
    artifact: 'slide-08-v5.png',
    prompt: 'image-5-0-unified-v5',
    model: 'openai/gpt-5.4 + gemini-3.1-flash-image',
    note: 'Approved active image after Force overwrite. Download uses this version.',
  },
  {
    key: 'v4',
    version: 'v4',
    status: 'available',
    createdAt: '2026-06-01 10:41',
    artifact: 'slide-08-v4.png',
    prompt: 'image-5-0-unified-v5',
    model: 'openai/gpt-5.4 + gemini-3.1-flash-image',
    note: 'Prompt path fixed, but evidence XML had low readability.',
  },
  {
    key: 'v3',
    version: 'v3',
    status: 'available',
    createdAt: '2026-06-01 10:22',
    artifact: 'slide-08-v3.png',
    prompt: 'image-5-0-unified-v4',
    model: 'openai/gpt-5.1 + gemini-3.1-flash-image',
    note: 'Older successful result. Can be set current without duplicating image.',
  },
];

const generationHistory: HistoryAttempt[] = [
  { key: 'a8', time: '11:08', operation: 'Model Test', scope: 'Image Generator', status: 'success', version: '-', summary: 'gemini-3-pro-image test created a temporary preview image; cleanup recorded after save.' },
  { key: 'a7', time: '11:02', operation: 'version_restored', scope: 'Slide 8', status: 'success', version: 'v5', summary: 'Set v5 as current. No artifact duplicated.' },
  { key: 'a6', time: '10:58', operation: 'Force overwrite', scope: 'Slide 8', status: 'success', version: 'v5', summary: 'Old v4 retained, active pointer moved to v5.' },
  { key: 'a5', time: '10:52', operation: 'Force overwrite', scope: 'Slide 8', status: 'failed', version: '-', summary: 'Provider returned empty image bytes. Active v4 stayed visible.' },
  { key: 'a4', time: '10:41', operation: 'Force new run', scope: 'Run 48', status: 'success', version: 'v4', summary: 'Created Run 49 under Batch 12, linked back to Slide 8.' },
  { key: 'a3', time: '10:33', operation: 'Legacy Continue', scope: 'Run 48', status: 'legacy', version: '-', summary: 'Old behavior kept as generation record only, not a current action.' },
  { key: 'a2', time: '10:22', operation: 'Retry', scope: 'Slide 8', status: 'success', version: 'v3', summary: 'Retry was valid because no displayable active artifact existed at the time.' },
  { key: 'a1', time: '10:17', operation: 'Initial generation', scope: 'Run 48', status: 'failed', version: '-', summary: 'Slide 8 image missing. Retry enabled.' },
];

const slide7Versions: VersionRow[] = [
  {
    key: 's7-v4',
    version: 'v4',
    status: 'active',
    createdAt: '2026-06-01 10:50',
    artifact: 'slide-07-v4.png',
    prompt: 'image-5-0-unified-v5',
    model: 'openai/gpt-5.4 + gemini-3.1-flash-image',
    note: 'Current successful Slide 7 image after scoped Force. Download uses this version.',
  },
  {
    key: 's7-v3',
    version: 'v3',
    status: 'available',
    createdAt: '2026-06-01 10:30',
    artifact: 'slide-07-v3.png',
    prompt: 'image-5-0-unified-v5',
    model: 'openai/gpt-5.4 + gemini-3.1-flash-image',
    note: 'Original successful image retained after Force Image/Slide rerun.',
  },
];

const slide7GenerationHistory: HistoryAttempt[] = [
  { key: 's7-a5', time: '10:50', operation: 'Force overwrite', scope: 'Slide 7', status: 'success', version: 'v4', summary: 'Scoped Force reran the whole slide and moved active pointer from v3 to v4.' },
  { key: 's7-a4', time: '10:44', operation: 'Force overwrite', scope: 'Slide 7', status: 'failed', version: '-', summary: 'Provider timeout. Active v3 stayed visible in the slide grid.' },
  { key: 's7-a3', time: '10:30', operation: 'Initial generation', scope: 'Slide 7', status: 'success', version: 'v3', summary: 'Successful image exists; Retry is disabled and Force is required for another attempt.' },
  { key: 's7-a2', time: '10:24', operation: 'Model Test', scope: 'Image Generator', status: 'success', version: '-', summary: 'Temporary image preview generated and then deleted after profile save.' },
  { key: 's7-a1', time: '10:18', operation: 'Legacy Continue', scope: 'Run 48', status: 'legacy', version: '-', summary: 'Historical continue attempt remains visible only as a record.' },
];

const slide9GenerationHistory: HistoryAttempt[] = [
  { key: 's9-a3', time: '11:05', operation: 'Retry', scope: 'Slide 9', status: 'failed', version: '-', summary: 'Retry attempted because no displayable active artifact existed; provider returned empty image bytes.' },
  { key: 's9-a2', time: '10:35', operation: 'Initial generation', scope: 'Slide 9', status: 'failed', version: '-', summary: 'No successful version was created. Retry remains available.' },
  { key: 's9-a1', time: '10:33', operation: 'Legacy Continue', scope: 'Run 48', status: 'legacy', version: '-', summary: 'Historical continue attempt remains visible only as a generation record.' },
];

const slide10GenerationHistory: HistoryAttempt[] = [
  { key: 's10-a2', time: '11:12', operation: 'Run Retry', scope: 'Run 48', status: 'running', version: '-', summary: 'Run-level Retry queues only missing or failed slides, including Slide 10.' },
  { key: 's10-a1', time: '10:35', operation: 'Initial generation', scope: 'Slide 10', status: 'failed', version: '-', summary: 'Pending active artifact. No successful version exists yet.' },
];

function slideNumber(slide: SlideRow) {
  return String(slide.position).padStart(2, '0');
}

function getVersionsForSlide(slide: SlideRow): VersionRow[] {
  if (slide.position === 7) return slide7Versions;
  if (slide.position === 8) return versions;
  if (!slide.hasArtifact) return [];
  return [{
    key: `${slide.key}-${slide.activeVersion}`,
    version: slide.activeVersion,
    status: 'active',
    createdAt: '2026-06-01 10:20',
    artifact: `slide-${slideNumber(slide)}-${slide.activeVersion}.png`,
    prompt: 'image-5-0-unified-v5',
    model: 'Test',
    note: `Current successful artifact for Slide ${slide.position}.`,
  }];
}

function getHistoryForSlide(slide: SlideRow): HistoryAttempt[] {
  if (slide.position === 7) return slide7GenerationHistory;
  if (slide.position === 8) return generationHistory;
  if (slide.position === 9) return slide9GenerationHistory;
  if (slide.position === 10) return slide10GenerationHistory;
  return [{
    key: `${slide.key}-initial`,
    time: '10:20',
    operation: 'Initial generation',
    scope: `Slide ${slide.position}`,
    status: slide.hasArtifact ? 'success' : 'failed',
    version: slide.hasArtifact ? slide.activeVersion : '-',
    summary: slide.hasArtifact ? 'Successful artifact is available.' : 'No successful version exists yet.',
  }];
}

function getActiveArtifact(slide: SlideRow) {
  const active = getVersionsForSlide(slide).find((version) => version.status === 'active');
  return active?.artifact || null;
}

function getRenderedPrompt(slide: SlideRow) {
  return `Route instruction:
Produce one production-ready slide image from the current slide content.

Deck:
Feature improvement feedback for History, Evidence, Prompt Management, RunFail Stats, and Config.

Requirement:
Show active version lineage, preserve successful images, and keep failed attempts visible in Generation History.

Required Color:
Operational blue, neutral surfaces, status colors for success, failure, warning.

Slide:
Slide ${slide.position} - ${slide.title}. ${slide.hasArtifact ? 'Show active version lineage and downloadable evidence.' : 'Show retry eligibility and the absence of a successful active version.'}`;
}

function getBlueprintXml(slide: SlideRow) {
  const artifact = getActiveArtifact(slide);
  if (slide.position === 8 || slide.key.startsWith('h')) {
    return richBlueprintXml.replace('slide-08-v5.png', artifact || 'no-active-artifact.json');
  }
  return `<slide id="${slide.position}" title="${slide.title}">
  <layout width="1280" height="720" grid="12">
    <region name="title" x="72" y="58" w="720" h="72" />
    <region name="version_rail" x="890" y="72" w="300" h="530" />
    <region name="evidence_table" x="72" y="166" w="760" h="430" />
  </layout>
  <text region="title" weight="700" size="44">${slide.title}</text>
  <block region="evidence_table" kind="table">
    <row label="Prompt Path">/artifacts/run-48/slide-${slideNumber(slide)}/prompt.txt</row>
    <row label="Rendered Prompt">Deck, Requirement, Required Color, Current Slide</row>
    <row label="Blueprint XML">Structured, Raw XML, Search</row>
  </block>
  ${artifact ? `<image region="version_rail" src="${artifact}" active="true" />` : '<missing region="version_rail" reason="no_active_artifact" />'}
</slide>`;
}

function stripXmlFence(xml: string) {
  return xml.replace(/^```xml\s*/i, '').replace(/```\s*$/i, '').trim();
}

function extractXmlRows(xml: string, query: string): XmlRow[] {
  const cleaned = stripXmlFence(xml);
  const rows: XmlRow[] = [];
  const addRow = (node: string, field: string, value: unknown) => {
    const text = String(value ?? '').trim();
    if (!text) return;
    rows.push({
      key: `${rows.length}-${node}-${field}`,
      node,
      field,
      value: text.length > 220 ? `${text.slice(0, 220)}...` : text,
    });
  };

  if (typeof DOMParser !== 'undefined') {
    const doc = new DOMParser().parseFromString(cleaned, 'application/xml');
    const parseError = doc.querySelector('parsererror');
    if (!parseError && doc.documentElement) {
      const walk = (element: Element, path: string) => {
        const currentPath = path ? `${path}/${element.tagName}` : element.tagName;
        Array.from(element.attributes).forEach((attr) => addRow(currentPath, `@${attr.name}`, attr.value));
        const directText = Array.from(element.childNodes)
          .filter((node) => node.nodeType === Node.TEXT_NODE)
          .map((node) => node.textContent?.trim())
          .filter(Boolean)
          .join(' ');
        addRow(currentPath, 'text', directText);
        Array.from(element.children).forEach((child) => walk(child, currentPath));
      };
      walk(doc.documentElement, '');
    }
  }

  if (!rows.length) {
    Array.from(cleaned.matchAll(/<([a-zA-Z0-9_-]+)([^>]*)>/g)).forEach((match, index) => {
      addRow(`node:${match[1]}`, 'attributes', match[2] || `tag #${index + 1}`);
    });
  }

  const needle = query.trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter((row) => `${row.node} ${row.field} ${row.value}`.toLowerCase().includes(needle));
}

const modelProfiles = [
  { role: 'HTML Director', tier: 'Test', model: 'google/gemini-3.1-flash-lite-preview', thinking: 'default', temperature: '1', status: 'verified' },
  { role: 'HTML Director', tier: 'Production Mini', model: 'openai/gpt-5.4-mini', thinking: 'low', temperature: '1', status: 'verified' },
  { role: 'HTML Director', tier: 'Production Pro', model: 'openai/gpt-5.4', thinking: 'high', temperature: '1', status: 'verified' },
  { role: 'HTML Worker', tier: 'Test', model: 'google/gemini-3.1-flash-lite-preview', thinking: 'default', temperature: '1', status: 'verified' },
  { role: 'HTML Worker', tier: 'Production Mini', model: 'gemini-3-flash-preview', thinking: 'high', temperature: '1', status: 'verified' },
  { role: 'HTML Worker', tier: 'Production Pro', model: 'gemini-3.1-pro-preview', thinking: 'high', temperature: '1', status: 'verified' },
  { role: 'Image Designer', tier: 'Test', model: 'google/gemini-3.1-flash-lite-preview', thinking: 'default', temperature: '1', status: 'verified' },
  { role: 'Image Designer', tier: 'Production Legacy', model: 'openai/gpt-5.1', thinking: 'high', temperature: '1', status: 'verified' },
  { role: 'Image Designer', tier: 'Production', model: 'openai/gpt-5.4', thinking: 'high', temperature: '1', status: 'verified' },
  { role: 'Image Generator', tier: 'Test', model: 'gemini-3.1-flash-image', thinking: 'low', temperature: '1', status: 'verified' },
  { role: 'Image Generator', tier: 'Production Mini', model: 'gemini-3.1-flash-image', thinking: 'high', temperature: '1', status: 'verified' },
  { role: 'Image Generator', tier: 'Production', model: 'gemini-3-pro-image', thinking: 'high', temperature: '1', status: 'verified' },
];

function statusTag(status: string) {
  const colors: Record<string, string> = {
    completed: 'success',
    success: 'success',
    active: 'success',
    available: 'blue',
    failed: 'error',
    missing: 'error',
    pending: 'default',
    running: 'processing',
    legacy: 'gold',
    verified: 'success',
    blocked: 'error',
    draft: 'warning',
  };
  return <Tag color={colors[status] || 'default'}>{status}</Tag>;
}

function copyText(label: string, content: string) {
  navigator.clipboard?.writeText(content)
    .then(() => message.success(`${label} copied`))
    .catch(() => message.warning(`Select and copy ${label} manually`));
}

function PageHeader({ title, subtitle, actions }: { title: string; subtitle: string; actions?: ReactNode }) {
  return (
    <header className="page-toolbar feedback-page-toolbar">
      <div>
        <div className="page-kicker"><span className="status-dot" />Feedback round prototype</div>
        <h2>{title}</h2>
        <p className="toolbar-subtitle">{subtitle}</p>
      </div>
      {actions && <Space className="page-toolbar-actions" wrap>{actions}</Space>}
    </header>
  );
}

function PrototypeLayout() {
  const [collapsed, setCollapsed] = useState(true);
  const navigate = useNavigate();
  const location = useLocation();
  const selectedKey = menuItems.find((item) => location.pathname.startsWith(item.key))?.key || '/data';

  return (
    <Layout className="app-shell feedback-prototype-shell">
      <Sider
        className="app-sidebar"
        width={208}
        breakpoint="lg"
        collapsedWidth={72}
        collapsed={collapsed}
        onCollapse={setCollapsed}
      >
        <div className="app-brand">
          <span className="app-brand-full">HTML-PPT-Gen</span>
          <span className="app-brand-short">PPT</span>
        </div>
        <Tooltip title={collapsed ? 'Expand navigation' : 'Collapse navigation'} placement="right">
          <Button
            className="sidebar-collapse-button"
            aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed((value) => !value)}
          />
        </Tooltip>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
        <div className="app-user-card">
          <div className="app-user-avatar"><UserOutlined /></div>
          <div className="app-user-copy">
            <strong>admin</strong>
            <span>Prototype data</span>
          </div>
        </div>
      </Sider>
      <Layout style={{ minWidth: 0 }}>
        <Content className="app-content feedback-prototype">
          <Routes>
            <Route path="/" element={<Navigate to="/data" replace />} />
            <Route path="/history" element={<HistoryPrototype />} />
            <Route path="/history/batch/12" element={<BatchOverviewPrototype />} />
            <Route path="/history/run/:runId" element={<RunDetailPrototype />} />
            <Route path="/runfail" element={<RunFailPrototype />} />
            <Route path="/prompts" element={<PromptPrototype />} />
            <Route path="/config" element={<ConfigPrototype />} />
            <Route path="/generate" element={<PlaceholderPage title="Generate" />} />
            <Route path="/data" element={<PlaceholderPage title="Data" />} />
            <Route path="/system-settings" element={<Navigate to="/config" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}

function ForceDialog({ pending, onClose }: { pending: PendingForce | null; onClose: () => void }) {
  const [mode, setMode] = useState<ForceMode>('overwrite');
  const isBatch = pending?.scope === 'Batch';
  const newLabel = isBatch ? 'Create new Batch' : pending?.scope === 'Run' ? 'Create new Run' : 'Create scoped Slide attempt';

  return (
    <Modal
      title={pending ? `Force ${pending.scope}: ${pending.target}` : 'Force'}
      open={Boolean(pending)}
      onCancel={onClose}
      okText="Confirm Force"
      onOk={() => {
        message.success(`${pending?.scope} Force queued as ${mode === 'overwrite' ? 'overwrite current' : newLabel}`);
        onClose();
      }}
      destroyOnHidden
    >
      <Alert
        type="warning"
        showIcon
        title="Force is explicit because it can replace the active version pointer."
        description="Old successful artifacts are retained. Failed overwrite attempts do not hide the current active result."
      />
      <Radio.Group className="force-mode-group" value={mode} onChange={(event) => setMode(event.target.value)}>
        <Radio.Button value="overwrite">Overwrite current</Radio.Button>
        <Radio.Button value="new">{newLabel}</Radio.Button>
      </Radio.Group>
      <Descriptions size="small" bordered column={1}>
        <Descriptions.Item label="Scope">{pending?.scope}</Descriptions.Item>
        <Descriptions.Item label="Target">{pending?.target}</Descriptions.Item>
        <Descriptions.Item label="History write">Always records a Generation History attempt</Descriptions.Item>
        <Descriptions.Item label="Active version">Moves only after successful output validation</Descriptions.Item>
      </Descriptions>
    </Modal>
  );
}

function HistoryPrototype() {
  const navigate = useNavigate();
  const [pendingForce, setPendingForce] = useState<PendingForce | null>(null);
  const [expandedKeys, setExpandedKeys] = useState<readonly React.Key[]>(['batch-12']);
  const [selectedBatchKeys, setSelectedBatchKeys] = useState<React.Key[]>(['batch-12']);
  const [filtersOpen, setFiltersOpen] = useState(true);
  const [historyState, setHistoryState] = useState('ready');

  const batchRows = [
    {
      key: 'batch-12',
      id: 12,
      title: 'Feature feedback deck',
      type: 'Image',
      route: 'Image route',
      config: 'Test',
      status: 'failed',
      statusDetail: '2 missing slides',
      progress: 80,
      requirement: 'History and Evidence repair',
      color: 'Operational blue',
      viewPath: '/history/run/48',
      batchPath: '/history/batch/12',
      runs: [
        { key: 'r47', id: 47, status: 'completed', slide: 'Slide 8 has active image', hasArtifact: true, progress: '10/10', error: '-', viewPath: '/history/run/48' },
        { key: 'r48', id: 48, status: 'failed', slide: 'Slide 9 has no displayable output', hasArtifact: false, progress: '8/10', error: 'No displayable active artifact', viewPath: '/history/run/48' },
        { key: 'r49', id: 49, status: 'completed', slide: 'Slide 8 force overwrite succeeded', hasArtifact: true, progress: '10/10', error: '-', viewPath: '/history/run/48' },
      ],
    },
    {
      key: 'batch-11-html',
      id: 11,
      title: 'HTML evidence deck',
      type: 'HTML',
      route: 'HTML route',
      config: 'Test',
      status: 'completed',
      statusDetail: '0 missing outputs',
      progress: 100,
      requirement: 'Rendered prompt and HTML capture review',
      color: 'Production neutral',
      viewPath: '/history/run/38',
      batchPath: '/history/batch/12',
      runs: [
        { key: 'r38', id: 38, status: 'completed', slide: 'HTML captured PNG and clean HTML available', hasArtifact: true, progress: '6/6', error: '-', viewPath: '/history/run/38' },
        { key: 'r37', id: 37, status: 'failed', slide: 'Slide 5 screenshot timeout', hasArtifact: false, progress: '5/6', error: 'Playwright capture timed out', viewPath: '/history/run/38' },
      ],
    },
  ];

  const columns = [
    {
      title: 'Batch',
      dataIndex: 'id',
      width: 118,
      render: (_: unknown, record: (typeof batchRows)[number]) => (
        <div className="stack-cell">
          <Text strong>#{record.id}</Text>
          <span>2026-06-01 10:13</span>
        </div>
      ),
    },
    {
      title: 'Deck / Mode / Config',
      key: 'context',
      render: (_: unknown, record: (typeof batchRows)[number]) => (
        <div className="stack-cell wide">
          <Text strong>{record.title}</Text>
          <Space size={4} wrap>
            <Tag color={record.type === 'HTML' ? 'blue' : 'purple'}>{record.route}</Tag>
            <Tag>manual</Tag>
            <Tag color="purple">Config: {record.config}</Tag>
          </Space>
          <span>Req: {record.requirement}</span>
          <span>Color: {record.color}</span>
        </div>
      ),
    },
    {
      title: 'State',
      key: 'state',
      width: 240,
      render: (_: unknown, record: (typeof batchRows)[number]) => (
        <div className="stack-cell">
          <Space size={4} wrap>{statusTag(record.status)}<Tag color={record.status === 'failed' ? 'error' : 'success'}>{record.statusDetail}</Tag></Space>
          <Progress percent={record.progress} size="small" status={record.status === 'failed' ? 'exception' : 'success'} />
          <span>Retry can run only targets without displayable output.</span>
        </div>
      ),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 280,
      render: (_: unknown, record: (typeof batchRows)[number]) => (
        <Space size={4} wrap>
          <Button size="small" icon={<EyeOutlined />} onClick={() => navigate(record.viewPath)}>View</Button>
          <Button size="small" icon={<AppstoreOutlined />} onClick={() => navigate(record.batchPath)}>Batch</Button>
          <Button size="small" icon={<DownloadOutlined />}>ZIP</Button>
          <Tooltip title="Batch has active displayable results; Retry is only for missing outputs.">
            <Button size="small" disabled={record.status !== 'failed'}>Retry</Button>
          </Tooltip>
          <Button size="small" danger onClick={() => setPendingForce({ scope: 'Batch', target: `Batch #${record.id}` })}>Force</Button>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title="History Operations"
        subtitle="Target behavior for Batch, Run, and Slide actions. Continue is removed; successful versions and operation history are separated."
        actions={<><Button icon={<ReloadOutlined />}>Refresh</Button><Button type="primary" icon={<ThunderboltOutlined />}>New Batch</Button></>}
      />
      <div className="prototype-summary-grid">
        <div><span>Active Batches</span><strong>1</strong><Tag color="processing">running or failed</Tag></div>
        <div><span>Active Versions</span><strong>28</strong><Tag color="success">download source</Tag></div>
        <div><span>Missing Outputs</span><strong>2</strong><Tag color="error">Retry eligible</Tag></div>
        <div><span>Legacy Continue</span><strong>3</strong><Tag color="gold">history only</Tag></div>
      </div>
      <section className="prototype-panel">
        <div className="panel-head">
          <div>
            <h3>Batch History</h3>
            <p>Preserves production selection, bulk actions, collapsed filters, pagination, loading/empty states, expandable runs, downloads, and mobile card path.</p>
          </div>
          <Segmented value={historyState} onChange={(value) => setHistoryState(String(value))} options={[
            { label: 'Ready', value: 'ready' },
            { label: 'Loading', value: 'loading' },
            { label: 'Empty', value: 'empty' },
            { label: 'Error', value: 'error' },
          ]} />
        </div>
        {selectedBatchKeys.length > 0 && (
          <div className="bulk-action-bar prototype-bulk-bar">
            <Text strong>{selectedBatchKeys.length} selected</Text>
            <Button size="small" danger>Delete</Button>
            <Button size="small" onClick={() => setSelectedBatchKeys([])}>Clear</Button>
          </div>
        )}
        <Collapse
          className="history-filter-collapse prototype-collapse"
          activeKey={filtersOpen ? ['filters'] : []}
          onChange={(keys) => setFiltersOpen(Array.isArray(keys) ? keys.includes('filters') : keys === 'filters')}
          items={[{
            key: 'filters',
            label: <Space><SearchOutlined />Filters</Space>,
            children: (
              <div className="history-filter-grid">
                <label><span>Search</span><Input prefix={<SearchOutlined />} placeholder="Search deck, requirement, color, config, run" /></label>
                <label><span>Mode</span><Select value="all" options={[{ label: 'All modes', value: 'all' }, { label: 'HTML', value: 'html' }, { label: 'Image', value: 'image' }]} /></label>
                <label><span>Status</span><Select value="failed" options={[{ label: 'Failed', value: 'failed' }, { label: 'Completed', value: 'completed' }, { label: 'Running', value: 'running' }]} /></label>
                <label><span>Date</span><RangePicker /></label>
                <Button>Clear Filters</Button>
              </div>
            ),
          }]}
        />
        {historyState === 'error' && <Alert type="error" showIcon title="Failed to load batches: provider or API error." />}
        <Table
          className="responsive-table"
          rowKey="key"
          dataSource={historyState === 'empty' ? [] : batchRows}
          columns={columns}
          loading={historyState === 'loading'}
          rowSelection={{ selectedRowKeys: selectedBatchKeys, onChange: setSelectedBatchKeys }}
          expandable={{
            expandedRowKeys: expandedKeys,
            onExpandedRowsChange: setExpandedKeys,
            expandedRowRender: (record) => (
              <div className="run-card-grid">
                {record.runs.map((run) => {
                  const retryDisabled = run.hasArtifact;
                  return (
                    <article className="run-card" key={run.key}>
                      <header>
                        <div><Text strong>Run {run.id}</Text><span>{run.slide}</span></div>
                        {statusTag(run.status)}
                      </header>
                      <div className="run-card-meta">
                        <span>Progress <strong>{run.progress}</strong></span>
                        <span>Current display <strong>{run.hasArtifact ? 'available' : 'missing'}</strong></span>
                        <span>Error <strong>{run.error}</strong></span>
                      </div>
                      <Space size={4} wrap>
                        <Button size="small" onClick={() => navigate(run.viewPath)}>View</Button>
                        <Button size="small" onClick={() => navigate(record.batchPath)}>Batch</Button>
                        <Button size="small" icon={<DownloadOutlined />}>ZIP</Button>
                        <Tooltip title={retryDisabled ? 'Retry is disabled because this run has an active displayable result. Use Force.' : 'Retry only missing or failed outputs.'}>
                          <Button
                            size="small"
                            disabled={retryDisabled}
                            onClick={() => message.success(`Retry queued for Run ${run.id} missing outputs`)}
                          >
                            Retry
                          </Button>
                        </Tooltip>
                        <Button size="small" danger onClick={() => setPendingForce({ scope: 'Run', target: `Run ${run.id}` })}>Force</Button>
                      </Space>
                    </article>
                  );
                })}
              </div>
            ),
          }}
          pagination={{ pageSize: 10, total: historyState === 'empty' ? 0 : 34 }}
          scroll={{ x: 980 }}
        />
        <div className="mobile-history-card">
          <Text strong>Mobile card path</Text>
          <span>Batch #12 - same View, Batch, ZIP, Retry, and Force actions collapse into stacked touch targets.</span>
          <Space wrap><Button size="small">View</Button><Button size="small">Batch</Button><Button size="small">ZIP</Button><Button size="small" danger>Force</Button></Space>
        </div>
      </section>
      <ForceDialog pending={pendingForce} onClose={() => setPendingForce(null)} />
    </div>
  );
}

function SlidePreview({ slide }: { slide: SlideRow }) {
  return (
    <div className={`slide-preview ${slide.hasArtifact ? 'has-artifact' : 'missing-artifact'}`}>
      <div className="slide-preview-canvas">
        <div className="slide-preview-title">{slide.title}</div>
        <div className="slide-preview-bars">
          <span />
          <span />
          <span />
        </div>
        <div className="slide-preview-rail">
          <strong>{slide.hasArtifact ? slide.activeVersion : 'No active image'}</strong>
          <small>{slide.status}</small>
        </div>
      </div>
    </div>
  );
}

function BatchOverviewPrototype() {
  const navigate = useNavigate();
  const [pendingForce, setPendingForce] = useState<PendingForce | null>(null);
  const [selectedRunId, setSelectedRunId] = useState(48);
  const runs = [
    { id: 47, status: 'completed', progress: '10/10', summary: 'all active versions available', error: '' },
    { id: 48, status: 'failed', progress: '8/10', summary: 'slide 9 missing active output', error: 'No displayable active artifact' },
    { id: 49, status: 'completed', progress: '10/10', summary: 'force overwrite succeeded', error: '' },
  ];
  const selectedRun = runs.find((run) => run.id === selectedRunId) || runs[1];

  return (
    <div>
      <PageHeader
        title="Batch #12 Overview"
        subtitle="Production batch overview route preserved: sibling run cards first, selected run evidence second, and target-specific actions in context."
        actions={<><Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/history')}>History</Button><Button icon={<ReloadOutlined />}>Refresh</Button><Button danger onClick={() => setPendingForce({ scope: 'Batch', target: 'Batch #12' })}>Force Batch</Button></>}
      />
      <Descriptions bordered column={{ xs: 1, md: 3 }} className="batch-context">
        <Descriptions.Item label="Deck">Feature feedback deck</Descriptions.Item>
        <Descriptions.Item label="Mode">manual</Descriptions.Item>
        <Descriptions.Item label="Config">Test</Descriptions.Item>
        <Descriptions.Item label="Route">Image 5.0 target, stored as Image route</Descriptions.Item>
        <Descriptions.Item label="Created">2026-06-01 10:13</Descriptions.Item>
        <Descriptions.Item label="Failure Rate">20%</Descriptions.Item>
      </Descriptions>
      <section className="prototype-panel batch-overview-surface">
        <div className="panel-head compact">
          <div><h3>Sibling Run Outputs</h3><p>{runs.length} runs in this batch. Selecting a run updates the evidence panel.</p></div>
        </div>
        <div className="run-card-grid">
          {runs.map((run) => (
            <button type="button" className={`run-card run-select-card ${selectedRunId === run.id ? 'selected' : ''}`} key={run.id} onClick={() => setSelectedRunId(run.id)}>
              <header><Text strong>Run {run.id}</Text>{statusTag(run.status)}</header>
              <span>{run.summary}</span>
              <span>Progress {run.progress}</span>
              {run.error && <Alert type="error" showIcon title={run.error} />}
            </button>
          ))}
        </div>
      </section>
      <section className="prototype-panel batch-overview-surface">
        <div className="panel-head compact">
          <div><h3>Selected Run Evidence</h3><p>Run {selectedRun.id}: route metadata, active versions, and operation targets stay visible without leaving batch context.</p></div>
          <Space wrap>
            <Button icon={<EyeOutlined />} onClick={() => navigate('/history/run/48')}>View</Button>
            <Button icon={<DownloadOutlined />}>Run ZIP</Button>
            <Button disabled={selectedRun.status === 'completed'}>Retry Run</Button>
            <Button danger onClick={() => setPendingForce({ scope: 'Run', target: `Run ${selectedRun.id}` })}>Force Run</Button>
          </Space>
        </div>
        <div className="operation-target-grid">
          {slides.slice(6, 10).map((slide) => (
            <article className="operation-target-card" key={slide.key}>
              <Text strong>Slide {slide.position}</Text>
              <span>{slide.title} - {slide.hasArtifact ? `active ${slide.activeVersion}` : 'missing output'}</span>
              <Space wrap>
                <Button size="small" disabled={slide.hasArtifact}>Retry Slide</Button>
                <Button size="small" danger onClick={() => setPendingForce({ scope: 'Slide', target: `Slide ${slide.position}` })}>Force Slide</Button>
              </Space>
            </article>
          ))}
        </div>
        <Tabs
          items={[
            { key: 'route', label: 'Route metadata', children: <pre className="raw-code">{`{"engine":"image","target_name":"Image","strategy":"image_5_0","phase7_rename":"pending"}`}</pre> },
            { key: 'errors', label: 'Errors', children: <pre className="raw-code">{selectedRun.error || 'No selected run error'}</pre> },
            {
              key: 'versions',
              label: 'Versions',
              children: <VersionPanel selectedSlide={slides[7]} evidenceVersionKey={null} onViewEvidence={() => message.info('Open the run detail View action to inspect version evidence.')} />,
            },
          ]}
        />
      </section>
      <ForceDialog pending={pendingForce} onClose={() => setPendingForce(null)} />
    </div>
  );
}

function VersionPanel({ selectedSlide, evidenceVersionKey, onViewEvidence }: {
  selectedSlide: SlideRow;
  evidenceVersionKey: string | null;
  onViewEvidence: (versionKey: string) => void;
}) {
  const currentVersions = getVersionsForSlide(selectedSlide);
  return (
    <section>
      <div className="subhead">
        <h4>Versions for Slide {selectedSlide.position}</h4>
        <span>
          {currentVersions.length
            ? `Successful artifacts only. Download uses Slide ${selectedSlide.position}'s active version.`
            : 'No successful version exists yet. Retry is available because there is no displayable active artifact.'}
        </span>
      </div>
      {currentVersions.length ? (
        <div className="version-card-grid">
          {currentVersions.map((version) => (
            <article className={`version-card ${evidenceVersionKey === version.key ? 'selected' : ''}`} key={version.key}>
              <SlidePreview slide={{ ...selectedSlide, activeVersion: version.version, hasArtifact: true, status: 'completed' }} />
              <div>
                <Space size={6} wrap><Text strong>{version.version}</Text>{statusTag(version.status)}</Space>
                <span>{version.createdAt} - {version.artifact}</span>
                <p>{version.note}</p>
              </div>
              <Space wrap>
                <Button size="small" icon={<FileSearchOutlined />} onClick={() => onViewEvidence(version.key)}>
                  View Evidence
                </Button>
                {version.status === 'active' && <Button size="small" icon={<DownloadOutlined />}>Download Active</Button>}
                <Button size="small" icon={<RollbackOutlined />} disabled={version.status === 'active'}>Set current</Button>
              </Space>
            </article>
          ))}
        </div>
      ) : (
        <Empty
          description={`Slide ${selectedSlide.position} has no successful versions yet`}
          image={Empty.PRESENTED_IMAGE_SIMPLE}
        />
      )}
    </section>
  );
}

function GenerationHistoryPanel({ selectedSlide }: { selectedSlide: SlideRow }) {
  const currentHistory = getHistoryForSlide(selectedSlide);
  return (
    <section>
      <div className="subhead">
        <h4>Generation History for Slide {selectedSlide.position}</h4>
        <span>Every attempt, including failures, model tests, rollback, and Legacy Continue records.</span>
      </div>
      <Table
        size="small"
        rowKey="key"
        dataSource={currentHistory}
        columns={[
          { title: 'Time', dataIndex: 'time', width: 72 },
          { title: 'Operation', dataIndex: 'operation', width: 150 },
          { title: 'Scope', dataIndex: 'scope', width: 110 },
          { title: 'Status', dataIndex: 'status', width: 90, render: statusTag },
          { title: 'Version', dataIndex: 'version', width: 82 },
          { title: 'Summary', dataIndex: 'summary' },
        ]}
        pagination={false}
        scroll={{ x: 820 }}
      />
    </section>
  );
}

function CollapsedCode({ label, content, onOpen }: { label: string; content: string; onOpen: () => void }) {
  return (
    <div className="collapsed-evidence-block">
      <div className="detail-launch-row compact">
        <div>
          <Text strong>{label}</Text>
          <p>Preview is capped to keep the slide review workspace compact.</p>
        </div>
        <Space wrap>
          <Button icon={<EyeOutlined />} onClick={onOpen}>View full</Button>
          <Button icon={<CopyOutlined />} onClick={() => copyText(label, content)}>Copy</Button>
        </Space>
      </div>
      <pre className="raw-code clamped-code">{content}</pre>
    </div>
  );
}

function EvidencePanel({ selectedSlide, runType, evidenceVersionKey }: {
  selectedSlide: SlideRow;
  runType: RunType;
  evidenceVersionKey: string | null;
}) {
  const [promptOpen, setPromptOpen] = useState(false);
  const [renderedOpen, setRenderedOpen] = useState(false);
  const [xmlOpen, setXmlOpen] = useState(false);
  const [detailModal, setDetailModal] = useState<{ title: string; content: string } | null>(null);
  const [xmlSearch, setXmlSearch] = useState('');
  const [promptPathState, setPromptPathState] = useState<'available' | 'missing'>('available');
  const currentVersions = getVersionsForSlide(selectedSlide);
  const activeVersion = currentVersions.find((version) => version.status === 'active');
  const selectedEvidenceVersion = currentVersions.find((version) => version.key === evidenceVersionKey) || activeVersion;
  const artifact = activeVersion?.artifact;
  const promptPath = `/artifacts/run-48/slide-${slideNumber(selectedSlide)}/prompt.txt`;
  const missingPromptPath = `/artifacts/run-48/slide-${slideNumber(selectedSlide)}/missing-prompt.txt`;
  const renderedPrompt = getRenderedPrompt(selectedSlide);
  const blueprintXml = getBlueprintXml(selectedSlide);
  const xmlRows = useMemo(() => extractXmlRows(blueprintXml, xmlSearch), [blueprintXml, xmlSearch]);
  const requestLabel = runType === 'html' ? 'HTML Request' : 'Image Request';
  const requestPayload = runType === 'html'
    ? `{"url":"/html-agent/render","headers":{"authorization":"<redacted>"},"body":{"prompt":"<rendered prompt>","slide_html":"<section .../>","capture_target":"slide-${slideNumber(selectedSlide)}"}}`
    : `{"url":"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-image:generateContent","headers":{"authorization":"<redacted>"},"body":{"prompt":"<rendered prompt>","blueprint_xml":"<slide .../>","version":"${selectedEvidenceVersion?.version || '-'}"}}`;
  const responsePayload = selectedSlide.hasArtifact
    ? `{"status_code":200,"elapsed_seconds":18.4,"request_id":"req_redacted_48","active_version":"${selectedEvidenceVersion?.version || '-'}","final_artifact":"${selectedEvidenceVersion?.artifact || artifact}"}`
    : `{"status_code":502,"elapsed_seconds":18.4,"request_id":"req_redacted_48","final_artifact":null,"error":"${selectedSlide.latestIssue}"}`;

  return (
    <>
      <Alert
        className="evidence-version-alert"
        type="info"
        showIcon
        title={`Evidence view: ${selectedEvidenceVersion?.version || 'no successful version'} / Slide ${selectedSlide.position}`}
        description="Clicking View Evidence on a historical version switches this panel to that version without moving the active pointer."
      />
      <Tabs
        className="evidence-tabs"
        items={[
          {
            key: 'overview',
            label: 'Overview',
            children: (
              <div className="overview-grid">
                <Descriptions bordered size="small" column={1}>
                  <Descriptions.Item label="Current version">
                    Slide {selectedSlide.position} / active {activeVersion?.version || 'none'} / evidence {selectedEvidenceVersion?.version || 'none'}
                  </Descriptions.Item>
                  <Descriptions.Item label="Status">
                    {selectedSlide.hasArtifact ? 'Completed, displayable, download source' : `${selectedSlide.status}: ${selectedSlide.latestIssue}`}
                  </Descriptions.Item>
                  <Descriptions.Item label="Key inputs">Deck, Requirement, Required Color, Slide {selectedSlide.position} content</Descriptions.Item>
                  <Descriptions.Item label="Latest warning">
                    {selectedSlide.hasArtifact ? 'Failed overwrite attempts keep the previous active artifact visible' : 'No successful version exists yet; Retry is allowed'}
                  </Descriptions.Item>
                  <Descriptions.Item label="Evidence links">{runType === 'image' ? 'Prompt Path, Rendered Prompt, Blueprint XML, Image Request, Response' : 'Prompt Path, Rendered Prompt, HTML Request, Response, HTML Evidence Path'}</Descriptions.Item>
                </Descriptions>
                <Alert
                  type={selectedSlide.hasArtifact ? 'success' : 'warning'}
                  showIcon
                  title="Overview is summarized content, not a raw prompt dump."
                  description="Long prompt, XML, request, and response content move into compact previews with full-screen or drawer detail views."
                />
              </div>
            ),
          },
          {
            key: 'prompt-path',
            label: 'Prompt Path',
            children: (
              <div className="prompt-path-state">
                <Segmented
                  value={promptPathState}
                  onChange={(value) => setPromptPathState(value as 'available' | 'missing')}
                  options={[
                    { label: 'TXT available', value: 'available' },
                    { label: 'TXT missing fallback', value: 'missing' },
                  ]}
                />
                <div className="detail-launch-row">
                  <div>
                    <Text code>{promptPathState === 'available' ? promptPath : missingPromptPath}</Text>
                    <p>{promptPathState === 'available'
                      ? `Open the Slide ${selectedSlide.position} TXT content directly.`
                      : 'File not found. The full prompt snapshot stored with the active version is shown instead.'}</p>
                  </div>
                  <Button icon={<FileSearchOutlined />} onClick={() => setPromptOpen(true)}>
                    {promptPathState === 'available' ? 'Open TXT' : 'Open Snapshot Fallback'}
                  </Button>
                </div>
                {promptPathState === 'missing' && (
                  <Alert
                    type="error"
                    showIcon
                    title="Prompt TXT file is missing"
                    description="The implementation must show this path error and then render the saved evidence snapshot so users do not hunt for files manually."
                  />
                )}
              </div>
            ),
          },
          {
            key: 'rendered-prompt',
            label: 'Rendered Prompt',
            children: (
              <CollapsedCode label="Rendered Prompt" content={renderedPrompt} onOpen={() => setRenderedOpen(true)} />
            ),
          },
          {
            key: 'config',
            label: 'Config',
            children: (
              <Descriptions bordered size="small" column={1}>
                  <Descriptions.Item label="Config">Test</Descriptions.Item>
                  <Descriptions.Item label="Engine / strategy">image / image_5_0, shown as Image route target</Descriptions.Item>
                  <Descriptions.Item label="Model profile">Image Designer Test + Image Generator Test</Descriptions.Item>
                  <Descriptions.Item label="Prompt role">Unified Designer, image request, XML cleanup</Descriptions.Item>
                  <Descriptions.Item label="Slide binding">Slide {selectedSlide.position} / {selectedSlide.title}</Descriptions.Item>
                  <Descriptions.Item label="API key">redacted</Descriptions.Item>
                </Descriptions>
            ),
          },
          ...(runType === 'image' ? [{
            key: 'xml',
            label: 'Blueprint XML',
            children: (
              <div className="xml-workbench">
                <div className="xml-toolbar">
                  <Input
                    id="blueprint-xml-search"
                    name="blueprint-xml-search"
                    aria-label="Search XML nodes fields or values"
                    prefix={<SearchOutlined />}
                    value={xmlSearch}
                    onChange={(event) => setXmlSearch(event.target.value)}
                    placeholder="Search XML nodes, fields, or values"
                  />
                  <Button icon={<CopyOutlined />} onClick={() => copyText('Blueprint XML', blueprintXml)}>Copy XML</Button>
                  <Button icon={<DownloadOutlined />}>Download XML</Button>
                  <Button icon={<EyeOutlined />} onClick={() => setXmlOpen(true)}>View full</Button>
                </div>
                <Tabs
                  items={[
                    {
                      key: 'structured',
                      label: 'Structured',
                      children: (
                        <Table
                          size="small"
                          rowKey="key"
                          dataSource={xmlRows}
                          columns={[
                            { title: 'Node', dataIndex: 'node', width: 170 },
                            { title: 'Field', dataIndex: 'field', width: 150 },
                            { title: 'Value', dataIndex: 'value' },
                          ]}
                          pagination={false}
                        />
                      ),
                    },
                    {
                      key: 'raw',
                      label: 'Raw XML',
                      children: <pre className="raw-code nowrap clamped-code">{blueprintXml}</pre>,
                    },
                    {
                      key: 'search',
                      label: 'Search',
                      children: xmlRows.length ? (
                        <div className="search-results">{xmlRows.map((row) => <Tag key={row.key}>{row.node}: {row.field}</Tag>)}</div>
                      ) : <Empty description="No matching XML nodes" />,
                    },
                  ]}
                />
              </div>
            ),
          }] : []),
          {
            key: 'request',
            label: requestLabel,
            children: <CollapsedCode label={requestLabel} content={requestPayload} onOpen={() => setDetailModal({ title: `${requestLabel} - Slide ${selectedSlide.position}`, content: requestPayload })} />,
          },
          {
            key: 'response',
            label: 'Response',
            children: <CollapsedCode label="Response" content={responsePayload} onOpen={() => setDetailModal({ title: `Response - Slide ${selectedSlide.position}`, content: responsePayload })} />,
          },
          {
            key: 'deps',
            label: 'Dependencies',
            children: <pre className="raw-code clamped-code">{`{"cover_reference":"slide-01-v3.png","seed_dependency":"none","active_version":"${activeVersion?.version || '-'}","source_run_slide_id":${400 + selectedSlide.position}}`}</pre>,
          },
          {
            key: 'download-evidence',
            label: 'Download Evidence',
            children: (
              <div className="download-evidence-panel">
                <Button type="primary" icon={<DownloadOutlined />} onClick={() => message.success(`Evidence ZIP prepared for Slide ${selectedSlide.position}`)}>Download Evidence ZIP</Button>
                <pre className="raw-code clamped-code">{`manifest.json\n${artifact ? `active_artifact/${artifact}` : 'active_artifact/no-active-artifact.json'}\nprompt/prompt.txt\nprompt/rendered_prompt.txt\nconfig/config.json\nresponse/provider_response.json\n${runType === 'html' ? 'html/clean.html\nhtml/captured.png' : 'blueprint/blueprint.xml'}\ngeneration_history/history.json\nversions/versions.json`}</pre>
              </div>
            ),
          },
        ]}
      />
      <Modal
        title={`Prompt TXT - Slide ${selectedSlide.position}`}
        open={promptOpen}
        onCancel={() => setPromptOpen(false)}
        footer={<Button type="primary" onClick={() => setPromptOpen(false)}>Close</Button>}
        width="92vw"
        style={{ top: 18 }}
      >
        <div className="fullscreen-detail">
          <div className="detail-side-index">
            <Button size="small" icon={<CopyOutlined />} onClick={() => copyText('Prompt TXT', renderedPrompt)}>Copy</Button>
            <Button size="small" icon={<DownloadOutlined />}>Download</Button>
          </div>
          <pre className="raw-code">{renderedPrompt}</pre>
        </div>
      </Modal>
      <Drawer
        title={`Rendered Prompt Detail - Slide ${selectedSlide.position}`}
        open={renderedOpen}
        onClose={() => setRenderedOpen(false)}
        width="88vw"
        extra={<Button icon={<CopyOutlined />} onClick={() => copyText('Rendered Prompt', renderedPrompt)}>Copy</Button>}
      >
        <div className="fullscreen-detail">
          <nav className="detail-side-index">
            {['Route instruction', 'Deck context', 'Requirement', 'Required Color', 'Current Slide'].map((item) => <a key={item}>{item}</a>)}
          </nav>
          <pre className="raw-code">{renderedPrompt}</pre>
        </div>
      </Drawer>
      <Modal
        title={`Blueprint XML - Slide ${selectedSlide.position}`}
        open={xmlOpen}
        onCancel={() => setXmlOpen(false)}
        footer={<Button type="primary" onClick={() => setXmlOpen(false)}>Close</Button>}
        width="92vw"
        style={{ top: 18 }}
      >
        <pre className="raw-code nowrap fullscreen-code">{blueprintXml}</pre>
      </Modal>
      <Modal
        title={detailModal?.title || 'Detail'}
        open={Boolean(detailModal)}
        onCancel={() => setDetailModal(null)}
        footer={<Button type="primary" onClick={() => setDetailModal(null)}>Close</Button>}
        width="86vw"
        style={{ top: 24 }}
      >
        <pre className="raw-code fullscreen-code">{detailModal?.content || ''}</pre>
      </Modal>
    </>
  );
}

function RunDetailPrototype() {
  const navigate = useNavigate();
  const { runId = '48' } = useParams();
  const runType: RunType = runId === '38' ? 'html' : 'image';
  const runSlides = runType === 'html' ? htmlSlides : slides;
  const [selectedSlideKey, setSelectedSlideKey] = useState('s8');
  const [pendingForce, setPendingForce] = useState<PendingForce | null>(null);
  const [reviewMode, setReviewMode] = useState('Tiled Review');
  const [zoom, setZoom] = useState(58);
  const [detailTab, setDetailTab] = useState('versions');
  const [evidenceVersionKey, setEvidenceVersionKey] = useState<string | null>(null);
  const selectedSlide = runSlides.find((slide) => slide.key === selectedSlideKey) || (runType === 'html' ? htmlSlides[0] : slides[7]);
  const selectedSlideIndex = runSlides.findIndex((slide) => slide.key === selectedSlide.key);
  const inlineDetailAfterIndex = Math.min(
    selectedSlideIndex + (selectedSlideIndex % 2 === 0 ? 1 : 0),
    runSlides.length - 1,
  );
  const retryDisabled = selectedSlide.hasArtifact;
  const routeFlow = runType === 'html'
    ? ['Designer Prompt', 'HTML Agent', 'Clean HTML', 'Captured PNG', 'Evidence ZIP']
    : ['Unified Designer', 'Blueprint XML', 'Image Request', 'Response', 'Active Version'];
  const topTabs = [
    {
      key: 'versions',
      label: 'Versions',
      children: (
        <VersionPanel
          selectedSlide={selectedSlide}
          evidenceVersionKey={evidenceVersionKey}
          onViewEvidence={(versionKey) => {
            setEvidenceVersionKey(versionKey);
            setDetailTab('evidence');
          }}
        />
      ),
    },
    { key: 'history', label: 'Generation History', children: <GenerationHistoryPanel selectedSlide={selectedSlide} /> },
    { key: 'evidence', label: 'Evidence Detail', children: <EvidencePanel selectedSlide={selectedSlide} runType={runType} evidenceVersionKey={evidenceVersionKey} /> },
    ...(runType === 'html' ? [{
      key: 'html-preview',
      label: 'HTML Evidence Path',
      children: (
        <Tabs
          items={[
            { key: 'captured', label: 'Captured PNG', children: <SlidePreview slide={selectedSlide} /> },
            { key: 'live', label: 'Live HTML', children: <pre className="raw-code clamped-code">{`<iframe title="Slide ${selectedSlide.position} live HTML">...</iframe>`}</pre> },
            { key: 'clean', label: 'Clean HTML', children: <pre className="raw-code clamped-code">{`<section class="slide"><h1>${selectedSlide.title}</h1></section>`}</pre> },
            { key: 'raw', label: 'Raw Response', children: <pre className="raw-code clamped-code">{`{"html":"<section ...>","model":"html-agent-test"}`}</pre> },
            { key: 'original', label: 'Original Content', children: <pre className="raw-code clamped-code">Slide source markdown and original content remain inspectable.</pre> },
          ]}
        />
      ),
    }] : []),
    {
      key: 'metadata',
      label: 'Run Metadata',
      children: (
        <>
          <Descriptions bordered column={{ xs: 1, md: 2 }}>
            <Descriptions.Item label="Run ID">{runId}</Descriptions.Item>
            <Descriptions.Item label="Type">{runType === 'html' ? 'HTML' : 'Image'}</Descriptions.Item>
            <Descriptions.Item label="Engine">{runType === 'html' ? 'html' : 'image, target label Image'}</Descriptions.Item>
            <Descriptions.Item label="Strategy">{runType === 'html' ? 'html_default' : 'image_5_0'}</Descriptions.Item>
            <Descriptions.Item label="Deck">Feature feedback deck</Descriptions.Item>
            <Descriptions.Item label="Requirement">History and Evidence repair</Descriptions.Item>
            <Descriptions.Item label="Color">Operational blue</Descriptions.Item>
            <Descriptions.Item label="Config">Test</Descriptions.Item>
            <Descriptions.Item label="Prompt versions">{runType === 'html' ? 'Designer v4, HTML Agent v6' : 'Unified Designer v5, Image Request v4'}</Descriptions.Item>
          </Descriptions>
          <Collapse
            className="design-principle-collapse"
            defaultActiveKey={['design']}
            items={[{
              key: 'design',
              label: 'Design Principle',
              children: <pre className="raw-code clamped-code">{`{"layout":"dense operational review","evidence":"prioritize version lineage and debug content","type":"${runType}"}`}</pre>,
            }]}
          />
        </>
      ),
    },
  ];

  return (
    <div>
      <PageHeader
        title={`Run #${runId} Detail`}
        subtitle="View target with active versions, full generation history, evidence expansion, and scoped Retry/Force behavior."
        actions={(
          <>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/history')}>Back to History</Button>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/history/batch/12')}>Back to Batch</Button>
            <Button icon={<DownloadOutlined />}>Run ZIP</Button>
            <Button icon={<ReloadOutlined />}>Refresh</Button>
            <Button danger onClick={() => setPendingForce({ scope: 'Run', target: `Run #${runId}` })}>Force Run</Button>
          </>
        )}
      />
      <div className="run-context-strip">
        <Button onClick={() => navigate('/history/batch/12')}>Batch Overview</Button>
        <Button onClick={() => navigate('/history/run/48')}>View #48</Button>
        <Button onClick={() => navigate('/history/run/38')}>View #38</Button>
        <Tag color={runType === 'html' ? 'blue' : 'purple'}>{runType === 'html' ? 'HTML type' : 'Image type'}</Tag>
        <Tag color="blue">Test only for functional regression</Tag>
      </div>
      <section className="prototype-panel route-flow-panel">
        <div className="panel-head compact">
          <div><h3>Run Route Flow</h3><p>Production route flow remains visible above review modes.</p></div>
        </div>
        <div className="route-flow-steps">
          {routeFlow.map((step, index) => (
            <div className="route-flow-step" key={step}><span>{index + 1}</span><strong>{step}</strong></div>
          ))}
        </div>
      </section>
      <section className="prototype-panel">
        <div className="slide-review-surface">
          <div className="panel-head compact">
            <div>
              <h3>Generated Outputs</h3>
              <p>Select a slide. Details open directly below the selected slide row; no right-side inspector steals preview space.</p>
            </div>
            <Space wrap>
              <Segmented value={reviewMode} onChange={(value) => setReviewMode(String(value))} options={['Tiled Review', 'Full Gallery', 'Split Review']} />
              <div className="zoom-control"><span>Scale</span><Slider min={40} max={90} value={zoom} onChange={setZoom} /></div>
            </Space>
          </div>
          <div className="slide-tile-grid-prototype">
            {runSlides.map((slide, index) => (
              <Fragment key={slide.key}>
                <button
                  type="button"
                  className={`slide-pick ${selectedSlide.key === slide.key ? 'selected' : ''}`}
                  onClick={() => {
                    setSelectedSlideKey(slide.key);
                    setEvidenceVersionKey(null);
                  }}
                >
                  <div className="slide-pick-header">
                    <Text strong>Slide {slide.position}</Text>
                    {statusTag(slide.status)}
                  </div>
                  <SlidePreview slide={slide} />
                  <span>{slide.title}</span>
                </button>
                {index === inlineDetailAfterIndex && (
                  <section className="slide-inline-detail" aria-label={`Details for slide ${selectedSlide.position}`}>
                    <div className="slide-inline-head">
                      <div>
                        <h3>Slide {selectedSlide.position}: {selectedSlide.title}</h3>
                        <span>{selectedSlide.hasArtifact ? `Active ${selectedSlide.activeVersion}` : selectedSlide.latestIssue}</span>
                      </div>
                      <Space wrap>
                        <Tooltip title={retryDisabled ? 'Retry is disabled because an active artifact is visible. Use Force.' : 'Retry will fill this missing slide.'}>
                          <Button disabled={retryDisabled} onClick={() => message.success(`Retry queued for Slide ${selectedSlide.position}`)}>Retry</Button>
                        </Tooltip>
                        <Button danger onClick={() => setPendingForce({ scope: 'Slide', target: `Slide ${selectedSlide.position}` })}>Force Slide</Button>
                        <Tooltip title={selectedSlide.hasArtifact ? 'Download the currently selected active version.' : 'No active artifact exists yet.'}>
                          <Button icon={<DownloadOutlined />} disabled={!selectedSlide.hasArtifact}>Download Active</Button>
                        </Tooltip>
                        <Button icon={<DownloadOutlined />} onClick={() => message.success(`Evidence ZIP prepared for Slide ${selectedSlide.position}`)}>Download Evidence</Button>
                      </Space>
                    </div>
                    <Alert
                      type={selectedSlide.hasArtifact ? 'info' : 'warning'}
                      showIcon
                      title={selectedSlide.hasArtifact ? 'Current artifact stays visible during failed overwrite attempts.' : 'No displayable active artifact. Retry is allowed.'}
                    />
                    <Tabs activeKey={detailTab} onChange={setDetailTab} items={topTabs} />
                  </section>
                )}
              </Fragment>
            ))}
          </div>
        </div>
      </section>
      <ForceDialog pending={pendingForce} onClose={() => setPendingForce(null)} />
    </div>
  );
}

function PromptPrototype() {
  const [caseKey, setCaseKey] = useState('missing-variable');
  const [selectedPromptKeys, setSelectedPromptKeys] = useState<React.Key[]>(['image-unified']);
  const [promptModalOpen, setPromptModalOpen] = useState(false);
  const [folderModalOpen, setFolderModalOpen] = useState(false);
  const [bulkMoveOpen, setBulkMoveOpen] = useState(false);
  const [assistantReviewOpen, setAssistantReviewOpen] = useState(false);
  const promptRows = [
    { key: 'designer', role: 'Designer', version: 'designer-default-v4', name: 'Designer system prompt', lifecycle: 'active', variables: 'ready', folders: 'Production, HTML' },
    { key: 'html', role: 'HTML Agent', version: 'html-agent-v6', name: 'HTML Agent build prompt', lifecycle: 'active', variables: 'ready', folders: 'Production, HTML' },
    { key: 'image-unified', role: 'Image 5.0 Unified', version: 'image-5-0-unified-v5', name: 'Unified Designer', lifecycle: 'active', variables: 'ready', folders: 'Image, Production' },
  ];
  const failed = caseKey !== 'clean';
  return (
    <div>
      <PageHeader
        title="Prompt Management"
        subtitle="Target diff and Auto Split guardrails. This keeps production filters, folders, bulk actions, inspector, and add/edit flow while making false OK states harder."
        actions={<><Button icon={<FileTextOutlined />} onClick={() => setFolderModalOpen(true)}>New Folder</Button><Button type="primary" onClick={() => setPromptModalOpen(true)}>Add Prompt</Button></>}
      />
      <section className="prototype-panel">
        <div className="panel-head">
          <div><h3>Production Prompt Surface</h3><p>Filters, table selection, bulk move/archive, inspector, add/edit/copy modal, variable picker, Auto insert, Analyze variables, and assistant review stay present.</p></div>
        </div>
        <div className="prompt-filter-grid">
          <label><span>Lifecycle</span><Select value="active" options={[{ label: 'Active', value: 'active' }, { label: 'Archived', value: 'archived' }]} /></label>
          <label><span>Role family</span><Select value="all" options={[{ label: 'All roles', value: 'all' }, { label: 'Designer', value: 'designer' }, { label: 'HTML Agent', value: 'html_agent' }, { label: 'Image roles', value: 'image' }]} /></label>
          <label><span>Location</span><Select value="production" options={[{ label: 'Production', value: 'production' }, { label: 'HTML', value: 'html' }, { label: 'Image', value: 'image' }]} /></label>
          <label><span>Search</span><Input prefix={<SearchOutlined />} placeholder="Search prompt name or version" /></label>
        </div>
        {selectedPromptKeys.length > 0 && (
          <div className="bulk-action-bar prototype-bulk-bar">
            <Text strong>{selectedPromptKeys.length} selected</Text>
            <Button size="small" onClick={() => setBulkMoveOpen(true)}>Move to Folder</Button>
            <Button size="small" danger>Archive</Button>
            <Button size="small" onClick={() => setSelectedPromptKeys([])}>Clear</Button>
          </div>
        )}
        <Table
          rowKey="key"
          dataSource={promptRows}
          rowSelection={{ selectedRowKeys: selectedPromptKeys, onChange: setSelectedPromptKeys }}
          columns={[
            { title: 'Role', dataIndex: 'role', width: 180, render: (value: string) => <Tag color={value.includes('Image') ? 'gold' : 'blue'}>{value}</Tag> },
            { title: 'Version', dataIndex: 'version', width: 180 },
            { title: 'Name', dataIndex: 'name' },
            { title: 'Lifecycle', dataIndex: 'lifecycle', width: 110 },
            { title: 'Variables', dataIndex: 'variables', width: 110, render: statusTag },
            { title: 'Folders', dataIndex: 'folders', width: 180 },
            { title: 'Actions', width: 220, render: () => <Space wrap><Button size="small">Default</Button><Button size="small" onClick={() => setPromptModalOpen(true)}>Edit</Button><Button size="small" onClick={() => setPromptModalOpen(true)}>Duplicate</Button><Button size="small" danger>Archive</Button></Space> },
          ]}
          pagination={{ pageSize: 20 }}
          scroll={{ x: 980 }}
        />
        <div className="prompt-inspector-prototype">
          <div>
            <h3>Prompt Inspector</h3>
            <span>Unified Designer - role id remains image_5_0_unified migration.</span>
          </div>
          <Space wrap><Tag color="gold">Default</Tag><Button onClick={() => setPromptModalOpen(true)}>Edit Prompt</Button><Button>Analyze Variables</Button><Button icon={<WarningOutlined />} onClick={() => setAssistantReviewOpen(true)}>Prompt Assistant Review</Button></Space>
          <pre className="raw-code">{`Produce a complete blueprint XML for {{Slide-Content}} with {{Deck-Full-Content}} and {{Deck-User-Requirement}} retained.`}</pre>
        </div>
      </section>
      <section className="prototype-panel">
        <div className="panel-head">
          <div>
            <h3>Prompt Diff Verification</h3>
            <p>Bad cases are fixture-backed. Failed checks block Publish but still allow saving a draft.</p>
          </div>
          <Segmented
            value={caseKey}
            onChange={(value) => setCaseKey(String(value))}
            options={[
              { label: 'Missing variable', value: 'missing-variable' },
              { label: 'Removed instruction', value: 'removed-instruction' },
              { label: 'Clean', value: 'clean' },
            ]}
          />
        </div>
        <div className="diff-layout">
          <div>
            <div className="subhead"><h4>Before</h4><span>Original prompt block</span></div>
            <pre className="raw-code diff removed">{`Use {{Deck-Full-Content}} and {{Slide-Content}}.
Preserve all layout constraints.
Never remove evidence instructions.`}</pre>
          </div>
          <div>
            <div className="subhead"><h4>After</h4><span>Assistant proposal</span></div>
            <pre className={`raw-code diff ${failed ? 'added warning' : 'added'}`}>{caseKey === 'clean'
              ? `Use {{Deck-Full-Content}} and {{Slide-Content}}.\nPreserve all layout constraints.\nNever remove evidence instructions.\nAdd {{Deck-Required-color}} exactly once.`
              : caseKey === 'missing-variable'
                ? `Use {{Deck-Full-Content}}.\nPreserve all layout constraints.\nNever remove evidence instructions.`
                : `Use {{Deck-Full-Content}} and {{Slide-Content}}.\nAdd {{Deck-Required-color}} exactly once.`}</pre>
          </div>
        </div>
        <div className="guardrail-grid">
          {[
            ['Required variables', caseKey === 'missing-variable' ? 'failed' : 'success', '{{Slide-Content}} or {{Deck-Required-color}} must be present'],
            ['Critical instruction block', caseKey === 'removed-instruction' ? 'failed' : 'success', 'Evidence instructions cannot be removed'],
            ['Character loss', failed ? 'failed' : 'success', 'Unexpected deletion exceeds threshold'],
            ['Publish gate', failed ? 'blocked' : 'success', failed ? 'Publish disabled, Save Draft enabled' : 'Publish enabled'],
          ].map(([label, status, detail]) => (
            <article className="guardrail-card" key={label}>
              <Space>{statusTag(status)}<Text strong>{label}</Text></Space>
              <span>{detail}</span>
            </article>
          ))}
        </div>
        <Space wrap>
          <Button>Save Draft</Button>
          <Button type="primary" disabled={failed}>Publish Prompt</Button>
          <Button icon={<WarningOutlined />}>Open Review Details</Button>
        </Space>
      </section>
      <Modal title="Add / Edit Prompt" open={promptModalOpen} onCancel={() => setPromptModalOpen(false)} okText={failed ? 'Save blocked until variables are resolved' : 'Save Prompt'} okButtonProps={{ disabled: failed }}>
        <Form layout="vertical">
          <Form.Item label="Agent Type"><Select defaultValue="image_5_0_unified" options={[{ label: 'Designer · designer', value: 'designer' }, { label: 'HTML Agent · html_agent', value: 'html_agent' }, { label: 'Image 5.0 Unified · image_5_0_unified', value: 'image_5_0_unified' }]} /></Form.Item>
          <Form.Item label="Version"><Input defaultValue="image-5-0-unified-v5" /></Form.Item>
          <Form.Item label="Folders"><Select mode="multiple" defaultValue={['Production']} options={[{ label: 'Production', value: 'Production' }, { label: 'HTML', value: 'HTML' }, { label: 'Image', value: 'Image' }]} /></Form.Item>
          <Form.Item label="Prompt Content"><Input.TextArea rows={5} defaultValue="Typing {{ opens role-scoped variables in production." /></Form.Item>
          <Space wrap><Button>Insert variable</Button><Button>Auto insert variables</Button><Button>Analyze variables</Button></Space>
        </Form>
      </Modal>
      <Modal title="New Prompt Folder" open={folderModalOpen} onCancel={() => setFolderModalOpen(false)} okText="Create Folder"><Input placeholder="Prompt folder name" /></Modal>
      <Modal title="Move Selected Prompts" open={bulkMoveOpen} onCancel={() => setBulkMoveOpen(false)} okText="Move Prompts"><Select mode="multiple" style={{ width: '100%' }} options={[{ label: 'Production', value: 'Production' }, { label: 'Image', value: 'Image' }]} /></Modal>
      <Modal title="Prompt Assistant Change Review" open={assistantReviewOpen} onCancel={() => setAssistantReviewOpen(false)} okText="Continue reviewing">
        <div className="diff-layout">
          <pre className="raw-code diff removed">Before: preserve all evidence instructions.</pre>
          <pre className="raw-code diff added">After: preserve all evidence instructions and insert missing variables.</pre>
        </div>
      </Modal>
    </div>
  );
}

function RunFailPrototype() {
  const [typeFilter, setTypeFilter] = useState('image');
  const [range, setRange] = useState('today');
  const allRows = [
    { key: 'empty', category: 'empty_image_response', count: 7, type: 'Image', next: 'Retry missing outputs', raw: 'No inline image bytes returned' },
    { key: 'timeout-image', category: 'timeout', count: 3, type: 'Image', next: 'Retry after endpoint check', raw: 'Run exceeded configured timeout' },
    { key: 'bad', category: 'bad_request', count: 2, type: 'Image', next: 'Fix config before Force', raw: '400 Bad Request from provider' },
    { key: 'html-timeout', category: 'timeout', count: 4, type: 'HTML', next: 'Retry missing HTML capture', raw: 'Playwright capture exceeded timeout' },
    { key: 'html-validation', category: 'validation_error', count: 1, type: 'HTML', next: 'Inspect clean HTML', raw: 'HTML validation failed before screenshot' },
  ];
  const rows = allRows.filter((row) => row.type.toLowerCase() === typeFilter);
  const breakdowns = [
    ['By Route', typeFilter === 'html' ? 'html_default' : 'image_5_0', 8],
    ['By Mode', 'manual', 7],
    ['By Status', 'failed', 9],
    ['By Error Class', rows[0]?.category || 'unknown', rows[0]?.count || 0],
    ['By Model', typeFilter === 'html' ? 'gemini-3-flash-preview' : 'gemini-3.1-flash-image', 6],
    ['By Retry Signal', 'retryable_provider_or_timeout', 10],
  ];
  return (
    <div>
      <PageHeader
        title="RunFail Stats"
        subtitle="Local, rule-based failure statistics with Singapore-day defaults and type/time filters. No LLM insight in this round."
        actions={<><Button icon={<ReloadOutlined />}>Refresh</Button><Button icon={<DownloadOutlined />}>Export JSON</Button><Button icon={<DownloadOutlined />}>Export CSV</Button></>}
      />
      <section className="prototype-panel">
        <div className="runfail-filter-grid">
          <label><span>Type</span><Segmented value={typeFilter} onChange={(value) => setTypeFilter(String(value))} options={[{ label: 'Image', value: 'image' }, { label: 'HTML', value: 'html' }]} /></label>
          <label><span>Subtype / mode</span><Select value="all" options={[{ label: 'All subtypes', value: 'all' }, { label: 'Banner', value: 'banner' }, { label: 'Manual', value: 'manual' }, { label: 'Auto', value: 'auto' }]} /></label>
          <label><span>Quick range</span><Select value={range} onChange={setRange} options={[
            { label: 'Today (Asia/Singapore)', value: 'today' },
            { label: 'Yesterday', value: 'yesterday' },
            { label: 'Last 7 days', value: '7d' },
            { label: 'Last month', value: 'last-month' },
            { label: 'This year', value: 'year' },
            { label: 'Custom', value: 'custom' },
          ]} /></label>
          <label><span>Custom range</span><RangePicker disabled={range !== 'custom'} /></label>
        </div>
        <Alert type="info" showIcon title="Default window: 2026-06-01 Asia/Singapore natural day" />
        <div className="filter-proof-strip">
          <Tag color="blue">HTML type</Tag>
          <Tag color="purple">Image type</Tag>
          <Tag>Banner subtype</Tag>
          <Tag>Manual / Auto mode</Tag>
        </div>
      </section>
      <div className="prototype-summary-grid">
        <div><span>Total Runs</span><strong>42</strong><Tag>{typeFilter.toUpperCase()}</Tag></div>
        <div><span>Failed / Timed Out</span><strong>12</strong><Tag color="error">28.6%</Tag></div>
        <div><span>Retryable</span><strong>10</strong><Tag color="success">missing or timeout</Tag></div>
        <div><span>Terminal Config</span><strong>2</strong><Tag color="warning">fix first</Tag></div>
      </div>
      <section className="prototype-panel">
        <Table
          rowKey="key"
          dataSource={rows}
          columns={[
            { title: 'Error category', dataIndex: 'category', render: (value: string) => <Tag>{value}</Tag> },
            { title: 'Type', dataIndex: 'type', width: 120 },
            { title: 'Count', dataIndex: 'count', width: 90 },
            { title: 'Recommended action', dataIndex: 'next', width: 220 },
            { title: 'Raw samples', dataIndex: 'raw' },
          ]}
          expandable={{ expandedRowRender: (record) => <pre className="raw-code">{`run_id=48 slide=8 message="${record.raw}"\nrun_id=50 slide=9 message="${record.raw}"`}</pre> }}
          pagination={false}
        />
      </section>
      <section className="prototype-panel">
        <div className="panel-head compact">
          <div><h3>Existing Breakdown Coverage</h3><p>Current production breakdowns remain: route, mode, status, error class, model, retry signal, trend, diagnostics, empty states, and export loading.</p></div>
        </div>
        <div className="runfail-breakdown-grid-prototype">
          {breakdowns.map(([title, label, count]) => (
            <article className="guardrail-card" key={title}>
              <Text strong>{title}</Text>
              <Tag>{label}</Tag>
              <Progress percent={Number(count) * 8} size="small" status={Number(count) ? 'exception' : 'success'} />
            </article>
          ))}
          <article className="guardrail-card"><Text strong>Failure Trend</Text><span>2026-06-01: 12 failed / 42 total</span><Progress percent={29} size="small" status="exception" /></article>
          <article className="guardrail-card"><Text strong>Diagnostics</Text><span>retry - inspect evidence - fix config classes stay visible</span></article>
          <article className="guardrail-card"><Text strong>Empty State</Text><span>No failed or timed out runs for selected range</span></article>
          <article className="guardrail-card"><Text strong>Export Loading</Text><span>JSON/CSV buttons keep loading feedback and secret-safe output</span></article>
        </div>
      </section>
    </div>
  );
}

function ConfigPrototype() {
  const [modelTestOpen, setModelTestOpen] = useState(false);
  const [modelTestStatus, setModelTestStatus] = useState<'idle' | 'success' | 'failed'>('idle');
  const [configModalOpen, setConfigModalOpen] = useState(false);
  const [routeDirty, setRouteDirty] = useState(true);
  return (
    <div>
      <PageHeader
        title="Config"
        subtitle="System Settings moves into Config, Prompt Library leaves Config, and model profile add is gated by real provider Test."
        actions={<><Button icon={<ApiOutlined />} onClick={() => { setModelTestStatus('idle'); setModelTestOpen(true); }}>Add Role Model</Button><Button type="primary" onClick={() => setConfigModalOpen(true)}>Add Combination</Button></>}
      />
      <section className="prototype-panel">
        <Alert
          type="info"
          showIcon
          title="Phase boundary"
          description="Active Config payloads preserve image_designer and image_generator role IDs after the final rename."
        />
        <Tabs
          className="wrapped-tabs"
          items={[
            {
              key: 'combinations',
              label: 'Combinations',
              children: (
                <Table
                  rowKey="key"
                  dataSource={[
                    { key: 'test', name: 'Test', html: 'Gemini 3.1 Flash-Lite Preview', image: 'gemini-3.1-flash-image', timeout: '20m', default: true },
                    { key: 'mini', name: 'Production Mini', html: 'openai/gpt-5.4-mini + gemini-3-flash-preview', image: 'gemini-3.1-flash-image', timeout: '30m', default: false },
                    { key: 'pro', name: 'Production Pro', html: 'openai/gpt-5.4 + gemini-3.1-pro-preview', image: 'gemini-3-pro-image', timeout: '45m', default: false },
                  ]}
                  columns={[
                    { title: 'Combination', dataIndex: 'name', render: (value: string, record: { key: string; name: string; html: string; image: string; timeout: string; default: boolean }) => <Space><Text strong>{value}</Text>{record.default && <Tag color="success">Default</Tag>}</Space> },
                    { title: 'HTML models', dataIndex: 'html' },
                    { title: 'Image/Image route models', dataIndex: 'image' },
                    { title: 'Timeout', dataIndex: 'timeout', width: 100 },
                    { title: 'Actions', width: 220, render: (_: unknown, record: { default: boolean }) => <Space><Button size="small" onClick={() => setConfigModalOpen(true)}>Edit</Button><Button size="small" disabled={record.default}>Set default</Button><Button size="small" danger>Delete</Button></Space> },
                  ]}
                  pagination={false}
                  scroll={{ x: 920 }}
                />
              ),
            },
            {
              key: 'models',
              label: 'Model Profiles',
              children: (
                <div>
                  <Alert type="info" showIcon title="Profiles are grouped first by route family, then by role." description="This matches the business hierarchy: HTML -> Director/Worker and Image -> Designer/Generator. Active internal role IDs remain image_designer and image_generator." />
                  <div className="model-family-grid">
                    {[
                      { family: 'HTML', roles: ['HTML Director', 'HTML Worker'] },
                      { family: 'Image', roles: ['Image Designer', 'Image Generator'] },
                    ].map((family) => (
                      <section className="model-family-panel" key={family.family}>
                        <div className="subhead">
                          <h4>{family.family}</h4>
                          <span>{family.family === 'HTML' ? 'Director decides structure; Worker builds HTML/capture.' : 'Designer produces blueprint/prompt; Generator creates image.'}</span>
                        </div>
                        {family.roles.map((role) => (
                          <div className="model-role-group" key={role}>
                            <Text strong>{role}</Text>
                            <div className="model-profile-grid compact">
                              {modelProfiles.filter((profile) => profile.role === role).map((profile) => (
                                <article className="model-card" key={`${profile.role}-${profile.tier}`}>
                                  <Space wrap><Tag color={profile.role.includes('Image') ? 'purple' : 'blue'}>{profile.tier}</Tag>{statusTag(profile.status)}</Space>
                                  <Text strong>{profile.model}</Text>
                                  <span>Thinking/effort: {profile.thinking} - Temperature {profile.temperature}</span>
                                  <Space wrap><Button size="small">Edit</Button><Button size="small">Disable</Button></Space>
                                </article>
                              ))}
                            </div>
                          </div>
                        ))}
                      </section>
                    ))}
                  </div>
                </div>
              ),
            },
            {
              key: 'variables-runtime',
              label: 'Variables & Runtime',
              children: (
                <div className="two-column">
                  <Descriptions bordered size="small" column={1}>
                    <Descriptions.Item label="Current DB observation">43 system variable rows exist in root ppt.db; repeated Deck/Slide variables appear across many image_* agent types.</Descriptions.Item>
                    <Descriptions.Item label="Target business rule">Variables should be shared by role family where possible, and temporarily disabled only when a change is under review.</Descriptions.Item>
                    <Descriptions.Item label="Runtime timeout">Per-combination timeout remains editable</Descriptions.Item>
                    <Descriptions.Item label="Route bindings">Stored as route_model_bindings rename</Descriptions.Item>
                  </Descriptions>
                  <div className="variable-audit-panel">
                    <Alert type="warning" showIcon title="Implementation follow-up" description="Deep audit required before changing data: detect duplicated variable names by role family, preserve references, then migrate without losing prompt validation." />
                    <Table
                      size="small"
                      pagination={false}
                      rowKey="agentType"
                      dataSource={[
                        { agentType: 'HTML family', current: 6, target: 'Designer + HTML Agent shared set' },
                        { agentType: 'Image/Image family', current: 29, target: 'Image Designer + Image Generator Generator shared set' },
                        { agentType: 'XML cleanup', current: 4, target: 'May merge into Image Designer if references allow' },
                        { agentType: 'Prompt assistant', current: 0, target: 'Only add if prompt assistant actually needs variables' },
                      ]}
                      columns={[
                        { title: 'Family', dataIndex: 'agentType' },
                        { title: 'Current rows', dataIndex: 'current', width: 120 },
                        { title: 'Target grouping', dataIndex: 'target' },
                      ]}
                    />
                  </div>
                </div>
              ),
            },
            {
              key: 'routes',
              label: 'Generation Routes',
              children: (
                <div className="routes-editor-prototype">
                  <div className="route-binding-grid">
                    <label><span>Combination</span><Select value="test" options={[{ label: 'Test · default', value: 'test' }, { label: 'Production Mini', value: 'mini' }]} /></label>
                    <label><span>Image Designer (image_designer)</span><Select value="image-designer-test" onChange={() => setRouteDirty(true)} options={[{ label: 'Image Designer Test · google/gemini-3.1-flash-lite-preview', value: 'image-designer-test' }]} /></label>
                    <label><span>Image Generator (image_generator)</span><Select value="image-generator-test" onChange={() => setRouteDirty(true)} options={[{ label: 'Image Generator Test · gemini-3.1-flash-image', value: 'image-generator-test' }]} /></label>
                    <Space className="route-binding-actions" wrap><Button disabled={!routeDirty} onClick={() => setRouteDirty(false)}>Cancel Changes</Button><Button type="primary" disabled={!routeDirty} onClick={() => setRouteDirty(false)}>Save Generation Routes</Button></Space>
                  </div>
                  <div className="route-map-binding-summary">
                    <span><Tag color="gold">image_designer</Tag>Image Designer Test</span>
                    <span><Tag color="volcano">image_generator</Tag>Image Generator Test</span>
                    <Tag color={routeDirty ? 'warning' : 'success'}>{routeDirty ? 'unsaved changes' : 'saved state'}</Tag>
                  </div>
                  <Table
                    size="small"
                    pagination={false}
                    rowKey="route"
                    dataSource={[
                      { route: 'HTML Default', prompts: 'Designer + HTML Agent', models: 'HTML Director / HTML Worker', evidence: 'Rendered prompt, Clean HTML, Live HTML, Captured PNG, raw response' },
                      { route: 'Image 1.0 legacy', prompts: 'Cover + Continuation', models: 'image_generator', evidence: 'Conversation, request, response, active image' },
                      { route: 'Image 3.0 seed', prompts: 'Seed + non-seed director prompts', models: 'image_designer + image_generator', evidence: 'Seed dependency, XML, request, response' },
                      { route: 'Image 3.2 cover ref', prompts: 'Cover reference + director prompts', models: 'image_designer + image_generator', evidence: 'Cover ref, seed dependency, XML, request, response' },
                      { route: 'Image 5.0 unified', prompts: 'Unified Designer', models: 'image_designer + image_generator', evidence: 'Blueprint XML, request, response, active image' },
                    ]}
                    columns={[
                      { title: 'Route / Version', dataIndex: 'route' },
                      { title: 'Prompt Roles', dataIndex: 'prompts' },
                      { title: 'Model Roles', dataIndex: 'models' },
                      { title: 'Run Detail Evidence', dataIndex: 'evidence' },
                    ]}
                    scroll={{ x: 860 }}
                  />
                  <div className="route-flow-grid-prototype">
                    {[
                      ['HTML Default', 'Designer -> HTML Agent -> Clean HTML -> Captured PNG'],
                      ['Image 1.0 Legacy', 'Cover prompt -> continuation -> image response'],
                      ['Image 3.0 Seed', 'Seed page -> designer XML -> image request'],
                      ['Image 3.2 Cover Ref', 'Cover reference -> seed dependency -> image request'],
                      ['Image 5.0 Unified', 'Unified director -> Blueprint XML -> active version'],
                    ].map(([route, flow]) => (
                      <article key={route} className="route-flow-card">
                        <Text strong>{route}</Text>
                        <span>{flow}</span>
                      </article>
                    ))}
                  </div>
                  <pre className="raw-code clamped-code">{`graph TD\n  HTMLDefault --> DesignerPrompt --> HTMLAgent --> CleanHTML --> CapturedPNG\n  Image10Legacy --> CoverPrompt --> ContinuationRequest --> ImageResponse\n  Image30Seed --> SeedSlide --> DirectorXML --> ImageRequest\n  Image32CoverRef --> CoverReference --> SeedDependency --> DirectorXML\n  Image50Unified --> UnifiedDirector --> BlueprintXML --> ImageRequest --> ActiveVersion`}</pre>
                </div>
              ),
            },
            {
              key: 'system',
              label: 'System Settings',
              children: (
                <div className="two-column">
                  <Form layout="vertical" className="settings-form">
                    <Form.Item label="Global Concurrency">
                      <InputNumber min={1} max={20} defaultValue={4} />
                    </Form.Item>
                    <Form.Item label="Designer Agent Concurrency">
                      <InputNumber min={1} max={20} defaultValue={2} />
                    </Form.Item>
                    <Form.Item label="HTML Agent Concurrency">
                      <InputNumber min={1} max={20} defaultValue={2} />
                    </Form.Item>
                    <Button type="primary">Save System Settings</Button>
                  </Form>
                  <div>
                    <Alert type="success" showIcon title="Advanced Roadmap tab is replaced by System Settings." />
                    <Table
                      size="small"
                      pagination={false}
                      rowKey="name"
                      dataSource={[
                        { name: '{{Deck-Full-Content}}', role: 'Designer', references: 8 },
                        { name: '{{Slide-Content}}', role: 'Image 5.0 Unified', references: 11 },
                      ]}
                      columns={[
                        { title: 'System Variable', dataIndex: 'name' },
                        { title: 'Role', dataIndex: 'role' },
                        { title: 'References', dataIndex: 'references' },
                      ]}
                    />
                  </div>
                </div>
              ),
            },
          ]}
        />
      </section>
      <section className="prototype-panel">
        <div className="panel-head">
          <div>
            <h3>Image Naming</h3>
            <p>Final implementation phase performs no-compat legacy image-route to Image migration across UI, APIs, DB names, evidence metadata, tests, and fixtures.</p>
          </div>
          <Badge status="processing" text="Not part of Phase 1 implementation" />
        </div>
        <Space wrap>
          <Tag color="blue">HTML Director</Tag>
          <Tag color="green">HTML Worker</Tag>
          <Tag color="purple">Image Designer</Tag>
          <Tag color="volcano">Image Generator</Tag>
        </Space>
      </section>
      <Modal
        title="Add Role Model - Test Gate"
        open={modelTestOpen}
        onCancel={() => { setModelTestOpen(false); setModelTestStatus('idle'); }}
        okText="Save Model"
        okButtonProps={{ disabled: modelTestStatus !== 'success' }}
        onOk={() => {
          message.success('Model saved after successful Test; temporary preview deleted');
          setModelTestOpen(false);
          setModelTestStatus('idle');
        }}
      >
        <Form layout="vertical">
          <Form.Item label="Role"><Select defaultValue="image_generator" onChange={() => setModelTestStatus('idle')} options={[{ label: 'Image Generator Model (Image Generator)', value: 'image_generator' }, { label: 'Designer Model', value: 'designer' }, { label: 'HTML Agent Model', value: 'html_agent' }]} /></Form.Item>
          <Form.Item label="Model ID"><Input defaultValue="gemini-3-pro-image" onChange={() => setModelTestStatus('idle')} /></Form.Item>
          <Form.Item label="Endpoint"><Input defaultValue="https://generativelanguage.googleapis.com/v1beta/models" onChange={() => setModelTestStatus('idle')} /></Form.Item>
          <Space wrap>
            <Button icon={<PlayCircleOutlined />} onClick={() => setModelTestStatus('success')}>Run Success Test</Button>
            <Button danger onClick={() => setModelTestStatus('failed')}>Show Failure Response</Button>
          </Space>
          {modelTestStatus === 'success' && (
            <Alert
              className="model-test-result"
              type="success"
              showIcon
              title="Test succeeded"
              description="Text tests return minimal JSON; image tests render a temporary preview image. The temp image is deleted after save or close, and a cleanup row is written to Generation History."
            />
          )}
          {modelTestStatus === 'success' && <div className="temp-image-preview">Temporary image preview - deleted after save</div>}
          {modelTestStatus === 'failed' && (
            <Alert
              className="model-test-result"
              type="error"
              showIcon
              title="Test failed"
              description={<pre className="inline-redacted-response">{`{"error":{"code":"MODEL_NOT_FOUND","message":"model not found"},"request_id":"req_123","authorization":"<redacted>"}`}</pre>}
            />
          )}
        </Form>
      </Modal>
      <Modal title="Add / Edit Combination" open={configModalOpen} onCancel={() => setConfigModalOpen(false)} okText="Save Combination">
        <Form layout="vertical">
          <Form.Item label="Combination Name"><Input defaultValue="Test" /></Form.Item>
          <Form.Item label="Designer Model"><Select defaultValue="designer-test" options={[{ label: 'Designer Test · google/gemini-3.1-flash-lite-preview', value: 'designer-test' }]} /></Form.Item>
          <Form.Item label="HTML Agent Model"><Select defaultValue="html-test" options={[{ label: 'HTML Agent Test · google/gemini-3.1-flash-lite-preview', value: 'html-test' }]} /></Form.Item>
          <Form.Item label="Auto-Spill Model"><Select defaultValue="auto-test" options={[{ label: 'Auto-Spill Test', value: 'auto-test' }]} /></Form.Item>
          <Form.Item label="Image Designer Model"><Select allowClear defaultValue="image-designer-test" options={[{ label: 'Image Designer Test', value: 'image-designer-test' }]} /></Form.Item>
          <Form.Item label="Image Generator Model"><Select allowClear defaultValue="image-generator-test" options={[{ label: 'Image Generator Test', value: 'image-generator-test' }]} /></Form.Item>
          <Form.Item label="Timeout (minutes)"><InputNumber min={1} max={240} defaultValue={20} /></Form.Item>
          <Form.Item label="Max concurrent runs"><InputNumber min={1} max={20} defaultValue={4} /></Form.Item>
        </Form>
      </Modal>
    </div>
  );
}

function PlaceholderPage({ title }: { title: string }) {
  return (
    <div>
      <PageHeader title={title} subtitle="This route is kept so the prototype navigation mirrors the current app shell." />
      <section className="prototype-panel">
        <Empty description={`${title} is outside the Phase 1 prototype focus`} />
      </section>
    </div>
  );
}

export default function FeedbackRoundPrototype() {
  return (
    <HashRouter>
      <PrototypeLayout />
    </HashRouter>
  );
}
