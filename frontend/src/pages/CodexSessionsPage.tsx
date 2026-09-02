import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Empty,
  Form,
  Input,
  List,
  Segmented,
  Space,
  Spin,
  Tag,
  Typography,
} from 'antd';

import { ApiError, api } from '../api';
import type {
  CodexSessionCoreConclusion,
  CodexSessionEnvelope,
  CodexSessionItem,
  CodexSessionLevel,
} from '../types';
import './CodexSessionsPage.css';

const { Text, Paragraph } = Typography;
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

type PageRequest = () => Promise<void>;

function isCodexSessionId(value: string): boolean {
  return UUID_PATTERN.test(value.trim());
}

function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 400) return 'Invalid session ID, cursor, or page selector.';
    if (error.status === 403) return 'The private Codex session reader is disabled or unavailable from this client.';
    if (error.status === 404) return 'The requested Codex session was not found.';
    if (error.status === 409) return 'The session source changed while this page was loading. Reload the summary before paging again.';
  }
  return error instanceof Error ? error.message : 'The page could not load. Try the request again.';
}

function itemLabel(item: CodexSessionItem): string {
  return [item.kind, item.role, item.tool_name, item.phase].filter(Boolean).join(' · ') || 'effective event';
}

function rangeLabel(item: CodexSessionItem): string | null {
  return typeof item.range_start === 'number' && typeof item.range_end === 'number'
    ? `${item.range_start}–${item.range_end}`
    : null;
}

function previewSourceLabel(item: CodexSessionItem): string {
  if (item.preview_source === 'text') return 'Message text preview';
  if (item.preview_source === 'output') return 'Tool output preview';
  if (item.preview_source === 'input') return 'Tool input preview';
  return 'No persisted text fragment';
}

function previewText(item: CodexSessionItem): string {
  if (typeof item.preview === 'string' && item.preview) return item.preview;
  if (item.preview_reason === 'no_persisted_fragment') return 'No persisted text fragment is available for this effective event.';
  return 'This effective event has no bounded preview.';
}

function hasPositiveSequence(item: CodexSessionItem): item is CodexSessionItem & { sequence: number } {
  return typeof item.sequence === 'number' && Number.isInteger(item.sequence) && item.sequence > 0;
}

function l1CoreConclusion(payload: CodexSessionEnvelope | null): CodexSessionCoreConclusion | null {
  if (payload?.level !== 'L1') return null;
  return payload.items.find((item) => item.kind === 'summary')?.core_conclusion ?? null;
}

function l1SummaryItem(payload: CodexSessionEnvelope | null): CodexSessionItem | null {
  if (payload?.level !== 'L1') return null;
  return payload.items.find((item) => item.kind === 'summary') ?? null;
}

const CodexSessionsPage: React.FC = () => {
  const [form] = Form.useForm<{ sessionId: string }>();
  const [sessionId, setSessionId] = useState('');
  const [level, setLevel] = useState<CodexSessionLevel>('L1');
  const [currentPayload, setCurrentPayload] = useState<CodexSessionEnvelope | null>(null);
  const [previousCursors, setPreviousCursors] = useState<Array<string | null>>([]);
  const [activeCursor, setActiveCursor] = useState<string | undefined>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rawPayload, setRawPayload] = useState<CodexSessionEnvelope | null>(null);
  const [rawDrawerOpen, setRawDrawerOpen] = useState(false);
  const [lastRequest, setLastRequest] = useState<PageRequest | null>(null);
  const [selectedSequence, setSelectedSequence] = useState<number | null>(null);
  const resultHeadingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    if (currentPayload) resultHeadingRef.current?.focus();
  }, [currentPayload]);

  const clearSessionState = useCallback((nextSessionId: string) => {
    setSessionId(nextSessionId);
    setLevel('L1');
    setCurrentPayload(null);
    setPreviousCursors([]);
    setActiveCursor(undefined);
    setRawPayload(null);
    setRawDrawerOpen(false);
    setSelectedSequence(null);
    setLastRequest(null);
    setError(null);
  }, []);

  const runRequest = useCallback(async (request: PageRequest) => {
    setLoading(true);
    setError(null);
    setLastRequest(() => request);
    try {
      await request();
    } catch (requestError: unknown) {
      setError(describeError(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  const requestLevel = useCallback(async (
    requestedLevel: 'L1' | 'L2',
    cursor?: string,
    appendHistory = false,
  ) => {
    if (!sessionId) {
      setError('Enter and load an exact session ID before requesting another level.');
      return;
    }
    const request = async () => {
      const payload = requestedLevel === 'L1'
        ? await api.codexSessions.summary(sessionId)
        : await api.codexSessions.index(sessionId, cursor);
      setCurrentPayload(payload);
      setLevel(requestedLevel);
      setActiveCursor(cursor);
      setSelectedSequence(null);
      setRawPayload(null);
      setRawDrawerOpen(false);
      if (appendHistory) setPreviousCursors((history) => [...history, activeCursor ?? null]);
      if (!appendHistory && !cursor) setPreviousCursors([]);
    };
    await runRequest(request);
  }, [activeCursor, runRequest, sessionId]);

  const loadSummary = useCallback(async (values: { sessionId?: string }) => {
    const nextSessionId = values.sessionId?.trim() || '';
    if (!isCodexSessionId(nextSessionId)) {
      clearSessionState('');
      setError('Enter an exact UUID session ID. File paths and query strings are not accepted.');
      return;
    }
    clearSessionState(nextSessionId);
    const request = async () => {
      const payload = await api.codexSessions.summary(nextSessionId);
      setCurrentPayload(payload);
      setLevel('L1');
    };
    await runRequest(request);
  }, [clearSessionState, runRequest]);

  const loadNext = useCallback(async () => {
    if (!currentPayload?.next_cursor || level !== 'L2') return;
    await requestLevel('L2', currentPayload.next_cursor, true);
  }, [currentPayload, level, requestLevel]);

  const loadPrevious = useCallback(async () => {
    if (!previousCursors.length || level !== 'L2') return;
    const cursor = previousCursors[previousCursors.length - 1] || undefined;
    setPreviousCursors((history) => history.slice(0, -1));
    await requestLevel('L2', cursor);
  }, [level, previousCursors, requestLevel]);

  const loadDetailForItem = useCallback(async (item: CodexSessionItem) => {
    if (!sessionId || !hasPositiveSequence(item)) {
      setError('Choose an L2 event with a valid positive sequence before loading L3 detail.');
      return;
    }
    const request = async () => {
      const payload = await api.codexSessions.detail(sessionId, item.sequence);
      setCurrentPayload(payload);
      setLevel('L3');
      setSelectedSequence(item.sequence);
      setPreviousCursors([]);
      setActiveCursor(undefined);
      setRawPayload(null);
      setRawDrawerOpen(false);
    };
    await runRequest(request);
  }, [runRequest, sessionId]);

  const rawCursor = currentPayload?.level === 'L3'
    ? currentPayload.items.find((item) => typeof item.raw_cursor === 'string' && item.raw_cursor)?.raw_cursor
    : undefined;

  const loadRaw = useCallback(async () => {
    if (!sessionId || !rawCursor) return;
    const request = async () => {
      const payload = await api.codexSessions.raw(sessionId, rawCursor);
      setRawPayload(payload);
      setRawDrawerOpen(true);
    };
    await runRequest(request);
  }, [rawCursor, runRequest, sessionId]);

  const changeLevel = useCallback(async (nextLevel: string | number) => {
    const requestedLevel = nextLevel as Exclude<CodexSessionLevel, 'L4'>;
    if (requestedLevel === 'L3') {
      setError('Choose an L2 event and use its View detail action before loading L3 detail.');
      return;
    }
    if (requestedLevel === 'L1' || requestedLevel === 'L2') {
      await requestLevel(requestedLevel);
    }
  }, [requestLevel]);

  const retryCurrentRequest = useCallback(async () => {
    if (lastRequest) await runRequest(lastRequest);
  }, [lastRequest, runRequest]);

  const pageRange = currentPayload?.items.length
    ? `${currentPayload.items.length} item${currentPayload.items.length === 1 ? '' : 's'} on this page`
    : 'No projected items on this page';
  const coreConclusion = l1CoreConclusion(currentPayload);
  const summaryItem = l1SummaryItem(currentPayload);

  return (
    <div className="work-surface codex-sessions-page">
      <div className="page-toolbar codex-sessions-toolbar">
        <div>
          <h2>Codex Sessions</h2>
          <Text type="secondary">Private local reader · effective projection · maximum response 30 KiB</Text>
        </div>
      </div>

      <Card className="codex-sessions-load-card">
        <Form form={form} layout="vertical" onFinish={loadSummary} initialValues={{ sessionId }}>
          <div className="codex-sessions-load-row">
            <Form.Item
              name="sessionId"
              label="Session ID"
              rules={[{ required: true, message: 'Enter an exact UUID session ID' }]}
            >
              <Input
                aria-label="Exact Codex session ID"
                placeholder="019f8d16-9519-74f1-9e4d-d0cd55eb1d81"
                autoComplete="off"
              />
            </Form.Item>
            <Button className="codex-session-primary" type="primary" htmlType="submit" disabled={loading}>
              Load
            </Button>
          </div>
        </Form>
      </Card>

      {error ? (
        <Alert
          className="codex-sessions-alert"
          role="alert"
          type="error"
          showIcon
          message={error}
          action={<Button aria-label="Retry current request" onClick={retryCurrentRequest}>Retry</Button>}
        />
      ) : null}

      <Card className="codex-sessions-result-card">
        <div className="codex-sessions-result-heading">
          <div>
            <h3 ref={resultHeadingRef} tabIndex={-1}>Source and layered result</h3>
            <div className="codex-sessions-live" aria-live="polite">
              {loading ? 'Loading current page…' : currentPayload ? `${currentPayload.level} loaded: ${pageRange}` : 'Load a session to begin.'}
            </div>
          </div>
          {currentPayload ? <Tag color={currentPayload.truncated ? 'gold' : 'blue'}>{currentPayload.truncated ? 'Truncated page' : 'Bounded page'}</Tag> : null}
        </div>

        {loading ? <div className="codex-sessions-loading"><Spin /><span>Reserving the result region while the current request loads.</span></div> : null}

        {currentPayload ? (
          <>
            <Descriptions className="codex-sessions-metadata" size="small" column={{ xs: 1, sm: 2, md: 4 }}>
              <Descriptions.Item label="Session"><span className="codex-sessions-wrap">{currentPayload.session_id}</span></Descriptions.Item>
              <Descriptions.Item label="Source bytes">{currentPayload.source.bytes.toLocaleString()}</Descriptions.Item>
              <Descriptions.Item label="Projection">{currentPayload.source.projection_version}</Descriptions.Item>
              <Descriptions.Item label="SHA-256"><span className="codex-sessions-wrap">{currentPayload.source.sha256}</span></Descriptions.Item>
            </Descriptions>

            {summaryItem ? (
              <Descriptions className="codex-sessions-summary" size="small" column={{ xs: 1, sm: 3 }}>
                <Descriptions.Item label="Messages">{summaryItem.message_count ?? 0}</Descriptions.Item>
                <Descriptions.Item label="Tools">{summaryItem.tool_count ?? 0}</Descriptions.Item>
                <Descriptions.Item label="Indexed events">{summaryItem.indexed_events ?? 0}</Descriptions.Item>
              </Descriptions>
            ) : null}

            {coreConclusion ? (
              <Alert
                className="codex-sessions-conclusion"
                type="info"
                showIcon
                message="L1 core conclusion"
                description={`${coreConclusion.role ?? 'message'} · ${coreConclusion.text}`}
              />
            ) : null}

            <Segmented
              aria-label="Codex session result level"
              className="codex-sessions-levels"
              value={level}
              options={[
                { label: 'L1 Summary', value: 'L1' },
                { label: 'L2 Coverage', value: 'L2' },
                { label: 'L3 Detail', value: 'L3' },
              ]}
              onChange={changeLevel}
            />

            {currentPayload.level === 'L2' && currentPayload.items.length ? (
              <List className="codex-sessions-list" size="small">
                {currentPayload.items.map((item, index) => (
                  <List.Item key={`${item.sequence ?? index}-${itemLabel(item)}`}>
                    <div className="codex-sessions-item">
                      <div className="codex-sessions-item-meta">
                        <Space wrap size={6}>
                          <Tag>{itemLabel(item)}</Tag>
                          {item.timestamp ? <Text type="secondary">{item.timestamp}</Text> : null}
                          {rangeLabel(item) ? <Text type="secondary" className="codex-sessions-wrap">range {rangeLabel(item)}</Text> : null}
                          <Text type="secondary">{previewSourceLabel(item)}</Text>
                          {item.truncated ? <Tag color="gold">Truncated event</Tag> : null}
                        </Space>
                        <Paragraph ellipsis={{ rows: 3, expandable: 'collapsible' }}>{previewText(item)}</Paragraph>
                      </div>
                      <Button
                        aria-label="Load exact selected event"
                        disabled={!hasPositiveSequence(item) || loading}
                        onClick={() => void loadDetailForItem(item)}
                      >
                          View detail
                      </Button>
                    </div>
                  </List.Item>
                ))}
              </List>
            ) : currentPayload.level === 'L2' && !loading ? <Empty description="This bounded page contains no effective projected items." /> : null}

            {currentPayload.level === 'L3' && currentPayload.items.length ? (
              <List className="codex-sessions-list" size="small">
                {currentPayload.items.slice(0, 1).map((item, index) => (
                  <List.Item key={`${item.sequence ?? selectedSequence ?? index}-${itemLabel(item)}`}>
                    <div className="codex-sessions-item">
                      <div className="codex-sessions-item-meta">
                        <Space wrap size={6}>
                          <Tag>{itemLabel(item)}</Tag>
                          <Text type="secondary">Selected event #{item.sequence ?? selectedSequence ?? 'unavailable'}</Text>
                          {item.timestamp ? <Text type="secondary">{item.timestamp}</Text> : null}
                          {rangeLabel(item) ? <Text type="secondary" className="codex-sessions-wrap">range {rangeLabel(item)}</Text> : null}
                          {item.truncated ? <Tag color="gold">Truncated event</Tag> : null}
                        </Space>
                        {typeof item.text === 'string' ? (
                          <div className="codex-sessions-detail-field">
                            <Text strong>Message text</Text>
                            <Paragraph className="codex-sessions-wrap">{item.text || 'The selected message has an empty persisted text fragment.'}</Paragraph>
                          </div>
                        ) : null}
                        {typeof item.input === 'string' ? (
                          <div className="codex-sessions-detail-field">
                            <Text strong>Tool input</Text>
                            <Paragraph className="codex-sessions-wrap">{item.input || 'The selected tool input is empty.'}</Paragraph>
                          </div>
                        ) : null}
                        {typeof item.output === 'string' ? (
                          <div className="codex-sessions-detail-field">
                            <Text strong>Tool output</Text>
                            <Paragraph className="codex-sessions-wrap">{item.output || 'The selected tool output is empty.'}</Paragraph>
                          </div>
                        ) : null}
                        {typeof item.text !== 'string' && typeof item.input !== 'string' && typeof item.output !== 'string' ? (
                          <Empty description="The selected effective event has no persisted message, tool input, or tool output." />
                        ) : null}
                      </div>
                    </div>
                  </List.Item>
                ))}
              </List>
            ) : currentPayload.level === 'L3' && !loading ? <Empty description="No selected effective event was returned for this exact sequence." /> : null}

            {currentPayload.level === 'L2' ? (
              <div className="codex-sessions-paging">
                <Button aria-label="Load previous page" disabled={!previousCursors.length || loading} onClick={() => void loadPrevious()}>
                  Previous
                </Button>
                <Text type="secondary">{pageRange}</Text>
                <Button aria-label="Load next page" disabled={!currentPayload.next_cursor || loading} onClick={() => void loadNext()}>
                  Next
                </Button>
              </div>
            ) : null}

            {rawCursor ? (
              <div className="codex-sessions-raw-action">
                <Button aria-label="View raw range" disabled={loading} onClick={() => void loadRaw()}>
                  View raw range
                </Button>
                <Text type="secondary">Raw text stays closed until this explicit action.</Text>
              </div>
            ) : <Text type="secondary">Raw text becomes available only for an L3 item with a signed raw cursor.</Text>}
          </>
        ) : null}
      </Card>

      <Drawer title="L4 private raw range" open={rawDrawerOpen} onClose={() => setRawDrawerOpen(false)} width={560}>
        {rawPayload ? (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            {rawPayload.items.map((item, index) => (
              <Card size="small" key={`${item.sha256 ?? index}-${rangeLabel(item) ?? 'raw'}`}>
                <Text className="codex-sessions-wrap">range {rangeLabel(item) ?? 'unavailable'}</Text>
                {item.sha256 ? <Text className="codex-sessions-wrap" type="secondary">SHA-256 {item.sha256}</Text> : null}
                <Paragraph className="codex-sessions-raw-text">{typeof item.text === 'string' ? item.text : 'No raw text was returned for this bounded range.'}</Paragraph>
              </Card>
            ))}
          </Space>
        ) : <Empty description="No raw range has been requested." />}
      </Drawer>
    </div>
  );
};

export default CodexSessionsPage;
