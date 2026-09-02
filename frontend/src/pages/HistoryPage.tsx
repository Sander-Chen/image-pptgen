import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Collapse, DatePicker, Input, Progress, Select, Space, Table, Tag, Tooltip, message } from 'antd';
import { DownloadOutlined, EyeOutlined, FilterOutlined, ReloadOutlined, SearchOutlined, UnorderedListOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import type { RunProgress } from '../types';
import RunDetailView from '../components/RunDetail';

const statusColorMap: Record<string, string> = {
  pending: 'default',
  queued: 'default',
  running: 'processing',
  completed: 'success',
  completed_with_failures: 'warning',
  failed: 'error',
  timed_out: 'warning',
};

const downloadableStatuses = new Set(['completed', 'completed_with_failures', 'failed', 'timed_out']);

type PublicProgress = Pick<RunProgress, 'total' | 'completed' | 'failed' | 'pending' | 'running'> & {
  displayable?: number;
  missing_displayable?: number;
};

/** Only fields emitted by the public Image 3.0 run projection are accepted here. */
export type PublicRun = {
  id: number;
  batch_id?: number;
  deck_id: number;
  deck_title?: string;
  config_id: number;
  config_name?: string;
  engine: 'image';
  strategy: 'image_3_0';
  status: string;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at?: string | null;
  progress?: PublicProgress;
  slides?: PublicSlide[];
};

export type PublicSlide = {
  id: number;
  run_id: number;
  slide_id: number;
  position: number;
  slide_type?: 'cover' | 'content';
  status: string;
  error_message?: string | null;
  has_displayable_artifact?: boolean;
  final_image_path?: string | null;
};

/** Only fields emitted by the public Image 3.0 batch projection are accepted here. */
export type PublicBatch = {
  id: number;
  deck_id: number;
  deck_title?: string;
  config_id: number;
  config_name?: string;
  status: string;
  total_runs: number;
  queued_runs: number;
  running_runs: number;
  completed_runs: number;
  completed_with_failures_runs?: number;
  failed_runs: number;
  timed_out_runs: number;
  failure_rate: number;
  engine: 'image';
  strategy: 'image_3_0';
  representative_run_id?: number | null;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
};

export type PublicBatchDetail = PublicBatch & { runs: PublicRun[] };

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function positiveInt(value: unknown): number | null {
  return typeof value === 'number' && Number.isInteger(value) && value > 0 ? value : null;
}

function optionalString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

function optionalNullableString(value: unknown): string | null | undefined {
  if (value === null) return null;
  return optionalString(value);
}

function publicProgress(value: unknown): PublicProgress | undefined {
  const record = objectValue(value);
  if (!record) return undefined;
  const numbers = ['total', 'completed', 'failed', 'pending', 'running'] as const;
  if (!numbers.every((key) => typeof record[key] === 'number' && Number.isFinite(record[key]))) return undefined;
  const progress: PublicProgress = {
    total: Number(record.total),
    completed: Number(record.completed),
    failed: Number(record.failed),
    pending: Number(record.pending),
    running: Number(record.running),
  };
  for (const key of ['displayable', 'missing_displayable'] as const) {
    if (typeof record[key] === 'number' && Number.isFinite(record[key])) progress[key] = Number(record[key]);
  }
  return progress;
}

function toPublicSlide(value: unknown): PublicSlide | null {
  const record = objectValue(value);
  const id = positiveInt(record?.id);
  const runId = positiveInt(record?.run_id);
  const slideId = positiveInt(record?.slide_id);
  const position = positiveInt(record?.position);
  if (!id || !runId || !slideId || !position || typeof record?.status !== 'string') return null;
  return {
    id,
    run_id: runId,
    slide_id: slideId,
    position,
    slide_type: record.slide_type === 'cover' || record.slide_type === 'content' ? record.slide_type : undefined,
    status: record.status,
    error_message: optionalNullableString(record.error_message),
    has_displayable_artifact: typeof record.has_displayable_artifact === 'boolean' ? record.has_displayable_artifact : undefined,
    final_image_path: optionalNullableString(record.final_image_path),
  };
}

function toPublicRun(value: unknown): PublicRun | null {
  const record = objectValue(value);
  if (!record || record.engine !== 'image' || record.strategy !== 'image_3_0') return null;
  const id = positiveInt(record.id);
  const deckId = positiveInt(record.deck_id);
  const configId = positiveInt(record.config_id);
  if (!id || !deckId || !configId || typeof record.status !== 'string') return null;
  const slides = Array.isArray(record.slides)
    ? record.slides.map(toPublicSlide).filter((slide): slide is PublicSlide => slide !== null)
    : undefined;
  return {
    id,
    batch_id: positiveInt(record.batch_id) || undefined,
    deck_id: deckId,
    deck_title: optionalString(record.deck_title),
    config_id: configId,
    config_name: optionalString(record.config_name),
    engine: 'image',
    strategy: 'image_3_0',
    status: record.status,
    error_message: optionalNullableString(record.error_message),
    started_at: optionalNullableString(record.started_at),
    completed_at: optionalNullableString(record.completed_at),
    created_at: optionalNullableString(record.created_at),
    progress: publicProgress(record.progress),
    slides,
  };
}

function toPublicBatch(value: unknown): PublicBatch | null {
  const record = objectValue(value);
  if (!record || record.engine !== 'image' || record.strategy !== 'image_3_0') return null;
  const id = positiveInt(record.id);
  const deckId = positiveInt(record.deck_id);
  const configId = positiveInt(record.config_id);
  const requiredNumbers = ['total_runs', 'queued_runs', 'running_runs', 'completed_runs', 'failed_runs', 'timed_out_runs', 'failure_rate'] as const;
  if (!id || !deckId || !configId || typeof record.status !== 'string') return null;
  if (!requiredNumbers.every((key) => typeof record[key] === 'number' && Number.isFinite(record[key]))) return null;
  const completedWithFailures = typeof record.completed_with_failures_runs === 'number'
    ? record.completed_with_failures_runs
    : undefined;
  return {
    id,
    deck_id: deckId,
    deck_title: optionalString(record.deck_title),
    config_id: configId,
    config_name: optionalString(record.config_name),
    status: record.status,
    total_runs: Number(record.total_runs),
    queued_runs: Number(record.queued_runs),
    running_runs: Number(record.running_runs),
    completed_runs: Number(record.completed_runs),
    completed_with_failures_runs: completedWithFailures,
    failed_runs: Number(record.failed_runs),
    timed_out_runs: Number(record.timed_out_runs),
    failure_rate: Number(record.failure_rate),
    engine: 'image',
    strategy: 'image_3_0',
    representative_run_id: record.representative_run_id === null ? null : positiveInt(record.representative_run_id) || undefined,
    error_message: optionalNullableString(record.error_message),
    created_at: optionalNullableString(record.created_at),
    updated_at: optionalNullableString(record.updated_at),
  };
}

function toPublicBatchDetail(value: unknown): PublicBatchDetail | null {
  const record = objectValue(value);
  const batch = toPublicBatch(record);
  if (!batch || !Array.isArray(record?.runs)) return null;
  const runs = record.runs.map(toPublicRun).filter((run): run is PublicRun => run !== null);
  return { ...batch, runs };
}

function failureRate(batch: PublicBatch): string {
  return `${Math.round(batch.failure_rate * 100)}%`;
}

function batchProgress(batch: PublicBatch): number {
  if (!batch.total_runs) return 0;
  const done = batch.completed_runs + (batch.completed_with_failures_runs || 0) + batch.failed_runs + batch.timed_out_runs;
  return Math.min(100, Math.round((done / batch.total_runs) * 100));
}

function statusHelp(status: string): string {
  if (status === 'queued' || status === 'pending') return 'Waiting for an execution slot';
  if (status === 'running') return 'Generating slides';
  if (status === 'timed_out') return 'Exceeded configured timeout';
  if (status === 'failed') return 'Generation failed';
  if (status === 'completed_with_failures') return 'Ready for review with failed slides';
  if (status === 'completed') return 'Ready for review';
  return status;
}

const ErrorPreview: React.FC<{ message?: string | null }> = ({ message: errorMessage }) => (
  errorMessage ? (
    <Tooltip title={errorMessage}>
      <div className="run-error-text">{errorMessage}</div>
    </Tooltip>
  ) : null
);

const HistoryPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [batches, setBatches] = useState<PublicBatch[]>([]);
  const [batchRuns, setBatchRuns] = useState<Record<number, PublicRun[]>>({});
  const [loading, setLoading] = useState(false);
  const [searchText, setSearchText] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [dateRange, setDateRange] = useState<[string, string] | null>(null);
  const [downloadingBatchId, setDownloadingBatchId] = useState<number | null>(null);
  const [downloadingRunId, setDownloadingRunId] = useState<number | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const batchRunsRef = useRef<Record<number, PublicRun[]>>({});

  useEffect(() => {
    batchRunsRef.current = batchRuns;
  }, [batchRuns]);

  const fetchBatches = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const data = await api.batches.list();
      setBatches(data.map(toPublicBatch).filter((batch): batch is PublicBatch => batch !== null));
      const expandedBatchIds = Object.keys(batchRunsRef.current).map(Number);
      if (expandedBatchIds.length) {
        const details = await Promise.all(expandedBatchIds.map((batchId) => api.batches.get(batchId).catch(() => null)));
        setBatchRuns((prev) => {
          const next = { ...prev };
          details.forEach((detail) => {
            const publicDetail = toPublicBatchDetail(detail);
            if (publicDetail) next[publicDetail.id] = publicDetail.runs;
          });
          return next;
        });
      }
    } catch (err: unknown) {
      message.error(`Failed to load batches: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!id) queueMicrotask(() => void fetchBatches());
  }, [id, fetchBatches]);

  useEffect(() => {
    if (id) return undefined;
    const timer = window.setInterval(() => void fetchBatches(true), 3000);
    return () => window.clearInterval(timer);
  }, [id, fetchBatches]);

  const loadBatchRuns = async (expanded: boolean, record: PublicBatch) => {
    if (!expanded || batchRuns[record.id]) return;
    try {
      const detail = toPublicBatchDetail(await api.batches.get(record.id));
      if (detail) setBatchRuns((prev) => ({ ...prev, [record.id]: detail.runs }));
    } catch (err: unknown) {
      message.error(`Failed to load batch: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const downloadBatch = async (batch: PublicBatch) => {
    if (!downloadableStatuses.has(batch.status)) return;
    setDownloadingBatchId(batch.id);
    try {
      const result = await api.batches.download(batch.id);
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      message.success(`Batch #${batch.id} download started`);
    } catch (err: unknown) {
      message.error(`Batch download failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setDownloadingBatchId(null);
    }
  };

  const downloadRun = async (run: PublicRun) => {
    if (!downloadableStatuses.has(run.status)) return;
    setDownloadingRunId(run.id);
    try {
      const result = await api.runs.download(run.id);
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      message.success(`Run #${run.id} download started`);
    } catch (err: unknown) {
      message.error(`Run download failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setDownloadingRunId(null);
    }
  };

  const filteredBatches = useMemo(() => {
    const needle = searchText.trim().toLowerCase();
    const [start, end] = dateRange || [];
    const startDate = start ? new Date(`${start}T00:00:00`) : null;
    const endDate = end ? new Date(`${end}T23:59:59`) : null;
    return batches.filter((batch) => {
      if (statusFilter !== 'all' && batch.status !== statusFilter) return false;
      if (startDate || endDate) {
        const created = batch.created_at ? new Date(batch.created_at) : null;
        if (!created || Number.isNaN(created.getTime())) return false;
        if (startDate && created < startDate) return false;
        if (endDate && created > endDate) return false;
      }
      if (!needle) return true;
      return [`#${batch.id}`, batch.deck_title, batch.config_name, batch.status].filter(Boolean).join(' ').toLowerCase().includes(needle);
    });
  }, [batches, dateRange, searchText, statusFilter]);

  const summary = batches.reduce(
    (acc, batch) => {
      acc.total += batch.total_runs;
      acc.active += batch.status === 'running' || batch.status === 'queued' ? 1 : 0;
      acc.completed += batch.completed_runs + (batch.completed_with_failures_runs || 0);
      acc.failed += batch.failed_runs + batch.timed_out_runs;
      return acc;
    },
    { active: 0, completed: 0, failed: 0, total: 0 },
  );

  const expandedRunGrid = (record: PublicBatch) => {
    const runs = batchRuns[record.id] || [];
    if (!runs.length) return <div className="history-expanded-empty">No sibling runs loaded yet.</div>;
    return (
      <div className="history-expanded-run-grid" aria-label={`Batch ${record.id} sibling runs`}>
        {runs.map((run) => {
          const progress = run.progress;
          return (
            <div key={run.id} className="history-expanded-run-card">
              <div className="history-expanded-run-card-header">
                <div>
                  <strong>Run {run.id}</strong>
                  <span>Image Route (3.0)</span>
                </div>
                <Tag color={statusColorMap[run.status] || 'default'}>{run.status}</Tag>
              </div>
              <div className="history-expanded-run-context">
                <div><span>Deck</span><strong>{run.deck_title || `#${run.deck_id}`}</strong></div>
                <div><span>Config</span><strong>{run.config_name || `#${run.config_id}`}</strong></div>
                <div><span>Slides</span><strong>{progress ? `${progress.completed}/${progress.total}` : '-'}</strong></div>
              </div>
              <div className="run-status-help">{statusHelp(run.status)}</div>
              <ErrorPreview message={run.error_message} />
              <Space className="history-action-buttons history-expanded-run-actions" size={4} wrap>
                <Button aria-label={`View run ${run.id}`} title="View run" icon={<EyeOutlined />} size="small" onClick={() => navigate(`/history/run/${run.id}`)}>
                  Run Detail
                </Button>
                <Button aria-label={`Open batch overview from batch ${record.id}`} title="Open batch overview" icon={<UnorderedListOutlined />} size="small" onClick={() => navigate(`/history/batch/${record.id}`)}>
                  Batch
                </Button>
                <Button aria-label={`Download run ${run.id}`} title={downloadableStatuses.has(run.status) ? 'Download PNG artifacts' : 'Run download available after terminal status'} icon={<DownloadOutlined />} size="small" onClick={() => void downloadRun(run)} disabled={!downloadableStatuses.has(run.status)} loading={downloadingRunId === run.id}>
                  PNG ZIP
                </Button>
              </Space>
            </div>
          );
        })}
      </div>
    );
  };

  const renderBatchContext = (record: PublicBatch) => (
    <div className="history-context-cell">
      <strong className="history-compact-title">{record.deck_title || `Deck #${record.deck_id}`}</strong>
      <Space size={4} wrap>
        <Tag color="gold">Image Route (3.0)</Tag>
        <Tag color="purple">Config: {record.config_name || record.config_id}</Tag>
      </Space>
    </div>
  );

  const renderBatchState = (record: PublicBatch) => {
    const completedWithFailures = record.completed_with_failures_runs || 0;
    const terminalRuns = record.completed_runs + completedWithFailures + record.failed_runs + record.timed_out_runs;
    return (
      <div className="history-state-cell">
        <Space size={4} wrap>
          <Tag color={statusColorMap[record.status] || 'default'}>{record.status}</Tag>
          <Tag color={record.failed_runs || record.timed_out_runs || completedWithFailures ? 'warning' : 'success'}>Failure Rate {failureRate(record)}</Tag>
          {completedWithFailures > 0 && <Tag color="warning">Partial {completedWithFailures}</Tag>}
        </Space>
        <div className="history-state-line"><span>Created</span><strong>{record.created_at ? new Date(record.created_at).toLocaleString() : '-'}</strong></div>
        <div className="history-state-line"><span>Runs</span><strong>{terminalRuns}/{record.total_runs}</strong></div>
        <Progress percent={batchProgress(record)} size="small" />
        <div className="run-status-help">{statusHelp(record.status)}</div>
        <ErrorPreview message={record.error_message} />
      </div>
    );
  };

  const renderBatchActions = (record: PublicBatch) => (
    <Space className="history-action-buttons" size={4} wrap>
      <Tooltip title="Open run detail">
        <Button aria-label={`Open run detail for batch ${record.id}`} icon={<EyeOutlined />} size="small" onClick={() => record.representative_run_id && navigate(`/history/run/${record.representative_run_id}`)} disabled={!record.representative_run_id}>
          Run Detail
        </Button>
      </Tooltip>
      <Tooltip title="Open batch overview">
        <Button aria-label={`Open batch overview ${record.id}`} icon={<UnorderedListOutlined />} size="small" onClick={() => navigate(`/history/batch/${record.id}`)}>
          Batch
        </Button>
      </Tooltip>
      <Tooltip title={downloadableStatuses.has(record.status) ? 'Download PNG artifacts' : 'Download available after the batch finishes'}>
        <Button aria-label={`Download batch ${record.id}`} icon={<DownloadOutlined />} size="small" onClick={() => void downloadBatch(record)} disabled={!downloadableStatuses.has(record.status)} loading={downloadingBatchId === record.id}>
          PNG ZIP
        </Button>
      </Tooltip>
    </Space>
  );

  const columns = [
    { title: 'Batch', dataIndex: 'id', key: 'id', width: 118, render: (_id: number, record: PublicBatch) => <div className="history-batch-cell"><strong>#{record.id}</strong><span>{record.created_at ? new Date(record.created_at).toLocaleString() : '-'}</span></div> },
    { title: 'Deck / Route / Config', key: 'context', render: (_: unknown, record: PublicBatch) => renderBatchContext(record) },
    { title: 'State', key: 'state', width: 230, render: (_: unknown, record: PublicBatch) => renderBatchState(record) },
    { title: 'Actions', key: 'actions', width: 250, render: (_: unknown, record: PublicBatch) => renderBatchActions(record) },
  ];

  if (id) return <RunDetailView key={id} runId={Number(id)} />;

  return (
    <div className="history-page">
      <div className="page-toolbar">
        <div>
          <div className="page-kicker"><span className="status-dot" />Image Route (3.0)</div>
          <h2>History</h2>
          <p className="toolbar-subtitle">Read-only history for Image 3.0 batches, runs, progress, and PNG artifacts.</p>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void fetchBatches()} aria-label="Refresh history">Refresh</Button>
      </div>
      <div className="module-summary-grid">
        <div className="module-summary-item"><span className="module-summary-label">Active Batches</span><strong>{summary.active}</strong><Tag color={summary.active ? 'processing' : 'default'}>queued / running</Tag></div>
        <div className="module-summary-item"><span className="module-summary-label">Completed Runs</span><strong>{summary.completed}</strong><Tag color="success">ready</Tag></div>
        <div className="module-summary-item"><span className="module-summary-label">Failed or Timed Out</span><strong>{summary.failed}</strong><Tag color={summary.failed ? 'error' : 'default'}>needs review</Tag></div>
        <div className="module-summary-item"><span className="module-summary-label">Total Runs</span><strong>{summary.total}</strong><Tag>all batches</Tag></div>
      </div>
      <Collapse className="history-filter-collapse" activeKey={filtersOpen ? ['filters'] : []} onChange={(keys) => setFiltersOpen(Array.isArray(keys) ? keys.includes('filters') : keys === 'filters')} items={[{
        key: 'filters',
        label: <Space><FilterOutlined />Filters</Space>,
        children: (
          <div className="table-filter-bar history-filter-bar" aria-label="Run history filters">
            <div className="filter-field wide"><span>Search</span><Input allowClear prefix={<SearchOutlined />} placeholder="Search deck, config, batch id, or status" value={searchText} onChange={(event) => setSearchText(event.target.value)} /></div>
            <div className="filter-field"><span>Status</span><Select aria-label="Status filter" value={statusFilter} onChange={setStatusFilter} options={[{ label: 'All statuses', value: 'all' }, { label: 'Queued', value: 'queued' }, { label: 'Running', value: 'running' }, { label: 'Completed', value: 'completed' }, { label: 'Completed with failures', value: 'completed_with_failures' }, { label: 'Failed', value: 'failed' }, { label: 'Timed out', value: 'timed_out' }]} /></div>
            <div className="filter-field"><span>Created</span><DatePicker.RangePicker key={dateRange ? dateRange.join(':') : 'empty-date-range'} aria-label="Filter created date range" onChange={(_, dateStrings) => { const [start, end] = dateStrings; setDateRange(start && end ? [start, end] : null); }} /></div>
            <Button aria-label="Clear run history filters" icon={<FilterOutlined />} onClick={() => { setSearchText(''); setStatusFilter('all'); setDateRange(null); }}>Clear</Button>
          </div>
        ),
      }]} />
      <Table className="responsive-table history-compact-table history-desktop-table" dataSource={filteredBatches} columns={columns} rowKey="id" rowClassName={() => 'history-compact-row'} loading={loading} pagination={{ pageSize: 20 }} expandable={{ onExpand: loadBatchRuns, expandedRowRender: expandedRunGrid, expandRowByClick: false }} />
      <div className="history-mobile-list" aria-label="Mobile history batches">
        {filteredBatches.map((batch) => (
          <article key={batch.id} className="history-mobile-card">
            <div className="history-mobile-card-head"><button type="button" className="history-mobile-expand" aria-label={`Load sibling runs for batch ${batch.id}`} onClick={() => void loadBatchRuns(true, batch)}>+</button><div className="history-batch-cell"><strong>#{batch.id}</strong><span>{batch.created_at ? new Date(batch.created_at).toLocaleString() : '-'}</span></div></div>
            {renderBatchContext(batch)}
            {renderBatchState(batch)}
            {renderBatchActions(batch)}
            {batchRuns[batch.id] ? expandedRunGrid(batch) : null}
          </article>
        ))}
      </div>
    </div>
  );
};

export default HistoryPage;
