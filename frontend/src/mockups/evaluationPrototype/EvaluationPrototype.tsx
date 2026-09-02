import React, { useState } from 'react';
import {
  Button,
  Checkbox,
  Drawer,
  Image,
  Input,
  Layout,
  Menu,
  Modal,
  Segmented,
  Select,
  Slider,
  Space,
  Table,
  Tag,
  Tooltip,
} from 'antd';
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  HistoryOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
  PictureOutlined,
  PlusOutlined,
  SettingOutlined,
  TagsOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { availableHistoryRuns, defaultIssueTags, evaluations, type Attempt, type Evaluation, type IssueTag, type SlideVisual, type Variant } from './fixtures';

const { Sider, Content } = Layout;

const navItems = [
  { key: '/data', icon: <DatabaseOutlined />, label: 'Data' },
  { key: '/generate', icon: <ThunderboltOutlined />, label: 'Generate' },
  { key: '/history', icon: <HistoryOutlined />, label: 'History' },
  { key: '/evaluations', icon: <FileSearchOutlined />, label: 'Evaluations' },
  { key: '/prompts', icon: <FileTextOutlined />, label: 'Prompts' },
  { key: '/config', icon: <SettingOutlined />, label: 'Config' },
];

type ReviewMode = 'active' | 'all';
type ExportPreset = 'Internal Report' | 'Full Archive';
type PrototypeScreen = 'list' | 'create' | 'compare';
type CreateMode = 'blank' | 'history';

interface VisualTarget {
  attempt: Attempt;
  slide: SlideVisual;
  variant: Variant;
  variantLabel: string;
}

const blankCreateOptions = {
  decks: ['中国历史'],
  requirements: ['Professional concise rewrite', 'Narrative history teaching', 'Dense internal report'],
  colors: ['System Empty Color', 'Ink red + warm paper', 'Blue gray institutional'],
  prompts: ['HTML baseline V5.3.20', 'Image route V5.4', 'Layout stability candidate'],
  configs: ['html_default', 'image_5_0', 'image_3_2'],
  models: ['Gemini 3.1 Pro', 'Gemini 3 Flash Image', 'Gemini 3 Flash'],
  strategies: ['html_default', 'image_5_0', 'image_3_2'],
};

const blankVariants = [
  {
    key: 'blank-a',
    label: 'A',
    name: 'A · HTML baseline',
    objective: 'Preserve current HTML prompt stability and content completeness.',
    requirement: 'Professional concise rewrite',
    prompt: 'HTML baseline V5.3.20',
    config: 'html_default',
    model: 'Gemini 3.1 Pro',
    strategy: 'html_default',
    repeats: 2,
  },
  {
    key: 'blank-b',
    label: 'B',
    name: 'B · Image V5.4',
    objective: 'Improve visual hierarchy and page polish with Image route while avoiding hard layout failures.',
    requirement: 'Professional concise rewrite',
    prompt: 'Image route V5.4',
    config: 'image_5_0',
    model: 'Gemini 3 Flash Image',
    strategy: 'image_5_0',
    repeats: 2,
  },
  {
    key: 'blank-c',
    label: 'C',
    name: 'C · Stability guardrail',
    objective: 'Test a conservative prompt change that should preserve structure while reducing overflow risk.',
    requirement: 'Dense internal report',
    prompt: 'Layout stability candidate',
    config: 'html_default',
    model: 'Gemini 3 Flash',
    strategy: 'html_default',
    repeats: 2,
  },
  {
    key: 'blank-d',
    label: 'D',
    name: 'D · Image conservative',
    objective: 'Check whether the Image route can improve polish without drifting away from the deck structure.',
    requirement: 'Narrative history teaching',
    prompt: 'Image route V5.4',
    config: 'image_3_2',
    model: 'Gemini 3 Flash Image',
    strategy: 'image_3_2',
    repeats: 2,
  },
];

function statusColor(status: Evaluation['status']) {
  if (status === 'Reviewed') return 'success';
  if (status === 'Reviewing') return 'processing';
  if (status === 'Archived') return 'default';
  return 'warning';
}

function qaIcon(slide: SlideVisual) {
  if (slide.machineVerdict === 'pass') return <CheckCircleOutlined className="evp-pass" />;
  if (slide.machineVerdict === 'fail') return <CloseCircleOutlined className="evp-fail" />;
  return <span className="evp-qa-dot" />;
}

function allAttempts(evaluation: Evaluation): Array<{ variant: Variant; attempt: Attempt }> {
  return evaluation.variants.flatMap((variant) => variant.attempts.map((attempt) => ({ variant, attempt })));
}

function issueCountForAttempt(attempt: Attempt) {
  return attempt.slides.reduce((total, slide) => total + slide.issueTags.length + (slide.machineVerdict === 'fail' ? 1 : 0), 0);
}

function machineIssueSignals(items: Array<{ variant: Variant; attempt: Attempt }>) {
  return items.flatMap(({ attempt }) => attempt.slides.flatMap((slide) => {
    const machineTags = slide.issueTags.filter((tag) => tag.source === 'machine');
    if (!machineTags.length && slide.machineVerdict !== 'fail') return [];
    const labels = machineTags.length ? machineTags.map((tag) => tag.label).join(', ') : 'Machine fail';
    return [{
      key: `${attempt.id}-${slide.position}`,
      label: `${attempt.label} · Slide ${slide.position}: ${labels}`,
    }];
  }));
}

const SlideCard: React.FC<{
  variant: Variant;
  variantLabel: string;
  attempt: Attempt;
  slide: SlideVisual;
  scale: number;
  isRepresentative: boolean;
  onSetRepresentative: () => void;
  onInspect: (target: VisualTarget) => void;
}> = ({ variant, variantLabel, attempt, slide, scale, isRepresentative, onSetRepresentative, onInspect }) => {
  return (
    <article className={`evp-slide-card ${isRepresentative ? 'representative' : ''}`}>
      <div className="evp-slide-card-head">
        <div>
          <strong>{attempt.label}</strong>
          <span>{variantLabel}</span>
        </div>
        <Space size={4}>
          {qaIcon(slide)}
          {isRepresentative ? (
            <Tag color="blue">Rep</Tag>
          ) : (
            <Tooltip title={`Use ${attempt.label} for ${variantLabel}`}>
              <Button
                aria-label={`Use ${attempt.label} for ${variantLabel}`}
                size="small"
                icon={<PlusOutlined />}
                onClick={onSetRepresentative}
              />
            </Tooltip>
          )}
        </Space>
      </div>
      <button
        className="evp-slide-image-button"
        style={{ ['--evp-scale' as string]: scale / 100 }}
        type="button"
        onClick={() => onInspect({ variant, variantLabel, attempt, slide })}
      >
        <img src={slide.image} alt={`${attempt.label} slide ${slide.position}`} />
      </button>
      <div className="evp-slide-tags">
        {slide.issueTags.length ? slide.issueTags.map((tag) => (
          <Tag key={tag.id} color={tag.source === 'machine' ? 'geekblue' : 'orange'}>
            {tag.label}
          </Tag>
        )) : <Tag>clean</Tag>}
      </div>
      {slide.note && <p>{slide.note}</p>}
    </article>
  );
};

const RepresentativePicker: React.FC<{
  variant: Variant;
  variantLabel: string;
  onVariantLabelChange: (label: string) => void;
  activeAttemptId: string;
  onAttemptChange: (attemptId: string) => void;
}> = ({ variant, variantLabel, onVariantLabelChange, activeAttemptId, onAttemptChange }) => {
  const activeAttempt = variant.attempts.find((attempt) => attempt.id === activeAttemptId) || variant.attempts[0];
  const issueCount = activeAttempt ? issueCountForAttempt(activeAttempt) : 0;

  return (
    <section className="evp-representative-card">
      <div className="evp-representative-title">
        <Input
          aria-label={`Column name for ${variant.label}`}
          value={variantLabel}
          size="small"
          onChange={(event) => onVariantLabelChange(event.target.value)}
        />
        <Tag>{variant.comparisonVariable}</Tag>
      </div>
      <p>{variant.objective}</p>
      <div className="evp-representative-select">
        <span>Representative</span>
        <Select
          aria-label={`Representative attempt for ${variant.label}`}
          value={activeAttemptId}
          size="small"
          onChange={onAttemptChange}
          options={variant.attempts.map((attempt) => ({ value: attempt.id, label: attempt.label }))}
        />
      </div>
      {activeAttempt && (
        <div className="evp-representative-meta">
          <Tag color={issueCount ? 'orange' : 'success'}>{issueCount ? `${issueCount} issue signals` : 'No issue signals'}</Tag>
          <span>{activeAttempt.promptVersion} · {activeAttempt.model} · {activeAttempt.strategy}</span>
        </div>
      )}
    </section>
  );
};

const EvaluationPrototype: React.FC = () => {
  const [collapsed, setCollapsed] = useState(true);
  const [screen, setScreen] = useState<PrototypeScreen>('list');
  const [selectedId, setSelectedId] = useState(evaluations[0].id);
  const [createMode, setCreateMode] = useState<CreateMode>('blank');
  const [blankVariantCount, setBlankVariantCount] = useState(4);
  const [reviewMode, setReviewMode] = useState<ReviewMode>('all');
  const [layoutColumns, setLayoutColumns] = useState(2);
  const [scale, setScale] = useState(50);
  const [issueOnly, setIssueOnly] = useState(false);
  const [exportPreset, setExportPreset] = useState<ExportPreset>('Internal Report');
  const [activeAttemptByVariant, setActiveAttemptByVariant] = useState<Record<string, string>>({
    'variant-a': 'attempt-a1',
    'variant-b': 'attempt-b1',
  });
  const [variantLabels, setVariantLabels] = useState<Record<string, string>>({
    'variant-a': 'A · HTML baseline',
    'variant-b': 'B · Image V5.4',
  });
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [qaDrawerOpen, setQaDrawerOpen] = useState(false);
  const [selectedHistoryKeys, setSelectedHistoryKeys] = useState<React.Key[]>(['93', '94']);
  const [visualTarget, setVisualTarget] = useState<VisualTarget | null>(null);

  const evaluation = evaluations.find((item) => item.id === selectedId) || evaluations[0];
  const attempts = allAttempts(evaluation);
  const activeAttempts = evaluation.variants.map((variant) => {
    const attemptId = activeAttemptByVariant[variant.id] || variant.attempts[0]?.id;
    return { variant, attempt: variant.attempts.find((item) => item.id === attemptId) || variant.attempts[0] };
  }).filter((item): item is { variant: Variant; attempt: Attempt } => Boolean(item.attempt));
  const displayedAttempts = reviewMode === 'active' ? activeAttempts : attempts;
  const visibleSlides = [1, 2, 3, 4].filter((position) => {
    if (!issueOnly) return true;
    return displayedAttempts.some(({ attempt }) => {
      const slide = attempt.slides.find((item) => item.position === position);
      return slide?.issueTags.length || slide?.machineVerdict === 'fail';
    });
  });
  const selectedDeck = availableHistoryRuns.find((run) => selectedHistoryKeys.includes(run.key))?.deck;
  const selectedHistoryRuns = availableHistoryRuns.filter((run) => selectedHistoryKeys.includes(run.key));
  const visibleBlankVariants = blankVariants.slice(0, blankVariantCount);
  const blankRunCount = visibleBlankVariants.reduce((total, variant) => total + variant.repeats, 0);
  const machineSignals = machineIssueSignals(attempts);
  const exportFields = exportPreset === 'Internal Report'
    ? ['Column names', 'Prompt / model / strategy', 'Slide number', 'Visuals']
    : ['Objectives', 'Requirement / color', 'Notes', 'Representative', 'Issue tags', 'Machine QA'];
  const screenTitle = screen === 'list' ? 'Evaluation List' : screen === 'create' ? 'Create Evaluation' : 'Evaluation Compare';
  const screenSubtitle = screen === 'list'
    ? 'Saved comparison records for deck-level review.'
    : screen === 'create'
      ? 'Create from existing completed runs, or configure a new multi-variant generation from one deck.'
      : evaluation.objective;

  return (
    <Layout className="app-shell evp-shell">
      <Sider
        className="app-sidebar"
        width={208}
        collapsedWidth={72}
        collapsed={collapsed}
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
        <Menu theme="dark" mode="inline" selectedKeys={['/evaluations']} items={navItems} />
      </Sider>
      <Layout style={{ minWidth: 0 }}>
        <Content className="app-content evp-content">
          <div className="page-toolbar evp-toolbar">
            <div>
              <div className="page-kicker"><span className="status-dot" />Evaluation</div>
              <h2>{screenTitle}</h2>
              <p className="toolbar-subtitle">{screenSubtitle}</p>
            </div>
            <Space className="page-toolbar-actions" wrap>
              <Button icon={<ArrowLeftOutlined />} onClick={() => setScreen('list')}>Evaluation List</Button>
              <Button
                icon={<HistoryOutlined />}
                onClick={() => {
                  setCreateMode('blank');
                  setScreen('create');
                }}
              >
                New Evaluation
              </Button>
              {screen === 'compare' && (
                <Button type="primary" icon={<DownloadOutlined />} onClick={() => setDrawerOpen(true)}>Export</Button>
              )}
            </Space>
          </div>

          <Segmented
            className="evp-flow-tabs"
            block
            value={screen}
            onChange={(value) => setScreen(value as PrototypeScreen)}
            options={[
              { label: '1. Evaluation List', value: 'list' },
              { label: '2. Create Evaluation', value: 'create' },
              { label: '3. Compare Result', value: 'compare' },
            ]}
          />

          {screen === 'compare' && (
            <div className="evp-context-bar">
              <span><strong>Deck</strong> {evaluation.deck}</span>
              <span><strong>Variants</strong> {evaluation.variants.length} / 4</span>
              <span><strong>Attempts</strong> {attempts.length}</span>
              <Tag color={statusColor(evaluation.status)}>{evaluation.status}</Tag>
            </div>
          )}

          {screen === 'list' && (
            <section className="evp-page-panel">
              <div className="evp-section-title">
                <div>
                  <h3>Evaluation records</h3>
                  <p>Each record compares runs from one deck.</p>
                </div>
                <Button
                  type="primary"
                  icon={<PlusOutlined />}
                  onClick={() => {
                    setCreateMode('blank');
                    setScreen('create');
                  }}
                >
                  Create Evaluation
                </Button>
              </div>
              <Table<Evaluation>
                size="middle"
                rowKey="id"
                dataSource={evaluations}
                pagination={false}
                columns={[
                  {
                    title: 'Evaluation',
                    dataIndex: 'title',
                    render: (title: string, record) => (
                      <div className="evp-table-title">
                        <strong>{title}</strong>
                        <span>{record.objective}</span>
                      </div>
                    ),
                  },
                  { title: 'Deck', dataIndex: 'deck' },
                  { title: 'Status', dataIndex: 'status', render: (status: Evaluation['status']) => <Tag color={statusColor(status)}>{status}</Tag> },
                  { title: 'Variants', key: 'variants', render: (_, record) => record.variants.length },
                  { title: 'Attempts', key: 'attempts', render: (_, record) => allAttempts(record).length },
                  {
                    title: '',
                    key: 'action',
                    render: (_, record) => (
                      <Button
                        size="small"
                        onClick={() => {
                          setSelectedId(record.id);
                          setScreen('compare');
                        }}
                      >
                        Open compare
                      </Button>
                    ),
                  },
                ]}
              />
            </section>
          )}

          {screen === 'create' && (
            <section className="evp-page-panel">
              <div className="evp-section-title">
                <div>
                  <h3>{createMode === 'history' ? 'Create from History' : 'Create from Blank'}</h3>
                  <p>
                    {createMode === 'history'
                      ? 'After the first run is selected, runs from other decks become unavailable.'
                      : 'Configure one deck, then reuse Generate options for each comparison variant.'}
                  </p>
                </div>
                <Space wrap>
                  <Segmented
                    value={createMode}
                    onChange={(value) => setCreateMode(value as CreateMode)}
                    options={[
                      { label: 'From Blank', value: 'blank' },
                      { label: 'From History', value: 'history' },
                    ]}
                  />
                  <Button type="primary" onClick={() => setScreen('compare')}>
                    {createMode === 'history' ? 'Create Evaluation' : 'Start Evaluation Runs'}
                  </Button>
                </Space>
              </div>
              {createMode === 'history' ? (
                <div className="evp-create-grid">
                  <Table
                    className="evp-create-table"
                    size="small"
                    rowKey="key"
                    dataSource={availableHistoryRuns}
                    pagination={false}
                    rowSelection={{
                      selectedRowKeys: selectedHistoryKeys,
                      onChange: setSelectedHistoryKeys,
                      getCheckboxProps: (record) => ({
                        disabled: record.status !== 'completed'
                          || Boolean(selectedDeck && record.deck !== selectedDeck)
                          || (!selectedHistoryKeys.includes(record.key) && selectedHistoryKeys.length >= 4),
                      }),
                    }}
                    columns={[
                      { title: 'Run', dataIndex: 'runId', render: (runId: number) => <strong>Run {runId}</strong> },
                      { title: 'Deck', dataIndex: 'deck' },
                      { title: 'Route', dataIndex: 'route' },
                      { title: 'Status', dataIndex: 'status', render: (status: string) => <Tag color={status === 'completed' ? 'success' : 'error'}>{status}</Tag> },
                      { title: 'Preview', key: 'preview', render: () => <Button size="small" icon={<EyeOutlined />}>Open</Button> },
                    ]}
                  />
                  <aside className="evp-create-side">
                    <div>
                      <span>Locked deck</span>
                      <strong>{selectedDeck || 'Select a run'}</strong>
                    </div>
                    <div>
                      <span>Selected runs</span>
                      <Space wrap>
                        {selectedHistoryRuns.map((run) => <Tag key={run.key}>Run {run.runId}</Tag>)}
                      </Space>
                    </div>
                    <label>
                      <span>Evaluation goal</span>
                      <Input.TextArea
                        rows={4}
                        defaultValue="Compare selected runs page by page and keep the strongest representative from each variant."
                      />
                    </label>
                  </aside>
                </div>
              ) : (
                <div className="evp-blank-create">
                  <section className="evp-blank-setup">
                    <div>
                      <span>Evaluation deck</span>
                      <Select
                        defaultValue="中国历史"
                        options={blankCreateOptions.decks.map((value) => ({ value, label: value }))}
                      />
                    </div>
                    <label>
                      <span>Evaluation goal</span>
                      <Input.TextArea
                        rows={3}
                        defaultValue="Compare HTML baseline against the Image V5.4 route, page by page, and confirm whether visual improvements keep the previous structure stable."
                      />
                    </label>
                  </section>

                  <section className="evp-blank-variant-panel">
                    <div className="evp-panel-head">
                      <strong>Variant setup</strong>
                      <Space wrap>
                        <Tag>Deck shared</Tag>
                        <Segmented
                          size="small"
                          value={blankVariantCount}
                          onChange={(value) => setBlankVariantCount(Number(value))}
                          options={[2, 3, 4].map((value) => ({ value, label: `${value} variants` }))}
                        />
                        <Button
                          size="small"
                          icon={<PlusOutlined />}
                          disabled={blankVariantCount >= 4}
                          onClick={() => setBlankVariantCount((value) => Math.min(4, value + 1))}
                        >
                          Add Variant
                        </Button>
                      </Space>
                    </div>
                    <div className="evp-blank-variant-grid">
                      {visibleBlankVariants.map((variant) => (
                        <article className="evp-blank-variant-card" key={variant.key}>
                          <div className="evp-blank-variant-title">
                            <Tag color={variant.label === 'A' ? 'blue' : variant.label === 'B' ? 'gold' : variant.label === 'C' ? 'purple' : 'cyan'}>
                              {variant.label}
                            </Tag>
                            <Input defaultValue={variant.name} aria-label={`${variant.label} variant name`} />
                          </div>
                          <label>
                            <span>Variant objective</span>
                            <Input.TextArea rows={3} defaultValue={variant.objective} />
                          </label>
                          <div className="evp-blank-field-grid">
                            <label>
                              <span>Requirement</span>
                              <Select
                                defaultValue={variant.requirement}
                                options={blankCreateOptions.requirements.map((value) => ({ value, label: value }))}
                              />
                            </label>
                            <label>
                              <span>Color</span>
                              <Select
                                defaultValue={blankCreateOptions.colors[0]}
                                options={blankCreateOptions.colors.map((value) => ({ value, label: value }))}
                              />
                            </label>
                            <label>
                              <span>Prompt</span>
                              <Select
                                defaultValue={variant.prompt}
                                options={blankCreateOptions.prompts.map((value) => ({ value, label: value }))}
                              />
                            </label>
                            <label>
                              <span>Config</span>
                              <Select
                                defaultValue={variant.config}
                                options={blankCreateOptions.configs.map((value) => ({ value, label: value }))}
                              />
                            </label>
                            <label>
                              <span>Model</span>
                              <Select
                                defaultValue={variant.model}
                                options={blankCreateOptions.models.map((value) => ({ value, label: value }))}
                              />
                            </label>
                            <label>
                              <span>Strategy</span>
                              <Select
                                defaultValue={variant.strategy}
                                options={blankCreateOptions.strategies.map((value) => ({ value, label: value }))}
                              />
                            </label>
                            <label>
                              <span>Repeats</span>
                              <Select
                                defaultValue={variant.repeats}
                                options={[1, 2, 3, 4, 5].map((value) => ({ value, label: `${value} attempt${value > 1 ? 's' : ''}` }))}
                              />
                            </label>
                          </div>
                        </article>
                      ))}
                    </div>
                  </section>

                  <aside className="evp-blank-summary">
                    <div>
                      <span>Planned output</span>
                      <strong>1 Evaluation · {blankVariantCount} Variants · {blankRunCount} Runs</strong>
                    </div>
                    <div>
                      <span>Reuse boundary</span>
                      <p>Use Generate selectors and payload assembly per Variant, then save all runs into one Evaluation for review.</p>
                    </div>
                    <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => setScreen('compare')}>Start Evaluation Runs</Button>
                  </aside>
                </div>
              )}
            </section>
          )}

          {screen === 'compare' && (
            <main className="evp-compare-shell">
              <section className="evp-compare-head">
                <div>
                  <Tag color={statusColor(evaluation.status)}>{evaluation.status}</Tag>
                  <h3>{evaluation.title}</h3>
                  <p>{evaluation.objective}</p>
                </div>
                <Space wrap>
                  <Segmented
                    value={reviewMode}
                    onChange={(value) => setReviewMode(value as ReviewMode)}
                    options={[
                      { label: 'Representative only', value: 'active' },
                      { label: 'All attempts', value: 'all' },
                    ]}
                  />
                  <Select
                    aria-label="Columns per row"
                    value={layoutColumns}
                    onChange={setLayoutColumns}
                    options={[2, 3, 4].map((value) => ({ value, label: `${value} columns` }))}
                  />
                </Space>
              </section>

              <section className="evp-representative-panel">
                <div className="evp-panel-head">
                  <strong>Representative attempts</strong>
                  <Button size="small" icon={<TagsOutlined />} onClick={() => setQaDrawerOpen(true)}>Notes & QA</Button>
                </div>
                <div className="evp-representative-grid">
                  {evaluation.variants.map((variant) => (
                    <RepresentativePicker
                      key={variant.id}
                      variant={variant}
                      variantLabel={variantLabels[variant.id] || variant.label}
                      onVariantLabelChange={(label) => setVariantLabels((current) => ({ ...current, [variant.id]: label }))}
                      activeAttemptId={activeAttemptByVariant[variant.id] || variant.attempts[0]?.id}
                      onAttemptChange={(attemptId) => setActiveAttemptByVariant((current) => ({ ...current, [variant.id]: attemptId }))}
                    />
                  ))}
                </div>
              </section>

              <section className={`evp-machine-alert ${machineSignals.length ? 'has-issues' : ''}`}>
                <div>
                  <strong>Machine QA flags</strong>
                  <span>{machineSignals.length ? 'Hard visual checks found issues before human review.' : 'No hard visual issues detected.'}</span>
                </div>
                <Space size={4} wrap>
                  {machineSignals.length ? machineSignals.map((signal) => (
                    <Tag key={signal.key} color="geekblue">{signal.label}</Tag>
                  )) : <Tag color="success">All sampled slides passed</Tag>}
                </Space>
              </section>

              <section className="evp-controls">
                <div className="slide-zoom-control">
                  <span>Scale</span>
                  <Slider min={10} max={88} value={scale} onChange={setScale} />
                </div>
                <Space wrap>
                  <Button
                    icon={<PictureOutlined />}
                    onClick={() => {
                      const nextIssueOnly = !issueOnly;
                      if (nextIssueOnly) setReviewMode('all');
                      setIssueOnly(nextIssueOnly);
                    }}
                  >
                    {issueOnly ? 'Show all slides' : 'Only issue slides'}
                  </Button>
                </Space>
              </section>

              <div className="evp-slide-rows">
                {visibleSlides.map((position) => {
                  const slideSignals = displayedAttempts.flatMap(({ variant, attempt }) => {
                    const slide = attempt.slides.find((item) => item.position === position);
                    if (!slide || (!slide.issueTags.length && slide.machineVerdict !== 'fail')) return [];
                    return [{
                      key: `${attempt.id}-${position}`,
                      label: `${attempt.label}: ${slide.issueTags.map((tag) => tag.label).join(', ') || 'Machine fail'}`,
                      source: slide.issueTags.some((tag) => tag.source === 'machine') || slide.machineVerdict === 'fail' ? 'machine' : 'human',
                      variant,
                    }];
                  });

                  return (
                    <section className="evp-slide-row" key={position}>
                      <div className="evp-slide-row-head">
                        <div>
                          <strong>Slide {position}</strong>
                          <span>{displayedAttempts.length} attempts shown</span>
                        </div>
                        <Space size={4} wrap>
                          {slideSignals.length ? slideSignals.map((signal) => (
                            <Tag key={signal.key} color={signal.source === 'machine' ? 'geekblue' : 'orange'}>
                              {signal.label}
                            </Tag>
                          )) : <Tag>No issue tagged</Tag>}
                        </Space>
                      </div>
                      <div className="evp-slide-grid" style={{ gridTemplateColumns: `repeat(${Math.min(displayedAttempts.length || 1, layoutColumns)}, minmax(0, 1fr))` }}>
                        {displayedAttempts.map(({ variant, attempt }) => {
                          const slide = attempt.slides.find((item) => item.position === position);
                          if (!slide) return null;
                          const variantLabel = variantLabels[variant.id] || variant.label;
                          return (
                            <SlideCard
                              key={`${attempt.id}-${position}`}
                              variant={variant}
                              variantLabel={variantLabel}
                              attempt={attempt}
                              slide={slide}
                              scale={scale}
                              isRepresentative={activeAttemptByVariant[variant.id] === attempt.id}
                              onSetRepresentative={() => setActiveAttemptByVariant((current) => ({ ...current, [variant.id]: attempt.id }))}
                              onInspect={setVisualTarget}
                            />
                          );
                        })}
                      </div>
                    </section>
                  );
                })}
              </div>
            </main>
          )}
        </Content>
      </Layout>

      <Drawer
        title="Export configuration"
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        size="large"
      >
        <Space orientation="vertical" size={16} style={{ width: '100%' }}>
          <Segmented
            block
            value={exportPreset}
            onChange={(value) => setExportPreset(value as ExportPreset)}
            options={['Internal Report', 'Full Archive']}
          />
          <div className="evp-export-fields">
            {exportFields.map((field) => (
              <Checkbox key={field} defaultChecked>{field}</Checkbox>
            ))}
          </div>
          <Button type="primary" icon={<DownloadOutlined />} block>Download current slide PNG</Button>
          <Button icon={<DownloadOutlined />} block>Download all slide PNGs as ZIP</Button>
        </Space>
      </Drawer>

      <Drawer
        title="Notes & QA"
        open={qaDrawerOpen}
        onClose={() => setQaDrawerOpen(false)}
        size="large"
      >
        <Space orientation="vertical" size={18} style={{ width: '100%' }}>
          <section className="evp-drawer-section">
            <h3>Evaluation notes</h3>
            <Input.TextArea
              rows={4}
              defaultValue={evaluation.summary}
              aria-label="Evaluation summary"
            />
          </section>
          <section className="evp-drawer-section">
            <h3>Representative attempts</h3>
            <div className="evp-qa-summary">
              {activeAttempts.map(({ variant, attempt }) => (
                <div key={attempt.id}>
                  <span>{variantLabels[variant.id] || variant.label} · {attempt.label}</span>
                  <small>{attempt.qaSummary}</small>
                </div>
              ))}
            </div>
          </section>
          <section className="evp-drawer-section">
            <h3>Global issue tags</h3>
            <Space size={4} wrap>
              {defaultIssueTags.map((tag) => <Tag key={tag.id}>{tag.label}</Tag>)}
            </Space>
          </section>
          <section className="evp-drawer-section">
            <h3>Machine QA</h3>
            <div className="evp-qa-summary">
              {attempts.map(({ attempt }) => (
                <div key={attempt.id}>
                  <span>{attempt.label}</span>
                  <small>{attempt.qaSummary}</small>
                </div>
              ))}
            </div>
          </section>
        </Space>
      </Drawer>

      <Modal
        title={visualTarget ? `${visualTarget.attempt.label} · Slide ${visualTarget.slide.position}` : 'Slide'}
        open={Boolean(visualTarget)}
        footer={null}
        width="86vw"
        onCancel={() => setVisualTarget(null)}
      >
        {visualTarget && (
          <div className="evp-inspect-modal">
            <Image src={visualTarget.slide.image} alt={`${visualTarget.attempt.label} slide ${visualTarget.slide.position}`} preview={false} />
            <aside>
              <Tag>{visualTarget.variantLabel}</Tag>
              <h3>{visualTarget.attempt.label}</h3>
              <p>{visualTarget.variant.objective}</p>
              <p>{visualTarget.attempt.promptVersion} · {visualTarget.attempt.model} · {visualTarget.attempt.strategy}</p>
              <Space wrap>
                {visualTarget.slide.issueTags.map((tag: IssueTag) => <Tag key={tag.id}>{tag.label}</Tag>)}
              </Space>
            </aside>
          </div>
        )}
      </Modal>
    </Layout>
  );
};

export default EvaluationPrototype;
