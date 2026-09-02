import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Empty,
  Input,
  InputNumber,
  Segmented,
  Select,
  Slider,
  Space,
  Spin,
  Table,
  Tag,
  Tooltip,
  message,
} from 'antd';
import {
  ArrowLeftOutlined,
  CheckCircleOutlined,
  DownloadOutlined,
  EyeOutlined,
  FileSearchOutlined,
  HistoryOutlined,
  PictureOutlined,
  PlusOutlined,
  ReloadOutlined,
  TagsOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { api } from '../api';
import { RunSlideEvidencePanel } from '../components/RunDetail';
import type {
  Color,
  Config,
  Deck,
  EvaluationAttempt,
  EvaluationBlankCreatePayload,
  EvaluationDetail,
  EvaluationExportPayload,
  EvaluationHistoryRunCandidate,
  EvaluationHistoryCreatePayload,
  EvaluationListItem,
  EvaluationSnapshot,
  EvaluationVariant,
  Prompt,
  Requirement,
  ImageRenderer,
  RouteEngine,
  RouteStrategy,
  RunSlide,
} from '../types';
import './evaluations.css';
import { toArtifactUrl } from '../lib/artifactUrls';

const VARIANT_LETTERS = ['A', 'B', 'C', 'D'];
const DEFAULT_SCALE_BY_COLUMN: Record<number, number> = { 2: 50, 3: 36, 4: 28 };

const statusColorMap: Record<string, string> = {
  draft: 'default',
  running: 'processing',
  reviewing: 'blue',
  reviewed: 'success',
  archived: 'default',
  queued: 'default',
  pending: 'default',
  completed: 'success',
  failed: 'error',
  skipped: 'warning',
  timed_out: 'warning',
};

const strategyOptions: Array<{ value: RouteStrategy; label: string }> = [
  { value: 'html_default', label: 'HTML Default' },
  { value: 'image_1_0', label: '1.0' },
  { value: 'image_3_0', label: '3.0' },
  { value: 'image_3_2', label: '3.2' },
  { value: 'image_5_0', label: '5.0' },
];

const imageStrategyPromptRoles: Record<RouteStrategy, string[]> = {
  html_default: [],
  codex_html: [],
  image_1_0: ['image_cover_3_1', 'image_1_0', 'image_generator'],
  image_3_0: ['image_cover_3_1', 'image_3_0_seed', 'image_3_0_non_seed', 'image_generator'],
  image_3_2: ['image_cover_3_1', 'image_3_2_seed', 'image_3_2_non_seed', 'image_generator'],
  image_5_0: ['image_cover_3_1', 'image_5_0_unified', 'image_generator'],
  image_direct: [],
};

const configImageRenderer = (config: Config): ImageRenderer => {
  const bindings = (config.route_model_bindings || {}) as Record<string, unknown>;
  const generator = bindings.image_generator as Record<string, unknown> | undefined;
  const directGenerator = config.html_agent || {};
  const apiType = String(directGenerator.api_type || generator?.api_type || '').toLowerCase();
  const model = String(directGenerator.model || generator?.model || '').toLowerCase();
  const endpoint = String(directGenerator.endpoint || generator?.endpoint || '').toLowerCase();
  const name = String(config.name || '').toLowerCase();
  if (
    generator?.renderer === 'gpt_image_2' ||
    generator?.provider_channel === 'zenmux_images_api' ||
    apiType === 'zenmux_images' ||
    model.includes('gpt-image-2') ||
    endpoint.includes('/images/generations') ||
    name.includes('production pro gpt')
  ) {
    return 'gpt_image_2';
  }
  return 'banana';
};

const strategyReferenceMap: Record<RouteStrategy, Array<[string, string, string]>> = {
  html_default: [
    ['Requirement', 'Required', 'Designer and HTML prompts use this requirement.'],
    ['Color', 'Optional', 'The selected color feeds the deck color variable.'],
  ],
  codex_html: [
    ['Requirement', 'Required', 'Designer and HTML Codex prompts use this requirement.'],
    ['Codex Audit', 'Stored', 'Run Detail records JSONL paths, parsed events, timestamps, attempts, and failures.'],
  ],
  image_1_0: [
    ['Conversation Session', 'Required', 'Slides continue through provider conversation state.'],
    ['Reference Image', 'Stored', 'Reference image evidence is recorded by the route.'],
  ],
  image_3_0: [
    ['Seed Page', 'Required', 'The first content page creates seed XML and image.'],
    ['Later Pages', 'Parallel', 'Later pages depend on seed image/XML.'],
  ],
  image_3_2: [
    ['Cover Palette', 'Required', 'The seed page uses cover-derived palette content.'],
    ['Seed Page', 'Required', 'Later pages still use the seed image/XML.'],
  ],
  image_5_0: [
    ['Unified Designer', 'Required', 'One prompt role is used for all pages.'],
    ['Palette Flow', 'Optional', 'System empty color can extract cover palette.'],
  ],
  image_direct: [
    ['Current Slide Content', 'Only input', 'Each slide is sent directly to the selected image model.'],
    ['Design Director', 'Disabled', 'No cover designer or image designer prompt is used.'],
  ],
};

type PageMode = 'list' | 'blank' | 'history' | 'detail';
type ReviewMode = 'representative' | 'all';
type BlankGenerationMode = 'auto' | 'manual';

type BlankVariantForm = {
  key: string;
  label: string;
  goal: string;
  comparison_variable: string;
  generation_mode: BlankGenerationMode;
  requirement_id: number | null;
  color_id: number | null;
  config_id: number | null;
  engine: RouteEngine;
  strategy: RouteStrategy;
  image_renderer: ImageRenderer;
  designer_prompt_id: number | null;
  html_prompt_id: number | null;
};

type BlankDefaults = {
  requirement_id: number | null;
  color_id: number | null;
  html_config_id: number | null;
  image_config_id: number | null;
  designer_prompt_id: number | null;
  html_prompt_id: number | null;
};

type BlankFormState = {
  title: string;
  goal: string;
  deck_id: number | null;
  repeat_count: number;
  variant_count: number;
  variants: BlankVariantForm[];
};

type HistoryVariantInput = {
  label: string;
  goal: string;
};

type VisualTarget = {
  variant: EvaluationVariant;
  attempt: EvaluationAttempt;
  slide: RunSlide;
};

function statusColor(status?: string | null): string {
  return statusColorMap[String(status || '').toLowerCase()] || 'default';
}

function formatDate(value?: string | null): string {
  if (!value) return '-';
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? value : time.toLocaleString();
}

function slideVisualUrl(slide: RunSlide): string | null {
  return toArtifactUrl(slide.final_image_path || slide.screenshot_path);
}

function slideHasIssue(slide: RunSlide, attempt?: EvaluationAttempt): boolean {
  const status = String(slide.status || attempt?.run_status || attempt?.status || '').toLowerCase();
  return Boolean(
    slide.error_message ||
    ['failed', 'timed_out', 'skipped'].includes(status) ||
    (status === 'completed' && !slide.has_displayable_artifact && !slideVisualUrl(slide) && !slide.clean_html)
  );
}

function runDisplayableComplete(run: EvaluationHistoryRunCandidate): boolean {
  const progress = run.progress;
  if (!progress || progress.total <= 0) return false;
  const displayable = progress.displayable ?? 0;
  const missing = progress.missing_displayable ?? Math.max(progress.total - displayable, 0);
  return displayable >= progress.total && missing === 0;
}

function historyRunDisabledReason(
  run: EvaluationHistoryRunCandidate,
  activeSnapshotKey: string | null,
  selectedRunIds: number[],
): string | null {
  const selected = selectedRunIds.includes(run.id);
  if (run.status !== 'completed') return 'Only completed runs can be selected.';
  if (!runDisplayableComplete(run)) return 'Displayable progress is incomplete.';
  if (activeSnapshotKey && runDeckSnapshotKey(run) !== activeSnapshotKey) return 'Select a run from the chosen Deck snapshot.';
  if (!selected && selectedRunIds.length >= 4) return 'Select at most 4 runs.';
  return null;
}

function snapshotEntityLabel(snapshot: EvaluationSnapshot | undefined, key: 'requirement' | 'color' | 'config' | 'deck'): string {
  const entity = snapshot?.[key];
  if (!entity) return '-';
  if ('title' in entity && entity.title) return String(entity.title);
  if ('name' in entity && entity.name) return String(entity.name);
  if ('id' in entity && entity.id) return `#${entity.id}`;
  return '-';
}

function promptSummary(snapshot: EvaluationSnapshot | undefined): string {
  const prompts = snapshot?.prompts || {};
  const labels = Object.entries(prompts)
    .flatMap(([role, prompt]) => {
      if (!prompt) return [];
      const version = prompt.version ? ` ${prompt.version}` : '';
      const name = prompt.name ? ` · ${prompt.name}` : '';
      return [`${role}${version}${name}`];
    });
  return labels.length ? labels.join(' / ') : 'Default prompts';
}

function routeLabel(engine: RouteEngine, strategy: RouteStrategy): string {
  if (engine === 'html') return 'HTML Default';
  if (strategy === 'image_direct') return 'ImageDirect';
  const label = strategyOptions.find((item) => item.value === strategy)?.label || strategy;
  return `Image ${label}`;
}

function promptOptionLabel(prompt: Prompt): string {
  return `${prompt.is_default ? 'Default · ' : ''}${prompt.name} (${prompt.version})`;
}

function promptsForRole(prompts: Prompt[], role: string): Prompt[] {
  return prompts.filter((prompt) => prompt.agent_type === role && prompt.status === 'active');
}

function defaultPromptId(prompts: Prompt[], role: string): number | null {
  const rolePrompts = promptsForRole(prompts, role);
  return (rolePrompts.find((prompt) => prompt.is_default) || rolePrompts[0])?.id ?? null;
}

function getBlankDefaults(
  requirements: Requirement[],
  colors: Color[],
  configs: Config[],
  prompts: Prompt[],
): BlankDefaults {
  return {
    requirement_id: requirements[0]?.id ?? null,
    color_id: colors[0]?.id ?? null,
    html_config_id: (configs.find((item) => item.type === 'html' && item.is_default) || configs.find((item) => item.type === 'html'))?.id ?? null,
    image_config_id: (configs.find((item) => item.type === 'image' && item.is_default) || configs.find((item) => item.type === 'image'))?.id ?? null,
    designer_prompt_id: defaultPromptId(prompts, 'designer'),
    html_prompt_id: defaultPromptId(prompts, 'html_agent'),
  };
}

function createBlankVariant(index: number, defaults: BlankDefaults): BlankVariantForm {
  const letter = VARIANT_LETTERS[index] || String(index + 1);
  return {
    key: `variant-${letter.toLowerCase()}`,
    label: `${letter} · Variant`,
    goal: index === 0
      ? 'Baseline generation plan for comparison.'
      : 'Alternative generation plan for side-by-side review.',
    comparison_variable: index === 0 ? 'Baseline' : 'Prompt / route change',
    generation_mode: 'auto',
    requirement_id: null,
    color_id: null,
    config_id: defaults.html_config_id,
    engine: 'html',
    strategy: 'html_default',
    image_renderer: 'banana',
    designer_prompt_id: defaults.designer_prompt_id,
    html_prompt_id: defaults.html_prompt_id,
  };
}

function ensureBlankVariants(
  variants: BlankVariantForm[],
  count: number,
  defaults: BlankDefaults,
): BlankVariantForm[] {
  return Array.from({ length: count }, (_, index) => {
    const current = variants[index] || createBlankVariant(index, defaults);
    const engine = current.engine || 'html';
    const strategy = engine === 'html' ? 'html_default' : (current.strategy || 'image_5_0');
    const imageAutoSupported = engine === 'image' && strategy === 'image_5_0';
    const generationMode = engine === 'image' && !imageAutoSupported ? 'manual' : (current.generation_mode || 'auto');
    const autoMode = generationMode === 'auto' && (engine === 'html' || imageAutoSupported);
    return {
      ...current,
      key: current.key || `variant-${index}`,
      engine,
      strategy,
      generation_mode: generationMode,
      image_renderer: current.image_renderer || 'banana',
      requirement_id: autoMode ? null : (current.requirement_id ?? defaults.requirement_id),
      color_id: engine === 'html' && autoMode ? null : (current.color_id ?? defaults.color_id),
      config_id: current.config_id ?? (engine === 'image' ? defaults.image_config_id : defaults.html_config_id),
      designer_prompt_id: current.designer_prompt_id ?? defaults.designer_prompt_id,
      html_prompt_id: current.html_prompt_id ?? defaults.html_prompt_id,
    };
  });
}

function runDeckSnapshotKey(run: EvaluationHistoryRunCandidate): string {
  return run.deck_snapshot_fingerprint || `deck:${run.deck_id}`;
}

function runDeckSnapshotLabel(run: EvaluationHistoryRunCandidate): string {
  return run.deck_snapshot_label || `${run.deck_title || `Deck #${run.deck_id}`} · ${run.deck_snapshot_slide_count ?? run.progress?.total ?? '?'} slides`;
}

function defaultBlankForm(): BlankFormState {
  return {
    title: '',
    goal: 'Compare variants page by page and keep the strongest representative attempt from each variant.',
    deck_id: null,
    repeat_count: 1,
    variant_count: 2,
    variants: [],
  };
}

function defaultRepresentativeMap(evaluation: EvaluationDetail): Record<number, number> {
  return Object.fromEntries(
    evaluation.variants.flatMap((variant) => {
      const firstAttemptId = variant.representative_attempt_id || variant.attempts[0]?.id;
      return firstAttemptId ? [[variant.id, firstAttemptId]] : [];
    }),
  );
}

function SlideCompareCard({
  variant,
  attempt,
  slide,
  scale,
  isRepresentative,
  slideTags,
  qaItems,
  onSetRepresentative,
  onOpen,
}: {
  variant: EvaluationVariant;
  attempt: EvaluationAttempt;
  slide: RunSlide;
  scale: number;
  isRepresentative: boolean;
  slideTags: Array<{ id: number; label: string; source: string }>;
  qaItems: Array<{ id: number; verdict: string }>;
  onSetRepresentative: () => void;
  onOpen: () => void;
}) {
  const visualUrl = slideVisualUrl(slide);
  const issue = slideHasIssue(slide, attempt);

  return (
    <article className={`evaluation-slide-card ${isRepresentative ? 'is-representative' : ''}`}>
      <div className="evaluation-slide-card-head">
        <div>
          <strong>{attempt.label || `Attempt ${attempt.attempt_index}`}</strong>
          <span>{variant.label} · Run {attempt.run_id || 'pending'}</span>
        </div>
        <Space size={4} wrap>
          <Tag color={statusColor(attempt.run_status || attempt.status)}>{attempt.run_status || attempt.status || 'pending'}</Tag>
          {isRepresentative ? (
            <Tag color="blue">Representative</Tag>
          ) : (
            <Tooltip title={`Use ${attempt.label} as representative`}>
              <Button size="small" icon={<PlusOutlined />} aria-label={`Use ${attempt.label} as representative`} onClick={onSetRepresentative} />
            </Tooltip>
          )}
        </Space>
      </div>

      <div className="evaluation-slide-scroll">
        <button
          className="evaluation-slide-image-button"
          style={{ '--evaluation-scale': scale / 100 } as React.CSSProperties}
          type="button"
          onClick={onOpen}
        >
          <div className="evaluation-slide-artboard">
            {visualUrl ? (
              <img src={visualUrl} alt={`${attempt.label} slide ${slide.position}`} />
            ) : (
              <div className="evaluation-slide-empty">
                <strong>No visual artifact</strong>
                <span>{slide.clean_html ? 'Clean HTML exists without a PNG preview.' : 'Waiting for generated slide output.'}</span>
              </div>
            )}
          </div>
        </button>
      </div>

      <div className="evaluation-slide-meta">
        <Tag color={issue ? 'orange' : 'success'}>{issue ? 'Needs review' : 'No issue detected'}</Tag>
        {slideTags.length ? slideTags.map((tag) => (
          <Tag key={tag.id} icon={<TagsOutlined />} color={tag.source === 'machine' ? 'geekblue' : 'orange'}>{tag.label}</Tag>
        )) : <Tag icon={<TagsOutlined />}>No human tags</Tag>}
        {qaItems.length ? qaItems.map((qa) => (
          <Tag key={qa.id} color={qa.verdict === 'fail' ? 'geekblue' : 'success'}>QA {qa.verdict}</Tag>
        )) : <Tag>QA not run</Tag>}
      </div>
      <div className="evaluation-slide-context">
        <span>Req: {snapshotEntityLabel(attempt.snapshot, 'requirement')}</span>
        <span>Color: {snapshotEntityLabel(attempt.snapshot, 'color')}</span>
      </div>
    </article>
  );
}

const EvaluationsPage: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const { id } = useParams();

  const pageMode: PageMode = location.pathname.endsWith('/new/blank')
    ? 'blank'
    : location.pathname.endsWith('/new/history')
      ? 'history'
      : id
        ? 'detail'
        : 'list';
  const evaluationId = id ? Number(id) : null;

  const [evaluations, setEvaluations] = useState<EvaluationListItem[]>([]);
  const [listLoading, setListLoading] = useState(false);
  const [listError, setListError] = useState<string | null>(null);

  const [detail, setDetail] = useState<EvaluationDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [reviewMode, setReviewMode] = useState<ReviewMode>('representative');
  const [columns, setColumns] = useState(2);
  const [scale, setScale] = useState(DEFAULT_SCALE_BY_COLUMN[2]);
  const [issueOnly, setIssueOnly] = useState(false);
  const [representativeByVariant, setRepresentativeByVariant] = useState<Record<number, number>>({});
  const [visualTarget, setVisualTarget] = useState<VisualTarget | null>(null);
  const [reviewDrawerOpen, setReviewDrawerOpen] = useState(false);
  const [exportDrawerOpen, setExportDrawerOpen] = useState(false);
  const [noteText, setNoteText] = useState('');
  const [tagLabel, setTagLabel] = useState('');
  const [tagAttemptId, setTagAttemptId] = useState<number | null>(null);
  const [tagSlidePosition, setTagSlidePosition] = useState<number>(1);
  const [exportScope, setExportScope] = useState<'current_slide' | 'all_slides'>('current_slide');
  const [exportSlidePosition, setExportSlidePosition] = useState<number>(1);
  const [exportFields, setExportFields] = useState<string[]>(['column_label', 'prompt', 'model', 'strategy', 'page_number']);
  const [exporting, setExporting] = useState(false);
  const [qaRunning, setQaRunning] = useState(false);
  const [qaScope, setQaScope] = useState<'all_slides' | 'selected_slides'>('all_slides');
  const [qaSlidePositions, setQaSlidePositions] = useState<number[]>([]);

  const [decks, setDecks] = useState<Deck[]>([]);
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [colors, setColors] = useState<Color[]>([]);
  const [configs, setConfigs] = useState<Config[]>([]);
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [blankForm, setBlankForm] = useState<BlankFormState>(() => defaultBlankForm());
  const [blankResourcesLoaded, setBlankResourcesLoaded] = useState(false);
  const [blankLoading, setBlankLoading] = useState(false);
  const [blankError, setBlankError] = useState<string | null>(null);

  const [historyRuns, setHistoryRuns] = useState<EvaluationHistoryRunCandidate[]>([]);
  const [historyRunsLoaded, setHistoryRunsLoaded] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [historyTitle, setHistoryTitle] = useState('');
  const [historyGoal, setHistoryGoal] = useState('Compare selected completed runs page by page and choose one representative output per variant.');
  const [historyDeckSnapshotKey, setHistoryDeckSnapshotKey] = useState<string | null>(null);
  const [historySelectedRunIds, setHistorySelectedRunIds] = useState<number[]>([]);
  const [historyVariantInputs, setHistoryVariantInputs] = useState<Record<number, HistoryVariantInput>>({});
  const [submitting, setSubmitting] = useState(false);

  const blankDefaults = useMemo(
    () => getBlankDefaults(requirements, colors, configs, prompts),
    [requirements, colors, configs, prompts],
  );
  const designerPrompts = useMemo(() => promptsForRole(prompts, 'designer'), [prompts]);
  const htmlPrompts = useMemo(() => promptsForRole(prompts, 'html_agent'), [prompts]);

  const fetchEvaluationList = useCallback(async () => {
    setListLoading(true);
    setListError(null);
    try {
      const data = await api.evaluations.list();
      setEvaluations(data);
    } catch (err: unknown) {
      setListError(err instanceof Error ? err.message : String(err));
    } finally {
      setListLoading(false);
    }
  }, []);

  const fetchEvaluationDetail = useCallback(async (targetId: number) => {
    setDetailLoading(true);
    setDetailError(null);
    try {
      const data = await api.evaluations.get(targetId);
      setDetail(data);
      setRepresentativeByVariant(defaultRepresentativeMap(data));
    } catch (err: unknown) {
      setDetailError(err instanceof Error ? err.message : String(err));
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const fetchBlankResources = useCallback(async () => {
    setBlankLoading(true);
    setBlankError(null);
    try {
      const [deckData, requirementData, colorData, configData, promptData] = await Promise.all([
        api.decks.list(),
        api.requirements.list(),
        api.colors.list(),
        api.configs.list(),
        api.prompts.list({ status: 'active' }),
      ]);
      const activePrompts = promptData.filter((prompt) => prompt.status === 'active');
      setDecks(deckData);
      setRequirements(requirementData);
      setColors(colorData);
      setConfigs(configData);
      setPrompts(activePrompts);
      const defaults = getBlankDefaults(requirementData, colorData, configData, activePrompts);
      setBlankForm((current) => ({
        ...current,
        title: current.title || `Evaluation ${new Date().toLocaleDateString()}`,
        deck_id: current.deck_id ?? deckData[0]?.id ?? null,
        variants: ensureBlankVariants(current.variants, current.variant_count, defaults),
      }));
      setBlankResourcesLoaded(true);
    } catch (err: unknown) {
      setBlankError(err instanceof Error ? err.message : String(err));
    } finally {
      setBlankLoading(false);
    }
  }, []);

  const fetchHistoryRuns = useCallback(async () => {
    setHistoryLoading(true);
    setHistoryError(null);
    try {
      const data = await api.evaluations.historyRuns();
      setHistoryRuns(data);
      setHistoryRunsLoaded(true);
      setHistoryTitle((current) => current || `History Evaluation ${new Date().toLocaleDateString()}`);
      setHistoryDeckSnapshotKey((current) => {
        if (current) return current;
        const firstSelectable = data.find((run) => run.status === 'completed' && runDisplayableComplete(run));
        return firstSelectable ? runDeckSnapshotKey(firstSelectable) : null;
      });
    } catch (err: unknown) {
      setHistoryError(err instanceof Error ? err.message : String(err));
    } finally {
      setHistoryLoading(false);
    }
  }, []);

  useEffect(() => {
    if (pageMode === 'list') {
      queueMicrotask(() => {
        void fetchEvaluationList();
      });
    }
  }, [fetchEvaluationList, pageMode]);

  useEffect(() => {
    if (pageMode === 'detail' && evaluationId) {
      queueMicrotask(() => {
        void fetchEvaluationDetail(evaluationId);
      });
    }
  }, [evaluationId, fetchEvaluationDetail, pageMode]);

  useEffect(() => {
    if (pageMode === 'blank' && !blankResourcesLoaded) {
      queueMicrotask(() => {
        void fetchBlankResources();
      });
    }
  }, [blankResourcesLoaded, fetchBlankResources, pageMode]);

  useEffect(() => {
    if (pageMode === 'history' && !historyRunsLoaded) {
      queueMicrotask(() => {
        void fetchHistoryRuns();
      });
    }
  }, [fetchHistoryRuns, historyRunsLoaded, pageMode]);

  const selectedHistoryRuns = useMemo(
    () => historySelectedRunIds
      .map((runId) => historyRuns.find((run) => run.id === runId))
      .filter((run): run is EvaluationHistoryRunCandidate => Boolean(run)),
    [historyRuns, historySelectedRunIds],
  );

  const historyDeckGroups = useMemo(() => {
    const groups = new Map<string, { value: string; label: string; run_count: number }>();
    historyRuns.forEach((run) => {
      const key = runDeckSnapshotKey(run);
      const existing = groups.get(key);
      if (existing) {
        existing.run_count += 1;
      } else {
        groups.set(key, { value: key, label: runDeckSnapshotLabel(run), run_count: 1 });
      }
    });
    return Array.from(groups.values()).map((group) => ({
      value: group.value,
      label: `${group.label} · ${group.run_count} run${group.run_count === 1 ? '' : 's'}`,
    }));
  }, [historyRuns]);

  const filteredHistoryRuns = useMemo(
    () => historyDeckSnapshotKey
      ? historyRuns.filter((run) => runDeckSnapshotKey(run) === historyDeckSnapshotKey)
      : [],
    [historyDeckSnapshotKey, historyRuns],
  );

  const selectedHistoryDeckLabel = useMemo(
    () => historyDeckGroups.find((group) => group.value === historyDeckSnapshotKey)?.label || '',
    [historyDeckGroups, historyDeckSnapshotKey],
  );

  const allDetailAttempts = useMemo(
    () => detail?.variants.flatMap((variant) => variant.attempts.map((attempt) => ({ variant, attempt }))) || [],
    [detail],
  );

  const representativeAttempts = useMemo(
    () => detail?.variants.flatMap((variant) => {
      const representativeId = representativeByVariant[variant.id] || variant.attempts[0]?.id;
      const attempt = variant.attempts.find((item) => item.id === representativeId);
      return attempt ? [{ variant, attempt }] : [];
    }) || [],
    [detail, representativeByVariant],
  );

  const displayedAttempts = reviewMode === 'representative' ? representativeAttempts : allDetailAttempts;

  const visibleSlidePositions = useMemo(() => {
    const positions = Array.from(new Set(
      displayedAttempts.flatMap(({ attempt }) => attempt.slides.map((slide) => slide.position)),
    )).sort((a, b) => a - b);
    if (!issueOnly) return positions;
    return positions.filter((position) =>
      displayedAttempts.some(({ attempt }) => {
        const slide = attempt.slides.find((item) => item.position === position);
        return slide ? slideHasIssue(slide, attempt) || Boolean(detail?.slide_tags?.some((tag) => tag.attempt_id === attempt.id && tag.slide_position === position)) || Boolean(detail?.machine_qa?.some((qa) => qa.attempt_id === attempt.id && qa.slide_position === position && qa.verdict === 'fail')) : false;
      }),
    );
  }, [detail?.machine_qa, detail?.slide_tags, displayedAttempts, issueOnly]);

  const qaSlidePositionOptions = useMemo(
    () => Array.from(new Set(
      displayedAttempts.flatMap(({ attempt }) => attempt.slides.map((slide) => slide.position)),
    )).sort((a, b) => a - b),
    [displayedAttempts],
  );

  const machineIssueCount = useMemo(
    () => (detail?.machine_qa || []).filter((item) => item.verdict === 'fail').length,
    [detail?.machine_qa],
  );
  const currentSlidePosition = visibleSlidePositions[0] || exportSlidePosition || 1;

  const patchBlankVariant = (index: number, patch: Partial<BlankVariantForm>) => {
    setBlankForm((current) => ({
      ...current,
      variants: current.variants.map((variant, variantIndex) =>
        variantIndex === index ? { ...variant, ...patch } : variant,
      ),
    }));
  };

  const defaultConfigIdForEngine = (
    engine: RouteEngine,
    strategy: RouteStrategy = 'image_5_0',
    imageRenderer: ImageRenderer = 'banana',
  ): number | null => {
    const routeConfigs = configs.filter((config) => {
      if (config.type !== engine) return false;
      if (engine !== 'image') return true;
      const renderer = configImageRenderer(config);
      if (strategy !== 'image_5_0') return renderer !== 'gpt_image_2';
      return renderer === imageRenderer;
    });
    return (routeConfigs.find((config) => config.is_default) || routeConfigs[0])?.id ?? null;
  };

  const variantRequiresRequirement = (variant: BlankVariantForm): boolean =>
    variant.generation_mode === 'manual';

  const handleBlankVariantCount = (nextCount: number) => {
    setBlankForm((current) => ({
      ...current,
      variant_count: nextCount,
      variants: ensureBlankVariants(current.variants, nextCount, blankDefaults),
    }));
  };

  const handleVariantEngineChange = (index: number, engine: RouteEngine) => {
    const strategy = engine === 'html' ? 'html_default' : 'image_5_0';
    const imageRenderer: ImageRenderer = 'banana';
    patchBlankVariant(index, {
      engine,
      generation_mode: engine === 'html' ? 'auto' : 'manual',
      strategy,
      image_renderer: imageRenderer,
      config_id: defaultConfigIdForEngine(engine, strategy, imageRenderer),
      requirement_id: engine === 'html' ? null : (blankForm.variants[index]?.requirement_id ?? blankDefaults.requirement_id),
      color_id: engine === 'html' ? null : (blankForm.variants[index]?.color_id ?? blankDefaults.color_id),
      designer_prompt_id: blankDefaults.designer_prompt_id,
      html_prompt_id: blankDefaults.html_prompt_id,
    });
  };

  const handleVariantStrategyChange = (index: number, strategy: RouteStrategy) => {
    const imageRenderer = strategy === 'image_5_0' ? blankForm.variants[index]?.image_renderer || 'banana' : 'banana';
    patchBlankVariant(index, {
      strategy,
      image_renderer: imageRenderer,
      generation_mode: strategy === 'image_5_0' ? blankForm.variants[index]?.generation_mode || 'manual' : 'manual',
      requirement_id: blankForm.variants[index]?.generation_mode === 'auto' && strategy === 'image_5_0'
        ? null
        : blankForm.variants[index]?.requirement_id ?? blankDefaults.requirement_id,
      config_id: defaultConfigIdForEngine('image', strategy, imageRenderer),
    });
  };

  const handleVariantImageRendererChange = (index: number, imageRenderer: ImageRenderer) => {
    const strategy = blankForm.variants[index]?.strategy || 'image_5_0';
    patchBlankVariant(index, {
      image_renderer: imageRenderer,
      config_id: defaultConfigIdForEngine('image', strategy, imageRenderer),
    });
  };

  const setHistoryRunSelection = (keys: React.Key[]) => {
    const nextIds = keys
      .map((key) => Number(key))
      .filter((runId) => Number.isFinite(runId))
      .filter((runId) => {
        const run = historyRuns.find((item) => item.id === runId);
        return Boolean(run && !historyRunDisabledReason(run, historyDeckSnapshotKey, historySelectedRunIds));
      })
      .slice(0, 4);
    setHistorySelectedRunIds(nextIds);
    setHistoryVariantInputs((current) => {
      const next = { ...current };
      nextIds.forEach((runId, index) => {
        if (!next[runId]) {
          const run = historyRuns.find((item) => item.id === runId);
          const letter = VARIANT_LETTERS[index] || String(index + 1);
          next[runId] = {
            label: `${letter} · Run ${runId}`,
            goal: `Review ${run?.deck_title || 'selected deck'} output from Run ${runId}.`,
          };
        }
      });
      return next;
    });
  };

  const handleHistoryDeckSnapshotChange = (value: string) => {
    setHistoryDeckSnapshotKey(value);
    setHistorySelectedRunIds([]);
    setHistoryVariantInputs({});
  };

  const updateHistoryVariantInput = (runId: number, patch: Partial<HistoryVariantInput>) => {
    setHistoryVariantInputs((current) => ({
      ...current,
      [runId]: {
        label: current[runId]?.label || `Run ${runId}`,
        goal: current[runId]?.goal || historyGoal,
        ...patch,
      },
    }));
  };

  const canCreateBlank = Boolean(
    blankForm.title.trim() &&
    blankForm.goal.trim() &&
    blankForm.deck_id &&
    blankForm.repeat_count >= 1 &&
    blankForm.repeat_count <= 5 &&
    blankForm.variants.length >= 2 &&
    blankForm.variants.length <= 4 &&
    blankForm.variants.every((variant) =>
      variant.label.trim() &&
      variant.goal.trim() &&
      (!variantRequiresRequirement(variant) || variant.requirement_id) &&
      variant.config_id &&
      (variant.engine === 'image' || (variant.designer_prompt_id && variant.html_prompt_id)),
    )
  );

  const canCreateHistory = Boolean(
    historyTitle.trim() &&
    historyGoal.trim() &&
    historyDeckSnapshotKey &&
    selectedHistoryRuns.length >= 2 &&
    selectedHistoryRuns.length <= 4 &&
    selectedHistoryRuns.every((run) => !historyRunDisabledReason(run, historyDeckSnapshotKey, historySelectedRunIds)) &&
    selectedHistoryRuns.every((run) => runDeckSnapshotKey(run) === historyDeckSnapshotKey) &&
    selectedHistoryRuns.every((run) => historyVariantInputs[run.id]?.label.trim() && historyVariantInputs[run.id]?.goal.trim()),
  );

  const createBlankEvaluation = async () => {
    if (!canCreateBlank || !blankForm.deck_id) return;
    setSubmitting(true);
    try {
      const payload: EvaluationBlankCreatePayload = {
        title: blankForm.title.trim(),
        goal: blankForm.goal.trim(),
        deck_id: blankForm.deck_id,
        repeat_count: blankForm.repeat_count,
        variants: blankForm.variants.map((variant) => {
          const strategy = variant.engine === 'html' ? 'html_default' : variant.strategy;
          const imageRouteMetadata = variant.engine === 'image'
            ? {
                image_renderer: variant.image_renderer,
                ...(variant.image_renderer === 'gpt_image_2'
                  ? {
                      provider_channel: 'zenmux_images_api',
                      request_mode: 'blueprint_first',
                    }
                  : {}),
              }
            : {};
          const base = {
            label: variant.label.trim(),
            goal: variant.goal.trim(),
            comparison_variable: variant.comparison_variable.trim() || null,
            config_id: Number(variant.config_id),
            engine: variant.engine,
            strategy,
            route_metadata: {
              route_label: routeLabel(variant.engine, strategy),
              reference_map: strategyReferenceMap[strategy],
              image_prompt_roles: variant.engine === 'image' ? imageStrategyPromptRoles[strategy] : undefined,
              ...imageRouteMetadata,
            },
            ...(variant.engine === 'html' ? {
              designer_prompt_id: Number(variant.designer_prompt_id),
              html_prompt_id: Number(variant.html_prompt_id),
            } : {}),
          };
          const autoMode = variant.generation_mode === 'auto' && (
            variant.engine === 'html' || (variant.engine === 'image' && strategy === 'image_5_0')
          );
          if (autoMode) {
            return {
              ...base,
              mode: 'auto' as const,
              auto_candidate_count: 1,
              ...(variant.engine === 'image' && variant.color_id ? { auto_color_id: Number(variant.color_id) } : {}),
            };
          }
          return {
            ...base,
            mode: 'manual' as const,
            requirement_id: Number(variant.requirement_id),
            ...(variant.color_id ? { color_id: Number(variant.color_id) } : {}),
          };
        }),
      };
      const created = await api.evaluations.createBlank(payload);
      message.success(`Started evaluation #${created.id}`);
      navigate(`/evaluations/${created.id}`);
    } catch (err: unknown) {
      message.error(`Failed to create evaluation: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSubmitting(false);
    }
  };

  const createHistoryEvaluation = async () => {
    if (!canCreateHistory) return;
    setSubmitting(true);
    try {
      const payload: EvaluationHistoryCreatePayload = {
        title: historyTitle.trim(),
        goal: historyGoal.trim(),
        variants: selectedHistoryRuns.map((run) => ({
          run_id: run.id,
          label: historyVariantInputs[run.id].label.trim(),
          goal: historyVariantInputs[run.id].goal.trim(),
        })),
      };
      const created = await api.evaluations.createHistory(payload);
      message.success(`Created evaluation #${created.id}`);
      navigate(`/evaluations/${created.id}`);
    } catch (err: unknown) {
      message.error(`Failed to create evaluation: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSubmitting(false);
    }
  };

  const handleColumnChange = (value: number) => {
    setColumns(value);
    setScale(DEFAULT_SCALE_BY_COLUMN[value] || scale);
  };

  const applyDetail = (nextDetail: EvaluationDetail) => {
    setDetail(nextDetail);
    setRepresentativeByVariant(defaultRepresentativeMap(nextDetail));
  };

  const updateRepresentative = async (variant: EvaluationVariant, attemptId: number) => {
    if (!detail) return;
    setRepresentativeByVariant((current) => ({ ...current, [variant.id]: attemptId }));
    try {
      const nextDetail = await api.evaluations.setRepresentative(detail.id, variant.id, attemptId);
      applyDetail(nextDetail);
      message.success('Representative saved');
    } catch (err: unknown) {
      message.error(`Failed to save representative: ${err instanceof Error ? err.message : String(err)}`);
      setRepresentativeByVariant(defaultRepresentativeMap(detail));
    }
  };

  const updateVariantLabel = async (variant: EvaluationVariant, label: string) => {
    if (!detail || !label.trim() || label.trim() === variant.label) return;
    try {
      const nextDetail = await api.evaluations.updateVariant(detail.id, variant.id, { label: label.trim() });
      applyDetail(nextDetail);
      message.success('Column label saved');
    } catch (err: unknown) {
      message.error(`Failed to save label: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const saveNote = async () => {
    if (!detail || !noteText.trim()) return;
    try {
      const nextDetail = await api.evaluations.addNote(detail.id, { note: noteText.trim() });
      applyDetail(nextDetail);
      setNoteText('');
      message.success('Note saved');
    } catch (err: unknown) {
      message.error(`Failed to save note: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const saveTag = async () => {
    if (!detail || !tagLabel.trim() || !tagAttemptId) return;
    try {
      const nextDetail = await api.evaluations.addSlideTag(detail.id, {
        attempt_id: tagAttemptId,
        slide_position: tagSlidePosition,
        label: tagLabel.trim(),
        source: 'human',
      });
      applyDetail(nextDetail);
      setTagLabel('');
      message.success('Tag saved');
    } catch (err: unknown) {
      message.error(`Failed to save tag: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const downloadExport = async () => {
    if (!detail) return;
    setExporting(true);
    try {
      const payload: EvaluationExportPayload = {
        scope: exportScope,
        slide_position: exportScope === 'current_slide' ? exportSlidePosition : undefined,
        metadata_fields: exportFields,
      };
      const result = await api.evaluations.export(detail.id, payload);
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      message.success('Evaluation export started');
    } catch (err: unknown) {
      message.error(`Export failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setExporting(false);
    }
  };

  const selectedQaPositions = useMemo(
    () => qaSlidePositions.filter((position) => qaSlidePositionOptions.includes(position)),
    [qaSlidePositions, qaSlidePositionOptions],
  );
  const qaTargetPositions = qaScope === 'all_slides' ? qaSlidePositionOptions : selectedQaPositions;

  const runMachineQaForScope = async () => {
    if (!detail) return;
    const attemptIds = displayedAttempts.map(({ attempt }) => attempt.id);
    if (!attemptIds.length) {
      message.warning('No visible attempts to check');
      return;
    }
    if (!qaTargetPositions.length) {
      message.warning('Select at least one slide for Machine QA');
      return;
    }
    setQaRunning(true);
    try {
      const nextDetail = await api.evaluations.runMachineQa(detail.id, {
        scope: qaScope,
        attempt_ids: attemptIds,
        ...(qaScope === 'selected_slides' ? { slide_positions: qaTargetPositions } : {}),
      });
      applyDetail(nextDetail);
      message.success(`Machine QA checked ${qaTargetPositions.length} slide${qaTargetPositions.length === 1 ? '' : 's'}`);
    } catch (err: unknown) {
      message.error(`Machine QA failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setQaRunning(false);
    }
  };

  const downloadSlideEvidence = async (slide: RunSlide) => {
    try {
      const result = await api.runSlides.evidenceDownload(slide.id);
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      message.success(`Slide ${slide.position} evidence download started`);
    } catch (err: unknown) {
      message.error(`Evidence download failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const pageTitle = pageMode === 'blank'
    ? 'Create Blank Evaluation'
    : pageMode === 'history'
      ? 'Create From History'
      : pageMode === 'detail'
        ? detail?.title || 'Evaluation Detail'
        : 'Evaluations';
  const pageSubtitle = pageMode === 'blank'
    ? 'Start a shared-deck multi-variant evaluation with one normalized generation plan per variant.'
    : pageMode === 'history'
      ? 'Select 2 to 4 completed, displayable runs from one deck.'
      : pageMode === 'detail'
        ? detail?.goal || 'Compare variants, attempts, and slide outputs.'
        : 'Saved comparison records for deck-level review.';

  const renderList = () => (
    <section className="evaluation-panel">
      <div className="evaluation-section-head">
        <div>
          <h3>Evaluation records</h3>
          <p>Each record compares runs from a single deck.</p>
        </div>
        <Space wrap>
          <Button icon={<HistoryOutlined />} onClick={() => navigate('/evaluations/new/history')}>From History</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/evaluations/new/blank')}>Blank Evaluation</Button>
        </Space>
      </div>
      {listError && <Alert type="error" showIcon message={listError} />}
      <Table<EvaluationListItem>
        className="responsive-table evaluation-table"
        rowKey="id"
        loading={listLoading}
        dataSource={evaluations}
        pagination={{ pageSize: 10 }}
        locale={{ emptyText: <Empty description="No evaluations yet" /> }}
        columns={[
          {
            title: 'Evaluation',
            dataIndex: 'title',
            render: (title: string, record) => (
              <div className="evaluation-table-title">
                <strong>{title}</strong>
                <span>{record.goal}</span>
              </div>
            ),
          },
          { title: 'Deck', dataIndex: 'deck_title', render: (value?: string) => value || '-' },
          { title: 'Status', dataIndex: 'status', render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
          { title: 'Variants', dataIndex: 'variant_count', render: (value?: number) => value ?? 0 },
          { title: 'Attempts', dataIndex: 'attempt_count', render: (value?: number) => value ?? 0 },
          { title: 'Updated', dataIndex: 'updated_at', render: (value: string | undefined, record) => formatDate(value || record.created_at) },
          {
            title: '',
            key: 'action',
            render: (_, record) => (
              <Button size="small" icon={<EyeOutlined />} onClick={() => navigate(`/evaluations/${record.id}`)}>
                Open
              </Button>
            ),
          },
        ]}
      />
    </section>
  );

  const renderBlankCreate = () => {
    const deckOptions = decks.map((deck) => ({ value: deck.id, label: deck.title }));
    const requirementOptions = requirements.map((requirement) => ({ value: requirement.id, label: requirement.title }));
    const colorOptions = colors.map((color) => ({ value: color.id, label: color.title }));
    const designerPromptOptions = designerPrompts.map((prompt) => ({ value: prompt.id, label: promptOptionLabel(prompt) }));
    const htmlPromptOptions = htmlPrompts.map((prompt) => ({ value: prompt.id, label: promptOptionLabel(prompt) }));

    return (
      <Spin spinning={blankLoading}>
        <section className="evaluation-panel">
          <div className="evaluation-section-head">
            <div>
              <h3>Blank setup</h3>
              <p>Blank is the default create path. History remains available as a fallback.</p>
            </div>
            <Space wrap>
              <Button icon={<HistoryOutlined />} onClick={() => navigate('/evaluations/new/history')}>Use History</Button>
              <Button
                type="primary"
                icon={<ThunderboltOutlined />}
                disabled={!canCreateBlank}
                loading={submitting}
                onClick={createBlankEvaluation}
              >
                Start Evaluation Runs
              </Button>
            </Space>
          </div>
          {blankError && <Alert type="error" showIcon message={blankError} />}
          <div className="evaluation-create-stack">
            <div className="evaluation-plan-strip">
              <div>
                <span>Planned runs</span>
                <strong>{blankForm.variant_count * blankForm.repeat_count}</strong>
                <p>{blankForm.variant_count} variants · {blankForm.repeat_count} attempt{blankForm.repeat_count > 1 ? 's' : ''} each</p>
              </div>
              <div>
                <span>Generation contract</span>
                <p>One shared deck. Each Variant owns its requirement, optional color, config, route, strategy, and HTML route prompts.</p>
              </div>
              <Tag color="blue">Blank is default</Tag>
            </div>
            <section className="evaluation-create-main">
              <div className="evaluation-form-grid">
                <label>
                  <span>Title</span>
                  <Input
                    id="evaluation-blank-title"
                    name="evaluation_blank_title"
                    value={blankForm.title}
                    onChange={(event) => setBlankForm((current) => ({ ...current, title: event.target.value }))}
                  />
                </label>
                <label>
                  <span>Shared deck</span>
                  <Select
                    value={blankForm.deck_id ?? undefined}
                    placeholder="Select deck"
                    options={deckOptions}
                    showSearch
                    optionFilterProp="label"
                    onChange={(value: number) => setBlankForm((current) => ({ ...current, deck_id: value }))}
                  />
                </label>
                <label className="evaluation-form-wide">
                  <span>Evaluation goal</span>
                  <Input.TextArea
                    id="evaluation-blank-goal"
                    name="evaluation_blank_goal"
                    rows={3}
                    value={blankForm.goal}
                    onChange={(event) => setBlankForm((current) => ({ ...current, goal: event.target.value }))}
                  />
                </label>
                <label>
                  <span>Variants</span>
                  <Segmented
                    block
                    value={blankForm.variant_count}
                    options={[2, 3, 4].map((value) => ({ value, label: `${value}` }))}
                    onChange={(value) => handleBlankVariantCount(Number(value))}
                  />
                </label>
                <label>
                  <span>Repeat</span>
                  <InputNumber
                    id="evaluation-blank-repeat"
                    name="evaluation_blank_repeat"
                    min={1}
                    max={5}
                    value={blankForm.repeat_count}
                    onChange={(value) => setBlankForm((current) => ({ ...current, repeat_count: Number(value || 1) }))}
                  />
                </label>
              </div>

              <div className={`evaluation-variant-grid variant-count-${blankForm.variant_count}`}>
                {blankForm.variants.map((variant, index) => {
                  const configOptions = configs
                    .filter((config) => {
                      if (config.type !== variant.engine) return false;
                      if (variant.engine !== 'image') return true;
                      const renderer = configImageRenderer(config);
                      if (variant.strategy !== 'image_5_0') return renderer !== 'gpt_image_2';
                      return renderer === variant.image_renderer;
                    })
                    .map((config) => ({ value: config.id, label: `${config.name}${config.is_default ? ' · default' : ''}` }));
                  const strategyChoices = strategyOptions.filter((option) =>
                    variant.engine === 'html' ? option.value === 'html_default' : option.value !== 'html_default',
                  );
                  const imageAutoSupported = variant.engine === 'image' && variant.strategy === 'image_5_0';
                  const imageGptSupported = imageAutoSupported;
                  const autoMode = variant.generation_mode === 'auto' && (variant.engine === 'html' || imageAutoSupported);

                  return (
                    <article className="evaluation-variant-card" key={variant.key}>
                      <header className="evaluation-variant-card-head">
                        <div className="evaluation-variant-title">
                          <Tag color={index === 0 ? 'blue' : index === 1 ? 'gold' : index === 2 ? 'purple' : 'cyan'}>
                            {VARIANT_LETTERS[index]}
                          </Tag>
                          <Input
                            id={`evaluation-blank-${variant.key}-label`}
                            name={`evaluation_blank_${variant.key}_label`}
                            value={variant.label}
                            onChange={(event) => patchBlankVariant(index, { label: event.target.value })}
                          />
                        </div>
                        <label className="evaluation-engine-row">
                          <span>Engine</span>
                          <Segmented
                            block
                            value={variant.engine}
                            options={[
                              { value: 'html', label: 'HTML' },
                              { value: 'image', label: 'Image' },
                            ]}
                            onChange={(value) => handleVariantEngineChange(index, value as RouteEngine)}
                          />
                        </label>
                        <Space size={4} wrap>
                          <Tag color={variant.engine === 'html' ? 'blue' : 'gold'}>{routeLabel(variant.engine, variant.engine === 'html' ? 'html_default' : variant.strategy)}</Tag>
                          {autoMode
                            ? <Tag color="green">{variant.engine === 'image' ? 'Auto inputs' : 'Palette empty'}</Tag>
                            : <Tag>Manual inputs</Tag>}
                          {variant.engine === 'image' && variant.strategy === 'image_5_0' && (
                            <Tag color={variant.image_renderer === 'gpt_image_2' ? 'purple' : 'gold'}>
                              {variant.image_renderer === 'gpt_image_2' ? 'GPT Image 2' : 'Banana'}
                            </Tag>
                          )}
                        </Space>
                      </header>
                      <label>
                        <span>Variant goal</span>
                        <Input.TextArea
                          id={`evaluation-blank-${variant.key}-goal`}
                          name={`evaluation_blank_${variant.key}_goal`}
                          rows={2}
                          value={variant.goal}
                          onChange={(event) => patchBlankVariant(index, { goal: event.target.value })}
                        />
                      </label>
                      <div className="evaluation-variant-fields">
                        {variant.engine === 'html' ? (
                          <>
                            <label className="evaluation-full-row">
                              <span>Generation mode</span>
                              <Segmented
                                block
                                aria-label="Generation mode"
                                value={variant.generation_mode}
                                options={[
                                  { value: 'auto', label: 'Auto (Recommended)' },
                                  { value: 'manual', label: 'Manual' },
                                ]}
                                onChange={(value) => patchBlankVariant(index, {
                                  generation_mode: value as BlankGenerationMode,
                                  requirement_id: value === 'manual' ? (variant.requirement_id ?? blankDefaults.requirement_id) : null,
                                  color_id: value === 'manual' ? (variant.color_id ?? blankDefaults.color_id) : null,
                                })}
                              />
                            </label>
                            {variant.generation_mode === 'auto' ? (
                              <div className="evaluation-auto-summary">
                                <span>Auto inputs</span>
                                <Space size={4} wrap>
                                  <Tag color="blue">Requirement auto-generated</Tag>
                                  <Tag color="green">Palette empty/runtime</Tag>
                                </Space>
                              </div>
                            ) : (
                              <>
                                <label>
                                  <span>Requirement</span>
                                  <Select
                                    value={variant.requirement_id ?? undefined}
                                    placeholder="Requirement"
                                    options={requirementOptions}
                                    showSearch
                                    optionFilterProp="label"
                                    onChange={(value: number) => patchBlankVariant(index, { requirement_id: value })}
                                  />
                                </label>
                                <label>
                                  <span>Color (optional)</span>
                                  <Select
                                    value={variant.color_id ?? undefined}
                                    placeholder="Default (System)"
                                    allowClear
                                    options={colorOptions}
                                    showSearch
                                    optionFilterProp="label"
                                    onChange={(value?: number) => patchBlankVariant(index, { color_id: value ?? null })}
                                  />
                                </label>
                              </>
                            )}
                            <label>
                              <span>Config</span>
                              <Select
                                value={variant.config_id ?? undefined}
                                placeholder="Config"
                                options={configOptions}
                                showSearch
                                optionFilterProp="label"
                                onChange={(value: number) => patchBlankVariant(index, { config_id: value })}
                              />
                            </label>
                            <label>
                              <span>Default Designer Prompt</span>
                              <Select
                                aria-label="Select Designer Prompt"
                                value={variant.designer_prompt_id ?? undefined}
                                placeholder="Select Designer Prompt"
                                options={designerPromptOptions}
                                showSearch
                                optionFilterProp="label"
                                onChange={(value: number) => patchBlankVariant(index, { designer_prompt_id: value })}
                              />
                            </label>
                            <label>
                              <span>Default HTML Agent Prompt</span>
                              <Select
                                aria-label="Select HTML Agent Prompt"
                                value={variant.html_prompt_id ?? undefined}
                                placeholder="Select HTML Agent Prompt"
                                options={htmlPromptOptions}
                                showSearch
                                optionFilterProp="label"
                                onChange={(value: number) => patchBlankVariant(index, { html_prompt_id: value })}
                              />
                            </label>
                          </>
                        ) : (
                          <>
                            <label>
                              <span>Image strategy</span>
                              <Select
                                value={variant.strategy}
                                options={strategyChoices}
                                onChange={(value: RouteStrategy) => handleVariantStrategyChange(index, value)}
                              />
                            </label>
                            {imageGptSupported && (
                              <label className="evaluation-full-row">
                                <span>Image renderer</span>
                                <Segmented
                                  block
                                  aria-label="Image renderer"
                                  value={variant.image_renderer}
                                  options={[
                                    { value: 'banana', label: 'Banana' },
                                    { value: 'gpt_image_2', label: 'GPT Image 2' },
                                  ]}
                                  onChange={(value) => handleVariantImageRendererChange(index, value as ImageRenderer)}
                                />
                              </label>
                            )}
                            {imageAutoSupported ? (
                              <label className="evaluation-full-row">
                                <span>Generation mode</span>
                                <Segmented
                                  block
                                  aria-label="Image generation mode"
                                  value={variant.generation_mode}
                                  options={[
                                    { value: 'auto', label: 'Auto' },
                                    { value: 'manual', label: 'Manual' },
                                  ]}
                                  onChange={(value) => patchBlankVariant(index, {
                                    generation_mode: value as BlankGenerationMode,
                                    requirement_id: value === 'manual' ? (variant.requirement_id ?? blankDefaults.requirement_id) : null,
                                  })}
                                />
                              </label>
                            ) : (
                              <div className="evaluation-manual-summary">
                                <span>Generation mode</span>
                                <Space size={4} wrap>
                                  <Tag color="gold">Manual only</Tag>
                                  <Tag>Image Auto disabled</Tag>
                                </Space>
                              </div>
                            )}
                            <label>
                              <span>Config</span>
                              <Select
                                value={variant.config_id ?? undefined}
                                placeholder="Config"
                                options={configOptions}
                                showSearch
                                optionFilterProp="label"
                                onChange={(value: number) => patchBlankVariant(index, { config_id: value })}
                              />
                            </label>
                            {autoMode ? (
                              <div className="evaluation-auto-summary">
                                <span>Auto inputs</span>
                                <Space size={4} wrap>
                                  <Tag color="blue">Requirement auto-generated</Tag>
                                  <Tag color="green">Optional color</Tag>
                                  {variant.image_renderer === 'gpt_image_2' && <Tag color="purple">blueprint_first</Tag>}
                                </Space>
                              </div>
                            ) : (
                              <label>
                                <span>Requirement</span>
                                <Select
                                  value={variant.requirement_id ?? undefined}
                                  placeholder="Requirement"
                                  options={requirementOptions}
                                  showSearch
                                  optionFilterProp="label"
                                  onChange={(value: number) => patchBlankVariant(index, { requirement_id: value })}
                                />
                              </label>
                            )}
                            <label>
                              <span>Color (optional)</span>
                              <Select
                                value={variant.color_id ?? undefined}
                                placeholder="Default (System)"
                                allowClear
                                options={colorOptions}
                                showSearch
                                optionFilterProp="label"
                                onChange={(value?: number) => patchBlankVariant(index, { color_id: value ?? null })}
                              />
                            </label>
                            <div className="evaluation-image-prompt-summary">
                              <span>Image prompt roles</span>
                              <Space size={4} wrap>
                                {(imageStrategyPromptRoles[variant.strategy] || []).map((role) => {
                                  const prompt = promptsForRole(prompts, role).find((item) => item.is_default) || promptsForRole(prompts, role)[0];
                                  return (
                                    <Tag key={role} color="gold">
                                      {role.replaceAll('_', ' ')}{prompt ? ` · ${prompt.version}` : ''}
                                    </Tag>
                                  );
                                })}
                              </Space>
                            </div>
                          </>
                        )}
                      </div>
                      <label>
                        <span>Comparison variable</span>
                        <Input
                          id={`evaluation-blank-${variant.key}-comparison-variable`}
                          name={`evaluation_blank_${variant.key}_comparison_variable`}
                          value={variant.comparison_variable}
                          onChange={(event) => patchBlankVariant(index, { comparison_variable: event.target.value })}
                        />
                      </label>
                    </article>
                  );
                })}
              </div>
            </section>
          </div>
        </section>
      </Spin>
    );
  };

  const renderHistoryCreate = () => (
    <Spin spinning={historyLoading}>
      <section className="evaluation-panel">
          <div className="evaluation-section-head">
            <div>
              <h3>Create from History</h3>
              <p>Pick one historical Deck snapshot first, then select 2 to 4 completed runs from that exact snapshot.</p>
            </div>
          <Space wrap>
            <Button icon={<ThunderboltOutlined />} onClick={() => navigate('/evaluations/new/blank')}>Use Blank</Button>
            <Button type="primary" disabled={!canCreateHistory} loading={submitting} onClick={createHistoryEvaluation}>
              Create Evaluation
            </Button>
          </Space>
        </div>
        {historyError && <Alert type="error" showIcon message={historyError} />}
        <div className="evaluation-create-grid">
          <section className="evaluation-create-main">
            <div className="evaluation-form-grid">
              <label>
                <span>Title</span>
                <Input value={historyTitle} onChange={(event) => setHistoryTitle(event.target.value)} />
              </label>
              <label>
                <span>Compare deck snapshot</span>
                <Select
                  value={historyDeckSnapshotKey ?? undefined}
                  placeholder="Select a historical Deck snapshot"
                  options={historyDeckGroups}
                  showSearch
                  optionFilterProp="label"
                  onChange={handleHistoryDeckSnapshotChange}
                />
              </label>
              <label className="evaluation-form-wide">
                <span>Evaluation goal</span>
                <Input.TextArea rows={3} value={historyGoal} onChange={(event) => setHistoryGoal(event.target.value)} />
              </label>
            </div>

            <Table<EvaluationHistoryRunCandidate>
              className="responsive-table evaluation-table"
              size="small"
              rowKey="id"
              dataSource={filteredHistoryRuns}
              pagination={{ pageSize: 8 }}
              rowSelection={{
                selectedRowKeys: historySelectedRunIds,
                onChange: setHistoryRunSelection,
                getCheckboxProps: (run) => ({
                  disabled: Boolean(historyRunDisabledReason(run, historyDeckSnapshotKey, historySelectedRunIds)),
                }),
              }}
              columns={[
                { title: 'Run', dataIndex: 'id', render: (runId: number) => <strong>Run {runId}</strong> },
                { title: 'Deck', dataIndex: 'deck_title', render: (value?: string) => value || '-' },
                { title: 'Route', key: 'route', render: (_, run) => routeLabel(run.engine || 'html', run.strategy || 'html_default') },
                { title: 'Status', dataIndex: 'status', render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag> },
                {
                  title: 'Displayable',
                  key: 'displayable',
                  render: (_, run) => {
                    const complete = runDisplayableComplete(run);
                    const total = run.progress?.total ?? 0;
                    const displayable = run.progress?.displayable ?? 0;
                    return (
                      <Space size={4}>
                        {complete && <CheckCircleOutlined className="evaluation-ok-icon" />}
                        <span>{displayable}/{total}</span>
                      </Space>
                    );
                  },
                },
                {
                  title: 'Selection rule',
                  key: 'rule',
                  render: (_, run) => {
                    const reason = historyRunDisabledReason(run, historyDeckSnapshotKey, historySelectedRunIds);
                    return reason ? <Tag color="warning">{reason}</Tag> : <Tag color="success">Selectable</Tag>;
                  },
                },
              ]}
            />
          </section>

          <aside className="evaluation-create-side">
            <div>
              <span>Selected runs</span>
              <strong>{selectedHistoryRuns.length}/4</strong>
              {selectedHistoryDeckLabel && <p>{selectedHistoryDeckLabel}</p>}
              <Space size={4} wrap>
                {selectedHistoryRuns.map((run) => <Tag key={run.id}>Run {run.id}</Tag>)}
              </Space>
            </div>
            <div className="evaluation-history-variants">
              <span>Variant labels and goals</span>
              {selectedHistoryRuns.length ? selectedHistoryRuns.map((run, index) => {
                const input = historyVariantInputs[run.id] || {
                  label: `${VARIANT_LETTERS[index]} · Run ${run.id}`,
                  goal: historyGoal,
                };
                return (
                  <div key={run.id} className="evaluation-history-variant-input">
                    <Tag>{VARIANT_LETTERS[index]}</Tag>
                    <Input value={input.label} onChange={(event) => updateHistoryVariantInput(run.id, { label: event.target.value })} />
                    <Input.TextArea rows={2} value={input.goal} onChange={(event) => updateHistoryVariantInput(run.id, { goal: event.target.value })} />
                  </div>
                );
              }) : <p>Select 2 to 4 runs to configure variants.</p>}
            </div>
          </aside>
        </div>
      </section>
    </Spin>
  );

  const renderDetail = () => {
    if (detailLoading) return <Spin className="evaluation-page-spin" />;
    if (detailError) return <Alert type="error" showIcon message={detailError} />;
    if (!detail) return <Empty description="Evaluation not found" />;

    return (
      <main className="evaluation-detail">
        <section className="evaluation-context-bar">
          <span><strong>Deck</strong> {detail.deck_title || `Deck #${detail.deck_id}`}</span>
          <span><strong>Status</strong> <Tag color={statusColor(detail.status)}>{detail.status}</Tag></span>
          <span><strong>Variants</strong> {detail.variants.length}/4</span>
          <span><strong>Attempts</strong> {allDetailAttempts.length}</span>
          <span><strong>Updated</strong> {formatDate(detail.updated_at || detail.created_at)}</span>
        </section>

        <section className="evaluation-panel evaluation-representative-panel">
          <div className="evaluation-section-head">
            <div>
              <h3>Representative attempts</h3>
              <p>Representative choice and column labels are saved per Variant.</p>
            </div>
            <Space wrap>
              <Button icon={<TagsOutlined />} onClick={() => setReviewDrawerOpen(true)}>Notes, Tags & QA</Button>
              <Select
                aria-label="Machine QA slide scope"
                value={qaScope}
                style={{ width: 150 }}
                options={[
                  { value: 'all_slides', label: 'All slides' },
                  { value: 'selected_slides', label: 'Selected slides' },
                ]}
                onChange={(value: 'all_slides' | 'selected_slides') => {
                  setQaScope(value);
                  if (value === 'selected_slides' && !selectedQaPositions.length) {
                    setQaSlidePositions([qaSlidePositionOptions[0] || currentSlidePosition]);
                  }
                }}
              />
              {qaScope === 'selected_slides' && (
                <Select
                  mode="multiple"
                  aria-label="Machine QA selected slides"
                  value={selectedQaPositions}
                  style={{ minWidth: 180 }}
                  placeholder="Slides"
                  options={qaSlidePositionOptions.map((position) => ({ value: position, label: `Slide ${position}` }))}
                  onChange={(values: number[]) => setQaSlidePositions(values)}
                />
              )}
              <Button
                icon={<ThunderboltOutlined />}
                loading={qaRunning}
                onClick={() => void runMachineQaForScope()}
              >
                Run QA
              </Button>
              <Button
                icon={<DownloadOutlined />}
                onClick={() => {
                  setExportSlidePosition(currentSlidePosition);
                  setExportDrawerOpen(true);
                }}
              >
                Export
              </Button>
              <Button icon={<ReloadOutlined />} onClick={() => evaluationId && fetchEvaluationDetail(evaluationId)}>Refresh</Button>
            </Space>
          </div>
          <div className="evaluation-representative-grid">
            {detail.variants.map((variant) => {
              const representativeId = representativeByVariant[variant.id] || variant.attempts[0]?.id;
              const representative = variant.attempts.find((attempt) => attempt.id === representativeId);
              return (
                <article className="evaluation-representative-card" key={variant.id}>
                  <div className="evaluation-representative-title">
                    <Input
                      defaultValue={variant.label}
                      aria-label={`Column label for ${variant.label}`}
                      onBlur={(event) => void updateVariantLabel(variant, event.target.value)}
                    />
                    <Tag>{variant.comparison_variable || 'Variant'}</Tag>
                  </div>
                  <p>{variant.goal}</p>
                  <Select
                    value={representativeId}
                    placeholder="Representative attempt"
                    disabled={!variant.attempts.length}
                    options={variant.attempts.map((attempt) => ({
                      value: attempt.id,
                      label: `${attempt.label} · Run ${attempt.run_id || 'pending'}`,
                    }))}
                    onChange={(value: number) => void updateRepresentative(variant, value)}
                  />
                  {representative && (
                    <div className="evaluation-representative-meta">
                      <Tag color={statusColor(representative.run_status || representative.status)}>
                        {representative.run_status || representative.status || 'pending'}
                      </Tag>
                      <span>{promptSummary(representative.snapshot || variant.generation_plan_snapshot)}</span>
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </section>

        <section className="evaluation-placeholder-row">
          <div>
            <strong>Machine QA signals</strong>
            <span>{machineIssueCount ? `${machineIssueCount} persisted hard-defect signal${machineIssueCount === 1 ? '' : 's'} recorded.` : 'No persisted Machine QA failure signals.'}</span>
          </div>
          <Space size={4} wrap>
            {(detail.machine_qa || []).slice(0, 4).map((qa) => (
              <Tag key={qa.id} color={qa.verdict === 'fail' ? 'geekblue' : 'success'}>
                Slide {qa.slide_position} · {qa.verdict}
              </Tag>
            ))}
            {(detail.slide_tags || []).slice(0, 4).map((tag) => (
              <Tag key={tag.id} icon={<TagsOutlined />} color={tag.source === 'machine' ? 'geekblue' : 'orange'}>
                Slide {tag.slide_position} · {tag.label}
              </Tag>
            ))}
            {!detail.machine_qa?.length && !detail.slide_tags?.length && <Tag>No QA or tag signals yet</Tag>}
          </Space>
        </section>

        <section className="evaluation-controls">
          <Segmented
            value={reviewMode}
            options={[
              { value: 'representative', label: 'Representative only' },
              { value: 'all', label: 'All attempts' },
            ]}
            onChange={(value) => setReviewMode(value as ReviewMode)}
          />
          <Select
            aria-label="Comparison columns"
            value={columns}
            className="evaluation-column-select"
            options={[2, 3, 4].map((value) => ({ value, label: `${value} columns` }))}
            onChange={handleColumnChange}
          />
          <div className="evaluation-scale-control">
            <span>Scale</span>
            <Slider min={10} max={90} value={scale} onChange={setScale} />
          </div>
          <Button
            icon={<PictureOutlined />}
            type={issueOnly ? 'primary' : 'default'}
            onClick={() => setIssueOnly((value) => !value)}
          >
            Only issue slides
          </Button>
        </section>

        <div className="evaluation-slide-rows">
          {visibleSlidePositions.length ? visibleSlidePositions.map((position) => (
            <section className="evaluation-slide-row" key={position}>
              <div className="evaluation-slide-row-head">
                <div>
                  <strong>Slide {position}</strong>
                  <span>{displayedAttempts.length} attempt{displayedAttempts.length === 1 ? '' : 's'} shown</span>
                </div>
                <Space size={4} wrap>
                  <Tag>{reviewMode === 'representative' ? 'Representative only' : 'All attempts'}</Tag>
                  {issueOnly && <Tag color="orange">Issue filter active</Tag>}
                </Space>
              </div>
              <div className="evaluation-slide-grid" style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}>
                {displayedAttempts.map(({ variant, attempt }) => {
                  const slide = attempt.slides.find((item) => item.position === position);
                  if (!slide) return null;
                  return (
                    <SlideCompareCard
                      key={`${attempt.id}-${position}`}
                      variant={variant}
                      attempt={attempt}
                      slide={slide}
                      scale={scale}
                      isRepresentative={representativeByVariant[variant.id] === attempt.id}
                      slideTags={(detail.slide_tags || [])
                        .filter((tag) => tag.attempt_id === attempt.id && tag.slide_position === position)
                        .map((tag) => ({ id: tag.id, label: tag.label, source: tag.source }))}
                      qaItems={(detail.machine_qa || [])
                        .filter((qa) => qa.attempt_id === attempt.id && qa.slide_position === position)
                        .map((qa) => ({ id: qa.id, verdict: qa.verdict }))}
                      onSetRepresentative={() => void updateRepresentative(variant, attempt.id)}
                      onOpen={() => setVisualTarget((current) => (
                        current?.attempt.id === attempt.id && current.slide.id === slide.id
                          ? null
                          : { variant, attempt, slide }
                      ))}
                    />
                  );
                })}
              </div>
              {visualTarget?.slide.position === position && (
                <RunSlideEvidencePanel
                  slide={visualTarget.slide}
                  run={{
                    id: visualTarget.attempt.run_id || undefined,
                    status: visualTarget.attempt.run_status || visualTarget.attempt.status || 'pending',
                    engine: visualTarget.attempt.engine || visualTarget.attempt.snapshot?.engine || 'html',
                    strategy: visualTarget.attempt.strategy || visualTarget.attempt.snapshot?.strategy || 'html_default',
                    route_metadata: {
                      ...(visualTarget.attempt.snapshot?.route_metadata || {}),
                      evaluation_id: detail.id,
                      variant_id: visualTarget.variant.id,
                      attempt_id: visualTarget.attempt.id,
                    },
                    model_call_metadata: visualTarget.attempt.snapshot?.model_call_metadata || {},
                  }}
                  inline
                  showRecoveryActions={false}
                  contextTags={(
                    <>
                      <Tag color="blue">{visualTarget.variant.label}</Tag>
                      <Tag>{visualTarget.attempt.label}</Tag>
                    </>
                  )}
                  onDownloadEvidence={(slide) => void downloadSlideEvidence(slide)}
                />
              )}
            </section>
          )) : (
            <section className="evaluation-panel">
              <Empty description={issueOnly ? 'No issue slides in the current view' : 'No slide attempts available yet'} />
            </section>
          )}
        </div>
      </main>
    );
  };

  return (
    <div className="evaluations-page">
      <div className="page-toolbar evaluations-toolbar">
        <div>
          <div className="page-kicker"><span className="status-dot" />Evaluation</div>
          <h2>{pageTitle}</h2>
          <p className="toolbar-subtitle">{pageSubtitle}</p>
        </div>
        <Space className="page-toolbar-actions" wrap>
          {pageMode !== 'list' && (
            <Button icon={<ArrowLeftOutlined />} onClick={() => navigate('/evaluations')}>Evaluations</Button>
          )}
          {pageMode === 'list' && (
            <Button icon={<ReloadOutlined />} onClick={fetchEvaluationList}>Refresh</Button>
          )}
          {pageMode === 'detail' && (
            <Button type="primary" icon={<PlusOutlined />} onClick={() => navigate('/evaluations/new/blank')}>New Evaluation</Button>
          )}
          {pageMode === 'history' && (
            <Button type="primary" icon={<FileSearchOutlined />} onClick={() => navigate('/evaluations/new/blank')}>Blank Evaluation</Button>
          )}
        </Space>
      </div>

      {pageMode === 'list' && renderList()}
      {pageMode === 'blank' && renderBlankCreate()}
      {pageMode === 'history' && renderHistoryCreate()}
      {pageMode === 'detail' && renderDetail()}

      <Drawer
        title="Notes, Tags & QA"
        open={reviewDrawerOpen}
        onClose={() => setReviewDrawerOpen(false)}
        size="large"
      >
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <section className="evaluation-drawer-section">
            <h3>Evaluation notes</h3>
            <Input.TextArea
              rows={4}
              value={noteText}
              placeholder="Record human review notes for this Evaluation"
              onChange={(event) => setNoteText(event.target.value)}
            />
            <Button type="primary" disabled={!noteText.trim()} onClick={() => void saveNote()}>Save note</Button>
            <div className="evaluation-drawer-list">
              {(detail?.notes || []).map((note) => (
                <div key={note.id}>
                  <strong>{note.slide_position ? `Slide ${note.slide_position}` : 'Evaluation'}</strong>
                  <span>{note.note}</span>
                </div>
              ))}
              {!detail?.notes?.length && <Empty description="No notes saved" />}
            </div>
          </section>

          <section className="evaluation-drawer-section">
            <h3>Issue tags</h3>
            <Select
              placeholder="Attempt"
              value={tagAttemptId ?? undefined}
              options={allDetailAttempts.map(({ variant, attempt }) => ({
                value: attempt.id,
                label: `${variant.label} · ${attempt.label}`,
              }))}
              onChange={(value: number) => setTagAttemptId(value)}
            />
            <InputNumber
              min={1}
              value={tagSlidePosition}
              onChange={(value) => setTagSlidePosition(Number(value || 1))}
              addonBefore="Slide"
            />
            <Input
              value={tagLabel}
              placeholder="Overlap, Overflow, Text clarity..."
              onChange={(event) => setTagLabel(event.target.value)}
            />
            <Button type="primary" disabled={!tagAttemptId || !tagLabel.trim()} onClick={() => void saveTag()}>Save tag</Button>
            <Space size={4} wrap>
              {(detail?.slide_tags || []).map((tag) => (
                <Tag key={tag.id} color={tag.source === 'machine' ? 'geekblue' : 'orange'}>
                  Slide {tag.slide_position} · {tag.label}
                </Tag>
              ))}
              {!detail?.slide_tags?.length && <Tag>No tags saved</Tag>}
            </Space>
          </section>

          <section className="evaluation-drawer-section">
            <div className="evaluation-drawer-section-head">
              <h3>Machine QA</h3>
              <Button
                icon={<ThunderboltOutlined />}
                loading={qaRunning}
                onClick={() => void runMachineQaForScope()}
              >
                Check {qaScope === 'all_slides' ? 'All Slides' : `${qaTargetPositions.length} Selected`}
              </Button>
            </div>
            <div className="evaluation-drawer-list">
              {(detail?.machine_qa || []).map((qa) => {
                const issues = qa.issues || [];
                return (
                  <div key={qa.id}>
                    <strong>Slide {qa.slide_position} · {qa.verdict}</strong>
                    <span>
                      {issues.length
                        ? issues.map((issue) => String(issue.evidence || issue.dimension || 'visual issue')).join(' · ')
                        : 'No hard visual issue recorded.'}
                    </span>
                  </div>
                );
              })}
              {!detail?.machine_qa?.length && <Empty description="No Machine QA results saved" />}
            </div>
          </section>
        </Space>
      </Drawer>

      <Drawer
        title="Export Evaluation"
        open={exportDrawerOpen}
        onClose={() => setExportDrawerOpen(false)}
        size="large"
      >
        <Space direction="vertical" size={18} style={{ width: '100%' }}>
          <Segmented
            block
            value={exportScope}
            options={[
              { value: 'current_slide', label: 'Current slide' },
              { value: 'all_slides', label: 'All slides' },
            ]}
            onChange={(value) => setExportScope(value as 'current_slide' | 'all_slides')}
          />
          {exportScope === 'current_slide' && (
            <InputNumber
              min={1}
              value={exportSlidePosition}
              onChange={(value) => setExportSlidePosition(Number(value || 1))}
              addonBefore="Slide"
            />
          )}
          <div className="evaluation-export-fields">
            {[
              ['column_label', 'Column label'],
              ['prompt', 'Prompt'],
              ['model', 'Model'],
              ['strategy', 'Strategy'],
              ['page_number', 'Page number'],
            ].map(([value, label]) => (
              <Checkbox
                key={value}
                checked={exportFields.includes(value)}
                onChange={(event) => {
                  setExportFields((current) =>
                    event.target.checked ? Array.from(new Set([...current, value])) : current.filter((field) => field !== value),
                  );
                }}
              >
                {label}
              </Checkbox>
            ))}
          </div>
          <Button
            type="primary"
            icon={<DownloadOutlined />}
            loading={exporting}
            disabled={!detail || !exportFields.length}
            onClick={() => void downloadExport()}
          >
            Download ZIP
          </Button>
        </Space>
      </Drawer>

    </div>
  );
};

export default EvaluationsPage;
