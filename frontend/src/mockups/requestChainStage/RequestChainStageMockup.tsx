import React, { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Collapse,
  Descriptions,
  Drawer,
  Layout,
  Menu,
  Segmented,
  Select,
  Slider,
  Space,
  Tabs,
  Tag,
  Tooltip,
} from 'antd';
import {
  ArrowLeftOutlined,
  BarChartOutlined,
  CheckCircleOutlined,
  CodeOutlined,
  DatabaseOutlined,
  DownloadOutlined,
  FileTextOutlined,
  HistoryOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PictureOutlined,
  ReloadOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { mockRuns, type MockRun, type SlideEvidence, type StageEvidence, type StageHealth } from './fixtures';

const { Sider, Content } = Layout;

function pretty(value: unknown) {
  if (typeof value === 'string') return value;
  return JSON.stringify(value, null, 2);
}

function healthColor(health: StageHealth) {
  if (health === 'complete') return 'success';
  if (health === 'failed') return 'error';
  if (health === 'skipped') return 'default';
  return 'warning';
}

const navItems = [
  { key: '/data', icon: <DatabaseOutlined />, label: 'Data' },
  { key: '/generate', icon: <ThunderboltOutlined />, label: 'Generate' },
  { key: '/history', icon: <HistoryOutlined />, label: 'History' },
  { key: '/runfail', icon: <BarChartOutlined />, label: 'RunFail Stats' },
  { key: '/prompts', icon: <FileTextOutlined />, label: 'Prompts' },
  { key: '/config', icon: <SettingOutlined />, label: 'Config' },
];

const CodeBlock: React.FC<{ label: string; value: unknown }> = ({ label, value }) => (
  <div className="rcm-code">
    <div className="rcm-code-title">{label}</div>
    <pre>{pretty(value)}</pre>
  </div>
);

const StageAuditCard: React.FC<{
  stage: StageEvidence;
  index: number;
  onInspect: (title: string, value: unknown) => void;
}> = ({ stage, index, onInspect }) => (
  <section className="rcm-stage-card">
    <div className="rcm-stage-card-head">
      <div>
        <span>{index + 1}</span>
        <strong>{stage.stageName}</strong>
        <small>{stage.role}</small>
      </div>
      <Tag color={healthColor(stage.health)}>{stage.health}</Tag>
    </div>
    <div className="rcm-stage-grid">
      <div>
        <label>Model</label>
        <b>{stage.model}</b>
      </div>
      <div>
        <label>Profile</label>
        <b>{stage.profile}</b>
      </div>
      <div>
        <label>Thinking</label>
        <span className={`rcm-thinking-pill ${stage.configuredThinking}`}>{stage.configuredThinking}</span>
      </div>
      <div>
        <label>Mapped Provider Thinking</label>
        <b>{stage.mappedProviderThinking}</b>
      </div>
      <div className="wide">
        <label>Raw Request Thinking Fields</label>
        <code>{Object.entries(stage.rawThinkingFields).map(([key, value]) => `${key}: ${Array.isArray(value) ? value.join('|') : String(value)}`).join('; ') || 'not_applicable'}</code>
      </div>
      <div className="wide">
        <label>Evidence Paths</label>
        <code>{[stage.requestPath, stage.responsePath, stage.promptPath, stage.artifactPath].filter(Boolean).join('  |  ') || 'not_applicable'}</code>
      </div>
    </div>
    {stage.references?.length ? (
      <div className="rcm-reference-strip">
        {stage.references.map((item) => (
          <Tag key={item.label} color={item.sent ? 'blue' : 'default'}>
            {item.label}: {item.sent ? item.value : 'not sent'}
          </Tag>
        ))}
      </div>
    ) : null}
    <div className="rcm-stage-actions">
      <Button icon={<CodeOutlined />} onClick={() => onInspect(`${stage.stageName} raw request`, stage.request)}>
        Raw Request
      </Button>
      <Button icon={<CodeOutlined />} onClick={() => onInspect(`${stage.stageName} raw response`, stage.response)}>
        Raw Response
      </Button>
      <Button icon={<FileTextOutlined />} onClick={() => onInspect(`${stage.stageName} rendered prompt`, stage.prompt)}>
        Rendered Prompt
      </Button>
    </div>
  </section>
);

const RequestChain: React.FC<{
  slide: SlideEvidence;
  onInspect: (title: string, value: unknown) => void;
}> = ({ slide, onInspect }) => (
  <div className="rcm-request-chain">
    <div className="rcm-panel-heading">
      <div>
        <h3>Request Chain</h3>
        <p>Planned route compared with evidence actually persisted for this slide.</p>
      </div>
      <Space wrap>
        <Tag color="blue">schema v{slide.requestChain.schemaVersion}</Tag>
        <Tag color={healthColor(slide.requestChain.health)}>{slide.requestChain.health}</Tag>
      </Space>
    </div>
    <div className="rcm-chain-map" aria-label="Stage map">
      {slide.requestChain.stages.map((stage, index) => (
        <React.Fragment key={stage.id}>
          <button type="button" onClick={() => onInspect(`${stage.stageName} request`, stage.request)}>
            <span>{index + 1}</span>
            <strong>{stage.stageName}</strong>
          </button>
          {index < slide.requestChain.stages.length - 1 ? <i /> : null}
        </React.Fragment>
      ))}
    </div>
    <div className="rcm-stage-card-list">
      {slide.requestChain.stages.map((stage, index) => (
        <StageAuditCard key={stage.id} stage={stage} index={index} onInspect={onInspect} />
      ))}
    </div>
    <Collapse
      className="rcm-collapse"
      size="small"
      items={[
        {
          key: 'actual',
          label: 'Actual Evidence',
          children: <CodeBlock label="Actual Evidence" value={slide.requestChain.actualEvidence} />,
        },
        {
          key: 'planned',
          label: 'Planned Chain',
          children: <CodeBlock label="Planned Chain" value={slide.requestChain.plannedChain} />,
        },
      ]}
    />
  </div>
);

const SlideFrame: React.FC<{ slide: SlideEvidence; scale: number }> = ({ slide, scale }) => (
  <div className={`rcm-slide-frame ${slide.visualVariant}`} style={{ ['--mock-scale' as string]: scale }}>
    {slide.previewKind === 'image' ? <PictureOutlined /> : <CodeOutlined />}
    <div className="rcm-slide-art">
      <h4>{slide.previewTitle}</h4>
      <p>{slide.previewBody}</p>
      {slide.visualVariant === 'content' && (
        <div className="rcm-content-panels">
          <span>礼制乐坏</span>
          <span>诸侯争霸</span>
          <span>核心时代</span>
        </div>
      )}
      {slide.visualVariant === 'html' && (
        <div className="rcm-html-lines">
          <i />
          <i />
          <i />
        </div>
      )}
    </div>
  </div>
);

const EvidenceTabs: React.FC<{
  run: MockRun;
  slide: SlideEvidence;
  onInspect: (title: string, value: unknown) => void;
}> = ({ run, slide, onInspect }) => {
  const keyStage = slide.requestChain.stages.find((stage) => stage.role.includes('html') || stage.id.includes('image-generation')) || slide.requestChain.stages[0];
  return (
    <Tabs
      defaultActiveKey="evidence"
      items={[
        {
          key: 'evidence',
          label: 'Evidence Detail',
          children: (
            <Tabs
              defaultActiveKey="request-chain"
              items={[
                {
                  key: 'request-chain',
                  label: 'Request Chain',
                  children: <RequestChain slide={slide} onInspect={onInspect} />,
                },
                {
                  key: 'prompt',
                  label: 'Rendered Prompt',
                  children: <CodeBlock label="Rendered Prompt" value={keyStage.prompt} />,
                },
                {
                  key: run.engine === 'html' ? 'html-path' : 'blueprint',
                  label: run.engine === 'html' ? 'HTML Evidence Path' : 'Blueprint XML',
                  children: <CodeBlock label={run.engine === 'html' ? 'HTML Evidence Path' : 'Blueprint XML'} value={keyStage.artifactPath || 'not_applicable'} />,
                },
                {
                  key: 'request',
                  label: run.engine === 'html' ? 'HTML Request' : 'Image Request',
                  children: <CodeBlock label="Selected Final Stage Request" value={keyStage.request} />,
                },
                {
                  key: 'response',
                  label: 'Response',
                  children: <CodeBlock label="Selected Final Stage Response" value={keyStage.response} />,
                },
                {
                  key: 'config',
                  label: 'Config',
                  children: <CodeBlock label="Config" value={{ engine: run.engine, strategy: run.strategy, config_name: run.configName }} />,
                },
                {
                  key: 'raw',
                  label: 'Raw Evidence',
                  children: <CodeBlock label="Raw Stage Artifacts" value={slide.requestChain} />,
                },
              ]}
            />
          ),
        },
        {
          key: 'versions',
          label: 'Versions',
          children: <Alert type="info" showIcon message="Viewing v1 active artifact evidence_snapshot with stage chain." />,
        },
        {
          key: 'history',
          label: 'Generation History',
          children: <CodeBlock label="Generation History" value={[{ action: 'initial_generation', status: 'success', run_id: run.runId }]} />,
        },
      ]}
    />
  );
};

const slideBadge = (slide: SlideEvidence) => (
  <Tag color={slide.type === 'cover' ? 'gold' : undefined}>{slide.type === 'cover' ? 'Cover' : 'Content'}</Tag>
);

const slideStatusTag = (slide: SlideEvidence) => (
  <Tag color={slide.status === 'completed' ? 'success' : slide.status === 'failed' ? 'error' : 'default'}>{slide.status}</Tag>
);

const EvidencePanel: React.FC<{
  run: MockRun;
  slide: SlideEvidence;
  onInspect: (title: string, value: unknown) => void;
}> = ({ run, slide, onInspect }) => (
  <section className="run-detail-evidence-panel run-detail-inline-evidence" aria-label={`Evidence for slide ${slide.position}`}>
    <div className="run-detail-evidence-header">
      <strong>Evidence For Slide {slide.position}</strong>
      <Space size={6} wrap>
        <Tag color="gold">Bound to slide {slide.position}</Tag>
        <Tag color="blue">Viewing {slide.versionTag}</Tag>
      </Space>
    </div>
    <Space className="run-detail-slide-actions" wrap>
      <Button disabled>Retry</Button>
      <Button danger>Force Slide</Button>
      <Button icon={<DownloadOutlined />}>Download Evidence</Button>
    </Space>
    <EvidenceTabs run={run} slide={slide} onInspect={onInspect} />
  </section>
);

const GeneratedOutputs: React.FC<{
  run: MockRun;
  reviewMode: 'gallery' | 'tiled';
  zoom: number;
  selectedSlideId: number | null;
  onSelectSlide: (slideId: number) => void;
  onInspect: (title: string, value: unknown) => void;
}> = ({ run, reviewMode, zoom, selectedSlideId, onSelectSlide, onInspect }) => {
  const selectedSlide = run.slides.find((slide) => slide.id === selectedSlideId) || run.slides[run.slides.length - 1];
  const selectedIndex = run.slides.findIndex((slide) => slide.id === selectedSlide.id);
  const inlineEvidenceAfterIndex = reviewMode === 'tiled'
    ? Math.min(selectedIndex + (selectedIndex % 2 === 0 ? 1 : 0), run.slides.length - 1)
    : selectedIndex;
  return (
    <div className={`run-detail-review-layout mode-${reviewMode}`}>
      <div
        className={`run-detail-preview-panel rcm-production-preview ${reviewMode === 'gallery' ? 'slide-gallery-list' : 'slide-tile-grid'}`}
        style={{ ['--slide-scale' as string]: zoom / 100 }}
      >
        {run.slides.map((slide, index) => (
          <React.Fragment key={slide.id}>
            <button
              type="button"
              className={`slide-tile rcm-production-tile ${selectedSlide.id === slide.id ? 'selected' : ''}`}
              onClick={() => onSelectSlide(slide.id)}
            >
              <div className="slide-tile-header">
                <span>Slide {slide.position}: {slide.title}</span>
                <Space size={4} wrap>{slideBadge(slide)}{slideStatusTag(slide)}</Space>
              </div>
              <SlideFrame slide={slide} scale={zoom / 100} />
            </button>
            {index === inlineEvidenceAfterIndex && (
              <EvidencePanel run={run} slide={selectedSlide} onInspect={onInspect} />
            )}
          </React.Fragment>
        ))}
      </div>
    </div>
  );
};

const RequestChainStageMockup: React.FC = () => {
  const [collapsed, setCollapsed] = useState(true);
  const [selectedKey, setSelectedKey] = useState(mockRuns[0].key);
  const [selectedSlideId, setSelectedSlideId] = useState<number | null>(
    mockRuns[0].slides[mockRuns[0].slides.length - 1]?.id || null,
  );
  const [reviewMode, setReviewMode] = useState<'gallery' | 'tiled'>('tiled');
  const [zoom, setZoom] = useState(58);
  const [drawer, setDrawer] = useState<{ title: string; value: unknown } | null>(null);
  const run = useMemo(() => mockRuns.find((item) => item.key === selectedKey) || mockRuns[0], [selectedKey]);
  return (
    <Layout className="app-shell rcm-shell">
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
        <Menu theme="dark" mode="inline" selectedKeys={['/history']} items={navItems} />
      </Sider>
      <Layout style={{ minWidth: 0 }}>
        <Content className="app-content rcm-content">
          <div className="page-toolbar run-detail-page-toolbar rcm-toolbar">
            <div>
              <div className="page-kicker"><span className="run-status-dot completed" />Run #{run.runId}</div>
              <h2>Run #{run.runId} Detail</h2>
              <p className="toolbar-subtitle">Inspect generated slides, prompt evidence, artifacts, and route-specific dependencies.</p>
            </div>
            <Space className="page-toolbar-actions" wrap>
              <Button aria-label="Back to History" icon={<ArrowLeftOutlined />}>Back to History</Button>
              <Button aria-label="Back to Batch" icon={<ArrowLeftOutlined />}>Back to Batch</Button>
              <Button icon={<DownloadOutlined />}>Run ZIP</Button>
              <Button icon={<ReloadOutlined />}>Refresh</Button>
              <Button danger>Force Run</Button>
              <Button type="primary" icon={<CheckCircleOutlined />}>Approve Mockup</Button>
            </Space>
          </div>

          <div className="run-detail-sibling-strip">
            <span>Sibling runs</span>
            <Select
              aria-label="Sibling run selector"
              value={selectedKey}
              onChange={(value) => {
                const nextRun = mockRuns.find((item) => item.key === value) || mockRuns[0];
                setSelectedKey(value);
                setSelectedSlideId(nextRun.slides[nextRun.slides.length - 1]?.id || null);
              }}
              options={mockRuns.map((item) => ({
                value: item.key,
                label: item.key === 'html' ? `Run ${item.runId} · HTML · completed` : `Run ${item.runId} · ${item.strategy} · completed`,
              }))}
            />
          </div>

          <section className="run-route-flow-panel" aria-label="Run Route Flow">
            <div className="section-heading compact">
              <h3>Run Route Flow</h3>
              <span>{run.engine === 'image' ? `${run.strategy} route` : 'HTML generation route'} · current run only</span>
            </div>
            <div className="run-route-flow-steps">
              {run.routeFlowSteps.map((step, index) => (
                <div className="run-route-flow-step" key={`${step}-${index}`}>
                  <span>{index + 1}</span>
                  <strong>{step}</strong>
                </div>
              ))}
            </div>
          </section>

          <div className="run-detail-toolbar">
            <strong>Generated Outputs</strong>
            <Segmented
              aria-label="Review mode"
              value={reviewMode}
              onChange={(value) => setReviewMode(value as 'gallery' | 'tiled')}
              options={[
                { label: 'Full Gallery', value: 'gallery' },
                { label: 'Tiled Review', value: 'tiled' },
              ]}
            />
            <div className="slide-zoom-control">
              <span>Scale</span>
              <Slider min={40} max={90} value={zoom} onChange={setZoom} />
            </div>
          </div>

          <GeneratedOutputs
            run={run}
            reviewMode={reviewMode}
            zoom={zoom}
            selectedSlideId={selectedSlideId}
            onSelectSlide={setSelectedSlideId}
            onInspect={(title, value) => setDrawer({ title, value })}
          />

          <Descriptions bordered column={{ xs: 1, md: 3 }} className="run-detail-meta rcm-meta">
            <Descriptions.Item label="Engine">{run.engine}</Descriptions.Item>
            <Descriptions.Item label="Strategy">{run.strategy}</Descriptions.Item>
            <Descriptions.Item label="Config">{run.configName}</Descriptions.Item>
            <Descriptions.Item label="Batch ID">#{run.batchId}</Descriptions.Item>
            <Descriptions.Item label="Status"><Tag color="success">{run.status}</Tag></Descriptions.Item>
            <Descriptions.Item label="Model Summary">{run.modelSummary}</Descriptions.Item>
          </Descriptions>

          {run.designPrincipleRaw && (
            <section className="run-detail-design-principle">
              <div className="section-heading compact">
                <h3>Design Principle</h3>
                <span>Run-level shared stage evidence</span>
              </div>
              <CodeBlock label="Design Principle Raw" value={run.designPrincipleRaw} />
            </section>
          )}
        </Content>
      </Layout>
      <Drawer
        title={drawer?.title}
        open={Boolean(drawer)}
        size="large"
        onClose={() => setDrawer(null)}
      >
        <CodeBlock label={drawer?.title || 'Evidence'} value={drawer?.value || ''} />
      </Drawer>
    </Layout>
  );
};

export default RequestChainStageMockup;
