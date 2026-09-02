import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Card, Descriptions, Empty, Image, Space, Spin, Tabs, Tag, message } from 'antd';
import { ArrowLeftOutlined, DownloadOutlined, EyeOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import type { PublicBatchDetail, PublicRun, PublicSlide } from './HistoryPage';
import { toArtifactUrl } from '../lib/artifactUrls';

const statusColorMap: Record<string, string> = {
  pending: 'default',
  queued: 'default',
  running: 'processing',
  completed: 'success',
  completed_with_failures: 'warning',
  failed: 'error',
  timed_out: 'warning',
};

const terminalStatuses = new Set(['completed', 'completed_with_failures', 'failed', 'timed_out']);

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

function publicProgress(value: unknown): PublicRun['progress'] | undefined {
  const record = objectValue(value);
  const numbers = ['total', 'completed', 'failed', 'pending', 'running'] as const;
  if (!record || !numbers.every((key) => typeof record[key] === 'number' && Number.isFinite(record[key]))) return undefined;
  const progress: NonNullable<PublicRun['progress']> = {
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
  const id = positiveInt(record?.id);
  const deckId = positiveInt(record?.deck_id);
  const configId = positiveInt(record?.config_id);
  if (!record || record.engine !== 'image' || record.strategy !== 'image_3_0' || !id || !deckId || !configId || typeof record.status !== 'string') return null;
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
    slides: Array.isArray(record.slides) ? record.slides.map(toPublicSlide).filter((slide): slide is PublicSlide => slide !== null) : undefined,
  };
}

function toPublicBatch(value: unknown): PublicBatchDetail | null {
  const record = objectValue(value);
  const id = positiveInt(record?.id);
  const deckId = positiveInt(record?.deck_id);
  const configId = positiveInt(record?.config_id);
  const numbers = ['total_runs', 'queued_runs', 'running_runs', 'completed_runs', 'failed_runs', 'timed_out_runs', 'failure_rate'] as const;
  if (!record || record.engine !== 'image' || record.strategy !== 'image_3_0' || !id || !deckId || !configId || typeof record.status !== 'string' || !numbers.every((key) => typeof record[key] === 'number' && Number.isFinite(record[key])) || !Array.isArray(record.runs)) return null;
  const runs = record.runs.map(toPublicRun).filter((run): run is PublicRun => run !== null);
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
    completed_with_failures_runs: typeof record.completed_with_failures_runs === 'number' ? record.completed_with_failures_runs : undefined,
    failed_runs: Number(record.failed_runs),
    timed_out_runs: Number(record.timed_out_runs),
    failure_rate: Number(record.failure_rate),
    engine: 'image',
    strategy: 'image_3_0',
    representative_run_id: record.representative_run_id === null ? null : positiveInt(record.representative_run_id) || undefined,
    error_message: optionalNullableString(record.error_message),
    created_at: optionalNullableString(record.created_at),
    updated_at: optionalNullableString(record.updated_at),
    runs,
  };
}

function progressText(run: PublicRun): string {
  if (!run.progress) return 'No slide progress yet';
  return `${run.progress.completed}/${run.progress.total} slides complete`;
}

const MiniSlide: React.FC<{ slide: PublicSlide }> = ({ slide }) => {
  const imageUrl = toArtifactUrl(slide.final_image_path);
  return (
    <div className="batch-slide-chip">
      <span>Slide {slide.position}</span>
      {imageUrl ? <Image src={imageUrl} alt={`Run slide ${slide.position}`} preview={false} /> : <div className="slide-empty-state">No PNG yet</div>}
    </div>
  );
};

const BatchOverviewPage: React.FC = () => {
  const { batchId } = useParams<{ batchId: string }>();
  const navigate = useNavigate();
  const [batch, setBatch] = useState<PublicBatchDetail | null>(null);
  const [selectedRun, setSelectedRun] = useState<PublicRun | null>(null);
  const [loading, setLoading] = useState(false);
  const [downloadingRunId, setDownloadingRunId] = useState<number | null>(null);
  const selectedRunIdRef = useRef<number | null>(null);
  const batchLoadEpochRef = useRef(0);
  const batchPollInFlightRef = useRef(false);
  const numericBatchId = Number(batchId);

  const loadBatch = useCallback(async (silent = false) => {
    if (!Number.isInteger(numericBatchId) || numericBatchId <= 0) return;
    const requestEpoch = ++batchLoadEpochRef.current;
    if (!silent) setLoading(true);
    try {
      const detail = toPublicBatch(await api.batches.get(numericBatchId));
      if (!detail) throw new Error('Batch is not available on the public Image 3.0 surface');
      const nextRun = detail.runs.find((run) => run.id === selectedRunIdRef.current) || detail.runs[0] || null;
      if (requestEpoch !== batchLoadEpochRef.current) return;
      selectedRunIdRef.current = nextRun?.id || null;
      setBatch(detail);
      setSelectedRun(nextRun);
    } catch (err: unknown) {
      message.error(`Failed to load batch overview: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      if (!silent && requestEpoch === batchLoadEpochRef.current) setLoading(false);
    }
  }, [numericBatchId]);

  const pollBatch = useCallback(async () => {
    if (!Number.isInteger(numericBatchId) || numericBatchId <= 0 || batchPollInFlightRef.current) return;
    batchPollInFlightRef.current = true;
    const requestEpoch = batchLoadEpochRef.current;
    try {
      const detail = toPublicBatch(await api.batches.get(numericBatchId));
      if (!detail || requestEpoch !== batchLoadEpochRef.current) return;
      const nextRun = detail.runs.find((run) => run.id === selectedRunIdRef.current) || detail.runs[0] || null;
      selectedRunIdRef.current = nextRun?.id || null;
      setBatch(detail);
      setSelectedRun(nextRun);
    } catch (err: unknown) {
      message.error(`Failed to refresh batch overview: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      batchPollInFlightRef.current = false;
    }
  }, [numericBatchId]);

  useEffect(() => {
    queueMicrotask(() => {
      selectedRunIdRef.current = null;
      setBatch(null);
      setSelectedRun(null);
      void loadBatch();
    });
  }, [loadBatch]);

  const sortedSlides = useMemo(() => [...(selectedRun?.slides || [])].sort((a, b) => a.position - b.position), [selectedRun?.slides]);

  useEffect(() => {
    if (!batch?.status || terminalStatuses.has(batch.status)) return undefined;
    const timer = window.setInterval(() => void pollBatch(), 3000);
    return () => window.clearInterval(timer);
  }, [batch?.status, pollBatch]);

  const selectRun = (run: PublicRun) => {
    selectedRunIdRef.current = run.id;
    setSelectedRun(run);
  };

  const downloadRun = async (run: PublicRun) => {
    if (!terminalStatuses.has(run.status)) return;
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

  if (loading || !batch || batch.id !== numericBatchId) {
    return <Spin spinning={loading} style={{ display: 'block', marginTop: 100, textAlign: 'center' }} />;
  }

  return (
    <div className="batch-overview-page">
      <div className="page-toolbar">
        <div>
          <div className="page-kicker"><span className="status-dot" />Image Route (3.0)</div>
          <h2>Batch #{batch.id} History</h2>
          <p className="toolbar-subtitle">Inspect sibling runs, honest progress, and PNG artifacts from one Image 3.0 batch.</p>
        </div>
        <Space className="page-toolbar-actions" wrap>
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/history')}>History</Button>
          <Button icon={<ReloadOutlined />} onClick={() => void loadBatch()}>Refresh</Button>
        </Space>
      </div>

      <Descriptions bordered column={{ xs: 1, md: 3 }} className="batch-context">
        <Descriptions.Item label="Deck">{batch.deck_title || batch.deck_id}</Descriptions.Item>
        <Descriptions.Item label="Route">Image Route (3.0)</Descriptions.Item>
        <Descriptions.Item label="Config">{batch.config_name || batch.config_id}</Descriptions.Item>
        <Descriptions.Item label="Created">{batch.created_at ? new Date(batch.created_at).toLocaleString() : '-'}</Descriptions.Item>
        <Descriptions.Item label="Status"><Tag color={statusColorMap[batch.status] || 'default'}>{batch.status}</Tag></Descriptions.Item>
        <Descriptions.Item label="Failure Rate">{Math.round(batch.failure_rate * 100)}%</Descriptions.Item>
      </Descriptions>

      <section className="batch-overview-section">
        <div className="section-heading"><h3>Sibling Run History</h3><span>{batch.runs.length} runs in this batch</span></div>
        <div className="batch-run-grid">
          {batch.runs.map((run) => (
            <button type="button" key={run.id} className={`batch-run-card ${selectedRun?.id === run.id ? 'selected' : ''}`} onClick={() => selectRun(run)}>
              <div className="batch-run-card-header"><strong>Run {run.id}</strong><Tag color={statusColorMap[run.status] || 'default'}>{run.status}</Tag></div>
              <div className="batch-run-meta"><span>Image Route (3.0)</span><span>{run.config_name || `Config ${run.config_id}`}</span><span>{progressText(run)}</span></div>
              {run.error_message && <Alert type="error" showIcon message={run.error_message} />}
            </button>
          ))}
        </div>
      </section>

      <section className="batch-overview-section selected-run-evidence">
        <div className="section-heading"><h3>Selected Run Evidence</h3><span>{selectedRun ? `Run ${selectedRun.id}` : 'No run selected'}</span></div>
        {selectedRun ? (
          <Card
            title={<Space wrap><Tag color={statusColorMap[selectedRun.status] || 'default'}>{selectedRun.status}</Tag><span>Image Route (3.0)</span></Space>}
            extra={<Space wrap>
              <Button icon={<EyeOutlined />} onClick={() => navigate(`/history/run/${selectedRun.id}`)}>Run Detail</Button>
              <Button icon={<DownloadOutlined />} onClick={() => void downloadRun(selectedRun)} disabled={!terminalStatuses.has(selectedRun.status)} loading={downloadingRunId === selectedRun.id}>PNG ZIP</Button>
            </Space>}
          >
            <div className="selected-target-copy">Selected target: Run #{selectedRun.id} / batch #{selectedRun.batch_id || batch.id}</div>
            <div className="batch-slide-strip">
              {sortedSlides.map((slide) => <MiniSlide key={slide.id} slide={slide} />)}
              {!sortedSlides.length && <Empty description="No generated PNG yet" />}
            </div>
            <Tabs items={[
              { key: 'overview', label: 'Overview', children: <Descriptions column={1} items={[{ key: 'deck', label: 'Deck', children: selectedRun.deck_title || selectedRun.deck_id }, { key: 'config', label: 'Config', children: selectedRun.config_name || selectedRun.config_id }, { key: 'route', label: 'Route', children: 'Image Route (3.0)' }, { key: 'progress', label: 'Progress', children: progressText(selectedRun) }]} /> },
              { key: 'error', label: 'Error', children: selectedRun.error_message ? <Alert type="error" showIcon message={selectedRun.error_message} /> : <Empty description="No error recorded" /> },
            ]} />
          </Card>
        ) : <Empty description="Select a sibling run" />}
      </section>
    </div>
  );
};

export default BatchOverviewPage;
