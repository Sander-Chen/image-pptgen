import { useEffect, useMemo, useState } from 'react';
import type { Key, ReactNode } from 'react';
import dayjs from 'dayjs';
import type { Dayjs } from 'dayjs';
import {
  Alert,
  Badge,
  Button,
  Checkbox,
  Descriptions,
  DatePicker,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Layout,
  Menu,
  Modal,
  Popconfirm,
  Progress,
  Segmented,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  ApiOutlined,
  AppstoreOutlined,
  ArrowLeftOutlined,
  BarChartOutlined,
  BellOutlined,
  BranchesOutlined,
  BgColorsOutlined,
  CheckCircleOutlined,
  CheckOutlined,
  ClockCircleOutlined,
  CopyOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  DownloadOutlined,
  EditOutlined,
  EyeOutlined,
  FilterOutlined,
  FileTextOutlined,
  FolderAddOutlined,
  HistoryOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  NumberOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SearchOutlined,
  SettingOutlined,
  ScissorOutlined,
  ThunderboltOutlined,
  UploadOutlined,
  UserOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import { HashRouter, Navigate, Outlet, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom';
import {
  batchRuns,
  combinationRows,
  dataColorRows,
  dataDeckRows,
  dataFolders,
  dataRequirementRows,
  historyRows,
  htmlSlides,
  modelGateRows,
  promptRoles,
  promptRows,
  roleModelProfiles,
  routeFlows,
  slides,
  variableRows,
  versionHistory,
} from './fixtures';
import type {
  ActionScope,
  CombinationRow,
  DataColorRow,
  DataDeckRow,
  DataRequirementRow,
  HistoryRow,
  ModelGateRow,
  PromptRow,
  RoleModelProfile,
  VariableRow,
} from './fixtures';

const { Content, Sider } = Layout;
const { Paragraph, Text } = Typography;

interface PendingAction {
  kind: 'Continue' | 'Retry' | 'Force Regenerate' | 'Download' | 'Model Gate' | 'Create Config';
  scope: ActionScope | 'Config';
  target: string;
}

interface PageProps {
  openAction: (action: PendingAction) => void;
  notify: (text: string) => void;
}

type MockApiState = 'Ready' | 'List loading' | 'List error' | 'Server empty' | 'Filter empty' | 'Mutation success' | 'Mutation error' | 'Bulk partial warning';
type GenerateApiState = 'Ready' | 'Initial loading' | 'Failed to load data' | 'Generate failed' | 'Failed to refresh batch';

const menuItems = [
  { key: '/data', icon: <DatabaseOutlined />, label: 'Data' },
  { key: '/generate', icon: <ThunderboltOutlined />, label: 'Generate' },
  { key: '/history', icon: <HistoryOutlined />, label: 'History' },
  { key: '/runfail', icon: <BarChartOutlined />, label: 'RunFail Stats · proposed' },
  { key: '/prompts', icon: <FileTextOutlined />, label: 'Prompts' },
  { key: '/config', icon: <SettingOutlined />, label: 'Config' },
  { key: '/system-settings', icon: <SettingOutlined />, label: 'System Settings' },
];

function statusTag(status: string) {
  const colorMap: Record<string, string> = {
    completed: 'success',
    failed: 'error',
    running: 'processing',
    queued: 'processing',
    pending: 'default',
    timed_out: 'warning',
    skipped: 'default',
  };
  return <Tag color={colorMap[status] || 'default'}>{status}</Tag>;
}

function ProposedActionTag() {
  return (
    <Tooltip title="Review-only action. No backend request, endpoint, payload, or scheduler contract exists yet.">
      <Tag color="warning">proposed / no backend request</Tag>
    </Tooltip>
  );
}

function retryTag(value: string) {
  const colorMap: Record<string, string> = {
    'auto-retryable': 'success',
    terminal: 'error',
    'manual-only': 'warning',
    none: 'default',
  };
  return <Tag color={colorMap[value] || 'default'}>{value}</Tag>;
}

function routeTag(route: string) {
  return <Tag color={route === 'HTML' || route === 'Image 5.3' ? 'blue' : 'gold'}>{route}</Tag>;
}

type RouteBindingDraft = {
  image_designer?: string;
  image_generator?: string;
};

const initialRouteBindingDrafts: Record<string, RouteBindingDraft> = {
  'image-prod': { image_designer: 'image-designer-prod', image_generator: 'image-generator-prod' },
  'html-review': {},
  'image10-legacy': { image_designer: 'image-designer-prod', image_generator: 'image-generator-prod' },
  'image53-gate': {},
};

const routeEvidenceRows = [
  { id: 'html', route: 'HTML Default', prompts: 'Designer + HTML Agent', models: 'Designer / HTML Agent', evidence: 'HTML, captured PNG, raw response' },
  { id: 'cover', route: 'Image Cover 3.1', prompts: 'Cover Prompt 3.1', models: 'Image Designer + Image Generator', evidence: 'Cover image prompt, request, response, final PNG' },
  { id: 'image10', route: 'Image 1.0', prompts: 'Cover 3.1 + Continuation', models: 'Image generator conversation', evidence: 'Conversation history, request, response, final PNG' },
  { id: 'image30', route: 'Image 3.0', prompts: 'Seed + Non-seed Designer', models: 'Image Designer + Image Generator', evidence: 'Seed dependency, XML blueprint, final PNG' },
  { id: 'image32', route: 'Image 3.2', prompts: 'Cover ref + Seed + Non-seed', models: 'Image Designer + Image Generator', evidence: 'Cover reference, seed dependency, final PNG' },
  { id: 'image50', route: 'Image 5.0', prompts: 'Unified Designer', models: 'Image Designer + Image Generator', evidence: 'Unified XML blueprint, request, response, final PNG' },
];

function MockLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(false);
  const selectedKey = menuItems.find((item) => location.pathname.startsWith(item.key))?.key || '/history';

  return (
    <Layout className="app-shell feature-upgrade-mock-shell">
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
          <div className="app-user-avatar">
            <UserOutlined />
          </div>
          <div className="app-user-copy">
            <strong>mock</strong>
            <span>Fake data only</span>
          </div>
        </div>
      </Sider>
      <Layout style={{ minWidth: 0 }}>
        <Content className="app-content">
          <div className="feature-upgrade-mock">
            <Outlet />
          </div>
        </Content>
      </Layout>
    </Layout>
  );
}

function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-toolbar">
      <div>
        <div className="page-kicker"><span className="status-dot" />Feature upgrade fake frontend</div>
        <h2>{title}</h2>
        <p className="toolbar-subtitle">{subtitle}</p>
      </div>
      {actions && <div className="page-toolbar-actions">{actions}</div>}
    </header>
  );
}

function HistoryOperationsPage({ openAction, notify }: PageProps) {
  const navigate = useNavigate();
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [routeFilter, setRouteFilter] = useState('all');
  const [statusFilter, setStatusFilter] = useState('all');
  const [createdRange, setCreatedRange] = useState<[Dayjs | null, Dayjs | null] | null>(null);
  const [expandedRowKeys, setExpandedRowKeys] = useState<Key[]>([128]);
  const [historyApiState, setHistoryApiState] = useState<MockApiState>('Ready');
  const [selectedHistoryRowKeys, setSelectedHistoryRowKeys] = useState<Key[]>([128]);
  const summary = useMemo(() => {
    const active = historyRows.filter((row) => row.status === 'queued' || row.status === 'running').length;
    const failed = historyRows.filter((row) => row.status === 'failed' || row.status === 'timed_out').length;
    const gated = historyRows.filter((row) => row.route === 'Image 5.3').length;
    const completedRuns = historyRows.reduce((total, row) => total + row.progress.done, 0);
    const totalRuns = historyRows.reduce((total, row) => total + row.progress.total, 0);
    return { active, completedRuns, failed, gated, totalRuns };
  }, []);

  const filteredHistoryRows = useMemo(() => {
    const needle = searchText.trim().toLowerCase();
    return historyRows.filter((row) => {
      if (routeFilter !== 'all' && row.route !== routeFilter) return false;
      if (statusFilter !== 'all' && row.status !== statusFilter) return false;
      if (createdRange?.[0] && createdRange?.[1]) {
        const rowDate = dayjs(row.createdDate);
        if (rowDate.isBefore(createdRange[0].startOf('day')) || rowDate.isAfter(createdRange[1].endOf('day'))) {
          return false;
        }
      }
      if (!needle) return true;
      return [
        row.id,
        row.title,
        row.route,
        row.mode,
        row.requirement,
        row.color,
        row.config,
        row.promptSet,
        row.status,
        row.errorSummary,
      ].join(' ').toLowerCase().includes(needle);
    });
  }, [createdRange, routeFilter, searchText, statusFilter]);

  const historyDisplayRows = useMemo(() => {
    if (historyApiState === 'Server empty' || historyApiState === 'Filter empty') return [];
    return filteredHistoryRows;
  }, [filteredHistoryRows, historyApiState]);

  const historyStateCopy: Record<MockApiState, { type: 'info' | 'success' | 'warning' | 'error'; title: string; description: string }> = {
    Ready: { type: 'success', title: 'Ready state', description: 'Batches are loaded from fake fixtures and row actions are available.' },
    'List loading': { type: 'info', title: 'List loading', description: 'History table keeps layout stable while /api/batches is loading.' },
    'List error': { type: 'error', title: 'List error', description: 'Failed to load batches: provider or API error.' },
    'Server empty': { type: 'warning', title: 'Server empty', description: 'No batches returned by fake API.' },
    'Filter empty': { type: 'warning', title: 'Filter empty', description: 'No batches match the selected route, status, or date filters.' },
    'Mutation success': { type: 'success', title: 'Mutation success', description: 'Bulk delete, retry, continue, or download action returned success.' },
    'Mutation error': { type: 'error', title: 'Mutation error', description: 'History mutation failed and row state remains unchanged.' },
    'Bulk partial warning': { type: 'warning', title: 'Bulk partial warning', description: 'Some selected batches could not be deleted.' },
  };

  const terminalBatchStatuses = new Set(['completed', 'failed', 'timed_out']);

  const columns: ColumnsType<HistoryRow> = [
    {
      title: 'Batch',
      dataIndex: 'id',
      width: 100,
      render: (_value, record) => (
        <div>
          <Text strong>#{record.id}</Text>
          <div className="context-meta">{record.createdAt}</div>
        </div>
      ),
    },
    {
      title: 'Deck / Mode / Config',
      key: 'context',
      render: (_, record) => (
        <div className="history-context">
          <Text strong>Deck: {record.title}</Text>
          <div className="tag-row">
            {routeTag(record.route)}
            <Tag>Mode: {record.mode}</Tag>
            <Tag>Req: {record.requirement}</Tag>
            <Tag color="purple">Config: {record.config}</Tag>
          </div>
          <span className="context-meta">Color: {record.color}</span>
          <span className="context-meta">Prompt: {record.promptSet}</span>
        </div>
      ),
    },
    {
      title: 'State',
      key: 'state',
      width: 260,
      render: (_, record) => (
        <div className="status-stack">
          <div className="tag-row">
            {statusTag(record.status)}
            {retryTag(record.retryClass)}
          </div>
          <Text strong>{record.progress.done} / {record.progress.total} done</Text>
          <Progress
            percent={Math.round((record.progress.done / record.progress.total) * 100)}
            status={record.status === 'failed' ? 'exception' : 'active'}
            showInfo={false}
            size="small"
          />
          <span className="context-meta">{record.errorSummary}</span>
          <span className="context-meta">{record.nextAction}</span>
          <Tag color={record.failureRate > 0 ? 'red' : 'green'}>Failure Rate: {record.failureRate}%</Tag>
        </div>
      ),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 320,
      render: (_, record) => (
        <Space size={6} wrap>
          {record.status !== 'completed' && <ProposedActionTag />}
          <Tooltip title="Open batch overview">
            <Button
              className="action-button"
              aria-label={`Open batch ${record.batchId}`}
              icon={<EyeOutlined />}
              onClick={() => navigate(`/history/batch/${record.batchId}`)}
            />
          </Tooltip>
          {record.status !== 'completed' && (
            <Button
              className="action-button"
              onClick={() => openAction({ kind: 'Continue', scope: 'Batch', target: `Batch #${record.batchId}` })}
            >
              Cont.
            </Button>
          )}
          {record.retryClass === 'auto-retryable' && (
            <Button
              className="action-button"
              onClick={() => openAction({ kind: 'Retry', scope: 'Batch', target: `Batch #${record.batchId}` })}
            >
              Retry
            </Button>
          )}
          {record.route === 'Image 5.3' ? (
            <Button className="action-button" icon={<ApiOutlined />} onClick={() => navigate('/config')}>
              Gate
            </Button>
          ) : (
            <Button
              className="action-button"
              danger
              onClick={() => openAction({ kind: 'Force Regenerate', scope: 'Batch', target: `Batch #${record.batchId}` })}
            >
              Force
            </Button>
          )}
          <Tooltip title="Download batch ZIP">
            <Button
              className="action-button"
              aria-label={`Download batch ZIP ${record.batchId}`}
              icon={<DownloadOutlined />}
              disabled={!terminalBatchStatuses.has(record.status)}
              onClick={() => notify(`Batch ZIP export queued for Batch #${record.batchId}`)}
            >
              Batch ZIP
            </Button>
          </Tooltip>
          {!terminalBatchStatuses.has(record.status) && <Tag>Download disabled until terminal</Tag>}
        </Space>
      ),
    },
  ];

  return (
    <>
      <PageHeader
        title="Run History"
        subtitle="Review generation batches, open batch/run detail, and test the action surface before backend wiring."
        actions={(
          <Space wrap>
            <Button icon={<ReloadOutlined />} onClick={() => notify('Fake history refreshed')}>Refresh</Button>
            <Button icon={<FilterOutlined />} onClick={() => setFiltersOpen((value) => !value)}>
              Filters
            </Button>
            <Button icon={<BarChartOutlined />} onClick={() => navigate('/runfail')}>RunFail Stats · proposed</Button>
            <Button type="primary" icon={<PlayCircleOutlined />} onClick={() => navigate('/generate')}>New Batch</Button>
          </Space>
        )}
      />
      <div className="summary-grid">
        <div className="summary-tile"><span>Active Batches</span><strong>{summary.active}</strong><Tag color="processing">queued / running</Tag></div>
        <div className="summary-tile"><span>Completed Runs</span><strong>{summary.completedRuns}</strong><Tag color="success">ready</Tag></div>
        <div className="summary-tile"><span>Failed Batches</span><strong>{summary.failed}</strong><Tag color="error">needs action</Tag></div>
        <div className="summary-tile"><span>Model Gates</span><strong>{summary.gated}</strong><Tag color="warning">pre-plan blocker</Tag></div>
      </div>

      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>History Operations</h3>
            <p className="section-subtitle">Actions stay visible while filters remain available on demand and rows can expand into run-level status.</p>
          </div>
          <Segmented options={['Compact', 'Expanded']} defaultValue="Compact" />
        </div>
        {filtersOpen && (
          <div className="filter-bar" aria-label="History filters">
            <label className="filter-field wide">
              <span>Search</span>
              <Input
                allowClear
                prefix={<SearchOutlined />}
                placeholder="deck, mode, config, route, error, batch id"
                value={searchText}
                onChange={(event) => setSearchText(event.target.value)}
              />
            </label>
            <label className="filter-field">
              <span>Route</span>
              <Select
                value={routeFilter}
                onChange={setRouteFilter}
                options={[
                  { label: 'All routes', value: 'all' },
                  { label: 'HTML', value: 'HTML' },
                  { label: 'Image 1.0', value: 'Image 1.0' },
                  { label: 'Image 5.0', value: 'Image 5.0' },
                  { label: 'Image 5.3', value: 'Image 5.3' },
                ]}
              />
            </label>
            <label className="filter-field">
              <span>Status</span>
              <Select
                value={statusFilter}
                onChange={setStatusFilter}
                options={[
                  { label: 'All statuses', value: 'all' },
                  { label: 'Pending', value: 'pending' },
                  { label: 'Queued', value: 'queued' },
                  { label: 'Running', value: 'running' },
                  { label: 'Failed', value: 'failed' },
                  { label: 'Timed out', value: 'timed_out' },
                  { label: 'Completed', value: 'completed' },
                ]}
              />
            </label>
            <label className="filter-field">
              <span>Created</span>
              <DatePicker.RangePicker
                value={createdRange}
                onChange={(range) => setCreatedRange(range)}
              />
            </label>
            <Button onClick={() => {
              setSearchText('');
              setRouteFilter('all');
              setStatusFilter('all');
              setCreatedRange(null);
            }}>
              Clear
            </Button>
          </div>
        )}
        <div className="api-state-panel history-api-panel qa-state-panel">
          <div className="mock-panel-head compact">
            <div>
              <h3>History API State Coverage</h3>
              <p className="section-subtitle">Production History loads batches, refreshes expanded rows, polls active work, and gates terminal downloads.</p>
            </div>
            <Segmented value={historyApiState} options={['Ready', 'List loading', 'List error', 'Server empty', 'Filter empty', 'Mutation success', 'Mutation error', 'Bulk partial warning']} onChange={(value) => setHistoryApiState(value as MockApiState)} />
          </div>
          <Alert showIcon type={historyStateCopy[historyApiState].type} title={historyStateCopy[historyApiState].title} description={historyStateCopy[historyApiState].description} />
          <div className="state-proof-grid">
            <div className="mini-tile"><strong>List loading</strong><span className="context-meta">Table loading keeps columns stable.</span></div>
            <div className="mini-tile"><strong>List error</strong><span className="context-meta">Failed to load batches: API error.</span></div>
            <div className="mini-tile"><strong>No batches returned by fake API</strong><span className="context-meta">Server empty is distinct from filter empty.</span></div>
            <div className="mini-tile"><strong>Batch ZIP terminal only</strong><span className="context-meta">Download disabled until terminal: completed, failed, or timed_out.</span></div>
          </div>
          <div className="tag-row">
            <Tag>pending</Tag>
            <Tag color="processing">queued</Tag>
            <Tag color="processing">running</Tag>
            <Tag color="warning">timed_out</Tag>
            <Tag color="success">completed</Tag>
            <Tag color="error">failed</Tag>
          </div>
        </div>
        {selectedHistoryRowKeys.length > 0 && (
          <div className="bulk-action-bar">
            <Text strong>{selectedHistoryRowKeys.length} selected</Text>
            <Popconfirm title="Delete selected batches?" description="Production uses a partial-failure warning when some rows cannot be deleted.">
              <Button danger>Bulk Delete</Button>
            </Popconfirm>
            <Tag color="warning">Bulk partial warning</Tag>
            <Button size="small" onClick={() => setSelectedHistoryRowKeys([])}>Clear</Button>
          </div>
        )}
        <Table
          className="history-table"
          columns={columns}
          dataSource={historyDisplayRows}
          rowKey="id"
          loading={historyApiState === 'List loading'}
          pagination={false}
          scroll={{ x: 900 }}
          rowSelection={{ selectedRowKeys: selectedHistoryRowKeys, onChange: setSelectedHistoryRowKeys }}
          locale={{ emptyText: <Empty description={historyApiState === 'Server empty' ? 'No batches returned by fake API' : 'No batches match the fake filters'} /> }}
          expandable={{
            expandedRowKeys,
            onExpandedRowsChange: (keys) => setExpandedRowKeys([...keys]),
            expandedRowRender: (record) => (
              <div className="expanded-run-grid" aria-label={`Runs in batch ${record.id}`}>
                {record.runs.map((run) => (
                  <button
                    type="button"
                    className={`expanded-run-card ${run.status}`}
                    key={run.id}
                    onClick={() => navigate(`/history/run/${run.id}`)}
                  >
                    <header>
                      <Text strong>{run.label}</Text>
                      {statusTag(run.status)}
                    </header>
                    <div className="context-meta">Deck: {run.deckName}</div>
                    <div className="context-meta">Mode: {run.mode}</div>
                    <div className="context-meta">Config: {run.config}</div>
                    <div className="tag-row">
                      <Tag>Slides: {run.slideSummary}</Tag>
                      <Tag>Image: {run.imageSummary}</Tag>
                    </div>
                  </button>
                ))}
              </div>
            ),
          }}
        />
      </section>
    </>
  );
}

function RunFailStatsPage() {
  const navigate = useNavigate();
  const errorClassRows = [
    { key: 'empty-image', errorClass: 'No inline image bytes', route: 'Image 5.0', share: 63, retryClass: 'auto-retryable', nextAction: 'Retry after provider/network backoff' },
    { key: 'bad-request', errorClass: '400 Bad Request', route: 'Image 1.0', share: 13, retryClass: 'terminal', nextAction: 'Correct request/config, then Continue or Force' },
    { key: 'invalid-endpoint', errorClass: 'Invalid test endpoint', route: 'HTML', share: 6, retryClass: 'terminal', nextAction: 'Fix endpoint/profile before retry' },
  ];
  const routeRows = [
    { key: 'image50', route: 'Image 5.0', modelProfile: 'image_generator production', failed: 12, retryable: '9 auto / 3 manual', topError: 'No inline image bytes' },
    { key: 'image10', route: 'Image 1.0', modelProfile: 'legacy Image route', failed: 2, retryable: '0 auto / 2 terminal', topError: '400 Bad Request' },
    { key: 'html', route: 'HTML', modelProfile: 'HTML Agent test', failed: 1, retryable: '0 auto / 1 terminal', topError: 'Invalid endpoint' },
    { key: 'image53', route: 'Image 5.3', modelProfile: 'roadmap gate', failed: 0, retryable: 'blocked by gate', topError: 'Model connectivity pending' },
  ];

  const errorColumns: ColumnsType<(typeof errorClassRows)[number]> = [
    { title: 'Error Class', dataIndex: 'errorClass', key: 'errorClass' },
    { title: 'Dominant Route', dataIndex: 'route', key: 'route', width: 160, render: routeTag },
    {
      title: 'Share',
      dataIndex: 'share',
      key: 'share',
      width: 180,
      render: (share: number) => <Progress percent={share} size="small" status={share > 50 ? 'exception' : 'active'} />,
    },
    { title: 'Retry Class', dataIndex: 'retryClass', key: 'retryClass', width: 160, render: retryTag },
    { title: 'Next Action', dataIndex: 'nextAction', key: 'nextAction' },
  ];
  const routeColumns: ColumnsType<(typeof routeRows)[number]> = [
    { title: 'Route', dataIndex: 'route', key: 'route', width: 150, render: routeTag },
    { title: 'Model/Profile', dataIndex: 'modelProfile', key: 'modelProfile' },
    { title: 'Failed', dataIndex: 'failed', key: 'failed', width: 100 },
    { title: 'Retry Split', dataIndex: 'retryable', key: 'retryable', width: 180 },
    { title: 'Top Error', dataIndex: 'topError', key: 'topError' },
  ];

  return (
    <>
      <PageHeader
        title="RunFail Statistics"
        subtitle="Additive Phase A analytics proposal for fixed failure aggregation, route/model breakdown, retry classification, and export packages."
        actions={(
          <Space wrap>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/history')}>Back to History</Button>
            <Button icon={<DownloadOutlined />}>Download CSV</Button>
            <Button icon={<DownloadOutlined />}>Download JSON</Button>
          </Space>
        )}
      />
      <div className="runfail-page-grid">
        <Alert
          showIcon
          type="info"
          message="Additive / proposed surface"
          description="No production RunFail route or API was found in the current source. This fake page is a Phase A review target and must be implemented with a new backend aggregation contract before it is treated as production parity."
        />
        <section className="mock-panel">
          <div className="mock-panel-head">
            <div>
              <h3>Phase A Summary</h3>
              <p className="section-subtitle">Fixed aggregation only. LLM insight stays deferred until explicit model/provider/security approval.</p>
            </div>
            <Tag color="success">Phase A</Tag>
          </div>
          <div className="runfail-metrics runfail-page-metrics">
            <div className="mini-tile"><strong>16</strong><span className="context-meta">failed or timed out</span></div>
            <div className="mini-tile"><strong>75%</strong><span className="context-meta">Image 5.0 cluster</span></div>
            <div className="mini-tile"><strong>2</strong><span className="context-meta">terminal classes</span></div>
            <div className="mini-tile"><strong>0</strong><span className="context-meta">LLM insights in Phase A</span></div>
          </div>
        </section>

        <section className="mock-panel">
          <div className="mock-panel-head">
            <div>
              <h3>Error Class Distribution</h3>
              <p className="section-subtitle">The dominant failure is retryable; terminal classes point users back to Config before Continue or Force.</p>
            </div>
          </div>
          <Table
            columns={errorColumns}
            dataSource={errorClassRows}
            rowKey="key"
            pagination={false}
            scroll={{ x: 900 }}
          />
        </section>

        <section className="mock-panel">
          <div className="mock-panel-head">
            <div>
              <h3>Route And Model Breakdown</h3>
              <p className="section-subtitle">Image 5.3 appears as a real route, but implementation remains blocked until its model gate passes.</p>
            </div>
          </div>
          <Table
            columns={routeColumns}
            dataSource={routeRows}
            rowKey="key"
            pagination={false}
            scroll={{ x: 860 }}
          />
        </section>

        <section className="mock-panel">
          <div className="mock-panel-head">
            <div>
              <h3>Export Package</h3>
              <p className="section-subtitle">CSV/JSON/report exports are part of Phase A; model-written insight remains disabled.</p>
            </div>
          </div>
          <div className="config-card-grid">
            <div className="mini-tile"><Text strong>CSV</Text><p className="context-meta">route, model, status, error class, retry class, time window</p></div>
            <div className="mini-tile"><Text strong>JSON</Text><p className="context-meta">raw aggregate package for external review or future agent handoff</p></div>
            <div className="mini-tile"><Text strong>Insight Model</Text><p className="context-meta">deferred; no provider call in Phase A</p></div>
          </div>
        </section>
      </div>
    </>
  );
}
function BatchOverviewPage({ openAction }: PageProps) {
  const navigate = useNavigate();
  const [selectedRunId, setSelectedRunId] = useState(801);
  const selectedRun = batchRuns.find((run) => run.id === selectedRunId) || batchRuns[0];

  return (
    <>
      <PageHeader
        title="Batch #128 Overview"
        subtitle="Scan all generated run outputs first, then expand one run only when its slide/image evidence needs inspection."
        actions={(
          <Space wrap>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/history')}>Back to History</Button>
            <Button icon={<BranchesOutlined />} onClick={() => navigate('/config')}>Generation Routes</Button>
          </Space>
        )}
      />
      <div className="summary-grid">
        <div className="summary-tile"><span>Deck</span><strong>Feature Upgrade Deck</strong><Tag color="gold">Image 5.0</Tag></div>
        <div className="summary-tile"><span>Config</span><strong>Production image</strong><Tag color="purple">Batch · Image route</Tag></div>
        <div className="summary-tile"><span>Runs</span><strong>4 / 10</strong><Tag color="error">60% failure rate</Tag></div>
        <div className="summary-tile"><span>Created</span><strong>Today 10:13</strong><Tag>Batch #128</Tag></div>
      </div>
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Sibling Run Outputs</h3>
            <p className="section-subtitle">Every candidate run stays visible for fast comparison. Click one result to reveal its slides and image actions below the grid.</p>
          </div>
          <Space wrap>
            <ProposedActionTag />
            <Button onClick={() => openAction({ kind: 'Continue', scope: 'Batch', target: 'Batch #128 unfinished runs' })}>Continue Batch</Button>
            <Button onClick={() => openAction({ kind: 'Retry', scope: 'Batch', target: 'Batch #128 retryable failures' })}>Retry Batch</Button>
            <Button danger onClick={() => openAction({ kind: 'Force Regenerate', scope: 'Batch', target: 'Batch #128 all runs' })}>Force Batch</Button>
          </Space>
        </div>
        <div className="scope-target-grid two-up batch-run-output-grid">
          {batchRuns.map((run) => (
            <article
              role="button"
              tabIndex={0}
              className={`scope-target-card image-output-card ${run.id === selectedRunId ? 'active' : ''}`}
              key={run.id}
              onClick={() => {
                setSelectedRunId(run.id);
                window.setTimeout(() => {
                  document.getElementById('selected-run-evidence')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                }, 0);
              }}
              onKeyDown={(event) => {
                if (event.key === 'Enter' || event.key === ' ') {
                  event.preventDefault();
                  setSelectedRunId(run.id);
                }
              }}
            >
              <div className="run-output-visual"><strong>{run.label}</strong><span>{run.imageSummary}</span></div>
              <header><Text strong>{run.label}</Text>{statusTag(run.status)}</header>
              <span>{run.candidateLabel} · {run.requirement}</span>
              <span>{run.color}</span>
              <span>{run.slideSummary}</span>
              <span className="context-meta">{run.statusHelp}</span>
              {run.errorPreview !== 'none' && <Tag color={run.retryClass === 'terminal' ? 'red' : 'gold'}>{run.errorPreview}</Tag>}
              {run.id === selectedRun.id && <Tag color="blue">Selected run</Tag>}
              <div className="button-row">
                <Button size="small" icon={<EyeOutlined />} onClick={(event) => {
                  event.stopPropagation();
                  navigate(`/history/run/${run.id}`);
                }}>
                  Open Detail
                </Button>
                <Button size="small" onClick={(event) => {
                  event.stopPropagation();
                  setSelectedRunId(run.id);
                  openAction({ kind: 'Retry', scope: 'Run', target: `${run.label}: ${run.summary}` });
                }}>Retry</Button>
                <Button size="small" danger onClick={(event) => {
                  event.stopPropagation();
                  setSelectedRunId(run.id);
                  openAction({ kind: 'Force Regenerate', scope: 'Run', target: `${run.label}: ${run.summary}` });
                }}>Force</Button>
              </div>
            </article>
          ))}
        </div>
      </section>
      <BatchOverviewArtifactSections
        navigate={navigate}
        openAction={openAction}
        selectedRunId={selectedRunId}
        setSelectedRunId={setSelectedRunId}
      />
    </>
  );
}

function BatchOverviewArtifactSections({
  navigate,
  openAction,
  selectedRunId,
  setSelectedRunId,
}: {
  navigate: ReturnType<typeof useNavigate>;
  openAction: PageProps['openAction'];
  selectedRunId: number;
  setSelectedRunId: (id: number) => void;
}) {
  const selectedRun = batchRuns.find((run) => run.id === selectedRunId) || batchRuns[0];
  const runIndex = batchRuns.findIndex((run) => run.id === selectedRun.id);
  const nextRun = batchRuns[(runIndex + 1) % batchRuns.length];
  const previousRun = batchRuns[(runIndex - 1 + batchRuns.length) % batchRuns.length];

  return (
    <section id="selected-run-evidence" className="mock-panel selected-run-evidence-panel" aria-label="Selected run evidence">
      <div className="mock-panel-head">
        <div>
          <h3>Selected Run Evidence</h3>
          <p className="section-subtitle">Run metadata, slide results, and image actions stay nested under the selected candidate instead of competing with the batch overview.</p>
        </div>
        <Space wrap>
          <Tag color="blue">{selectedRun.label}</Tag>
          {statusTag(selectedRun.status)}
          {retryTag(selectedRun.retryClass)}
        </Space>
      </div>
      <div className="selected-run-layout">
        <aside className="selected-run-summary">
          <div className="mini-tile">
            <Text strong>{selectedRun.deckName}</Text>
            <p className="context-meta">{selectedRun.candidateLabel} · {selectedRun.requirement}</p>
            <div className="tag-row">
              <Tag color="gold">{selectedRun.mode}</Tag>
              <Tag color="purple">{selectedRun.config}</Tag>
              <Tag>{selectedRun.color}</Tag>
            </div>
          </div>
          <Descriptions size="small" bordered column={1}>
            <Descriptions.Item label="Run">{selectedRun.label}</Descriptions.Item>
            <Descriptions.Item label="Slides">{selectedRun.slideSummary}</Descriptions.Item>
            <Descriptions.Item label="Image output">{selectedRun.imageSummary}</Descriptions.Item>
            <Descriptions.Item label="Failure preview">{selectedRun.errorPreview}</Descriptions.Item>
          </Descriptions>
          <div className="button-row">
            <Button icon={<ArrowLeftOutlined />} onClick={() => setSelectedRunId(previousRun.id)}>Previous Run</Button>
            <Button onClick={() => setSelectedRunId(nextRun.id)}>Next Run</Button>
          </div>
          <Space wrap>
            <ProposedActionTag />
            <Button icon={<EyeOutlined />} onClick={() => navigate(`/history/run/${selectedRun.id}`)}>Open Run Detail</Button>
            <Button onClick={() => openAction({ kind: 'Continue', scope: 'Run', target: `${selectedRun.label}: unfinished slides/images` })}>Continue Run</Button>
            <Button onClick={() => openAction({ kind: 'Retry', scope: 'Run', target: `${selectedRun.label}: ${selectedRun.summary}` })}>Retry Run</Button>
            <Button danger onClick={() => openAction({ kind: 'Force Regenerate', scope: 'Run', target: `${selectedRun.label}: ${selectedRun.summary}` })}>Force Run</Button>
          </Space>
        </aside>
        <div className="selected-run-slide-grid" aria-label="Slides and image artifacts for selected run">
          {slides.map((slide) => (
            <article className="selected-run-slide-card" key={slide.id}>
              <div className="slide-visual"><strong>{slide.visualLabel}</strong></div>
              <div className="selected-run-slide-copy">
                <header>
                  <Text strong>Slide {slide.position}: {slide.title}</Text>
                  <div className="tag-row">
                    {statusTag(slide.status)}
                    <Tag>{slide.artifactVersion}</Tag>
                  </div>
                </header>
                <p className="context-meta">{slide.imageStatus}</p>
                <div className="artifact-scope-row">
                  <span><Text strong>Slide scope</Text><span className="context-meta"> continue/retry the slide stage</span></span>
                  <span><Text strong>Image scope</Text><span className="context-meta"> regenerate only this final image artifact</span></span>
                </div>
                <Space wrap>
                  <ProposedActionTag />
                  <Button size="small" onClick={() => openAction({ kind: 'Continue', scope: 'Slide', target: `${selectedRun.label} / Slide ${slide.position}: ${slide.title}` })}>Continue Slide</Button>
                  <Button size="small" onClick={() => openAction({ kind: 'Retry', scope: 'Slide', target: `${selectedRun.label} / Slide ${slide.position}: ${slide.title}` })}>Retry Slide</Button>
                  <Button size="small" danger onClick={() => openAction({ kind: 'Force Regenerate', scope: 'Image', target: `${selectedRun.label} / Image for Slide ${slide.position}: ${slide.artifactVersion}` })}>Force Image</Button>
                </Space>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

function OperationPanel({
  scope,
  setScope,
  target,
  openAction,
}: {
  scope: ActionScope;
  setScope: (scope: ActionScope) => void;
  target: string;
  openAction: PageProps['openAction'];
}) {
  return (
    <aside className="mock-panel">
      <div className="mock-panel-head">
        <div>
          <h3>Operation Scope</h3>
          <p className="section-subtitle">Choose Batch, Run, Slide, or Image to focus the matching target section and confirmation payload.</p>
        </div>
      </div>
      <div className="side-stack scope-control">
        <Segmented
          value={scope}
          options={['Batch', 'Run', 'Slide', 'Image']}
          onChange={(value) => setScope(value as ActionScope)}
        />
        <div className="operation-target">
          <span className="context-meta">Selected target</span>
          <Text strong>{target}</Text>
        </div>
        <ScopePreview scope={scope} target={target} />
        <Alert
          type="warning"
          showIcon
          title="Proposed action contract"
          description="Continue, Retry, and Force Regenerate are shown for review only until the API payload, allowed statuses, idempotency, and version-write rules are approved."
        />
        <Space wrap>
          <ProposedActionTag />
          <Button onClick={() => openAction({ kind: 'Continue', scope, target })}>Continue</Button>
          <Button onClick={() => openAction({ kind: 'Retry', scope, target })}>Retry</Button>
          <Button danger onClick={() => openAction({ kind: 'Force Regenerate', scope, target })}>
            Force Regenerate
          </Button>
        </Space>
        <Alert
          type="info"
          showIcon
          title="Force Regenerate creates a new version."
          description="The fake flow keeps five historical versions and marks the oldest retained version for rotation."
        />
      </div>
    </aside>
  );
}

function ScopePreview({ scope, target }: { scope: ActionScope; target: string }) {
  const content = {
    Batch: ['all sibling runs included', '40% progress', 'Force creates new batch version'],
    Run: ['selected run only', 'slide/image state preserved', 'Retry only failed retryable parts'],
    Slide: ['selected slide only', 'stage evidence remains visible', 'Continue from failed slide onward'],
    Image: ['selected image artifact only', 'version lineage preserved', 'Force creates next image version'],
  }[scope];

  return (
    <div className="scope-preview">
      <Tag color="blue">{target}</Tag>
      {content.map((item) => <Tag key={item}>{item}</Tag>)}
    </div>
  );
}

function RunDetailPage({ openAction, notify }: PageProps) {
  const navigate = useNavigate();
  const { runId } = useParams();
  const [selectedRunId, setSelectedRunId] = useState(() => Number(runId) || 801);
  const [selectedSlideId, setSelectedSlideId] = useState<number | null>(null);
  const [scope, setScope] = useState<ActionScope>('Image');
  const [reviewMode, setReviewMode] = useState<'Tiled Review' | 'Split Review' | 'Full Gallery'>('Tiled Review');
  const allRuns = useMemo(() => historyRows.flatMap((row) => row.runs), []);
  const selectedRun = allRuns.find((run) => run.id === selectedRunId) || batchRuns[0];
  const parentBatch = historyRows.find((row) => row.runs.some((run) => run.id === selectedRun.id)) || historyRows[0];
  const siblingRuns = parentBatch.runs.length > 0 ? parentBatch.runs : batchRuns;
  const isHtmlRun = selectedRun.mode === 'HTML';
  const runSlides = isHtmlRun ? htmlSlides : slides;
  const selectedSlide = runSlides.find((slide) => slide.id === selectedSlideId);
  const evidenceSlide = selectedSlide || runSlides[0];
  const engineLabel = isHtmlRun ? 'HTML Agent · captured PNG' : 'Image Generator · unified Designer XML';
  const routeFailureRate = parentBatch.failureRate;
  const runDetailTarget = {
    Batch: `Batch #${parentBatch.batchId}`,
    Run: `Run #${selectedRun.id}`,
    Slide: `Run #${selectedRun.id} / Slide ${evidenceSlide.position}: ${evidenceSlide.title}`,
    Image: `Run #${selectedRun.id} / Image for Slide ${evidenceSlide.position}: ${evidenceSlide.artifactVersion}`,
  }[scope];
  const imageEvidenceItems = [
    {
      key: 'final-image',
      label: 'Final Image',
      children: (
        <div className="lineage-stack">
          <div className="scan-slide-card active">
            <div className="slide-visual"><strong>{evidenceSlide.visualLabel}</strong></div>
            <div className="scan-slide-caption">
              <div>
                <Text strong>Slide {evidenceSlide.position}: {evidenceSlide.title}</Text>
                <span className="context-meta">{evidenceSlide.imageStatus}</span>
              </div>
              <div className="tag-row">
                {statusTag(evidenceSlide.status)}
                {routeTag(evidenceSlide.route)}
                <Tag>{evidenceSlide.artifactVersion}</Tag>
              </div>
            </div>
          </div>
          <div className="artifact-matrix">
            <div className="mini-tile"><Text strong>Generated output first</Text><p className="context-meta">The selected image remains the default evidence tab; Config, Prompt, XML, Request, and Response are secondary detail tabs.</p></div>
            <div className="mini-tile"><Text strong>Operation target</Text><p className="context-meta">{runDetailTarget}</p></div>
            <div className="mini-tile"><Text strong>Version</Text><p className="context-meta">{evidenceSlide.artifactVersion} keeps lineage until a proposed Force Regenerate action is approved.</p></div>
          </div>
        </div>
      ),
    },
    {
      key: 'overview',
      label: 'Overview',
      children: (
        <div className="lineage-stack">
          <div className="lineage-grid">
            {['Deck', 'Prompt', 'Blueprint', 'Image Request', 'Response'].map((item) => (
              <div className="lineage-step" key={item}>
                <Text strong>{item}</Text>
                <div className="context-meta">{item === 'Response' ? evidenceSlide.imageStatus : 'persisted stage artifact'}</div>
              </div>
            ))}
          </div>
          <div className="artifact-matrix">
            <div className="mini-tile"><Text strong>Image Final Image</Text><p className="context-meta">Final image, request, response, dependencies, version lineage.</p></div>
            <div className="mini-tile"><Text strong>HTML Outputs</Text><p className="context-meta">Captured PNG, Live HTML, Clean HTML, Raw Response, Original Content.</p></div>
            <div className="mini-tile"><Text strong>Design Principle</Text><p className="context-meta">Design Principle JSON and raw text stay downloadable when present.</p></div>
          </div>
        </div>
      ),
    },
    {
      key: 'config',
      label: 'Config',
      children: (
        <Descriptions size="small" bordered column={1}>
          <Descriptions.Item label="API Type">Gemini compatible image endpoint</Descriptions.Item>
          <Descriptions.Item label="Endpoint">masked provider URL</Descriptions.Item>
          <Descriptions.Item label="Model">image_generator production profile, canonical ID pending gate</Descriptions.Item>
          <Descriptions.Item label="API Key">[REDACTED]</Descriptions.Item>
        </Descriptions>
      ),
    },
    {
      key: 'prompt',
      label: 'Rendered Prompt',
      children: (
        <Descriptions size="small" bordered column={1}>
          <Descriptions.Item label="Deck Theme">Feature upgrade flow and UI recovery</Descriptions.Item>
          <Descriptions.Item label="Requirement">Visualize actual request order and dependencies</Descriptions.Item>
          <Descriptions.Item label="Required Color">Yellow Image operational palette</Descriptions.Item>
          <Descriptions.Item label="Current Slide">{evidenceSlide.title}</Descriptions.Item>
          <Descriptions.Item label="Route Instruction">Image 5.0 unified designer produces XML before image request</Descriptions.Item>
        </Descriptions>
      ),
    },
    {
      key: 'xml',
      label: 'Blueprint XML',
      children: <pre>{`<image-slide version="5.0" slide="${evidenceSlide.position}">
  <canvas ratio="16:9" />
  <reference slide="1" type="cover" />
  <layout density="medium" />
  <visual name="${evidenceSlide.title.toLowerCase().replaceAll(' ', '-')}" />
</image-slide>`}</pre>,
    },
    {
      key: 'request',
      label: 'Image Request',
      children: <pre>{JSON.stringify({
        scope: 'image',
        route: 'image_5_0',
        slide_position: evidenceSlide.position,
        headers: { authorization: '[REDACTED]' },
        references: ['slide-1-cover-reference.png'],
        temperature: 1,
      }, null, 2)}</pre>,
    },
    {
      key: 'response',
      label: 'Response',
      children: <pre>{JSON.stringify({
        status: evidenceSlide.status,
        error_class: evidenceSlide.status === 'failed' ? 'empty_image_response' : null,
        text: evidenceSlide.status === 'failed' ? [] : ['ok'],
        inline_image_bytes: evidenceSlide.status === 'failed' ? null : '[REDACTED_BYTES]',
        retry_classification: evidenceSlide.status === 'failed' ? 'auto-retryable' : 'none',
      }, null, 2)}</pre>,
    },
    {
      key: 'dependencies',
      label: 'Dependencies',
      children: (
        <Descriptions size="small" bordered column={1}>
          <Descriptions.Item label="Cover Reference">Slide 1 final image retained</Descriptions.Item>
          <Descriptions.Item label="Seed Dependency">Not required for Image 5.0</Descriptions.Item>
          <Descriptions.Item label="Sibling Runs">Run 802 and Run 807 share failure class</Descriptions.Item>
          <Descriptions.Item label="Regenerate Target">{scope} scope creates a new version if confirmed</Descriptions.Item>
        </Descriptions>
      ),
    },
  ];
  const htmlEvidenceItems = [
    {
      key: 'captured',
      label: 'Captured PNG',
      children: (
        <div className="lineage-stack">
          <div className="lineage-grid">
            {['Deck', 'Designer Prompt', 'HTML Agent', 'Clean HTML', 'Captured PNG'].map((item) => (
              <div className="lineage-step" key={item}>
                <Text strong>{item}</Text>
                <div className="context-meta">{item === 'Captured PNG' ? evidenceSlide.imageStatus : 'persisted HTML artifact'}</div>
              </div>
            ))}
          </div>
          <div className="artifact-matrix">
            <div className="mini-tile"><Text strong>Captured PNG</Text><p className="context-meta">Production preview image captured from the clean HTML render.</p></div>
            <div className="mini-tile"><Text strong>Live HTML</Text><p className="context-meta">Inspectable live document output for visual and accessibility review.</p></div>
            <div className="mini-tile"><Text strong>Design Principle</Text><p className="context-meta">Design Principle JSON and Raw Response remain attached when present.</p></div>
          </div>
        </div>
      ),
    },
    {
      key: 'live-html',
      label: 'HTML / Live HTML',
      children: (
        <div className="evidence-html-live">
          <div className="slide-visual"><strong>{evidenceSlide.visualLabel}</strong></div>
          <pre>{`<main class="slide" data-run="${selectedRun.id}">
  <section aria-label="${evidenceSlide.title}">
    <h1>${evidenceSlide.title}</h1>
    <p>Live HTML preview retained for route ${selectedRun.mode}.</p>
  </section>
</main>`}</pre>
        </div>
      ),
    },
    {
      key: 'clean-html',
      label: 'Clean HTML',
      children: <pre>{`<section class="slide-clean">
  <h1>${evidenceSlide.title}</h1>
  <p>Cleaned HTML artifact for captured PNG generation.</p>
</section>`}</pre>,
    },
    {
      key: 'request',
      label: 'Request',
      children: <pre>{JSON.stringify({
        route: 'html',
        run_id: selectedRun.id,
        slide_position: evidenceSlide.position,
        designer_prompt: 'designer-default-v4',
        html_agent_prompt: 'html-agent-v6',
        config: selectedRun.config,
      }, null, 2)}</pre>,
    },
    {
      key: 'response',
      label: 'Response',
      children: <pre>{JSON.stringify({
        status: evidenceSlide.status,
        captured_png: `${evidenceSlide.title.toLowerCase().replaceAll(' ', '-')}.png`,
        clean_html: `${evidenceSlide.title.toLowerCase().replaceAll(' ', '-')}.html`,
        retry_classification: 'none',
      }, null, 2)}</pre>,
    },
    {
      key: 'deps',
      label: 'Dependencies',
      children: (
        <Descriptions size="small" bordered column={1}>
          <Descriptions.Item label="Original Content">Deck slide content retained for HTML regeneration.</Descriptions.Item>
          <Descriptions.Item label="Prompt Versions">Designer default and HTML Agent build prompt.</Descriptions.Item>
          <Descriptions.Item label="Config">{selectedRun.config}</Descriptions.Item>
          <Descriptions.Item label="Regenerate Target">{scope} scope preserves clean HTML and captured PNG lineage.</Descriptions.Item>
        </Descriptions>
      ),
    },
    {
      key: 'raw',
      label: 'Raw Output',
      children: <pre>{`raw_response:
  route: HTML
  slide: ${evidenceSlide.position}
  captured_png: retained
  live_html: retained
  clean_html: retained`}</pre>,
    },
    {
      key: 'original',
      label: 'Original Content',
      children: <pre>{`Slide ${evidenceSlide.position}: ${evidenceSlide.title}
Original deck content, user requirement, and required color stay available for review.`}</pre>,
    },
    {
      key: 'design',
      label: 'Design Principle',
      children: (
        <Tabs
          items={[
            {
              key: 'json',
              label: 'JSON',
              children: <pre>{JSON.stringify({
                layout: 'dense review packet',
                hierarchy: 'title, evidence summary, generated output',
                route: 'HTML',
              }, null, 2)}</pre>,
            },
            {
              key: 'raw',
              label: 'Raw Response',
              children: <pre>{`Design Principle raw text retained when present.
Run ${selectedRun.id} keeps the generated HTML route rationale with the captured artifact package.`}</pre>,
            },
          ]}
        />
      ),
    },
  ];
  const evidenceItems = isHtmlRun ? htmlEvidenceItems : imageEvidenceItems;

  return (
    <>
      <PageHeader
        title={`Run #${selectedRun.id} Detail`}
        subtitle="Default view prioritizes scanning generated images. Evidence opens inline only after a slide or image is selected."
        actions={(
          <Space wrap>
            <Select
              aria-label="Sibling run switcher"
              value={selectedRunId}
              onChange={(value) => {
                setSelectedRunId(value);
                setSelectedSlideId(null);
              }}
              options={siblingRuns.map((run) => ({ label: `${run.label} · ${run.summary}`, value: run.id }))}
              style={{ minWidth: 240 }}
            />
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate(`/history/batch/${parentBatch.batchId}`)}>Back to Batch</Button>
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/history')}>Back to History</Button>
            <Button icon={<ReloadOutlined />} onClick={() => notify(`Run #${selectedRun.id} refreshed in fake state`)}>Refresh</Button>
            <Button icon={<DownloadOutlined />} onClick={() => notify(`Run ZIP export queued for Run #${selectedRun.id}`)}>Run ZIP</Button>
            <Button icon={<DownloadOutlined />} onClick={() => notify(`Evidence Package downloaded for Run #${selectedRun.id}`)}>Evidence Package</Button>
          </Space>
        )}
      />
      <Descriptions bordered column={{ xs: 1, md: 2 }} className="run-summary-meta">
        <Descriptions.Item label="Run ID">{selectedRun.id}</Descriptions.Item>
        <Descriptions.Item label="Deck">{selectedRun.deckName}</Descriptions.Item>
        <Descriptions.Item label="Requirement">{parentBatch.requirement}</Descriptions.Item>
        <Descriptions.Item label="Color">{parentBatch.color}</Descriptions.Item>
        <Descriptions.Item label="Mode"><Tag color="gold">{selectedRun.mode}</Tag></Descriptions.Item>
        <Descriptions.Item label="Status">{statusTag(selectedRun.status)} {retryTag(selectedRun.status === 'failed' ? 'auto-retryable' : 'none')}</Descriptions.Item>
        <Descriptions.Item label="Created">{parentBatch.createdAt}</Descriptions.Item>
        <Descriptions.Item label="Started">{isHtmlRun ? '2026-05-28 18:40' : '2026-05-29 10:13'}</Descriptions.Item>
        <Descriptions.Item label="Completed">{selectedRun.status === 'completed' ? '2026-05-28 18:44' : 'not completed'}</Descriptions.Item>
        <Descriptions.Item label="Error">{selectedRun.status === 'failed' ? selectedRun.imageSummary : 'none'}</Descriptions.Item>
        <Descriptions.Item label="Failure Rate"><Tag color={routeFailureRate > 0 ? 'red' : 'green'}>{routeFailureRate}%</Tag></Descriptions.Item>
        <Descriptions.Item label="Config">{selectedRun.config}</Descriptions.Item>
        <Descriptions.Item label="Engine">{engineLabel}</Descriptions.Item>
        <Descriptions.Item label="Designer Prompt">{isHtmlRun ? 'designer-default-v4' : 'image-5.0-unified-director'}</Descriptions.Item>
        <Descriptions.Item label="HTML Prompt">{isHtmlRun ? 'html-agent-v6' : 'not used'}</Descriptions.Item>
        <Descriptions.Item label="Strategy">Tiled Review / Split Review / Full Gallery</Descriptions.Item>
        <Descriptions.Item label="Auto Candidate">candidate 1 retained</Descriptions.Item>
      </Descriptions>
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>{isHtmlRun ? 'Slides / HTML Output Scan' : 'Generated Image Scan'}</h3>
            <p className="section-subtitle">{isHtmlRun ? 'HTML route keeps captured PNG, live HTML, clean HTML, raw response, and original content visible for each slide.' : 'Two cards per row by default. Click a generated result only when it needs closer inspection.'}</p>
          </div>
          <Segmented
            value={reviewMode}
            options={['Tiled Review', 'Split Review', 'Full Gallery']}
            onChange={(value) => setReviewMode(value as 'Tiled Review' | 'Split Review' | 'Full Gallery')}
          />
        </div>
        <div className="review-mode-strip">
          <Tag color="blue">{reviewMode}</Tag>
          {reviewMode === 'Tiled Review' && <span className="context-meta">Two-column scan stays primary; details open under the selected result.</span>}
          {reviewMode === 'Split Review' && <span className="context-meta">Selected image compares current artifact against retained version history without hiding evidence tabs.</span>}
          {reviewMode === 'Full Gallery' && <span className="context-meta">All generated outputs stay visible for fast batch-level scanning before any deep inspection.</span>}
        </div>
        <div className={`scan-slide-grid ${reviewMode.toLowerCase().replaceAll(' ', '-')}`}>
          {Array.from({ length: Math.ceil(runSlides.length / 2) }, (_, rowIndex) => {
            const rowSlides = runSlides.slice(rowIndex * 2, rowIndex * 2 + 2);
            const rowHasSelectedSlide = Boolean(selectedSlide && rowSlides.some((slide) => slide.id === selectedSlide.id));
            return (
              <div className="scan-slide-row" key={`row-${rowIndex}`}>
                {rowSlides.map((slide) => (
                  <div
                    role="button"
                    tabIndex={0}
                    className={`scan-slide-card ${slide.id === selectedSlide?.id ? 'active' : ''}`}
                    key={slide.id}
                    onClick={() => setSelectedSlideId((current) => (current === slide.id ? null : slide.id))}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        setSelectedSlideId((current) => (current === slide.id ? null : slide.id));
                      }
                    }}
                  >
                    <div className="slide-visual"><strong>{slide.visualLabel}</strong></div>
                    <div className="scan-slide-caption">
                      <div>
                        <Text strong>Slide {slide.position}: {slide.title}</Text>
                        <span className="context-meta">{slide.imageStatus}</span>
                      </div>
                      <div className="tag-row">
                        {statusTag(slide.status)}
                        {routeTag(slide.route)}
                        <Tag>{slide.artifactVersion}</Tag>
                      </div>
                      {slide.id === selectedSlide?.id && (
                        <div className="local-action-row">
                          <ProposedActionTag />
                          <Button size="small" onClick={(event) => {
                            event.stopPropagation();
                            setScope('Image');
                            openAction({ kind: 'Retry', scope: 'Image', target: `Run #${selectedRun.id} / Image for Slide ${slide.position}: ${slide.artifactVersion}` });
                          }}>Retry Image</Button>
                          <Button size="small" danger onClick={(event) => {
                            event.stopPropagation();
                            setScope('Image');
                            openAction({ kind: 'Force Regenerate', scope: 'Image', target: `Run #${selectedRun.id} / Image for Slide ${slide.position}: ${slide.artifactVersion}` });
                          }}>Force Image</Button>
                        </div>
                      )}
                    </div>
                  </div>
                ))}
                {selectedSlide && rowHasSelectedSlide && (
                <section className="inline-evidence-tray" aria-label={`Evidence for slide ${selectedSlide.position}`}>
                  <div className="mock-panel-head compact">
                    <div>
                      <h3>Evidence For Slide {selectedSlide.position}</h3>
                      <p className="section-subtitle">Details open under the selected result, preserving the scan-first grid above and below.</p>
                    </div>
                    <div className="tag-row">
                      <Tag color="blue">{scope} scope</Tag>
                      <Tag color="red">Force creates version</Tag>
                    </div>
                  </div>
                  {reviewMode === 'Split Review' && (
                    <div className="split-review-comparison" aria-label="Split Review comparison">
                      <div className="mini-tile">
                        <Text strong>Current artifact</Text>
                        <p className="context-meta">{selectedSlide.artifactVersion} · {selectedSlide.imageStatus}</p>
                      </div>
                      <div className="mini-tile">
                        <Text strong>Previous retained version</Text>
                        <p className="context-meta">v4 · layout drift retained for side-by-side comparison</p>
                      </div>
                    </div>
                  )}
                  <div className="inline-evidence-layout">
                    <div className="evidence-main">
                      <Tabs items={evidenceItems} defaultActiveKey={isHtmlRun ? 'captured' : 'final-image'} />
                    </div>
                    <div className="inline-evidence-side">
                      <OperationPanel
                        scope={scope}
                        setScope={setScope}
                        target={runDetailTarget}
                        openAction={openAction}
                      />
                      <div className="mini-tile">
                        <Text strong>Version History</Text>
                        <div className="side-stack" style={{ padding: '10px 0 0' }}>
                          {versionHistory.map((item) => (
                            <div className="version-row" key={item.version}>
                              <Text strong>{item.version}</Text>
                              <span>{item.summary}</span>
                              <Tag color={item.status === 'current' ? 'red' : item.status === 'rotates next' ? 'gold' : 'default'}>{item.status}</Tag>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  </div>
                </section>
                )}
              </div>
            );
          })}
        </div>
      </section>
    </>
  );
}

type StandaloneConfigArea = 'prompt-library' | 'system-settings';

function ConfigCenterPage({ openAction, notify, initialArea = 'workspace', standaloneArea }: PageProps & { initialArea?: string; standaloneArea?: StandaloneConfigArea }) {
  const [selectedArea, setSelectedArea] = useState(initialArea);
  const [promptLifecycle, setPromptLifecycle] = useState<'all' | 'active' | 'archived'>('active');
  const [promptRole, setPromptRole] = useState('all');
  const [promptFolder, setPromptFolder] = useState('all');
  const [promptSearch, setPromptSearch] = useState('');
  const [selectedPromptKeys, setSelectedPromptKeys] = useState<Key[]>(['image-50-unified']);
  const [selectedPrompt, setSelectedPrompt] = useState<PromptRow>(promptRows[8]);
  const [promptModalMode, setPromptModalMode] = useState<'add' | 'edit' | null>(null);
  const [promptFolderModalOpen, setPromptFolderModalOpen] = useState(false);
  const [promptBulkMoveOpen, setPromptBulkMoveOpen] = useState(false);
  const [assistantReviewOpen, setAssistantReviewOpen] = useState(false);
  const [promptVariablePickerOpen, setPromptVariablePickerOpen] = useState(false);
  const [selectedCombination, setSelectedCombination] = useState<CombinationRow>(combinationRows[0]);
  const [combinationModalMode, setCombinationModalMode] = useState<'add' | 'edit' | null>(null);
  const [selectedRoleProfile, setSelectedRoleProfile] = useState<RoleModelProfile>(roleModelProfiles[0]);
  const [roleProfileModalMode, setRoleProfileModalMode] = useState<'add' | 'edit' | null>(null);
  const [variableTableRows, setVariableTableRows] = useState<VariableRow[]>(variableRows);
  const [selectedVariable, setSelectedVariable] = useState<VariableRow | null>(null);
  const [variableRoleFilter, setVariableRoleFilter] = useState('all');
  const [variableStatusFilter, setVariableStatusFilter] = useState<'all' | VariableRow['status']>('all');
  const [variableSearch, setVariableSearch] = useState('');
  const [variableDrawerMode, setVariableDrawerMode] = useState<'add' | 'edit' | null>(null);
  const [variableDraftRole, setVariableDraftRole] = useState('Designer');
  const [variableDraftName, setVariableDraftName] = useState('Deck-Custom-Review');
  const [variableDraftDescription, setVariableDraftDescription] = useState('New role-scoped production variable.');
  const [variableDraftStatus, setVariableDraftStatus] = useState<VariableRow['status']>('active');
  const [variableValidationCase, setVariableValidationCase] = useState('current');
  const [referenceVariable, setReferenceVariable] = useState<VariableRow | null>(null);
  const [routeCombination, setRouteCombination] = useState(combinationRows[0].key);
  const [savedRouteDrafts, setSavedRouteDrafts] = useState<Record<string, RouteBindingDraft>>(initialRouteBindingDrafts);
  const [routeDraft, setRouteDraft] = useState<RouteBindingDraft>(initialRouteBindingDrafts[combinationRows[0].key]);
  const [routeDirty, setRouteDirty] = useState(false);
  const [zenmuxProviderConcurrency, setZenmuxProviderConcurrency] = useState(100);
  const [geminiProviderConcurrency, setGeminiProviderConcurrency] = useState(10);
  const [concurrencySavedAt, setConcurrencySavedAt] = useState('default 100 / 10');
  const [promptApiState, setPromptApiState] = useState<MockApiState>('Ready');
  const [configApiState, setConfigApiState] = useState<MockApiState>('Ready');
  const [variablesApiState, setVariablesApiState] = useState<MockApiState>('Ready');
  const isPromptStandalone = standaloneArea === 'prompt-library';
  const isSystemSettingsStandalone = standaloneArea === 'system-settings';

  useEffect(() => {
    queueMicrotask(() => setSelectedArea(initialArea));
  }, [initialArea]);

  const promptFolders = useMemo(() => Array.from(new Set(promptRows.flatMap((row) => row.folders))), []);
  const roleIdMap = useMemo(() => new Map([
    ['Designer', 'designer'],
    ['HTML Agent', 'html_agent'],
    ['Image Cover 3.1', 'image_cover_3_1'],
    ['Image 1.0', 'image_1_0'],
    ['Image 3.0 Seed', 'image_3_0_seed'],
    ['Image 3.0 Non-Seed', 'image_3_0_non_seed'],
    ['Image 3.2 Seed', 'image_3_2_seed'],
    ['Image 3.2 Non-Seed', 'image_3_2_non_seed'],
    ['Image 5.0 Unified', 'image_5_0_unified'],
    ['Image Generator', 'image_generator'],
    ['XML Cleanup', 'xml_cleanup'],
    ['Image 5.3 Route Gate', 'image_5_3_route_gate'],
  ]), []);
  const roleIdFor = (role: string) => roleIdMap.get(role) || role.toLowerCase().replaceAll(' ', '_');
  const scopedAddPromptRole = promptRole === 'all' || promptRole === 'Image 5.3 Route Gate' ? 'Designer' : promptRole;
  const promptDraftFor = (role: string, source?: PromptRow): PromptRow => ({
    key: source ? `copy-${source.key}` : `new-${roleIdFor(role)}`,
    role,
    roleFamily: source?.roleFamily || (role.includes('Image') ? 'Image' : role === 'XML Cleanup' ? 'Shared' : 'HTML'),
    version: '',
    name: source ? `${source.name} (copy)` : '',
    lifecycle: 'active',
    folders: source?.folders || (promptFolder === 'all' ? [] : [promptFolder]),
    description: source?.description || '',
    isDefault: false,
    variables: source?.variables || [],
    variableState: source?.variableState || 'missing',
    createdAt: 'new editable version',
    contentPreview: source?.contentPreview || '',
  });
  const openAddPrompt = () => {
    setSelectedPrompt(promptDraftFor(scopedAddPromptRole));
    setPromptVariablePickerOpen(false);
    setPromptModalMode('add');
  };
  const handlePromptDuplicate = (row: PromptRow) => {
    setSelectedPrompt(promptDraftFor(row.role, row));
    setPromptVariablePickerOpen(false);
    setPromptModalMode('add');
    notify('Copied content into a new editable prompt version; version is blank until saved.');
  };
  const filteredPromptRows = useMemo(() => {
    const needle = promptSearch.trim().toLowerCase();
    return promptRows.filter((row) => {
      if (row.lifecycle === 'draft') return false;
      if (promptLifecycle !== 'all' && row.lifecycle !== promptLifecycle) return false;
      if (promptRole !== 'all' && row.role !== promptRole) return false;
      if (promptFolder !== 'all' && !row.folders.includes(promptFolder)) return false;
      if (!needle) return true;
      return [row.name, row.version, row.description, row.role].join(' ').toLowerCase().includes(needle);
    });
  }, [promptFolder, promptLifecycle, promptRole, promptSearch]);
  const promptTableRows = useMemo(() => {
    if (promptApiState === 'Server empty' || promptApiState === 'Filter empty') return [];
    return filteredPromptRows;
  }, [filteredPromptRows, promptApiState]);
  const inspectedPrompt = promptTableRows.find((row) => row.key === selectedPrompt.key) || promptTableRows[0] || null;

  const filteredVariableRows = useMemo(() => {
    const needle = variableSearch.trim().toLowerCase();
    return variableTableRows.filter((row) => {
      if (variableRoleFilter !== 'all' && row.role !== variableRoleFilter) return false;
      if (variableStatusFilter !== 'all' && row.status !== variableStatusFilter) return false;
      if (!needle) return true;
      return [row.role, roleIdFor(row.role), row.token, row.description].join(' ').toLowerCase().includes(needle);
    });
  }, [roleIdFor, variableRoleFilter, variableSearch, variableStatusFilter, variableTableRows]);
  const variableDisplayRows = useMemo(() => {
    if (variablesApiState === 'Server empty' || variablesApiState === 'Filter empty') return [];
    return filteredVariableRows;
  }, [filteredVariableRows, variablesApiState]);
  const promptModalVariableOptions = useMemo(() => {
    return variableTableRows.filter((row) => row.role === selectedPrompt.role && row.status === 'active');
  }, [selectedPrompt.role, variableTableRows]);
  const insertPromptVariable = (row: VariableRow) => {
    setSelectedPrompt((current) => ({
      ...current,
      contentPreview: current.contentPreview ? `${current.contentPreview}\n${row.token}` : row.token,
      variables: Array.from(new Set([...current.variables, row.token])),
      variableState: 'ready',
    }));
    setPromptVariablePickerOpen(false);
    notify(`Inserted ${row.token} for ${roleIdFor(row.role)}`);
  };
  const openVariableDrawer = (mode: 'add' | 'edit', row?: VariableRow) => {
    setSelectedVariable(row || null);
    setVariableDraftRole(row?.role || 'Designer');
    setVariableDraftName(row?.token.replace(/[{}]/g, '') || 'Deck-Custom-Review');
    setVariableDraftDescription(row?.description || 'New role-scoped production variable.');
    setVariableDraftStatus(row?.status || 'active');
    setVariableValidationCase('current');
    setVariableDrawerMode(mode);
  };
  const variableValidationResult = useMemo(() => {
    const duplicateName = variableDraftName.trim();
    const duplicateExists = variableTableRows.some((row) => {
      if (selectedVariable && row.key === selectedVariable.key) return false;
      return row.role === variableDraftRole && row.token.replace(/[{}]/g, '') === duplicateName;
    });
    if (variableValidationCase === 'empty-name') {
      return {
        type: 'error' as const,
        title: '400 name is required',
        description: 'Production trims the submitted name and rejects an empty system variable name.',
        payload: `agent_type=${roleIdFor(variableDraftRole)}, name="   ", status=${variableDraftStatus}`,
      };
    }
    if (variableValidationCase === 'duplicate' || duplicateExists) {
      return {
        type: 'error' as const,
        title: '409 System variable already exists for this role',
        description: 'Production enforces unique role/name pairs and returns a conflict for duplicate variables.',
        payload: `agent_type=${roleIdFor(variableDraftRole)}, name=Deck-Full-Content, status=${variableDraftStatus}`,
      };
    }
    if (variableValidationCase === 'invalid-agent') {
      return {
        type: 'error' as const,
        title: '400 agent_type must be one of: image_1_0, image_3_0_non_seed, image_3_0_seed, image_3_2_non_seed, image_3_2_seed, image_5_0_unified, image_cover_3_1, image_generator, designer, html_agent, xml_cleanup',
        description: 'Production rejects unknown agent_type values before creating or listing variables.',
        payload: `agent_type=bad, name=${duplicateName || 'Deck-Custom-Review'}, status=${variableDraftStatus}`,
      };
    }
    if (variableValidationCase === 'invalid-status') {
      return {
        type: 'error' as const,
        title: '400 status must be active or disabled',
        description: 'Production validates list and mutation status values against active/disabled only.',
        payload: `agent_type=${roleIdFor(variableDraftRole)}, name=${duplicateName || 'Deck-Custom-Review'}, status=archived`,
      };
    }
    if (variableValidationCase === 'missing-reference') {
      return {
        type: 'warning' as const,
        title: '404 System variable not found',
        description: 'Reference lookup for a missing system variable id returns not found instead of an empty success payload.',
        payload: 'GET /api/system-variables/999999/references',
      };
    }
    return {
      type: 'success' as const,
      title: variableDrawerMode === 'edit' ? '200 System variable updated' : '201 System variable created',
      description: 'Current fake draft passes production-style agent_type, status, nonempty name, and duplicate checks.',
      payload: `agent_type=${roleIdFor(variableDraftRole)}, name=${duplicateName || 'Deck-Custom-Review'}, status=${variableDraftStatus}`,
    };
  }, [roleIdFor, selectedVariable, variableDraftName, variableDraftRole, variableDraftStatus, variableDrawerMode, variableTableRows, variableValidationCase]);

  const roleGroups = useMemo(() => {
    return roleModelProfiles.reduce<Record<string, typeof roleModelProfiles>>((groups, profile) => {
      groups[profile.role] = [...(groups[profile.role] || []), profile];
      return groups;
    }, {});
  }, []);
  const profileLabelByKey = useMemo(() => new Map(roleModelProfiles.map((profile) => [profile.key, `${profile.environment} · ${profile.model}`])), []);
  const routeDesignerOptions = useMemo(() => roleModelProfiles
    .filter((profile) => profile.role === 'Image Designer')
    .map((profile) => ({ label: `${profile.environment} · ${profile.model}`, value: profile.key })), []);
  const routeImageOptions = useMemo(() => roleModelProfiles
    .filter((profile) => profile.role === 'Image Generator')
    .map((profile) => ({ label: `${profile.environment} · ${profile.model}`, value: profile.key })), []);

  const modelColumns: ColumnsType<ModelGateRow> = [
    { title: 'Role', dataIndex: 'role', key: 'role', width: 160 },
    { title: 'Env', dataIndex: 'env', key: 'env', width: 170 },
    { title: 'Target Model', dataIndex: 'targetModel', key: 'targetModel' },
    { title: 'Effort / Thinking', dataIndex: 'effort', key: 'effort', width: 160 },
    { title: 'Temp', dataIndex: 'temperature', key: 'temperature', width: 90 },
    { title: 'Gate', dataIndex: 'gate', key: 'gate', width: 140, render: (gate) => <Tag color="gold">{gate}</Tag> },
  ];

  const promptVariableTag = (state: PromptRow['variableState']) => {
    const colorMap: Record<PromptRow['variableState'], string> = {
      ready: 'success',
      missing: 'error',
      disabled: 'warning',
      'needs confirmation': 'gold',
    };
    return <Tag color={colorMap[state]}>{state}</Tag>;
  };
  const apiStateCopy: Record<MockApiState, { type: 'info' | 'success' | 'warning' | 'error'; title: string; description: string }> = {
    Ready: {
      type: 'success',
      title: 'Ready state',
      description: 'Rows are loaded from fake fixtures and action controls are available.',
    },
    'List loading': {
      type: 'info',
      title: 'List loading',
      description: 'Production tables show Ant Design loading while configs, prompts, folders, or variables are fetched.',
    },
    'Server empty': {
      type: 'warning',
      title: 'Server empty',
      description: 'A successful API response with zero rows keeps the table shell and hides the inspector, matching production.',
    },
    'Filter empty': {
      type: 'warning',
      title: 'Filter empty',
      description: 'Client-side filters can return zero rows while preserving filter controls and empty table copy.',
    },
    'List error': {
      type: 'error',
      title: 'List error',
      description: 'Failed list calls surface an error message and retain the last known review context.',
    },
    'Mutation success': {
      type: 'success',
      title: 'Mutation success',
      description: 'Save, set-default, archive, restore, and move actions show success feedback and refresh local rows.',
    },
    'Mutation error': {
      type: 'error',
      title: 'Mutation error',
      description: 'Create/update/archive/restore failures show action-specific errors without dropping the current table.',
    },
    'Bulk partial warning': {
      type: 'warning',
      title: 'Bulk partial warning',
      description: 'Bulk actions can return mixed results; successful rows remain visible while failed rows are called out.',
    },
  };
  const apiStateOptions: MockApiState[] = ['Ready', 'List loading', 'List error', 'Server empty', 'Filter empty', 'Mutation success', 'Mutation error', 'Bulk partial warning'];

  const configWorkspace = (
    <div className="config-tab-body">
      <section className="mock-panel qa-state-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Config API State Coverage</h3>
            <p className="section-subtitle">Fixture-only controls make production loading, empty, error, success, and partial-bulk states visible without backend calls.</p>
          </div>
          <Segmented value={configApiState} options={apiStateOptions} onChange={(value) => setConfigApiState(value as MockApiState)} />
        </div>
        <Alert showIcon type={apiStateCopy[configApiState].type} title={apiStateCopy[configApiState].title} description={apiStateCopy[configApiState].description} />
      </section>
      <section className="mock-panel qa-state-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Production Data Shape Guard</h3>
            <p className="section-subtitle">Parity rows stay mapped to Config and ModelProfile fields; generation-route template rows remain separate fixtures.</p>
          </div>
          <Tag color="blue">Config / ModelProfile schema</Tag>
        </div>
        <div className="config-card-grid">
          <div className="mini-tile">
            <Text strong>Config</Text>
            <p className="context-meta">id, name, is_default, designer_profile_id, html_agent_profile_id, auto_spill_profile_id, timeout_minutes, max_concurrent_runs</p>
          </div>
          <div className="mini-tile">
            <Text strong>Generation route overrides</Text>
            <p className="context-meta">route_model_bindings.image_designer.profile_id and image_generator.profile_id are edited from Generation Routes.</p>
          </div>
          <div className="mini-tile">
            <Text strong>ModelProfile</Text>
            <p className="context-meta">id, role, name, api_type, model, endpoint, temperature, thinking/effort, status; API key stays masked.</p>
          </div>
        </div>
      </section>
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Combinations</h3>
            <p className="section-subtitle">Runnable configs combine a generation route with concrete model profiles, timeout, and concurrency. This stays the first Config module because it is what users pick before generation.</p>
          </div>
          <Button type="primary" onClick={() => { setSelectedCombination(combinationRows[0]); setCombinationModalMode('add'); }}>
            Add Combination
          </Button>
        </div>
        <Table
          columns={[
            {
              title: 'Combination',
              key: 'combination',
              width: 220,
              render: (_, row) => (
                <div className="history-context">
                  <Text strong>{row.name}</Text>
                  <div className="tag-row">
                    {row.isDefault && <Tag icon={<CheckCircleOutlined />} color="success">Default</Tag>}
                  </div>
                </div>
              ),
            },
            {
              title: 'HTML Route',
              key: 'html',
              width: 300,
              render: (_, row) => (
                <div className="route-binding-cell">
                  <span><Tag color="blue">Designer</Tag>{row.designer}</span>
                  <span><Tag color="green">HTML</Tag>{row.htmlAgent}</span>
                  <span><Tag color="purple">Auto</Tag>{row.autoSpill}</span>
                </div>
              ),
            },
            {
              title: 'Image Route',
              key: 'image',
              width: 300,
              render: (_, row) => (
                <div className="route-binding-cell">
                  <span><Tag color="gold">Designer</Tag>{row.imageDesigner}</span>
                  <span><Tag color="volcano">Image</Tag>{row.imageGenerator}</span>
                </div>
              ),
            },
            {
              title: 'Execution',
              key: 'execution',
              width: 170,
              render: (_, row) => (
                <div className="history-context">
                  <span>{row.timeoutMinutes}m timeout</span>
                  <span className="context-meta">max concurrent runs {row.maxConcurrentRuns}</span>
                </div>
              ),
            },
            {
              title: 'Actions',
              key: 'actions',
              width: 220,
              render: (_, row) => (
                <Space size={6} wrap>
                  {!row.isDefault && <Button size="small" icon={<CheckCircleOutlined />}>Set Default</Button>}
                  <Button size="small" icon={<EditOutlined />} onClick={() => { setSelectedCombination(row); setCombinationModalMode('edit'); }}>Edit</Button>
                  <Popconfirm
                    title="Delete combination?"
                    description={`Fake confirmation for ${row.name}; real delete must preserve existing default fallback rules.`}
                    okText="Delete"
                    cancelText="Cancel"
                  >
                    <Button size="small" danger>Delete</Button>
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
          dataSource={combinationRows}
          rowKey="key"
          pagination={false}
          scroll={{ x: 1220 }}
        />
      </section>
    </div>
  );

  const modelProfiles = (
    <div className="config-tab-body">
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Model Profiles</h3>
            <p className="section-subtitle">Model profiles are grouped by role and environment. Combinations reference these profiles; Generation Routes only describe which roles a workflow needs.</p>
          </div>
          <Button icon={<ApiOutlined />} onClick={() => { setSelectedRoleProfile(roleModelProfiles[0]); setRoleProfileModalMode('add'); }}>Add Model Profile</Button>
        </div>
        <div className="model-profile-overview">
          {Object.entries(roleGroups).map(([role, rows]) => (
            <article className="model-profile-group" key={role}>
              <header>
                <div>
                  <Text strong>{role}</Text>
                  <p className="context-meta">{rows.length} profile{rows.length === 1 ? '' : 's'} · used by Combinations and route stages</p>
                </div>
                <Tag color={rows.some((row) => row.status === 'needs request') ? 'gold' : 'success'}>
                  {rows.some((row) => row.status === 'needs request') ? 'gate needed' : 'active'}
                </Tag>
              </header>
              <div className="profile-chip-grid">
                {rows.map((row) => (
                  <button
                    type="button"
                    className="profile-chip"
                    key={row.key}
                    onClick={() => { setSelectedRoleProfile(row); setRoleProfileModalMode('edit'); }}
                  >
                    <span>
                      <Text strong>{row.environment}</Text>
                      <Tag color={row.status === 'active' ? 'success' : row.status === 'blocked' ? 'error' : 'gold'}>{row.status}</Tag>
                    </span>
                    <Text>{row.model}</Text>
                    <small>{row.apiType} · {row.effort || 'default'} · temp {row.temperature}</small>
                  </button>
                ))}
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );

  const promptLibrary = (
    <div className="config-tab-body">
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>{isPromptStandalone ? 'Prompt Library Records' : 'Prompt Management'}</h3>
            <p className="section-subtitle">{isPromptStandalone ? 'Top-level production Prompt Management route with filters, row actions, folder/bulk states, inspector, variable analysis, and Prompt Assistant review.' : 'Embedded production Prompt Library surface with filters, row actions, folder/bulk states, inspector, variable analysis, and Prompt Assistant review.'}</p>
          </div>
          {!isPromptStandalone && (
          <Space wrap>
            <Button icon={<FolderAddOutlined />} onClick={() => setPromptFolderModalOpen(true)}>New Folder</Button>
            <Button type="primary" onClick={openAddPrompt}>Add Prompt</Button>
          </Space>
          )}
        </div>
        <div className="filter-bar">
          <label className="filter-field wide"><span>Search</span><Input prefix={<SearchOutlined />} allowClear value={promptSearch} onChange={(event) => setPromptSearch(event.target.value)} placeholder="name, version, description" /></label>
          <label className="filter-field"><span>Lifecycle</span><Select value={promptLifecycle} onChange={setPromptLifecycle} options={[{ label: 'All', value: 'all' }, { label: 'Active', value: 'active' }, { label: 'Archived', value: 'archived' }]} /></label>
          <label className="filter-field"><span>Role</span><Select value={promptRole} onChange={setPromptRole} options={[{ label: 'All roles', value: 'all' }, ...promptRoles.map((role) => ({ label: role, value: role }))]} /></label>
          <label className="filter-field"><span>Folder</span><Select value={promptFolder} onChange={setPromptFolder} options={[{ label: 'All folders', value: 'all' }, ...promptFolders.map((folder) => ({ label: folder, value: folder }))]} /></label>
          <Button onClick={() => {
            setPromptSearch('');
            setPromptLifecycle('active');
            setPromptRole('all');
            setPromptFolder('all');
          }}>Clear</Button>
        </div>
        {selectedPromptKeys.length > 0 && promptTableRows.length > 0 && (
          <div className="bulk-action-bar">
            <Text strong>{selectedPromptKeys.length} selected</Text>
            <Button size="small" onClick={() => setPromptBulkMoveOpen(true)}>Move to Folder</Button>
            <Button size="small" icon={<FolderAddOutlined />}>Archive / Restore</Button>
            <Button size="small" onClick={() => setSelectedPromptKeys([])}>Clear</Button>
          </div>
        )}
        <section className="mock-panel compact-panel qa-state-panel">
          <div className="mock-panel-head">
            <div>
              <h3>Prompt API State Coverage</h3>
              <p className="section-subtitle">Production Prompt Management is API-backed; these fake states keep loading, empty, error, success, and partial bulk outcomes reviewable.</p>
            </div>
            <Segmented value={promptApiState} options={apiStateOptions} onChange={(value) => setPromptApiState(value as MockApiState)} />
          </div>
          <Alert showIcon type={apiStateCopy[promptApiState].type} title={apiStateCopy[promptApiState].title} description={apiStateCopy[promptApiState].description} />
          <div className="tag-row panel-inline-alert">
            <Tag color="blue">pageSize 20</Tag>
            <Tag>Version sorter</Tag>
            <Tag>Name sorter</Tag>
            <Tag>emptyText preserved</Tag>
          </div>
        </section>
        <Table<PromptRow>
          rowSelection={{ selectedRowKeys: promptTableRows.length ? selectedPromptKeys : [], onChange: setSelectedPromptKeys }}
          columns={[
            {
              title: 'Role',
              dataIndex: 'role',
              key: 'role',
              width: 230,
              render: (role) => (
                <Space orientation="vertical" size={0}>
                  <Tag color={role === 'Image 5.3 Route Gate' ? 'blue' : 'geekblue'}>{role}</Tag>
                  <Text code>{roleIdFor(role)}</Text>
                </Space>
              ),
            },
            { title: 'Version', dataIndex: 'version', key: 'version', width: 190, sorter: (a, b) => a.version.localeCompare(b.version) },
            {
              title: 'Name',
              key: 'name',
              width: 260,
              sorter: (a, b) => a.name.localeCompare(b.name),
              render: (_, row) => (
                <button type="button" className="link-button" onClick={() => setSelectedPrompt(row)}>
                  <Text strong>{row.name}</Text>
                  {row.isDefault && <Tag color="success">Default</Tag>}
                </button>
              ),
            },
            { title: 'Lifecycle', dataIndex: 'lifecycle', key: 'lifecycle', width: 120, render: (value) => <Tag color={value === 'archived' ? 'default' : value === 'draft' ? 'gold' : 'success'}>{value}</Tag> },
            { title: 'Variables', dataIndex: 'variableState', key: 'variableState', width: 170, render: promptVariableTag },
            { title: 'Folders', dataIndex: 'folders', key: 'folders', width: 190, render: (folders: string[]) => <div className="tag-row">{folders.map((folder) => <Tag key={folder}>{folder}</Tag>)}</div> },
            { title: 'Description', dataIndex: 'description', key: 'description' },
            {
              title: 'Actions',
              key: 'actions',
              width: 260,
              render: (_, row) => (
                <Space size={6} wrap>
                  {!row.isDefault && row.lifecycle === 'active' && <Button size="small">Set Default</Button>}
                  <Button size="small" icon={<EditOutlined />} onClick={() => { setSelectedPrompt(row); setPromptVariablePickerOpen(false); setPromptModalMode('edit'); }}>Edit</Button>
                  <Button size="small" icon={<CopyOutlined />} onClick={() => handlePromptDuplicate(row)}>Duplicate</Button>
                  {row.lifecycle === 'archived' ? <Button size="small">Restore</Button> : <Button size="small" danger>Archive</Button>}
                </Space>
              ),
            },
          ]}
          dataSource={promptTableRows}
          rowKey="key"
          loading={promptApiState === 'List loading'}
          pagination={{ pageSize: 20, showSizeChanger: false }}
          scroll={{ x: 1420 }}
          locale={{ emptyText: <Empty description={promptApiState === 'Server empty' ? 'No prompts returned by the fake API' : 'No prompts match the fake filters'} /> }}
        />
      </section>
      {inspectedPrompt && (
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Prompt Inspector</h3>
            <p className="section-subtitle">Selecting a row exposes metadata, prompt content, variables, and production edit/analyze actions.</p>
          </div>
          <Space wrap>
            <Button icon={<EditOutlined />} onClick={() => { setPromptVariablePickerOpen(false); setPromptModalMode('edit'); }}>Edit Prompt</Button>
            <Button icon={<SearchOutlined />}>Analyze Variables</Button>
            <Button icon={<WarningOutlined />} onClick={() => setAssistantReviewOpen(true)}>Prompt Assistant Review</Button>
          </Space>
        </div>
        <div className="inspector-grid">
          <Descriptions size="small" bordered column={1}>
            <Descriptions.Item label="Role">{inspectedPrompt.role} {inspectedPrompt.isDefault && <Tag color="success">Default</Tag>}</Descriptions.Item>
            <Descriptions.Item label="Canonical agent_type"><Text code>{roleIdFor(inspectedPrompt.role)}</Text></Descriptions.Item>
            <Descriptions.Item label="Version">{inspectedPrompt.version}</Descriptions.Item>
            <Descriptions.Item label="Lifecycle">{inspectedPrompt.lifecycle}</Descriptions.Item>
            <Descriptions.Item label="Created">{inspectedPrompt.createdAt}</Descriptions.Item>
            <Descriptions.Item label="Variables">{promptVariableTag(inspectedPrompt.variableState)} {inspectedPrompt.variables.map((item) => <Tag key={item}>{item}</Tag>)}</Descriptions.Item>
          </Descriptions>
          <pre>{inspectedPrompt.contentPreview}</pre>
        </div>
      </section>
      )}
    </div>
  );

  const generationRoutes = (
    <div className="config-tab-body">
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Generation Routes</h3>
            <p className="section-subtitle">Generation Routes are workflow templates: which stages run, which prompt roles are required, which model roles are used, and which evidence artifacts are produced.</p>
          </div>
          <Tag color={routeDirty ? 'warning' : 'success'}>{routeDirty ? 'unsaved changes' : 'saved state'}</Tag>
        </div>
        <div className="route-map-controls">
          <label className="filter-field wide"><span>Combination preview</span><Select value={routeCombination} onChange={(value) => { setRouteCombination(value); setRouteDraft(savedRouteDrafts[value] || {}); setRouteDirty(false); }} options={combinationRows.map((row) => ({ label: `${row.name}${row.isDefault ? ' · default' : ''}`, value: row.key }))} /></label>
          <label className="filter-field"><span>Designer profile override</span><Select allowClear placeholder="Use route default" value={routeDraft.image_designer} onChange={(value) => { setRouteDraft((current) => ({ ...current, image_designer: value })); setRouteDirty(true); }} options={routeDesignerOptions} /></label>
          <label className="filter-field"><span>Image profile override</span><Select allowClear placeholder="Use route default" value={routeDraft.image_generator} onChange={(value) => { setRouteDraft((current) => ({ ...current, image_generator: value })); setRouteDirty(true); }} options={routeImageOptions} /></label>
          <Space wrap>
            <Button disabled={!routeDirty} onClick={() => { setRouteDraft(savedRouteDrafts[routeCombination] || {}); setRouteDirty(false); }}>Cancel Changes</Button>
            <Button type="primary" disabled={!routeDirty} onClick={() => { setSavedRouteDrafts((current) => ({ ...current, [routeCombination]: routeDraft })); setRouteDirty(false); notify('Generation route overrides saved in fake state'); }}>Save Overrides</Button>
          </Space>
        </div>
        <div className="route-map-binding-summary">
          <span><Tag color="gold">Designer</Tag>{routeDraft.image_designer ? profileLabelByKey.get(routeDraft.image_designer) : 'Default fallback'}</span>
          <span><Tag color="volcano">Image</Tag>{routeDraft.image_generator ? profileLabelByKey.get(routeDraft.image_generator) : 'Default fallback'}</span>
          <span><Tag>{combinationRows.find((row) => row.key === routeCombination)?.name}</Tag></span>
        </div>
        <Table
          columns={[
            { title: 'Route Template', dataIndex: 'route', key: 'route', width: 180 },
            { title: 'Prompt Roles', dataIndex: 'prompts', key: 'prompts', width: 240 },
            { title: 'Model Roles', dataIndex: 'models', key: 'models', width: 220 },
            { title: 'Evidence Produced', dataIndex: 'evidence', key: 'evidence' },
          ]}
          dataSource={routeEvidenceRows}
          rowKey="id"
          pagination={false}
          scroll={{ x: 920 }}
        />
      </section>
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Workflow Template Steps</h3>
            <p className="section-subtitle">Global route coverage lives here, not inside a single Image 5.0 run evidence tray.</p>
          </div>
        </div>
        <div className="route-flow-grid">
          {routeFlows.map((route) => (
            <article className={`route-card ${route.state}`} key={route.key}>
              <header><Text strong>{route.title}</Text><Tag color={route.state === 'roadmap' ? 'blue' : 'gold'}>{route.badge}</Tag></header>
              <div className="flow-steps">{route.steps.map((step) => <div className="flow-step" key={step}>{step}</div>)}</div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );

  const disableVariable = (row: VariableRow) => {
    setVariableTableRows((current) => current.map((item) => (item.key === row.key ? { ...item, status: 'disabled' } : item)));
    notify(`Variable ${row.token} disabled for new prompts`);
  };

  const saveConcurrency = () => {
    setConcurrencySavedAt(`saved fake state ${zenmuxProviderConcurrency} / ${geminiProviderConcurrency}`);
    notify('Concurrency settings saved in fake state');
  };

  const systemSettings = (
    <div className="config-tab-body">
      <Alert
        showIcon
        type="info"
        title={isSystemSettingsStandalone ? 'Production System Settings route preserved.' : 'Variables & Runtime'}
        description="Global Concurrency keeps provider request limits; System Variables keep production table, drawer, reference, and disabled-variable safety semantics."
      />
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Global Concurrency</h3>
            <p className="section-subtitle">Concurrency limits apply at the real LLM request boundary.</p>
          </div>
          <Button type="primary" onClick={saveConcurrency}>Save Concurrency</Button>
        </div>
        <div className="concurrency-grid">
          <label className="filter-field"><span>ZenMux provider concurrency</span><InputNumber min={1} max={200} value={zenmuxProviderConcurrency} onChange={(value) => setZenmuxProviderConcurrency(value || 1)} /></label>
          <label className="filter-field"><span>Gemini provider concurrency</span><InputNumber min={1} max={50} value={geminiProviderConcurrency} onChange={(value) => setGeminiProviderConcurrency(value || 1)} /></label>
          <Descriptions size="small" bordered column={1}>
            <Descriptions.Item label="ZenMux provider limit">{zenmuxProviderConcurrency}</Descriptions.Item>
            <Descriptions.Item label="Gemini provider limit">{geminiProviderConcurrency}</Descriptions.Item>
            <Descriptions.Item label="Saved state">{concurrencySavedAt}</Descriptions.Item>
          </Descriptions>
        </div>
      </section>
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>System Variables</h3>
            <p className="section-subtitle">Exact production tokens, active/disabled status, references, and edit drawer states are represented with fake data.</p>
          </div>
          <Button type="primary" onClick={() => openVariableDrawer('add')}>Add Variable</Button>
        </div>
        <section className="mock-panel compact-panel qa-state-panel">
          <div className="mock-panel-head">
            <div>
              <h3>System Variables API State Coverage</h3>
              <p className="section-subtitle">Production System Settings loads settings and variables together, then shows table loading, message errors, empty references, and save feedback.</p>
            </div>
            <Segmented value={variablesApiState} options={apiStateOptions} onChange={(value) => setVariablesApiState(value as MockApiState)} />
          </div>
          <Alert showIcon type={apiStateCopy[variablesApiState].type} title={apiStateCopy[variablesApiState].title} description={apiStateCopy[variablesApiState].description} />
          <div className="tag-row panel-inline-alert">
            <Tag color="blue">Card loading state</Tag>
            <Tag>No system variables returned by the fake API</Tag>
            <Tag>No prompt references found</Tag>
            <Tag>System variable saved</Tag>
          </div>
          <div className="config-card-grid panel-inline-alert">
            <div className="mini-tile">
              <Text strong>Fetch failure</Text>
              <p className="context-meta">Failed to load system settings: provider or API error; existing review context stays on screen.</p>
            </div>
            <div className="mini-tile">
              <Text strong>Create / update</Text>
              <p className="context-meta">System variable created, System variable updated, or mutation error message shown near the action.</p>
            </div>
            <div className="mini-tile">
              <Text strong>Disable</Text>
              <p className="context-meta">Variable disabled for new prompts; Disable failed keeps the row unchanged.</p>
            </div>
            <div className="mini-tile">
              <Text strong>References</Text>
              <p className="context-meta">Reference drawer supports loading, populated prompt rows, No prompt references found, and Failed to load references.</p>
            </div>
          </div>
        </section>
        <div className="bulk-action-bar">
          <Tag color="success">Active {variableDisplayRows.filter((row) => row.status === 'active').length}</Tag>
          <Tag>Disabled {variableDisplayRows.filter((row) => row.status === 'disabled').length}</Tag>
          <Tag color="warning">Disabled variables remain valid for old prompt versions and block new saves until replaced.</Tag>
        </div>
        <div className="filter-bar" aria-label="Variable filters">
          <label className="filter-field wide"><span>Search</span><Input prefix={<SearchOutlined />} allowClear value={variableSearch} onChange={(event) => setVariableSearch(event.target.value)} placeholder="token, role, canonical agent_type" /></label>
          <label className="filter-field"><span>Role</span><Select value={variableRoleFilter} onChange={setVariableRoleFilter} options={[{ label: 'All roles', value: 'all' }, ...promptRoles.filter((role) => role !== 'Image 5.3 Route Gate').map((role) => ({ label: `${role} · ${roleIdFor(role)}`, value: role }))]} /></label>
          <label className="filter-field"><span>Status</span><Select value={variableStatusFilter} onChange={setVariableStatusFilter} options={[{ label: 'All statuses', value: 'all' }, { label: 'Active', value: 'active' }, { label: 'Disabled', value: 'disabled' }]} /></label>
          <Button onClick={() => {
            setVariableSearch('');
            setVariableRoleFilter('all');
            setVariableStatusFilter('all');
          }}>Clear</Button>
        </div>
        <Table<VariableRow>
          columns={[
            {
              title: 'Role',
              dataIndex: 'role',
              key: 'role',
              width: 230,
              render: (role) => (
                <Space orientation="vertical" size={0}>
                  <Tag color="geekblue">{role}</Tag>
                  <Text code>{roleIdFor(role)}</Text>
                </Space>
              ),
            },
            { title: 'Variable', key: 'token', render: (_, row) => <span><Text code>{row.token}</Text><br /><span className="context-meta">{row.description}</span></span> },
            { title: 'Status', dataIndex: 'status', key: 'status', width: 120, render: (status) => <Tag color={status === 'active' ? 'success' : 'default'}>{status}</Tag> },
            {
              title: 'Actions',
              key: 'actions',
              width: 260,
              render: (_, row) => (
                <Space wrap size={6}>
                  <Button size="small" onClick={() => setReferenceVariable(row)}>View references</Button>
                  <Button size="small" icon={<EditOutlined />} onClick={() => openVariableDrawer('edit', row)}>Edit</Button>
                  <Button size="small" disabled={row.status === 'disabled'} onClick={() => disableVariable(row)}>Disable for new prompts</Button>
                </Space>
              ),
            },
          ]}
          dataSource={variableDisplayRows}
          rowKey="key"
          loading={variablesApiState === 'List loading'}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          scroll={{ x: 860 }}
          locale={{ emptyText: <Empty description={variablesApiState === 'Server empty' ? 'No system variables returned by the fake API' : 'No system variables match the fake filters'} /> }}
        />
        <Alert
          showIcon
          type="warning"
          className="panel-inline-alert"
          title="Duplicate role/name validation"
          description="Production rejects empty names, invalid agent_type/status values, and duplicate variables for the same role. The fake drawer keeps those states visible without calling the backend."
        />
      </section>
    </div>
  );

  const roadmapGate = (
    <div className="config-tab-body">
      <section className="mock-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Model Connectivity Gate</h3>
            <p className="section-subtitle">Additive roadmap/pre-plan surface. It does not replace production Config, Prompt Library, or System Settings behavior.</p>
          </div>
          <Badge status="warning" text="pre-plan blocker" />
        </div>
        <div className="runfail-metrics model-gate-summary">
          <div className="mini-tile"><strong>12</strong><span className="context-meta">target profiles</span></div>
          <div className="mini-tile"><strong>0</strong><span className="context-meta">verified this round</span></div>
          <div className="mini-tile"><strong>Plan</strong><span className="context-meta">blocked until success</span></div>
        </div>
        <div className="model-gate-table">
          <Table columns={modelColumns} dataSource={modelGateRows} rowKey="key" pagination={false} size="small" scroll={{ x: 960 }} />
        </div>
      </section>
    </div>
  );
  const standaloneContent = isPromptStandalone ? promptLibrary : isSystemSettingsStandalone ? systemSettings : null;
  const headerTitle = isPromptStandalone ? 'Prompt Management' : isSystemSettingsStandalone ? 'System Settings' : 'Config';
  const headerSubtitle = isPromptStandalone
    ? 'Production top-level /prompts route is preserved with fake data, while Config can still embed Prompt Library for the migration review.'
    : isSystemSettingsStandalone
      ? 'Production top-level /system-settings route is preserved for Global Concurrency and System Variables, while Config can still embed the same surface.'
      : 'Config is organized by how users build a runnable generation setup: Combinations pick model profiles and route templates; prompts, variables, runtime settings, and roadmap gates stay in their own modules.';
  const headerActions = isPromptStandalone ? (
    <Space wrap>
      <Button onClick={() => setPromptFolderModalOpen(true)} icon={<FolderAddOutlined />}>New Folder</Button>
      <Button type="primary" onClick={openAddPrompt}>Add Prompt</Button>
    </Space>
  ) : isSystemSettingsStandalone ? (
    <Space wrap>
      <Button onClick={() => openVariableDrawer('add')}>Add Variable</Button>
      <Button type="primary" onClick={saveConcurrency}>Save Concurrency</Button>
    </Space>
  ) : (
    <Space wrap>
      <Button icon={<ApiOutlined />} onClick={() => openAction({ kind: 'Model Gate', scope: 'Config', target: 'Roadmap model profiles only' })}>Run Model Gate</Button>
      <Button onClick={() => { setSelectedRoleProfile(roleModelProfiles[0]); setRoleProfileModalMode('add'); }}>Add Model Profile</Button>
      <Button type="primary" onClick={() => { setSelectedCombination(combinationRows[0]); setCombinationModalMode('add'); }}>Add Combination</Button>
    </Space>
  );

  return (
    <>
      <PageHeader
        title={headerTitle}
        subtitle={headerSubtitle}
        actions={headerActions}
      />
      {standaloneContent ? (
        <>
          <Alert
            showIcon
            type="info"
            className="standalone-route-alert"
            title={isPromptStandalone ? 'Top-level Prompt Management route preserved.' : 'Top-level System Settings route preserved.'}
            description={isPromptStandalone ? 'This route intentionally omits Config tabs so implementation agents can map /prompts back to production PromptsPage responsibilities.' : 'This route intentionally omits Config tabs so implementation agents can map /system-settings back to production SystemSettingsPage responsibilities.'}
          />
          {standaloneContent}
        </>
      ) : (
      <Tabs
        className="config-tabs"
        activeKey={selectedArea}
        onChange={setSelectedArea}
        items={[
          { key: 'workspace', label: 'Combinations', children: configWorkspace },
          { key: 'model-profiles', label: 'Model Profiles', children: modelProfiles },
          { key: 'prompt-library', label: 'Prompt Library', children: promptLibrary },
          { key: 'system-settings', label: 'Variables & Runtime', children: systemSettings },
          { key: 'route-map', label: 'Generation Routes', children: generationRoutes },
          { key: 'roadmap-gate', label: 'Advanced / Roadmap', children: roadmapGate },
        ]}
      />
      )}
      <Modal
        title={combinationModalMode === 'edit' ? 'Edit Combination' : 'Add Combination'}
        open={Boolean(combinationModalMode)}
        onCancel={() => setCombinationModalMode(null)}
        okText={combinationModalMode === 'edit' ? 'Save Combination' : 'Create Combination'}
        width={880}
      >
        <Alert
          showIcon
          type="info"
          title="Production-shaped Config form."
          description="This fake modal keeps required Designer, HTML Agent, Auto-Spill, timeout, max concurrent runs, and optional Image Designer/Image fields separate from roadmap gates."
        />
        <div className="form-grid modal-form-grid">
          <label className="filter-field"><span>Name</span><Input value={selectedCombination.name} readOnly /></label>
          <label className="filter-field"><span>Default</span><Select value={selectedCombination.isDefault ? 'default' : 'not-default'} options={[{ label: 'Default', value: 'default' }, { label: 'Not default', value: 'not-default' }]} /></label>
          <label className="filter-field"><span>Designer</span><Select value={selectedCombination.designer} options={roleModelProfiles.filter((profile) => profile.role === 'Designer').map((profile) => ({ label: `${profile.environment} · ${profile.model}`, value: `${profile.role} / ${profile.model}` }))} /></label>
          <label className="filter-field"><span>HTML Agent</span><Select value={selectedCombination.htmlAgent} options={[{ label: 'Not used', value: 'Not used' }, ...roleModelProfiles.filter((profile) => profile.role === 'HTML Agent').map((profile) => ({ label: `${profile.environment} · ${profile.model}`, value: `${profile.role} / ${profile.model}` }))]} /></label>
          <label className="filter-field"><span>Auto-Spill</span><Select value={selectedCombination.autoSpill} options={roleModelProfiles.filter((profile) => profile.role === 'Auto-Spill').map((profile) => ({ label: `${profile.environment} · ${profile.model}`, value: `${profile.role} / ${profile.model}` }))} /></label>
          <label className="filter-field"><span>Image Designer optional</span><Select allowClear value={selectedCombination.imageDesigner} options={[{ label: 'Not used', value: 'Not used' }, ...roleModelProfiles.filter((profile) => profile.role === 'Image Designer').map((profile) => ({ label: `${profile.environment} · ${profile.model}`, value: `${profile.role} / ${profile.model}` }))]} /></label>
          <label className="filter-field"><span>Image Generator optional</span><Select allowClear value={selectedCombination.imageGenerator} options={[{ label: 'Captured PNG', value: 'Captured PNG' }, { label: 'Not used', value: 'Not used' }, ...roleModelProfiles.filter((profile) => profile.role === 'Image Generator').map((profile) => ({ label: `${profile.environment} · ${profile.model}`, value: `${profile.role} / ${profile.model}` }))]} /></label>
          <label className="filter-field"><span>Timeout minutes</span><InputNumber min={1} max={120} value={selectedCombination.timeoutMinutes} /></label>
          <label className="filter-field"><span>Max concurrent runs</span><InputNumber min={1} max={10} value={selectedCombination.maxConcurrentRuns} /></label>
        </div>
        <Alert
          showIcon
          type="warning"
          title="No production description/status fields"
          description="Route labels, roadmap status, and review notes stay outside the production Config payload so later backend work copies the real schema."
        />
      </Modal>
      <Modal
        title={roleProfileModalMode === 'edit' ? 'Edit Model Profile' : 'Add Model Profile'}
        open={Boolean(roleProfileModalMode)}
        onCancel={() => setRoleProfileModalMode(null)}
        okText={roleProfileModalMode === 'edit' ? 'Save Model Profile' : 'Create Model Profile'}
        width={820}
      >
        <Alert
          showIcon
          type="warning"
          title="API keys stay masked in the fake frontend."
          description="The modal mirrors production fields without sending a backend request or exposing credentials."
        />
        <div className="form-grid modal-form-grid">
          <label className="filter-field"><span>Role</span><Select value={selectedRoleProfile.role} options={Array.from(new Set(roleModelProfiles.map((profile) => profile.role))).map((role) => ({ label: role, value: role }))} /></label>
          <label className="filter-field"><span>Profile Name</span><Input value={selectedRoleProfile.environment} readOnly /></label>
          <label className="filter-field"><span>API Type</span><Select value={selectedRoleProfile.apiType} options={[{ label: 'OpenAI', value: 'openai' }, { label: 'Gemini', value: 'gemini' }]} /></label>
          <label className="filter-field"><span>Model</span><Input value={selectedRoleProfile.model} readOnly /></label>
          <label className="filter-field"><span>Endpoint</span><Input value={selectedRoleProfile.endpoint} readOnly /></label>
          <label className="filter-field"><span>API Key</span><Input.Password value="[REDACTED]" readOnly /></label>
          <label className="filter-field"><span>Temperature</span><Input value={selectedRoleProfile.temperature} readOnly /></label>
          <label className="filter-field"><span>Effort / Thinking</span><Input value={selectedRoleProfile.effort || 'default'} readOnly /></label>
        </div>
      </Modal>
      <Modal
        title={promptModalMode === 'edit' ? 'Edit Prompt' : 'Add Prompt'}
        open={Boolean(promptModalMode)}
        onCancel={() => setPromptModalMode(null)}
        okText={selectedPrompt.variableState === 'ready' ? 'Save Prompt' : 'Save blocked until variables are resolved'}
        okButtonProps={{ disabled: selectedPrompt.variableState !== 'ready' }}
        width={820}
      >
        {promptModalMode === 'add' && (
          <Alert
            showIcon
            type="info"
            title="Add state uses production defaults."
            description="A real add flow resets to a new prompt form with agent_type defaulting to the current scoped role filter, active status, optional folders, and blank editable prompt content."
          />
        )}
        <div className="form-grid">
          <label className="filter-field"><span>Agent Type</span><Select disabled={promptModalMode === 'edit'} value={promptModalMode === 'add' ? selectedPrompt.role : selectedPrompt.role} options={promptRoles.filter((role) => role !== 'Image 5.3 Route Gate').map((role) => ({ label: `${role} · ${roleIdFor(role)}`, value: role }))} /></label>
          <label className="filter-field"><span>Version</span><Input disabled={promptModalMode === 'edit'} value={selectedPrompt.version} placeholder="Blank until saved" /></label>
          <label className="filter-field"><span>Status</span><Select value={selectedPrompt.lifecycle === 'archived' ? 'deprecated' : 'active'} options={[{ label: 'Active', value: 'active' }, { label: 'Deprecated', value: 'deprecated' }]} /></label>
          <label className="filter-field"><span>Name</span><Input value={selectedPrompt.name} placeholder="Prompt name" /></label>
        </div>
        <label className="filter-field"><span>Folders</span><Select mode="multiple" value={selectedPrompt.folders} options={promptFolders.map((folder) => ({ label: folder, value: folder }))} /></label>
        <label className="filter-field"><span>Description</span><Input value={selectedPrompt.description} placeholder="Short prompt description" /></label>
        <label className="filter-field"><span>Prompt Content</span><Input.TextArea rows={5} value={selectedPrompt.contentPreview} onChange={(event) => setSelectedPrompt((current) => ({ ...current, contentPreview: event.target.value }))} placeholder="Typing {{ opens role-scoped variables in production." /></label>
        <div className="button-row">
          <Button onClick={() => setPromptVariablePickerOpen((value) => !value)}>Insert variable</Button>
          <Button>Analyze Variables</Button>
          <Button onClick={() => setAssistantReviewOpen(true)}>Auto insert variables</Button>
        </div>
        {promptVariablePickerOpen && (
          <div className="prompt-variable-picker" role="listbox" aria-label="Prompt variables">
            <div className="prompt-variable-picker-head">
              <div>
                <Text strong>Role-scoped variable picker</Text>
                <p className="context-meta">Typing {'{{'} opens this picker near the caret in production; this fake panel keeps the same role-scoped options visible for review.</p>
              </div>
              <Tag color="blue">{selectedPrompt.role} · {roleIdFor(selectedPrompt.role)}</Tag>
            </div>
            {promptModalVariableOptions.length ? (
              <div className="prompt-variable-option-grid">
                {promptModalVariableOptions.map((row) => (
                  <button
                    type="button"
                    className="prompt-variable-option"
                    key={row.key}
                    onClick={() => insertPromptVariable(row)}
                  >
                    <Text code>{row.token}</Text>
                    <span>{row.description}</span>
                    <small>{row.sampleReference}</small>
                  </button>
                ))}
              </div>
            ) : (
              <Empty description="No active variables for this role" />
            )}
          </div>
        )}
        <div className="analysis-grid">
          <div className="mini-tile"><strong>Ready</strong><span className="context-meta">All active variables present.</span></div>
          <div className="mini-tile"><strong>Missing</strong><span className="context-meta">Save remains blocked.</span></div>
          <div className="mini-tile"><strong>Disabled</strong><span className="context-meta">Old versions valid, new save blocked.</span></div>
          <div className="mini-tile">
            <Checkbox>Confirm mapped variable</Checkbox>
            <span className="context-meta">Needs-confirmation mappings require reviewer approval.</span>
          </div>
        </div>
        <Alert
          showIcon
          type={selectedPrompt.variableState === 'ready' ? 'success' : 'warning'}
          title={`Variable readiness: ${selectedPrompt.variableState}`}
          description="Fake modal preserves the production save gate: missing, disabled, or needs-confirmation mappings block saving until resolved."
        />
      </Modal>
      <Modal
        title="Prompt Assistant Change Review"
        open={assistantReviewOpen}
        onCancel={() => setAssistantReviewOpen(false)}
        okText="Continue reviewing"
        width={760}
      >
        <Alert showIcon type="warning" title="Medium risk · 84% similarity" description="One original line was removed; reviewer confirmation is required before the assistant output can be saved." />
        <div className="tag-row">
          <Tag color="blue">LLM insert</Tag>
          <Tag color="success">Inserted {'{{Deck-Full-Content}}'}</Tag>
          <Tag color="success">Inserted {'{{Deck-Required-color}}'}</Tag>
          <Tag color="warning">Removed-line warning</Tag>
        </div>
        <div className="diff-grid">
          <pre>{`Before\nUse deck_name and required_color placeholders.`}</pre>
          <pre>{`After\nUse {{Deck-Full-Content}} and {{Deck-Required-color}}.\nInserted variables: {{Deck-Full-Content}}, {{Deck-Required-color}}`}</pre>
        </div>
      </Modal>
      <Modal
        title="New Prompt Folder"
        open={promptFolderModalOpen}
        onCancel={() => setPromptFolderModalOpen(false)}
        okText="Create Folder"
        width={560}
      >
        <label className="filter-field"><span>Folder Name</span><Input placeholder="Prompt folder name" /></label>
        <label className="filter-field"><span>Parent folder</span><Select allowClear options={promptFolders.map((folder) => ({ label: folder, value: folder }))} /></label>
      </Modal>
      <Modal
        title="Move Selected Prompts"
        open={promptBulkMoveOpen}
        onCancel={() => setPromptBulkMoveOpen(false)}
        okText="Move Prompts"
        width={560}
      >
        <Alert showIcon type="info" title={`${selectedPromptKeys.length} selected`} description="Production bulk move assigns selected prompts to one or more prompt folders." />
        <label className="filter-field"><span>Folders</span><Select mode="multiple" options={promptFolders.map((folder) => ({ label: folder, value: folder }))} /></label>
      </Modal>
      <Drawer
        title={variableDrawerMode === 'edit' ? 'Edit System Variable' : 'Add System Variable'}
        open={Boolean(variableDrawerMode)}
        onClose={() => setVariableDrawerMode(null)}
        size={520}
        extra={<Space><Button onClick={() => setVariableDrawerMode(null)}>Cancel</Button><Button type="primary" onClick={() => notify(variableDrawerMode === 'edit' ? 'Variable saved in fake state' : 'Variable created in fake state')}>Save</Button></Space>}
      >
        <Alert showIcon type="info" title="Production System Settings selector" description="The production add/edit drawer exposes Designer Agent and HTML Agent. Broader Image role variables remain visible in the Config parity table as route-scoped extension data." />
        <label className="filter-field"><span>Role</span><Select disabled={variableDrawerMode === 'edit'} value={variableDraftRole} onChange={setVariableDraftRole} options={[{ label: 'Designer Agent · designer', value: 'Designer' }, { label: 'HTML Agent · html_agent', value: 'HTML Agent' }]} /></label>
        <label className="filter-field"><span>Variable Name</span><Input value={variableDraftName} onChange={(event) => setVariableDraftName(event.target.value)} /></label>
        <label className="filter-field"><span>Description</span><Input.TextArea rows={3} value={variableDraftDescription} onChange={(event) => setVariableDraftDescription(event.target.value)} /></label>
        <label className="filter-field"><span>Status</span><Select value={variableDraftStatus} onChange={setVariableDraftStatus} options={[{ label: 'Active', value: 'active' }, { label: 'Disabled', value: 'disabled' }]} /></label>
        <section className="mock-panel compact-panel">
          <div className="mock-panel-head">
            <div>
              <h3>Production API Validation Probe</h3>
              <p className="section-subtitle">Interactive fake cases mirror the backend validator for agent_type, status, trimmed name, duplicate role/name, and missing reference lookup.</p>
            </div>
          </div>
          <Segmented
            value={variableValidationCase}
            onChange={(value) => setVariableValidationCase(String(value))}
            options={[
              { label: 'Current draft', value: 'current' },
              { label: 'Empty name', value: 'empty-name' },
              { label: 'Duplicate', value: 'duplicate' },
              { label: 'Invalid agent_type', value: 'invalid-agent' },
              { label: 'Invalid status', value: 'invalid-status' },
              { label: 'Missing reference 404', value: 'missing-reference' },
            ]}
          />
          <Alert className="panel-inline-alert" type={variableValidationResult.type} showIcon title={variableValidationResult.title} description={variableValidationResult.description} />
          <div className="mini-tile">
            <Text strong>Payload under test</Text>
            <p className="context-meta">{variableValidationResult.payload}</p>
          </div>
        </section>
        <Alert type="warning" showIcon title="Disabled variables remain valid for old prompt versions." description="They are hidden from new autocomplete and block saving new prompt versions until replaced." />
      </Drawer>
      <Drawer
        title={referenceVariable ? `References for ${referenceVariable.token}` : 'References'}
        open={Boolean(referenceVariable)}
        onClose={() => setReferenceVariable(null)}
        size={560}
      >
        {referenceVariable ? (
          <div className="side-stack">
            <Alert showIcon type="info" title="Reference loading state" description="Loading prompt references keeps the drawer shell visible while the production API resolves the prompt usage list." />
            {referenceVariable.references > 0 ? (
              <>
                {[
                  {
                    prompt_id: `${roleIdFor(referenceVariable.role)}-prompt-current`,
                    prompt_name: referenceVariable.sampleReference,
                    agent_type: roleIdFor(referenceVariable.role),
                    version: 'v-current',
                    snippet: `...prompt content includes ${referenceVariable.token} near the generation instruction...`,
                  },
                  {
                    prompt_id: `${roleIdFor(referenceVariable.role)}-prompt-${Math.max(1, referenceVariable.references - 1)}`,
                    prompt_name: `Prompt reference #${Math.max(1, referenceVariable.references - 1)}`,
                    agent_type: roleIdFor(referenceVariable.role),
                    version: 'v-previous',
                    snippet: `...legacy prompt row still references ${referenceVariable.token} and stays valid for old runs...`,
                  },
                ].map((reference) => (
                  <div className="mini-tile" key={reference.prompt_id}>
                    <Text strong>{reference.prompt_name}</Text>
                    <div className="kv compact-kv">
                      <div><b>prompt_id</b><span>{reference.prompt_id}</span></div>
                      <div><b>prompt_name</b><span>{reference.prompt_name}</span></div>
                      <div><b>agent_type</b><span>{reference.agent_type}</span></div>
                      <div><b>version</b><span>{reference.version}</span></div>
                      <div><b>snippet</b><span>{reference.snippet}</span></div>
                    </div>
                  </div>
                ))}
                <Alert showIcon type="warning" title="Reference error state" description="Failed to load references keeps the selected variable open and shows the retryable production error copy." />
              </>
            ) : (
              <Empty description="No prompt references found" />
            )}
            <Alert showIcon title="Reference empty state" description="No prompt references found appears for zero-reference variables; populated variables list structured prompt rows." />
          </div>
        ) : null}
  </Drawer>
    </>
  );
}

type DataTabKey = 'decks' | 'requirements' | 'colors';
type LifecycleState = 'active' | 'archived' | 'recycle_bin';
type GenerateRouteEngine = 'html' | 'image';
type GenerateMode = 'auto' | 'manual';

const lifecycleOptions = [
  { label: 'Active', value: 'active' },
  { label: 'Archived', value: 'archived' },
  { label: 'Recycle Bin', value: 'recycle_bin' },
];

const imageStrategyOptions = [
  { label: '1.0', value: 'Image 1.0', detail: 'conversation' },
  { label: '3.0', value: 'Image 3.0', detail: 'content seed' },
  { label: '3.2', value: 'Image 3.2', detail: 'cover ref seed' },
  { label: '5.0', value: 'Image 5.0', detail: 'unified designer' },
];

const referenceInputMap: Record<string, Array<[string, string, string]>> = {
  HTML: [
    ['User Requirement', 'Required', 'Designer and HTML Agent prompts use the selected requirement.'],
    ['Deck Required Color', 'Required', 'Color XML stays in the HTML design-principle stage.'],
  ],
  'Image 1.0': [
    ['Conversation Session', 'Required', 'Each slide continues from a provider conversation.'],
    ['Per-slide Prompt', 'Stored', 'Prompt, request, response, and session evidence are retained.'],
  ],
  'Image 3.0': [
    ['First Content Page', 'Seed', 'The first content page creates seed XML and image.'],
    ['Later Pages', 'Parallel', 'Later pages depend on the seed image/XML.'],
  ],
  'Image 3.2': [
    ['Cover Reference', 'Required', 'Cover image participates in seed-page design.'],
    ['First Content Page', 'Seed', 'The first content page remains the content seed.'],
  ],
  'Image 5.0': [
    ['Unified Designer', 'Required', 'One prompt role is used for all pages.'],
    ['Style/Reference', 'Optional', 'Reference inputs can enrich settings without seed/non-seed prompts.'],
  ],
};

function lifecycleTag(status: LifecycleState) {
  const color = status === 'active' ? 'green' : status === 'archived' ? 'gold' : 'red';
  return <Tag color={color}>{status === 'recycle_bin' ? 'Recycle Bin' : status}</Tag>;
}

function DataControlStrip({
  scope,
  status,
  folder,
  onStatusChange,
  onFolderChange,
  onNewFolder,
}: {
  scope: 'deck' | 'requirement' | 'color';
  status: LifecycleState;
  folder: string | null;
  onStatusChange: (status: LifecycleState) => void;
  onFolderChange: (folder: string | null) => void;
  onNewFolder: () => void;
}) {
  const folders = dataFolders.filter((item) => item.scope === scope).map((item) => ({ label: item.name, value: item.name }));

  return (
    <div className="data-control-strip">
      <Segmented
        aria-label={`${scope} lifecycle filter`}
        value={status}
        options={lifecycleOptions}
        onChange={(value) => onStatusChange(value as LifecycleState)}
      />
      <Select
        allowClear
        aria-label={`${scope} folder filter`}
        placeholder="Filter by folder"
        value={folder || undefined}
        options={folders}
        onChange={(value) => onFolderChange(value || null)}
      />
      <Button icon={<FolderAddOutlined />} onClick={onNewFolder}>New Folder</Button>
    </div>
  );
}

function filteredByDataState<T extends { lifecycle: LifecycleState; folders: string[] }>(
  rows: T[],
  status: LifecycleState,
  folder: string | null,
) {
  return rows.filter((row) => row.lifecycle === status && (!folder || row.folders.includes(folder)));
}

function DataManagementPage({ notify }: { notify: (text: string) => void }) {
  const [tab, setTab] = useState<DataTabKey>('decks');
  const [dataApiState, setDataApiState] = useState<MockApiState>('Ready');
  const [statusByTab, setStatusByTab] = useState<Record<DataTabKey, LifecycleState>>({
    decks: 'active',
    requirements: 'active',
    colors: 'active',
  });
  const [folderByTab, setFolderByTab] = useState<Record<DataTabKey, string | null>>({
    decks: null,
    requirements: null,
    colors: null,
  });
  const [selectedByTab, setSelectedByTab] = useState<Record<DataTabKey, Key[]>>({
    decks: [],
    requirements: [],
    colors: [],
  });
  const [entityDrawer, setEntityDrawer] = useState<{ title: string; kind: DataTabKey } | null>(null);
  const [autoSplitDeck, setAutoSplitDeck] = useState<DataDeckRow | null>(null);
  const [extractOpen, setExtractOpen] = useState(false);
  const [folderModal, setFolderModal] = useState<'deck' | 'requirement' | 'color' | null>(null);

  const currentStatus = statusByTab[tab];
  const selectedKeys = selectedByTab[tab];
  const dataApiStateOptions: MockApiState[] = ['Ready', 'List loading', 'List error', 'Server empty', 'Filter empty', 'Mutation success', 'Mutation error', 'Bulk partial warning'];
  const dataStateCopy: Record<MockApiState, { type: 'info' | 'success' | 'warning' | 'error'; title: string; description: string }> = {
    Ready: {
      type: 'success',
      title: 'Ready state',
      description: 'Decks, requirements, colors, folders, and lifecycle controls are loaded from fake fixtures.',
    },
    'List loading': {
      type: 'info',
      title: 'List loading',
      description: 'Production tables keep their column layout while decks, requirements, colors, and folders are loading.',
    },
    'List error': {
      type: 'error',
      title: 'List error',
      description: 'Failed to load data summary, decks, requirements, colors, or folders; existing controls remain visible for recovery.',
    },
    'Server empty': {
      type: 'warning',
      title: 'Server empty',
      description: 'The fake API returned no decks, requirements, or color palettes for this workspace.',
    },
    'Filter empty': {
      type: 'warning',
      title: 'Filter empty',
      description: 'Lifecycle or folder filters are valid, but no rows match the selected tab state.',
    },
    'Mutation success': {
      type: 'success',
      title: 'Mutation success',
      description: 'Create, edit, archive, restore, folder move, split, auto-split, and color extraction success messages are represented.',
    },
    'Mutation error': {
      type: 'error',
      title: 'Mutation error',
      description: 'CRUD, folder, bulk, split, auto-split, and image extraction errors keep the table state intact and show action-specific copy.',
    },
    'Bulk partial warning': {
      type: 'warning',
      title: 'Bulk partial warning',
      description: 'Bulk archive, restore, delete, force delete, or move-to-folder can partially fail while successful rows remain visible.',
    },
  };
  const dataRowsBlockedByApiState = dataApiState === 'Server empty' || dataApiState === 'Filter empty';

  const setCurrentStatus = (next: LifecycleState) => {
    setStatusByTab((current) => ({ ...current, [tab]: next }));
    setSelectedByTab((current) => ({ ...current, [tab]: [] }));
  };

  const setCurrentFolder = (next: string | null) => {
    setFolderByTab((current) => ({ ...current, [tab]: next }));
    setSelectedByTab((current) => ({ ...current, [tab]: [] }));
  };

  const bulkBar = (label: string) => selectedKeys.length ? (
    <div className="bulk-action-bar">
      <Text strong>{selectedKeys.length} selected</Text>
      <Button size="small">Move to Folder</Button>
      {currentStatus === 'active' && <Button size="small">Archive</Button>}
      {currentStatus !== 'active' && <Button size="small">Restore</Button>}
      <Popconfirm title={`Move selected ${label} to Recycle Bin?`}>
        <Button size="small" danger>{currentStatus === 'recycle_bin' ? 'Force Delete' : 'Delete'}</Button>
      </Popconfirm>
      <Button size="small" onClick={() => setSelectedByTab((current) => ({ ...current, [tab]: [] }))}>Clear</Button>
    </div>
  ) : null;

  const deckRows = dataRowsBlockedByApiState ? [] : filteredByDataState(dataDeckRows, statusByTab.decks, folderByTab.decks);
  const requirementRows = dataRowsBlockedByApiState ? [] : filteredByDataState(dataRequirementRows, statusByTab.requirements, folderByTab.requirements);
  const colorRows = dataRowsBlockedByApiState ? [] : filteredByDataState(dataColorRows, statusByTab.colors, folderByTab.colors);
  const emptyTextFor = (entity: string) => (
    <Empty description={dataApiState === 'Server empty' ? `No ${entity} returned by the fake API` : `No ${entity} match the selected lifecycle or folder filters`} />
  );
  const lifecycleActions = ({
    entity,
    status,
    onEdit,
    activeExtras,
  }: {
    entity: string;
    status: LifecycleState;
    onEdit: () => void;
    activeExtras?: ReactNode;
  }) => (
    <Space wrap>
      {status === 'active' ? (
        <>
          <Button size="small" icon={<EditOutlined />} onClick={onEdit}>Edit</Button>
          {activeExtras}
          <Button size="small">Archive</Button>
          <Popconfirm title={`Move this ${entity} to Recycle Bin?`}>
            <Button size="small" danger icon={<DeleteOutlined />}>Delete</Button>
          </Popconfirm>
        </>
      ) : (
        <>
          <Button size="small">Restore</Button>
          {status === 'recycle_bin' ? (
            <Popconfirm title={`Force delete this ${entity}?`} description="Production exports historical data, then hides it from the product UI.">
              <Button size="small" danger>Force Delete</Button>
            </Popconfirm>
          ) : (
            <Popconfirm title={`Move archived ${entity} to Recycle Bin?`}>
              <Button size="small" danger icon={<DeleteOutlined />}>Delete</Button>
            </Popconfirm>
          )}
        </>
      )}
    </Space>
  );

  const deckColumns: ColumnsType<DataDeckRow> = [
    { title: 'Title', dataIndex: 'title', key: 'title', sorter: (a, b) => a.title.localeCompare(b.title) },
    { title: 'Slides', dataIndex: 'slideCount', key: 'slideCount', width: 90 },
    { title: 'Status', dataIndex: 'lifecycle', key: 'lifecycle', width: 130, render: lifecycleTag },
    { title: 'Folders', dataIndex: 'folders', key: 'folders', width: 220, render: (folders: string[]) => <Space size={4} wrap>{folders.length ? folders.map((folder) => <Tag key={folder}>{folder}</Tag>) : '-'}</Space> },
    { title: 'Created', dataIndex: 'createdAt', key: 'createdAt', width: 170 },
    {
      title: 'Actions',
      key: 'actions',
      width: 290,
      render: (_, record) => (
        lifecycleActions({
          entity: 'deck',
          status: statusByTab.decks,
          onEdit: () => setEntityDrawer({ title: 'Edit Deck', kind: 'decks' }),
          activeExtras: (
            <>
              <Button size="small" icon={<ScissorOutlined />}>Split</Button>
              <Button size="small" icon={<RobotOutlined />} onClick={() => setAutoSplitDeck(record)}>Auto split with LLM draft</Button>
            </>
          ),
        })
      ),
    },
  ];

  const requirementColumns: ColumnsType<DataRequirementRow> = [
    { title: 'Title', dataIndex: 'title', key: 'title', width: 220, sorter: (a, b) => a.title.localeCompare(b.title) },
    { title: 'Content', dataIndex: 'content', key: 'content', ellipsis: true },
    { title: 'Created', dataIndex: 'createdAt', key: 'createdAt', width: 170 },
    { title: 'Status', dataIndex: 'lifecycle', key: 'lifecycle', width: 130, render: lifecycleTag },
    { title: 'Folders', dataIndex: 'folders', key: 'folders', width: 220, render: (folders: string[]) => <Space size={4} wrap>{folders.map((folder) => <Tag key={folder}>{folder}</Tag>)}</Space> },
    { title: 'Actions', key: 'actions', width: 240, render: () => lifecycleActions({ entity: 'requirement', status: statusByTab.requirements, onEdit: () => setEntityDrawer({ title: 'Edit Requirement', kind: 'requirements' }) }) },
  ];

  const colorColumns: ColumnsType<DataColorRow> = [
    { title: 'Title', dataIndex: 'title', key: 'title', width: 220, sorter: (a, b) => a.title.localeCompare(b.title) },
    { title: 'Content', dataIndex: 'content', key: 'content', render: (value: string) => <pre className="inline-code-preview">{value}</pre> },
    { title: 'Source', dataIndex: 'sourceType', key: 'sourceType', width: 130, render: (value: string) => value === 'image_extract' ? 'image_extract' : 'manual' },
    { title: 'Created', dataIndex: 'createdAt', key: 'createdAt', width: 170 },
    { title: 'Status', dataIndex: 'lifecycle', key: 'lifecycle', width: 130, render: lifecycleTag },
    { title: 'Folders', dataIndex: 'folders', key: 'folders', width: 210, render: (folders: string[]) => <Space size={4} wrap>{folders.map((folder) => <Tag key={folder}>{folder}</Tag>)}</Space> },
    { title: 'Actions', key: 'actions', width: 240, render: () => lifecycleActions({ entity: 'color palette', status: statusByTab.colors, onEdit: () => setEntityDrawer({ title: 'Edit Color Palette', kind: 'colors' }) }) },
  ];

  return (
    <>
      <PageHeader
        title="Data Management"
        subtitle="Maintain source decks, user requirements, color palettes, folders, and lifecycle state with isolated fake data."
        actions={<><Button icon={<DatabaseOutlined />}>DB: fake-preview.db</Button><Button icon={<ReloadOutlined />} onClick={() => notify('Data summary refreshed from fake fixtures')}>Refresh</Button></>}
      />
      <div className="summary-grid">
        <div className="summary-tile"><span>Decks</span><strong>{dataApiState === 'Server empty' ? 0 : dataDeckRows.filter((row) => row.lifecycle === 'active').length}</strong><Tag color="blue">active</Tag></div>
        <div className="summary-tile"><span>Requirements</span><strong>{dataApiState === 'Server empty' ? 0 : dataRequirementRows.filter((row) => row.lifecycle === 'active').length}</strong><Tag color="green">active</Tag></div>
        <div className="summary-tile"><span>Colors</span><strong>{dataApiState === 'Server empty' ? 0 : dataColorRows.filter((row) => row.lifecycle === 'active').length}</strong><Tag color="purple">active</Tag></div>
        <div className="summary-tile"><span>Lifecycle</span><strong>Active</strong><Tag>Archived / Recycle Bin</Tag></div>
      </div>

      <section className="mock-panel qa-state-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Data API State Coverage</h3>
            <p className="section-subtitle">Production Data loads summary counts, entity lists, folders, bulk actions, split drafts, and color extraction through API-backed states.</p>
          </div>
          <Segmented
            value={dataApiState}
            options={dataApiStateOptions}
            onChange={(value) => setDataApiState(value as MockApiState)}
          />
        </div>
        <Alert showIcon type={dataStateCopy[dataApiState].type} title={dataStateCopy[dataApiState].title} description={dataStateCopy[dataApiState].description} />
        <div className="state-proof-grid">
          <div className="mini-tile"><Text strong>Summary fetch</Text><span className="context-meta">Failed to load data summary and Refresh loading are explicit review states.</span></div>
          <div className="mini-tile"><Text strong>Entity lists</Text><span className="context-meta">Failed to load decks, requirements, colors, or folders without losing lifecycle controls.</span></div>
          <div className="mini-tile"><Text strong>Bulk outcomes</Text><span className="context-meta">Move to Folder, Archive, Restore, Delete, and Force Delete include partial-warning copy.</span></div>
          <div className="mini-tile"><Text strong>LLM/image tasks</Text><span className="context-meta">Split failed, Auto split failed, Confirm failed, Choose an image first, and Color palette extracted states stay visible.</span></div>
        </div>
        <div className="tag-row panel-inline-alert">
          <Tag color="blue">table loading</Tag>
          <Tag>No decks returned by the fake API</Tag>
          <Tag>No requirements match filters</Tag>
          <Tag>No color palettes match filters</Tag>
          <Tag color="warning">bulk partial warning</Tag>
        </div>
      </section>

      <section className="mock-panel">
        <Tabs
          className="module-tabs"
          activeKey={tab}
          onChange={(key) => setTab(key as DataTabKey)}
          items={[
            {
              key: 'decks',
              label: 'Decks',
              children: (
                <div className="tab-workspace">
                  <DataControlStrip scope="deck" status={statusByTab.decks} folder={folderByTab.decks} onStatusChange={(status) => setCurrentStatus(status)} onFolderChange={(folder) => setCurrentFolder(folder)} onNewFolder={() => setFolderModal('deck')} />
                  <div className="button-row"><Button type="primary" icon={<PlusOutlined />} onClick={() => setEntityDrawer({ title: 'Add Deck', kind: 'decks' })}>Add Deck</Button></div>
                  {bulkBar('decks')}
                  <Table rowKey="key" className="responsive-table" columns={deckColumns} dataSource={deckRows} loading={dataApiState === 'List loading'} locale={{ emptyText: emptyTextFor('decks') }} rowSelection={{ selectedRowKeys: selectedByTab.decks, onChange: (keys) => setSelectedByTab((current) => ({ ...current, decks: keys })) }} pagination={false} scroll={{ x: 1100 }} expandable={{ expandedRowRender: (record) => <div className="slide-list-mini">{record.slides.map((slide, index) => <Tag key={slide}>{index + 1}. {slide}</Tag>)}</div> }} />
                </div>
              ),
            },
            {
              key: 'requirements',
              label: 'Requirements',
              children: (
                <div className="tab-workspace">
                  <DataControlStrip scope="requirement" status={statusByTab.requirements} folder={folderByTab.requirements} onStatusChange={(status) => setCurrentStatus(status)} onFolderChange={(folder) => setCurrentFolder(folder)} onNewFolder={() => setFolderModal('requirement')} />
                  <div className="button-row"><Button type="primary" icon={<PlusOutlined />} onClick={() => setEntityDrawer({ title: 'Add Requirement', kind: 'requirements' })}>Add Requirement</Button></div>
                  {bulkBar('requirements')}
                  <Table rowKey="key" className="responsive-table" columns={requirementColumns} dataSource={requirementRows} loading={dataApiState === 'List loading'} locale={{ emptyText: emptyTextFor('requirements') }} rowSelection={{ selectedRowKeys: selectedByTab.requirements, onChange: (keys) => setSelectedByTab((current) => ({ ...current, requirements: keys })) }} pagination={false} scroll={{ x: 1080 }} />
                </div>
              ),
            },
            {
              key: 'colors',
              label: 'Colors',
              children: (
                <div className="tab-workspace">
                  <DataControlStrip scope="color" status={statusByTab.colors} folder={folderByTab.colors} onStatusChange={(status) => setCurrentStatus(status)} onFolderChange={(folder) => setCurrentFolder(folder)} onNewFolder={() => setFolderModal('color')} />
                  <div className="button-row"><Button type="primary" icon={<PlusOutlined />} onClick={() => setEntityDrawer({ title: 'Add Color Palette', kind: 'colors' })}>Add Color Palette</Button><Button icon={<UploadOutlined />} onClick={() => setExtractOpen(true)}>Extract from image</Button></div>
                  {bulkBar('color palettes')}
                  <Table rowKey="key" className="responsive-table" columns={colorColumns} dataSource={colorRows} loading={dataApiState === 'List loading'} locale={{ emptyText: emptyTextFor('color palettes') }} rowSelection={{ selectedRowKeys: selectedByTab.colors, onChange: (keys) => setSelectedByTab((current) => ({ ...current, colors: keys })) }} pagination={false} scroll={{ x: 1180 }} />
                </div>
              ),
            },
          ]}
        />
      </section>

      <Drawer title={entityDrawer?.title} open={Boolean(entityDrawer)} onClose={() => setEntityDrawer(null)} size="large" extra={<Button type="primary" onClick={() => { notify(`${entityDrawer?.title} saved in fake state`); setEntityDrawer(null); }}>Save</Button>}>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <label className="filter-field"><span>Title</span><Input placeholder="Title" /></label>
          <label className="filter-field"><span>Content</span><Input.TextArea rows={entityDrawer?.kind === 'colors' ? 8 : 6} placeholder={entityDrawer?.kind === 'colors' ? 'XML palette data' : 'Content'} /></label>
          <label className="filter-field"><span>Folders</span><Select mode="multiple" placeholder="Folders" options={dataFolders.map((folder) => ({ label: folder.name, value: folder.name }))} /></label>
          <Alert showIcon type="info" message="Fake drawer mirrors production Add/Edit form fields; Save updates notification only." />
        </Space>
      </Drawer>

      <Modal title="New Folder" open={Boolean(folderModal)} onCancel={() => setFolderModal(null)} onOk={() => { notify('Folder created in fake state'); setFolderModal(null); }} destroyOnHidden>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <label className="filter-field"><span>Folder name</span><Input /></label>
          <label className="filter-field"><span>Parent folder</span><Select allowClear options={dataFolders.filter((folder) => folder.scope === folderModal).map((folder) => ({ label: folder.name, value: folder.name }))} /></label>
        </Space>
      </Modal>

      <Modal title={autoSplitDeck ? 'Review Auto Split Draft' : 'Auto Split Deck'} open={Boolean(autoSplitDeck)} onCancel={() => setAutoSplitDeck(null)} footer={[<Button key="discard" onClick={() => setAutoSplitDeck(null)}>Discard</Button>, <Button key="confirm" type="primary" onClick={() => { notify('Auto split draft confirmed in fake state'); setAutoSplitDeck(null); }}>Confirm Replace Slides</Button>]} width={720} destroyOnHidden>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Text strong>{autoSplitDeck?.title}</Text>
          <Select aria-label="Split config" placeholder="Select config" defaultValue={combinationRows[0].key} options={combinationRows.map((row) => ({ label: `${row.isDefault ? 'Default · ' : ''}${row.name}`, value: row.key }))} />
          <Alert showIcon type="info" message={`Auto-Spill Model: ${combinationRows[0].autoSpill}`} description={`Designer: ${combinationRows[0].designer} · HTML Agent: ${combinationRows[0].htmlAgent}`} />
          <div className="draft-slide-list">{autoSplitDeck?.slides.map((slide, index) => <div key={slide}><Text strong>{index + 1}. {slide}</Text><Paragraph className="context-meta">Draft content preview for fake auto split review.</Paragraph></div>)}</div>
        </Space>
      </Modal>

      <Modal title="Extract Color Palette" open={extractOpen} onCancel={() => setExtractOpen(false)} onOk={() => { notify('Color palette extracted in fake state'); setExtractOpen(false); }} okText="Extract" width={680} destroyOnHidden>
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <label className="filter-field"><span>Title</span><Input placeholder="New palette title" /></label>
          <Button icon={<UploadOutlined />}>Choose Image</Button>
          <label className="filter-field"><span>XML Preview</span><Input.TextArea rows={8} readOnly value="<palette><primary>#1463ff</primary><surface>#eff6ff</surface></palette>" /></label>
        </Space>
      </Modal>
    </>
  );
}

function GenerateMockPage({ notify }: { notify: (text: string) => void }) {
  const navigate = useNavigate();
  const [current, setCurrent] = useState(0);
  const [selectedDeckKey, setSelectedDeckKey] = useState(dataDeckRows[0].key);
  const [routeEngine, setRouteEngine] = useState<GenerateRouteEngine>('html');
  const [generationMode, setGenerationMode] = useState<GenerateMode>('auto');
  const [imageStrategy, setImageStrategy] = useState('Image 5.0');
  const [candidateCount, setCandidateCount] = useState(2);
  const [selectedReqKeys, setSelectedReqKeys] = useState<string[]>([dataRequirementRows[0].key]);
  const [selectedColorKeys, setSelectedColorKeys] = useState<string[]>([dataColorRows[0].key]);
  const [selectedConfigKey, setSelectedConfigKey] = useState(combinationRows[0].key);
  const [activeBatch, setActiveBatch] = useState(false);
  const [activeBatchTerminal, setActiveBatchTerminal] = useState(false);
  const [generateApiState, setGenerateApiState] = useState<GenerateApiState>('Ready');

  const selectedDeck = dataDeckRows.find((row) => row.key === selectedDeckKey) || dataDeckRows[0];
  const selectedConfig = combinationRows.find((row) => row.key === selectedConfigKey) || combinationRows[0];
  const selectedRequirements = dataRequirementRows.filter((row) => selectedReqKeys.includes(row.key));
  const selectedColors = dataColorRows.filter((row) => selectedColorKeys.includes(row.key));
  const isAutoMode = routeEngine === 'html' && generationMode === 'auto';
  const routeLabel = routeEngine === 'html' ? 'HTML Default' : imageStrategy;
  const runCount = isAutoMode ? candidateCount * Math.max(selectedColors.length, 1) : Math.max(selectedRequirements.length, 1) * Math.max(selectedColors.length, 1);
  const slideGenerations = runCount * selectedDeck.slideCount;
  const currentBatchLimit = isAutoMode ? 10 : 5;
  const overBatchLimit = runCount > currentBatchLimit;
  const activeReferences = referenceInputMap[routeEngine === 'html' ? 'HTML' : imageStrategy];
  const generateStateCopy: Record<GenerateApiState, { type: 'success' | 'info' | 'warning' | 'error'; title: string; description: string }> = {
    Ready: {
      type: 'success',
      title: 'Ready',
      description: 'Decks, requirements, colors, configs, prompts, role models, and active batch state are available from fake fixtures.',
    },
    'Initial loading': {
      type: 'info',
      title: 'Initial loading',
      description: 'Production blocks the Generate form while decks, requirements, colors, configs, prompts, role models, and active batch state are loading.',
    },
    'Failed to load data': {
      type: 'error',
      title: 'Failed to load data',
      description: 'Production shows a page-level load error if initial Generate dependencies cannot be fetched.',
    },
    'Generate failed': {
      type: 'error',
      title: 'Generate failed',
      description: 'Production keeps selections visible and reports the failed /api/generate request without losing the draft.',
    },
    'Failed to refresh batch': {
      type: 'warning',
      title: 'Failed to refresh batch',
      description: 'Production keeps the active batch panel visible while warning that polling or batch detail refresh failed.',
    },
  };
  const canMoveFromDeck = Boolean(selectedDeckKey && selectedDeck.slideCount > 0);
  const canMoveFromOptions = Boolean(selectedConfigKey && !overBatchLimit && (isAutoMode || (selectedReqKeys.length && selectedColorKeys.length)));
  const nextBlockedText = current === 0
    ? 'Select a deck with at least one slide.'
    : isAutoMode
      ? overBatchLimit
        ? `Current selection creates ${runCount} runs. Keep one batch at ${currentBatchLimit} runs or fewer.`
        : 'Select a config and keep candidate count within the batch limit.'
      : overBatchLimit
        ? `Current selection creates ${runCount} runs. Keep one batch at ${currentBatchLimit} runs or fewer.`
        : 'Select requirements, colors, config, and stay under the batch limit.';
  const enterOverLimitPreview = () => {
    setRouteEngine('html');
    setGenerationMode('auto');
    setCandidateCount(10);
    setSelectedColorKeys(dataColorRows.filter((row) => row.lifecycle === 'active').map((row) => row.key));
  };
  const resetSafeEstimate = () => {
    setCandidateCount(2);
    setSelectedColorKeys([dataColorRows[0].key]);
  };

  const stepButtons = [
    { title: 'Select Deck', subtitle: selectedDeck ? 'Deck selected' : 'Choose source deck' },
    { title: 'Options', subtitle: 'Configure generation options' },
    { title: 'Confirm', subtitle: 'Review and confirm' },
  ];

  return (
    <>
      <PageHeader
        title="Generate Slides"
        subtitle="Configure HTML or Image route options and start a new fake batch without calling backend APIs."
        actions={<Button icon={<HistoryOutlined />} onClick={() => navigate('/history')}>View Generate History</Button>}
      />
      <section className="mock-panel qa-state-panel">
        <div className="mock-panel-head">
          <div>
            <h3>Generate API State Coverage</h3>
            <p className="section-subtitle">Production Generate has explicit loading and error states for dependency loading, batch creation, and active-batch refresh.</p>
          </div>
          <Segmented
            value={generateApiState}
            onChange={(value) => setGenerateApiState(value as GenerateApiState)}
            options={['Ready', 'Initial loading', 'Failed to load data', 'Generate failed', 'Failed to refresh batch']}
          />
        </div>
        <div className="state-panel-body">
          <Alert
            showIcon
            type={generateStateCopy[generateApiState].type}
            message={generateStateCopy[generateApiState].title}
            description={generateStateCopy[generateApiState].description}
          />
        </div>
      </section>
      {activeBatch && !activeBatchTerminal && (
        <Alert
          showIcon
          type="info"
          title="Restored active batch #128"
          description="Progress is restored from fake fixtures, so leaving this page will not lose the run state."
          style={{ marginBottom: 16 }}
        />
      )}
      <section className="mock-panel generate-workflow-frame">
        <div className="generate-stepper" aria-label="Generate workflow steps">
          {stepButtons.map((step, index) => (
            <button
              type="button"
              key={step.title}
              className={`generate-step-button ${index === current ? 'active' : ''} ${index < current ? 'complete' : ''}`}
              onClick={() => {
                if (index < current || (index === 1 && canMoveFromDeck) || (index === 2 && canMoveFromOptions)) setCurrent(index);
              }}
              aria-current={index === current ? 'step' : undefined}
            >
              <span className="generate-step-index">{index < current ? <CheckCircleOutlined /> : index + 1}</span>
              <span><strong>{step.title}</strong><small>{step.subtitle}</small></span>
            </button>
          ))}
        </div>

        <div className="generate-step-content">
          {current === 0 && (
            <div className="work-surface narrow">
              <label className="filter-field"><span>Select deck</span><Select aria-label="Select deck" value={selectedDeckKey} onChange={setSelectedDeckKey} options={dataDeckRows.filter((row) => row.lifecycle === 'active').map((row) => ({ label: row.title, value: row.key }))} /></label>
              <Alert showIcon type="info" title={`${selectedDeck.slideCount} slide(s) in this deck`} description={selectedDeck.slides.join(' · ')} />
            </div>
          )}

          {current === 1 && (
            <div className="generate-step-grid">
              <section className="generate-options-panel" aria-label="Generation options">
                <div className="generate-section-title">Generation Mode</div>
                <Segmented
                  aria-label="Generation route"
                  value={routeEngine}
                  options={[{ label: 'HTML Route', value: 'html' }, { label: 'Image Route', value: 'image' }]}
                  onChange={(value) => {
                    const engine = value as GenerateRouteEngine;
                    setRouteEngine(engine);
                    if (engine === 'image') setGenerationMode('manual');
                  }}
                />
                {routeEngine === 'image' ? (
                  <Segmented
                    aria-label="Image strategy"
                    value={imageStrategy}
                    options={imageStrategyOptions.map((item) => ({ label: <span><strong>{item.label}</strong> {item.detail}</span>, value: item.value }))}
                    onChange={(value) => setImageStrategy(value as string)}
                  />
                ) : (
                  <Segmented
                    aria-label="Generation mode"
                    value={generationMode}
                    options={[{ label: 'Auto (Recommended)', value: 'auto' }, { label: 'Manual', value: 'manual' }]}
                    onChange={(value) => setGenerationMode(value as GenerateMode)}
                  />
                )}
                {routeEngine === 'image' && <Alert showIcon type="info" message="Image Route uses Manual inputs" description="Image flows use selected requirements, colors, strategy prompts, and Image generator models directly." />}
                <div className="reference-map">
                  <div className="reference-map-heading"><Text strong>Reference Input Map</Text><Tag color={routeEngine === 'image' ? 'gold' : 'blue'}>{routeLabel}</Tag></div>
                  {activeReferences.map(([name, status, detail]) => (
                    <div className="reference-map-row" key={name}><Text strong>{name}</Text><Tag>{status}</Tag><span>{detail}</span></div>
                  ))}
                </div>
                {isAutoMode ? (
                  <div className="option-list-card">
                    <div className="option-row"><div className="option-row-icon"><NumberOutlined /></div><div className="option-row-copy"><Text strong>Candidate Count</Text><span>Number of slide candidates to generate.</span></div><InputNumber className="option-row-control" min={1} max={10} value={candidateCount} onChange={(value) => setCandidateCount(value || 1)} /></div>
                    <div className="option-row compact-action-row">
                      <div className="option-row-icon"><WarningOutlined /></div>
                      <div className="option-row-copy"><Text strong>Limit Preview</Text><span>Show the production blocked-next state before backend wiring.</span></div>
                      <Space wrap>
                        <Button onClick={enterOverLimitPreview}>Over-limit</Button>
                        <Button onClick={resetSafeEstimate}>Safe estimate</Button>
                      </Space>
                    </div>
                    <div className="option-row"><div className="option-row-icon"><BgColorsOutlined /></div><div className="option-row-copy"><Text strong>Color Palette (Optional)</Text><span>Apply a color palette to influence the style.</span></div><Select className="option-row-control" mode="multiple" allowClear value={selectedColorKeys} options={dataColorRows.filter((row) => row.lifecycle === 'active').map((row) => ({ label: row.title, value: row.key }))} onChange={setSelectedColorKeys} /></div>
                    <div className="option-row"><div className="option-row-icon"><SettingOutlined /></div><div className="option-row-copy"><Text strong>Default Config</Text><span>System configuration to use.</span></div><Select className="option-row-control" value={selectedConfigKey} options={combinationRows.map((row) => ({ label: `${row.isDefault ? 'Default · ' : ''}${row.name} (${row.timeoutMinutes}m)`, value: row.key }))} onChange={setSelectedConfigKey} /></div>
                    <div className="option-row"><div className="option-row-icon"><FileTextOutlined /></div><div className="option-row-copy"><Text strong>Default Designer Prompt</Text><span>Prompt used by the Designer agent.</span></div><Select className="option-row-control" value="designer-default" options={promptRows.filter((row) => row.role === 'Designer').map((row) => ({ label: `${row.isDefault ? 'Default · ' : ''}${row.name} (${row.version})`, value: row.key }))} /></div>
                    <div className="option-row"><div className="option-row-icon"><AppstoreOutlined /></div><div className="option-row-copy"><Text strong>Default HTML Agent Prompt</Text><span>Prompt used by the HTML Agent.</span></div><Select className="option-row-control" value="html-agent-default" options={promptRows.filter((row) => row.role === 'HTML Agent').map((row) => ({ label: `${row.isDefault ? 'Default · ' : ''}${row.name} (${row.version})`, value: row.key }))} /></div>
                  </div>
                ) : (
                  <div className="manual-option-grid">
                    <div className="option-panel"><div className="option-panel-head"><h3>Requirements</h3><Button size="small" onClick={() => setSelectedReqKeys(selectedReqKeys.length === dataRequirementRows.filter((row) => row.lifecycle === 'active').length ? [] : dataRequirementRows.filter((row) => row.lifecycle === 'active').map((row) => row.key))}>{selectedReqKeys.length ? 'Clear all' : 'Select all'}</Button></div><Checkbox.Group value={selectedReqKeys} onChange={(values) => setSelectedReqKeys(values as string[])} className="stacked-options">{dataRequirementRows.filter((row) => row.lifecycle === 'active').map((row) => <Checkbox key={row.key} value={row.key}>{row.title}</Checkbox>)}</Checkbox.Group></div>
                    <div className="option-panel"><div className="option-panel-head"><h3>Colors</h3><Button size="small" onClick={() => setSelectedColorKeys(selectedColorKeys.length === dataColorRows.filter((row) => row.lifecycle === 'active').length ? [] : dataColorRows.filter((row) => row.lifecycle === 'active').map((row) => row.key))}>{selectedColorKeys.length ? 'Clear all' : 'Select all'}</Button></div><Checkbox.Group value={selectedColorKeys} onChange={(values) => setSelectedColorKeys(values as string[])} className="stacked-options">{dataColorRows.filter((row) => row.lifecycle === 'active').map((row) => <Checkbox key={row.key} value={row.key}>{row.title}</Checkbox>)}</Checkbox.Group></div>
                  </div>
                )}
              </section>
              <aside className="generate-estimate-panel" aria-label="Batch estimate">
                <div className="estimate-heading"><Text strong>Batch Estimate (Live)</Text><Tag color={overBatchLimit ? 'error' : 'success'}>{overBatchLimit ? 'Limit' : 'Live'}</Tag></div>
                <div className="estimate-line"><span><ThunderboltOutlined /> Estimated Runs</span><strong>{runCount}</strong></div>
                <div className="estimate-line"><span><AppstoreOutlined /> Estimated Slide Generations</span><strong>{slideGenerations}</strong></div>
                <div className="estimate-line"><span><ClockCircleOutlined /> Per-Run Timeout</span><strong>{selectedConfig.timeoutMinutes}m</strong></div>
                <div className="estimate-line"><span><WarningOutlined /> Batch Limit</span><strong>{currentBatchLimit}</strong></div>
                <div className="role-model-list">
                  <span className="role-list-title">Model Profiles</span>
                  {routeEngine === 'image' ? (
                    <>
                      <div><Tag color="gold">B</Tag><span>Image Strategy</span><strong>{routeLabel}</strong></div>
                      <div><Tag color="gold">D</Tag><span>Designer Model</span><strong>{selectedConfig.imageDesigner}</strong></div>
                      <div><Tag color="purple">I</Tag><span>Image Model</span><strong>{selectedConfig.imageGenerator}</strong></div>
                    </>
                  ) : (
                    <>
                      <div><Tag color="green">D</Tag><span>Designer</span><strong>designer-default-v4</strong></div>
                      <div><Tag color="blue">H</Tag><span>HTML Agent</span><strong>html-agent-v6</strong></div>
                      <div><Tag color="blue">R</Tag><span>Route</span><strong>{routeLabel}</strong></div>
                    </>
                  )}
                </div>
                <div className="validation-list">
                  <span className="role-list-title">Validation</span>
                  <div><CheckOutlined /> Deck selected <strong>{selectedDeck.title}</strong></div>
                  <div><CheckOutlined /> Config available <strong>{selectedConfig.name}</strong></div>
                  <div><CheckOutlined /> Route ready <strong>{routeLabel}</strong></div>
                </div>
                {overBatchLimit && <Alert showIcon type="error" message="Run Limit Exceeded" description={`The estimated runs (${runCount}) exceed the system limit (${currentBatchLimit}).`} />}
              </aside>
            </div>
          )}

          {current === 2 && (
            <div className="work-surface">
              <div className="batch-summary">
                <div className="summary-tile"><span>Runs</span><strong>{runCount}</strong></div>
                <div className="summary-tile"><span>Slides</span><strong>{selectedDeck.slideCount}</strong></div>
                <div className="summary-tile"><span>Failure Rate</span><strong>0%</strong></div>
                <div className="summary-tile"><span>Config</span><strong>{selectedConfig.name}</strong></div>
              </div>
              <Descriptions bordered size="small" column={2}>
                <Descriptions.Item label="Deck">{selectedDeck.title}</Descriptions.Item>
                <Descriptions.Item label="Mode">{routeEngine === 'image' ? 'Manual inputs' : generationMode === 'auto' ? 'Auto' : 'Manual'}</Descriptions.Item>
                <Descriptions.Item label="Route">{routeLabel}</Descriptions.Item>
                <Descriptions.Item label="Colors">{selectedColors.map((row) => row.title).join(' · ') || 'None'}</Descriptions.Item>
                <Descriptions.Item label="Designer Prompt">Designer system prompt (designer-default-v4)</Descriptions.Item>
                <Descriptions.Item label={routeEngine === 'image' ? 'Image Prompts' : 'HTML Prompt'}>{routeEngine === 'image' ? 'Image unified/director/image defaults' : 'HTML Agent build prompt (html-agent-v6)'}</Descriptions.Item>
              </Descriptions>
              <div className="combination-list">{(isAutoMode ? selectedColors.map((color) => `Auto candidate x${candidateCount} / ${color.title}`) : selectedRequirements.flatMap((req) => selectedColors.map((color) => `${req.title} / ${color.title}`))).map((row) => <Tag key={row}>{row}</Tag>)}</div>
              <div className="execution-plan-preview">{(routeEngine === 'html' ? ['Designer Agent', 'HTML Agent per slide', 'Playwright screenshot', 'Run Detail evidence'] : ['Unified designer XML', 'Parallel image generation', 'XML cleanup', 'Per-slide evidence']).map((stage) => <Tag key={stage} color={routeEngine === 'image' ? 'gold' : 'blue'}>{stage}</Tag>)}</div>
              <Space style={{ marginTop: 20 }} wrap>
                <Button type="primary" size="large" icon={<ThunderboltOutlined />} disabled={overBatchLimit} onClick={() => { setActiveBatch(true); setActiveBatchTerminal(false); notify('Started fake batch #128 with isolated fixture runs'); }}>Generate Batch</Button>
                <Button icon={<BellOutlined />} onClick={() => notify('Batch notifications enabled in fake state')}>Enable Notifications</Button>
              </Space>
              {activeBatch && (
                <div className="active-batch">
                  <div className="active-batch-header"><h3>Batch #128</h3><Tag color={activeBatchTerminal ? 'success' : 'processing'}>{activeBatchTerminal ? 'completed' : 'running'}</Tag></div>
                  <div className="batch-summary compact">
                    <div className="summary-tile"><span>Total</span><strong>10</strong></div>
                    <div className="summary-tile"><span>Running</span><strong>{activeBatchTerminal ? 0 : 2}</strong></div>
                    <div className="summary-tile"><span>Completed</span><strong>{activeBatchTerminal ? 8 : 4}</strong></div>
                    <div className="summary-tile"><span>Failed</span><strong>{activeBatchTerminal ? 2 : 4}</strong></div>
                    <div className="summary-tile"><span>Failure Rate</span><strong>{activeBatchTerminal ? '20%' : '40%'}</strong></div>
                  </div>
                  <Progress percent={activeBatchTerminal ? 100 : 40} status={activeBatchTerminal ? 'success' : 'active'} />
                  <div className="active-run-list">{batchRuns.slice(0, 4).map((run) => <div className="run-progress-row" key={run.id}><div><Text strong>{run.deckName} / {run.summary}</Text><div className="context-meta">{run.slideSummary}</div></div><Tag color={run.status === 'failed' ? 'error' : 'processing'}>{run.status}</Tag><Progress percent={run.status === 'failed' ? 100 : 45} size="small" /></div>)}</div>
                  <Space wrap>
                    {!activeBatchTerminal && <Button onClick={() => setActiveBatchTerminal(true)}>Simulate terminal handoff</Button>}
                    {activeBatchTerminal && <Button type="link" onClick={() => navigate('/history')}>View batch in History</Button>}
                  </Space>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="generate-frame-footer">
          <div>{current > 0 && <Button onClick={() => setCurrent(current - 1)}>Back to {stepButtons[current - 1].title}</Button>}</div>
          <Space>{current > 0 && <Button>Cancel</Button>}{current < 2 && <Button type="primary" disabled={current === 0 ? !canMoveFromDeck : !canMoveFromOptions} onClick={() => setCurrent(current + 1)}>Next: {current === 1 ? 'Confirm' : stepButtons[current + 1].title}</Button>}</Space>
        </div>
      </section>
      {current < 2 && ((current === 0 && !canMoveFromDeck) || (current === 1 && !canMoveFromOptions)) && (
        <div className="generate-blocked-hint"><WarningOutlined /> {nextBlockedText}</div>
      )}
    </>
  );
}

function FeatureUpgradeMockApp() {
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [messageApi, contextHolder] = message.useMessage();
  const notify = (text: string) => {
    void messageApi.success(text);
  };

  return (
    <HashRouter>
      {contextHolder}
      <Routes>
        <Route element={<MockLayout />}>
          <Route path="/" element={<Navigate to="/data" replace />} />
          <Route path="/data" element={<DataManagementPage notify={notify} />} />
          <Route path="/generate" element={<GenerateMockPage notify={notify} />} />
          <Route path="/history" element={<HistoryOperationsPage openAction={setPendingAction} notify={notify} />} />
          <Route path="/history/batch/:batchId" element={<BatchOverviewPage openAction={setPendingAction} notify={notify} />} />
          <Route path="/history/run/:runId" element={<RunDetailPage openAction={setPendingAction} notify={notify} />} />
          <Route path="/history/:runId" element={<RunDetailPage openAction={setPendingAction} notify={notify} />} />
          <Route path="/runfail" element={<RunFailStatsPage />} />
          <Route path="/prompts" element={<ConfigCenterPage openAction={setPendingAction} notify={notify} initialArea="prompt-library" standaloneArea="prompt-library" />} />
          <Route path="/config" element={<ConfigCenterPage openAction={setPendingAction} notify={notify} />} />
          <Route path="/system-settings" element={<ConfigCenterPage openAction={setPendingAction} notify={notify} initialArea="system-settings" standaloneArea="system-settings" />} />
          <Route path="*" element={<Navigate to="/history" replace />} />
        </Route>
      </Routes>
      <Modal
        title={pendingAction ? `${pendingAction.kind}: ${pendingAction.scope}` : 'Action'}
        open={Boolean(pendingAction)}
        okText="Confirm fake action"
        cancelText="Cancel"
        onCancel={() => setPendingAction(null)}
        onOk={() => {
          if (pendingAction) {
            notify(`${pendingAction.kind} recorded for ${pendingAction.target}`);
          }
          setPendingAction(null);
        }}
      >
        {pendingAction && (
          <Space orientation="vertical" size="middle">
            <Alert
              showIcon
              type={pendingAction.kind === 'Force Regenerate' ? 'warning' : 'info'}
              title={pendingAction.target}
              description={
                pendingAction.kind === 'Force Regenerate'
                  ? 'Fake confirmation only. Real implementation should create a new version and retain five historical versions before rotating the oldest retained record.'
                  : 'Fake confirmation only. No backend request will be sent from this preview.'
              }
            />
            <Descriptions size="small" bordered column={1}>
              <Descriptions.Item label="Action">{pendingAction.kind}</Descriptions.Item>
              <Descriptions.Item label="Scope">{pendingAction.scope}</Descriptions.Item>
              <Descriptions.Item label="Target">{pendingAction.target}</Descriptions.Item>
            </Descriptions>
          </Space>
        )}
      </Modal>
    </HashRouter>
  );
}

export default FeatureUpgradeMockApp;
