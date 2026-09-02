import React, { useCallback, useEffect, useState } from 'react';
import { Button, Card, Empty, Input, Progress, Select, Space, Table, Tag, message } from 'antd';
import { DownloadOutlined, ReloadOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { RunFailBreakdownItem, RunFailDiagnosticItem, RunFailStats, RunFailTrendItem } from '../types';

type RunFailRouteType = 'all' | 'html' | 'image';
type RunFailDatePreset = 'today' | 'yesterday' | 'last_7_days' | 'last_month' | 'this_year' | 'custom';

const downloadBlob = (result: { blob: Blob; filename: string }) => {
  const url = URL.createObjectURL(result.blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = result.filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
};

const BreakdownTable: React.FC<{ title: string; labelKey: keyof RunFailBreakdownItem; rows: RunFailBreakdownItem[] }> = ({ title, labelKey, rows }) => (
  <Card title={title} className="runfail-breakdown-card">
    <Table
      size="small"
      pagination={false}
      rowKey={(record) => `${title}-${String(record[labelKey])}`}
      dataSource={rows}
      locale={{ emptyText: <Empty description="No failed or timed out runs" /> }}
      columns={[
        { title: title.replace('By ', ''), key: 'label', render: (_: unknown, record: RunFailBreakdownItem) => <Tag>{String(record[labelKey] || 'unknown')}</Tag> },
        { title: 'Count', dataIndex: 'count', key: 'count', width: 90 },
        { title: 'Percent', key: 'percent', width: 110, render: (_: unknown, record: RunFailBreakdownItem) => `${record.percent}%` },
      ]}
    />
  </Card>
);

const TrendTable: React.FC<{ rows: RunFailTrendItem[] }> = ({ rows }) => (
  <Card title="Failure Trend" className="runfail-breakdown-card">
    <Table
      size="small"
      pagination={false}
      rowKey={(record) => record.window}
      dataSource={rows}
      locale={{ emptyText: <Empty description="No run trend data" /> }}
      columns={[
        { title: 'Window', dataIndex: 'window', key: 'window' },
        { title: 'Failed / Timed Out', dataIndex: 'failed_or_timed_out', key: 'failed_or_timed_out', width: 140 },
        { title: 'Total', dataIndex: 'total_runs', key: 'total_runs', width: 90 },
        {
          title: 'Rate',
          key: 'failure_rate',
          width: 150,
          render: (_: unknown, record: RunFailTrendItem) => (
            <Progress percent={record.failure_rate} size="small" status={record.failure_rate > 0 ? 'exception' : 'success'} />
          ),
        },
      ]}
    />
  </Card>
);

const DiagnosticsTable: React.FC<{ rows: RunFailDiagnosticItem[] }> = ({ rows }) => (
  <Table
    size="small"
    pagination={false}
    dataSource={rows}
    rowKey="key"
    expandable={{
      rowExpandable: (record) => record.raw_messages.length > 0,
      expandedRowRender: (record) => (
        <div className="runfail-raw-messages" aria-label={`Raw messages for ${record.key}`}>
          {record.raw_messages.map((messageText, index) => (
            <pre key={`${record.key}-${index}`}>{messageText}</pre>
          ))}
        </div>
      ),
    }}
    columns={[
      { title: 'Signal', dataIndex: 'key', key: 'key', render: (value: string) => <Tag>{value}</Tag> },
      { title: 'Count', dataIndex: 'count', key: 'count', width: 90 },
      { title: 'Percent', dataIndex: 'percent', key: 'percent', width: 100, render: (value: number) => `${value}%` },
      { title: 'Recommended Action', dataIndex: 'recommended_action', key: 'recommended_action', width: 160 },
      { title: 'Insight', dataIndex: 'insight', key: 'insight' },
    ]}
  />
);

const RunFailStatsPage: React.FC = () => {
  const [stats, setStats] = useState<RunFailStats | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState<'json' | 'csv' | null>(null);
  const [routeType, setRouteType] = useState<RunFailRouteType>('all');
  const [datePreset, setDatePreset] = useState<RunFailDatePreset>('today');
  const [customStart, setCustomStart] = useState('');
  const [customEnd, setCustomEnd] = useState('');

  const buildFilters = useCallback(() => ({
    route_type: routeType,
    date_preset: datePreset,
    start_date: datePreset === 'custom' ? customStart : undefined,
    end_date: datePreset === 'custom' ? customEnd : undefined,
  }), [customEnd, customStart, datePreset, routeType]);

  const fetchStats = useCallback(async () => {
    setLoading(true);
    try {
      setStats(await api.runFail.stats(buildFilters()));
    } catch (err: unknown) {
      message.error(`Failed to load RunFail stats: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [buildFilters]);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchStats();
    });
  }, [fetchStats]);

  const exportReport = async (format: 'json' | 'csv') => {
    setExporting(format);
    try {
      downloadBlob(await api.runFail.export(format, buildFilters()));
      message.success(`RunFail ${format.toUpperCase()} export started`);
    } catch (err: unknown) {
      message.error(`RunFail export failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setExporting(null);
    }
  };

  return (
    <div className="runfail-page">
      <div className="page-toolbar">
        <div>
          <div className="page-kicker"><span className="status-dot" />Phase A fixed aggregation</div>
          <h2>RunFail Stats</h2>
          <p className="toolbar-subtitle">Backend-derived failure totals, route/model/status/error breakdowns, and secret-safe exports.</p>
        </div>
        <Space className="page-toolbar-actions" wrap>
          <Button icon={<ReloadOutlined />} onClick={fetchStats} loading={loading}>Refresh</Button>
          <Button icon={<DownloadOutlined />} onClick={() => exportReport('json')} loading={exporting === 'json'}>Export JSON</Button>
          <Button icon={<DownloadOutlined />} onClick={() => exportReport('csv')} loading={exporting === 'csv'}>Export CSV</Button>
        </Space>
      </div>

      <Card className="runfail-filter-card" aria-label="RunFail filters">
        <Space wrap align="end">
          <div className="runfail-filter-field">
            <span>Type</span>
            <Select
              aria-label="RunFail type filter"
              value={routeType}
              onChange={setRouteType}
              style={{ width: 150 }}
              options={[
                { label: 'All types', value: 'all' },
                { label: 'HTML', value: 'html' },
                { label: 'Image', value: 'image' },
              ]}
            />
          </div>
          <div className="runfail-filter-field">
            <span>Time range</span>
            <Select
              aria-label="RunFail time range"
              value={datePreset}
              onChange={setDatePreset}
              style={{ width: 170 }}
              options={[
                { label: 'Today', value: 'today' },
                { label: 'Yesterday', value: 'yesterday' },
                { label: 'Last 7 days', value: 'last_7_days' },
                { label: 'Last 30 days', value: 'last_month' },
                { label: 'This year', value: 'this_year' },
                { label: 'Custom', value: 'custom' },
              ]}
            />
          </div>
          {datePreset === 'custom' && (
            <>
              <div className="runfail-filter-field">
                <span>Start</span>
                <Input
                  aria-label="RunFail start date"
                  type="date"
                  value={customStart}
                  onChange={(event) => setCustomStart(event.target.value)}
                />
              </div>
              <div className="runfail-filter-field">
                <span>End</span>
                <Input
                  aria-label="RunFail end date"
                  type="date"
                  value={customEnd}
                  onChange={(event) => setCustomEnd(event.target.value)}
                />
              </div>
            </>
          )}
          <Button onClick={fetchStats} loading={loading}>Apply</Button>
        </Space>
        {stats && (
          <div className="runfail-filter-summary">
            <Tag color="blue">{stats.filters.timezone}</Tag>
            <Tag>{stats.filters.start_date} to {stats.filters.end_date}</Tag>
            <Tag>{stats.filters.route_type}</Tag>
          </div>
        )}
      </Card>

      <div className="runfail-summary-grid">
        <Card>
          <span className="module-summary-label">Total Runs</span>
          <strong>{stats?.total_runs ?? '-'}</strong>
          <Tag>window: {stats?.window || 'all'}</Tag>
        </Card>
        <Card>
          <span className="module-summary-label">Failed / Timed Out</span>
          <strong>{stats?.failed_or_timed_out ?? '-'}</strong>
          <Tag color={(stats?.failed_or_timed_out || 0) > 0 ? 'error' : 'success'}>terminal failures</Tag>
        </Card>
        <Card>
          <span className="module-summary-label">Failure Rate</span>
          <strong>{stats ? `${stats.failure_rate}%` : '-'}</strong>
          <Progress percent={stats?.failure_rate || 0} status={(stats?.failure_rate || 0) > 0 ? 'exception' : 'success'} />
        </Card>
      </div>

      <div className="runfail-breakdown-grid">
        <BreakdownTable title="By Type" labelKey="route_type" rows={stats?.by_route_type || []} />
        <BreakdownTable title="By Route" labelKey="route" rows={stats?.by_route || []} />
        <BreakdownTable title="By Mode" labelKey="mode" rows={stats?.by_mode || []} />
        <BreakdownTable title="By Status" labelKey="status" rows={stats?.by_status || []} />
        <BreakdownTable title="By Error Class" labelKey="error_class" rows={stats?.by_error_class || []} />
        <BreakdownTable title="By Model" labelKey="model" rows={stats?.by_model || []} />
        <BreakdownTable title="By Retry Signal" labelKey="retry_signal" rows={stats?.by_retry_signal || []} />
        <TrendTable rows={stats?.trend || []} />
        <DiagnosticsTable rows={stats?.diagnostics || []} />
      </div>
    </div>
  );
};

export default RunFailStatsPage;
