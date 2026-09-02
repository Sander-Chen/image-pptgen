import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Collapse,
  Descriptions,
  Empty,
  Image,
  Spin,
  Space,
  Table,
  Tag,
  message,
} from 'antd';
import { ArrowLeftOutlined, DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { api } from '../api';
import type {
  CodexAuditDetailEvent,
  CodexAuditEventPage,
  CodexAuditInvocation,
  CodexAuditInvocationDetail,
  CodexRunAudit,
  RunDetail as RunDetailType,
  RunSlide,
} from '../types';
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

const TERMINAL = new Set(['completed', 'completed_with_failures', 'failed', 'timed_out']);

interface RunDetailProps {
  runId: number;
}

type SafeRecord = Record<string, unknown>;

function asRecord(value: unknown): SafeRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as SafeRecord
    : null;
}

function displayValue(value: unknown, fallback = '—'): string {
  if (value === undefined || value === null || value === '') return fallback;
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return fallback;
  }
}

const PRIVATE_KEY = /(api[_-]?key|authorization|cookie|credential|secret|token|password|private|session|thread|cwd|command|stderr|stdout|raw[_-]?jsonl|observed[_-]?jsonl|output[_-]?path|(^|_)path$)/i;

/** Keep the public evidence projection readable even when a nested payload is richer than expected. */
function safeEvidence(value: unknown, key = ''): unknown {
  if (PRIVATE_KEY.test(key)) return undefined;
  if (Array.isArray(value)) return value.map((item) => safeEvidence(item, key)).filter((item) => item !== undefined);
  const record = asRecord(value);
  if (record) {
    return Object.fromEntries(
      Object.entries(record)
        .map(([childKey, child]) => [childKey, safeEvidence(child, childKey)] as const)
        .filter(([, child]) => child !== undefined),
    );
  }
  if (typeof value === 'string' && value.startsWith('/') && !value.startsWith('/artifacts/')) return undefined;
  return value;
}

function safeJson(value: unknown, fallback = 'No public evidence recorded yet.'): string {
  const safe = safeEvidence(value);
  if (safe === undefined || safe === null || safe === '') return fallback;
  return displayValue(safe, fallback);
}

/**
 * Project only the authoritative Image 3.0 Seed/Palette lineage for Deck
 * position 2. The lineage is deliberately read from its exact top-level
 * location; nested request-chain placeholders are not interchangeable with
 * the persisted extraction result.
 */
function publicPositionTwoSeedEvidence(
  run: { stage_artifacts?: unknown } | null | undefined,
  seedSlide: Pick<RunSlide, 'id' | 'position' | 'status'> | null | undefined,
): Record<string, unknown> | undefined {
  if (!seedSlide || seedSlide.position !== 2 || !Number.isSafeInteger(seedSlide.id) || seedSlide.id <= 0) return undefined;
  const runArtifacts = asRecord(run?.stage_artifacts);
  const lineage = asRecord(runArtifacts?.seed_palette_lineage);
  if (!lineage || lineage.run_slide_id !== seedSlide.id || lineage.deck_position !== 2) return undefined;

  const seedPngSha = lineage.seed_png_sha256;
  const paletteSha = lineage.palette_sha256;
  const colors = lineage.colors;
  if (
    typeof seedPngSha !== 'string'
    || !seedPngSha
    || typeof paletteSha !== 'string'
    || !paletteSha
    || !Array.isArray(colors)
    || colors.length === 0
    || !colors.every((color) => typeof color === 'string' && Boolean(color.trim()))
  ) {
    return undefined;
  }

  return {
    run_slide_id: seedSlide.id,
    deck_position: 2,
    status: seedSlide.status,
    seed_png_sha256: seedPngSha,
    palette_sha256: paletteSha,
    colors: [...colors],
  };
}

function findByKey(value: unknown, matcher: RegExp): unknown {
  if (Array.isArray(value)) {
    for (const item of value) {
      const found = findByKey(item, matcher);
      if (found !== undefined) return found;
    }
    return undefined;
  }
  const record = asRecord(value);
  if (!record) return undefined;
  for (const [key, child] of Object.entries(record)) {
    if (matcher.test(key)) return child;
    const found = findByKey(child, matcher);
    if (found !== undefined) return found;
  }
  return undefined;
}

function statusTag(status: string | undefined | null) {
  const normalized = String(status || 'unknown');
  return <Tag color={statusColorMap[normalized] || 'default'}>{normalized}</Tag>;
}

function EvidenceBlock({ label, value }: { label: string; value: unknown }) {
  return (
    <section className="evidence-readable-section" aria-label={label}>
      <h4>{label}</h4>
      <pre className="evidence-code-block">{safeJson(value)}</pre>
    </section>
  );
}

function SlideFrame({ slide }: { slide: RunSlide }) {
  const artifactUrl = toArtifactUrl(slide.final_image_path);
  return (
    <div className="slide-frame public-image-slide-frame">
      {artifactUrl ? (
        <Image
          src={artifactUrl}
          alt={`Slide ${slide.position} final image`}
          preview
          width="100%"
        />
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="No public PNG is available for this slide" />
      )}
    </div>
  );
}

type AuditDetailState =
  | { status: 'loading' }
  | { status: 'loaded'; detail: CodexAuditInvocationDetail }
  | { status: 'error'; error: string };

type EventPageState = {
  status: 'loading' | 'loaded' | 'error';
  items: CodexAuditEventPage['items'];
  nextCursor: string | null;
  error?: string;
};

function auditDetailText(detail: CodexAuditInvocationDetail, fallbackEvents: CodexAuditInvocation['events'] = []): React.ReactNode {
  const errorEvents = detail.errors?.event_errors || [];
  const lineage = detail.lineage;
  const call = lineage?.call;
  return (
    <div className="evidence-section-stack">
      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="Run ID">{lineage?.run_id ?? detail.run_id}</Descriptions.Item>
        <Descriptions.Item label="Invocation ID">{lineage?.invocation_id ?? detail.invocation_id}</Descriptions.Item>
        <Descriptions.Item label="Run Slide ID">{lineage?.run_slide_id ?? 'Run-level'}</Descriptions.Item>
        <Descriptions.Item label="Stage">{lineage?.stage_id || '—'}</Descriptions.Item>
        <Descriptions.Item label="Attempt">{lineage?.attempt ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="Imagegen Call ID">{call?.id || '—'}</Descriptions.Item>
        <Descriptions.Item label="Imagegen Arguments SHA256">{call?.arguments_sha256 || '—'}</Descriptions.Item>
        <Descriptions.Item label="Canonical Session SHA256">{lineage?.session?.sha256 || '—'}</Descriptions.Item>
      </Descriptions>
      <EvidenceBlock label="Prompt / Input" value={detail.prompt || 'No prompt evidence recorded.'} />
      <EvidenceBlock label="Assistant Output" value={detail.assistant_output || 'No assistant output recorded.'} />
      <EvidenceBlock label="Tool Calls" value={detail.tool_calls} />
      <EvidenceBlock label="Imagegen Calls" value={detail.imagegen_calls} />
      <EvidenceBlock
        label="JSONL References"
        value={{
          rawSha256: detail.jsonl?.raw?.sha256,
          observedSha256: detail.jsonl?.observed?.sha256,
          canonicalSessionSha256: detail.jsonl?.canonical_session?.sha256,
          canonicalSessionBytes: detail.jsonl?.canonical_session?.bytes,
        }}
      />
      <EvidenceBlock label="Errors" value={{ ...detail.errors, event_count: errorEvents.length }} />
      <EvidenceBlock label="Audit Metadata" value={detail.metadata} />
      <EvidenceBlock label="Raw Event Timeline" value={detail.events || fallbackEvents} />
    </div>
  );
}

function eventRows(items: CodexAuditDetailEvent[]) {
  return items.map((event, index) => ({
    key: `${event.sequence ?? index}-${event.event_type || 'event'}`,
    sequence: event.sequence ?? index + 1,
    eventType: event.event_type || 'event',
    itemType: event.item_type || '—',
    error: Boolean(event.is_error),
  }));
}

/** The audit projection deliberately starts collapsed and fetches detail only on an explicit expansion. */
    const CodexAuditPanel: React.FC<{
      audit?: CodexRunAudit | null;
    }> = ({ audit }) => {
  const [activeKeys, setActiveKeys] = useState<string[]>([]);
  const [detailStates, setDetailStates] = useState<Record<string, AuditDetailState>>({});
  const [eventStates, setEventStates] = useState<Record<string, EventPageState>>({});
  const detailRequests = useRef(new Set<string>());
  const eventRequests = useRef(new Set<string>());
  const loadedDetails = useRef(new Set<string>());

  useEffect(() => {
    setActiveKeys([]);
    setDetailStates({});
    setEventStates({});
    detailRequests.current.clear();
    eventRequests.current.clear();
    loadedDetails.current.clear();
  }, [audit?.run_id]);

  if (!audit) return <Alert type="info" showIcon message="No Codex Audit evidence is recorded for this run." />;

  const invocations = audit.invocations || [];
  const detailKey = (invocation: CodexAuditInvocation) => `${audit.run_id}:${invocation.id}`;

  const loadDetail = async (invocation: CodexAuditInvocation) => {
    if (!Number.isFinite(invocation.id)) return;
    const key = detailKey(invocation);
    if (detailRequests.current.has(key) || loadedDetails.current.has(key)) return;
    detailRequests.current.add(key);
    setDetailStates((current) => ({ ...current, [key]: { status: 'loading' } }));
    try {
      const detail = await api.runs.codexAuditDetail(audit.run_id, invocation.id);
      loadedDetails.current.add(key);
      setDetailStates((current) => ({ ...current, [key]: { status: 'loaded', detail } }));
    } catch (error: unknown) {
      setDetailStates((current) => ({
        ...current,
        [key]: { status: 'error', error: error instanceof Error ? error.message : String(error) },
      }));
    } finally {
      detailRequests.current.delete(key);
    }
  };

  const loadEvents = async (invocation: CodexAuditInvocation, cursor?: string) => {
    if (!Number.isFinite(invocation.id)) return;
    const key = detailKey(invocation);
    if (eventRequests.current.has(key)) return;
    const previous = eventStates[key];
    const priorItems = cursor && previous ? previous.items : [];
    eventRequests.current.add(key);
    setEventStates((current) => ({
      ...current,
      [key]: { status: 'loading', items: priorItems, nextCursor: cursor || null },
    }));
    try {
      const page = await api.runs.codexAuditEvents(audit.run_id, invocation.id, cursor);
      setEventStates((current) => ({
        ...current,
        [key]: { status: 'loaded', items: [...priorItems, ...page.items], nextCursor: page.next_cursor },
      }));
    } catch (error: unknown) {
      setEventStates((current) => ({
        ...current,
        [key]: {
          status: 'error',
          items: priorItems,
          nextCursor: cursor || null,
          error: error instanceof Error ? error.message : String(error),
        },
      }));
    } finally {
      eventRequests.current.delete(key);
    }
  };

  const auditRows = (audit.per_slide_statuses || []).map((row, index) => ({
    key: `${String(row.run_slide_id || row.position || index)}-${index}`,
    slide: row.position || row.run_slide_id || '—',
    status: String(row.status || 'unknown'),
    attempts: row.attempt_count ?? '—',
  }));

  return (
    <div className="evidence-section-stack">
      <div className="evidence-panel-heading">
        <h4>Codex Audit</h4>
        <span>Public Native Image execution evidence for this run.</span>
      </div>
      <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
        <Descriptions.Item label="Aggregate Status">{statusTag(audit.status)}</Descriptions.Item>
        <Descriptions.Item label="Failure Count">{audit.failure_count ?? 0}</Descriptions.Item>
        <Descriptions.Item label="Attempt Count">{audit.attempt_count ?? 0}</Descriptions.Item>
        <Descriptions.Item label="Invocation Count">{audit.invocation_count ?? invocations.length}</Descriptions.Item>
        <Descriptions.Item label="Event Count">{audit.event_count ?? 0}</Descriptions.Item>
        <Descriptions.Item label="Error Event Count">{audit.error_event_count ?? 0}</Descriptions.Item>
      </Descriptions>
      {auditRows.length > 0 && (
        <Table
          size="small"
          pagination={false}
          rowKey="key"
          dataSource={auditRows}
          columns={[
            { title: 'Slide', dataIndex: 'slide' },
            { title: 'Final Status', render: (_: unknown, row: typeof auditRows[number]) => statusTag(row.status) },
            { title: 'Attempts', dataIndex: 'attempts' },
          ]}
        />
      )}
      <Collapse
        activeKey={activeKeys}
        onChange={async (keys) => {
          const nextKeys = (Array.isArray(keys) ? keys : [keys]).map(String);
          setActiveKeys(nextKeys);
          await Promise.all(nextKeys.map(async (key) => {
            const invocation = invocations.find((candidate) => String(candidate.id) === key);
            if (invocation) await loadDetail(invocation);
          }));
        }}
        items={invocations.map((invocation, index) => {
          const key = String(invocation.id || index);
          const state = detailStates[detailKey(invocation)];
          const events = eventStates[detailKey(invocation)];
          const invocationAudit = invocation.native_image;
          return {
            key,
            label: (
              <Space size={8} wrap>
                <strong>{invocation.stage_id || `Invocation ${index + 1}`}</strong>
                {statusTag(invocation.status)}
                <span>Run Slide ID {invocation.run_slide_id ?? 'Run-level'}</span>
                <span>attempt {invocation.attempt ?? '—'}</span>
                <span>{invocation.model || 'Codex Native Image'}</span>
              </Space>
            ),
            children: (
              <div className="evidence-section-stack">
                <Descriptions bordered size="small" column={1}>
                  <Descriptions.Item label="Invocation ID">{invocation.id}</Descriptions.Item>
                  <Descriptions.Item label="Run Slide ID">{invocation.run_slide_id ?? 'Run-level'}</Descriptions.Item>
                  <Descriptions.Item label="Stage">{invocation.stage_id || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Role">{invocation.role || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Model">{invocation.model || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Reasoning Effort">{invocation.reasoning_effort || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Terminal State">{invocationAudit?.terminal_state || invocation.status || '—'}</Descriptions.Item>
                  <Descriptions.Item label="Failure Code">{invocationAudit?.failure_code || '—'}</Descriptions.Item>
                  <Descriptions.Item label="PNG Valid">{invocationAudit?.business_image?.png_valid == null ? '—' : String(invocationAudit.business_image.png_valid)}</Descriptions.Item>
                </Descriptions>
                {state?.status === 'loading' && <Space><Spin size="small" />Loading Run-owned conversation detail…</Space>}
                {state?.status === 'error' && <Alert type="error" showIcon message="Conversation detail failed to load" description={state.error} />}
                {state?.status === 'loaded' && auditDetailText(state.detail, invocation.events)}
                {!state && <div className="evidence-muted">Expand this invocation to load its Run-owned conversation detail.</div>}
                <section className="evidence-readable-section">
                  <h4>Raw Event Timeline</h4>
                  <Space size={8} wrap>
                    <Button
                      size="small"
                      loading={events?.status === 'loading'}
                      onClick={() => void loadEvents(invocation)}
                    >
                      {events?.items.length ? 'Reload first event page' : 'Load event timeline'}
                    </Button>
                    {events?.nextCursor && (
                      <Button
                        size="small"
                        loading={events.status === 'loading'}
                        onClick={() => void loadEvents(invocation, events.nextCursor || undefined)}
                      >
                        Load next page
                      </Button>
                    )}
                  </Space>
                  {events?.status === 'error' && <Alert type="error" showIcon message="Event timeline failed to load" description={events.error} />}
                  {events?.items.length ? (
                    <div className="public-audit-events-table">
                      <Table
                        size="small"
                        pagination={false}
                        rowKey="key"
                        dataSource={eventRows(events.items)}
                        columns={[
                          { title: '#', dataIndex: 'sequence', width: 52 },
                          { title: 'Event Type', dataIndex: 'eventType' },
                          { title: 'Item Type', dataIndex: 'itemType' },
                          { title: 'Error', width: 64, render: (_: unknown, row: ReturnType<typeof eventRows>[number]) => row.error ? <Tag color="error">yes</Tag> : <Tag>no</Tag> },
                        ]}
                      />
                    </div>
                  ) : (
                    <div className="evidence-muted">Request a bounded page only when the timeline is needed.</div>
                  )}
                </section>
              </div>
            ),
          };
        })}
      />
    </div>
  );
};

type PublicEvidenceRun = {
  id?: number;
  status?: string | null;
  engine?: string | null;
  strategy?: string | null;
  stage_artifacts?: Record<string, unknown> | null;
};

type PublicEvidenceProps = {
  slide: RunSlide;
  // Keep compatibility with retained internal callers without projecting
  // their richer DTO fields into the public panel.
  run: PublicEvidenceRun | (PublicEvidenceRun & Record<string, unknown>);
  inline?: boolean;
  contextTags?: React.ReactNode;
  onDownloadEvidence?: (slide: RunSlide) => void;
  [key: string]: unknown;
};

/** Shared read-only evidence panel retained for History and other legacy callers. */
export const RunSlideEvidencePanel: React.FC<PublicEvidenceProps> = ({ slide, run, inline = false, contextTags, onDownloadEvidence }) => {
  const slideEvidence = slide.stage_artifacts || slide.seed_dependency;
  const requestChain = findByKey(slideEvidence, /request[_ -]?chain/i);
  const seedLineage = slide.position === 2 ? publicPositionTwoSeedEvidence(run, slide) : undefined;
  const seed = slide.position === 2 ? seedLineage : slide.seed_dependency;
  const palette = seedLineage;
  const content = (
    <div className="run-detail-evidence-panel-body">
      <div className="run-detail-evidence-header">
        <strong>Evidence For Slide {slide.position}</strong>
        <Space size={6} wrap>{contextTags}<Tag>Image 3.0</Tag>{statusTag(slide.status)}</Space>
      </div>
      {slide.error_message && <Alert type="error" showIcon message="Selected Slide Failure" description={slide.error_message} />}
      <Descriptions bordered size="small" column={1}>
        <Descriptions.Item label="Run ID">{run.id ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="Run Slide ID">{slide.id}</Descriptions.Item>
        <Descriptions.Item label="Position">{slide.position}</Descriptions.Item>
        <Descriptions.Item label="PNG available">{slide.final_image_path ? 'yes' : 'no'}</Descriptions.Item>
      </Descriptions>
      <EvidenceBlock label="Request Chain" value={requestChain || slideEvidence} />
      <EvidenceBlock label="Seed dependency" value={seed} />
      <EvidenceBlock label="Palette lineage" value={palette} />
      {slide.final_image_path && <SlideFrame slide={slide} />}
      {onDownloadEvidence && slide.final_image_path && (
        <Button icon={<DownloadOutlined />} onClick={() => onDownloadEvidence(slide)}>Download PNG Evidence</Button>
      )}
    </div>
  );
  return inline ? (
    <section className="run-detail-evidence-panel run-detail-inline-evidence" aria-label={`Evidence for slide ${slide.position}`}>
      {content}
    </section>
  ) : <Card className="run-detail-evidence-panel">{content}</Card>;
};

const RunDetailView: React.FC<RunDetailProps> = ({ runId }) => {
  const navigate = useNavigate();
  const [run, setRun] = useState<RunDetailType | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedSlideId, setSelectedSlideId] = useState<number | null>(null);
  const [downloading, setDownloading] = useState(false);

  const fetchRun = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const result = await api.runs.get(runId);
      setRun(result);
      setSelectedSlideId((current) => result.slides?.some((slide) => slide.id === current) ? current : null);
    } catch (error: unknown) {
      message.error(`Failed to load run: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    queueMicrotask(() => void fetchRun());
  }, [fetchRun]);

  useEffect(() => {
    if (!run || TERMINAL.has(run.status)) return undefined;
    const timer = window.setInterval(() => void fetchRun(true), 3000);
    return () => window.clearInterval(timer);
  }, [fetchRun, run]);

  const slides = useMemo(() => [...(run?.slides || [])].sort((left, right) => left.position - right.position), [run?.slides]);
  const selectedSlide = slides.find((slide) => slide.id === selectedSlideId) || slides[0];
  const seedSlide = slides.find((slide) => slide.position === 2);
  const seedEvidence = publicPositionTwoSeedEvidence(run, seedSlide);
  const paletteEvidence = seedEvidence;
  const requestChain = findByKey(selectedSlide?.stage_artifacts, /request[_ -]?chain/i)
    || findByKey(run?.stage_artifacts, /request[_ -]?chain/i);

  const downloadRun = async () => {
    setDownloading(true);
    try {
      const result = await api.runs.download(runId);
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
    } catch (error: unknown) {
      message.error(`Run download failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      setDownloading(false);
    }
  };

  if (loading && !run) return <Spin spinning style={{ display: 'block', marginTop: 100, textAlign: 'center' }} />;
  if (!run) return <Empty description="Run not found" />;

  return (
    <div className="run-detail-page public-image-run-detail">
      <div className="page-toolbar run-detail-page-toolbar">
        <div>
          <div className="page-kicker"><span className={`run-status-dot ${run.status}`} />Image PPT 3.0 · Run #{run.id}</div>
          <h2>Run #{run.id} Detail</h2>
          <p className="toolbar-subtitle">Read-only Image 3.0 generation evidence.</p>
        </div>
        <Space wrap>
          <Button aria-label="Back to History" icon={<ArrowLeftOutlined />} onClick={() => navigate('/history')}>Back to History</Button>
          <Button aria-label="Refresh run detail" icon={<ReloadOutlined />} onClick={() => void fetchRun()}>Refresh</Button>
          <Button aria-label={`Download run ${run.id}`} icon={<DownloadOutlined />} onClick={() => void downloadRun()} loading={downloading}>Run ZIP</Button>
        </Space>
      </div>

      <section className="run-route-flow-panel" aria-label="Image 3.0 Run Status">
        <div className="section-heading compact"><h3>Image 3.0 Run Status</h3>{statusTag(run.status)}</div>
        <Descriptions bordered size="small" column={{ xs: 1, sm: 2 }}>
          <Descriptions.Item label="Run ID">{run.id}</Descriptions.Item>
          <Descriptions.Item label="Deck">{run.deck_title || run.deck_id}</Descriptions.Item>
          <Descriptions.Item label="Config">{run.config_name || run.config_id}</Descriptions.Item>
          <Descriptions.Item label="Engine">{run.engine || 'image'}</Descriptions.Item>
          <Descriptions.Item label="Route">Image 3.0</Descriptions.Item>
          <Descriptions.Item label="Progress">{run.progress ? `${run.progress.completed}/${run.progress.total}` : '—'}</Descriptions.Item>
        </Descriptions>
        {run.status === 'completed_with_failures' && run.error_message && (
          <Alert type="warning" showIcon message="Run completed with partial results" description={run.error_message} />
        )}
        {run.status !== 'completed_with_failures' && run.error_message && (
          <Alert type="error" showIcon message="Run error" description={run.error_message} />
        )}
      </section>

      <div className="run-detail-toolbar"><strong>Generated Outputs</strong><span>PNG previews and status are read-only.</span></div>
      <div className="slide-tile-grid public-image-slide-grid">
        {slides.map((slide) => (
          <Card
            key={slide.id}
            className={`slide-tile ${selectedSlide?.id === slide.id ? 'selected' : ''}`}
            onClick={() => setSelectedSlideId(slide.id)}
            title={<Space><span>Slide {slide.position}: {slide.slide_title || 'Untitled'}</span>{statusTag(slide.status)}</Space>}
          >
            <SlideFrame slide={slide} />
          </Card>
        ))}
      </div>

      <section className="run-route-flow-panel" aria-label="Seed and palette lineage">
        <div className="section-heading compact"><h3>Reference Input Map</h3><Tag color="gold">Image 3.0</Tag></div>
        <div className="evidence-section-stack">
          <EvidenceBlock label="Position 2 Seed" value={seedEvidence} />
          <EvidenceBlock label="Palette lineage" value={paletteEvidence} />
          <EvidenceBlock label="Request Chain" value={requestChain} />
        </div>
      </section>

      {run.codex_audit && (
        <section className="run-route-flow-panel run-codex-audit-panel" aria-label="Codex Audit">
          <CodexAuditPanel audit={run.codex_audit} />
        </section>
      )}

      {selectedSlide && (
        <RunSlideEvidencePanel
          slide={selectedSlide}
          run={run}
          onDownloadEvidence={(slide) => {
            void api.runSlides.evidenceDownload(slide.id).then((result) => {
              const url = URL.createObjectURL(result.blob);
              const anchor = document.createElement('a');
              anchor.href = url;
              anchor.download = result.filename;
              document.body.appendChild(anchor);
              anchor.click();
              anchor.remove();
              URL.revokeObjectURL(url);
            }).catch((error: unknown) => {
              message.error(`PNG evidence download failed: ${error instanceof Error ? error.message : String(error)}`);
            });
          }}
        />
      )}
    </div>
  );
};

export default RunDetailView;
