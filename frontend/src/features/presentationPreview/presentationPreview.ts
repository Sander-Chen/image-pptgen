import type { RunDetail, RunSlide } from '../../types';

export type PreviewReasoning = 'Low' | 'Medium' | 'High' | 'Mixed';

export interface PresentationPreviewSlide {
  id: number;
  position: number;
  title: string;
  status: string;
  artifactPath: string | null;
  displayable: boolean;
}

export interface PresentationPreviewData {
  runId: number;
  title: string;
  status: string;
  statusTone: 'success' | 'active' | 'warning' | 'error';
  statusTitle: string;
  statusDescription: string;
  durationLabel: string | null;
  modelLabel: string | null;
  reasoningLabel: PreviewReasoning | null;
  slides: PresentationPreviewSlide[];
  displayableCount: number;
  failedCount: number;
  downloadEnabled: boolean;
  shouldPoll: boolean;
}

type ModelFacts = { model: string | null; reasoning: string | null };

const TERMINAL_STATUSES = new Set(['completed', 'completed_with_failures', 'failed', 'timed_out']);
const FAILED_SLIDE_STATUSES = new Set(['failed', 'timed_out']);

const asRecord = (value: unknown): Record<string, unknown> | null => (
  value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
);

const text = (value: unknown): string | null => (
  typeof value === 'string' && value.trim() ? value.trim() : null
);

function artifactPath(slide: RunSlide): string | null {
  const candidate = text(slide.final_image_path);
  if (!candidate || !/^(?:\/)?artifacts\//.test(candidate)) return null;

  const normalized = candidate.startsWith('/') ? candidate : `/${candidate}`;
  const relative = normalized.slice('/artifacts/'.length);
  const segments = relative.split('/');
  if (
    !relative
    || !/\.png$/i.test(relative)
    || segments.some((segment) => !segment || segment === '.' || segment === '..' || segment === '.codex-private')
  ) {
    return null;
  }
  return normalized;
}

function stagePriority(stage: Record<string, unknown>): number {
  const key = `${text(stage.id) || ''} ${text(stage.stage_name) || ''} ${text(stage.role) || ''}`.toLowerCase();
  if (key.includes('image-generation') || key.includes('image generation') || key.includes('image_generator')) return 3;
  if (key.includes('generation') || key.includes('generator')) return 2;
  return 1;
}

function requestChainFacts(slide: RunSlide): ModelFacts {
  const source = asRecord(slide.stage_artifacts);
  const stages = [source].filter(Boolean).flatMap((candidate) => {
    const chain = asRecord(candidate?.request_chain);
    if (!Array.isArray(chain?.stages)) return [];
    return chain.stages.map(asRecord).filter(Boolean) as Record<string, unknown>[];
  });
  const preferred = stages
    .map((stage, index) => ({ stage, index, priority: stagePriority(stage) }))
    .sort((a, b) => a.priority - b.priority || a.index - b.index)
    .at(-1)?.stage;
  return {
    model: text(preferred?.model),
    reasoning: text(preferred?.configured_thinking) || text(preferred?.reasoning_effort),
  };
}

function aggregate(values: Array<string | null>, mixedLabel: string): string | null {
  const unique = [...new Set(values.filter(Boolean) as string[])];
  if (!unique.length) return null;
  return unique.length === 1 ? unique[0] : mixedLabel;
}

function normalizeReasoning(value: string | null): PreviewReasoning | null {
  if (!value) return null;
  if (value === 'Mixed') return 'Mixed';
  const normalized = value.toLowerCase();
  if (normalized === 'low' || normalized === 'medium' || normalized === 'high') {
    return `${normalized[0].toUpperCase()}${normalized.slice(1)}` as PreviewReasoning;
  }
  return null;
}

export function formatDuration(startedAt?: string, completedAt?: string): string | null {
  if (!startedAt || !completedAt) return null;
  const started = new Date(startedAt.replace(' ', 'T')).getTime();
  const completed = new Date(completedAt.replace(' ', 'T')).getTime();
  if (!Number.isFinite(started) || !Number.isFinite(completed)) return null;
  const seconds = Math.max(0, Math.round((completed - started) / 1000));
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function buildPresentationPreview(run: RunDetail): PresentationPreviewData {
  const orderedSlides = [...run.slides].sort((a, b) => a.position - b.position);
  const slides = orderedSlides.map((slide) => {
    const path = artifactPath(slide);
    return {
      id: slide.id,
      position: slide.position,
      title: slide.slide_title || slide.slide_title_snapshot || `Slide ${slide.position}`,
      status: slide.status,
      artifactPath: path,
      displayable: Boolean(path),
    };
  });
  const displayableCount = slides.filter((slide) => slide.displayable).length;
  const failedCount = slides.filter((slide) => FAILED_SLIDE_STATUSES.has(slide.status)).length;
  const factSlides = orderedSlides.filter((slide) => Boolean(artifactPath(slide)));
  const facts = factSlides.map((slide) => requestChainFacts(slide));
  const modelLabel = aggregate(facts.map((item) => item.model), 'Mixed models');
  const reasoningLabel = normalizeReasoning(aggregate(facts.map((item) => item.reasoning), 'Mixed'));
  const terminal = TERMINAL_STATUSES.has(run.status);
  const active = !terminal;
  const completed = run.status === 'completed' && failedCount === 0;
  const partial = run.status === 'completed_with_failures' || (terminal && displayableCount > 0 && failedCount > 0);
  const statusTone = completed ? 'success' : partial ? 'warning' : active ? 'active' : 'error';
  const statusTitle = completed
    ? 'Ready to present'
    : partial
      ? 'Presentation ready with issues'
      : active
        ? 'Creating your presentation'
        : 'Presentation could not be completed';
  const statusDescription = displayableCount > 0
    ? `${displayableCount} of ${slides.length} slides available${failedCount > 0 ? ` · ${failedCount} failed` : ''}`
    : active
      ? 'Slides will appear here as they become available'
      : 'No slide preview is available';

  return {
    runId: run.id,
    title: run.deck_title || `Presentation ${run.id}`,
    status: run.status,
    statusTone,
    statusTitle,
    statusDescription,
    durationLabel: formatDuration(run.started_at, run.completed_at),
    modelLabel,
    reasoningLabel,
    slides,
    displayableCount,
    failedCount,
    downloadEnabled: terminal && displayableCount > 0,
    shouldPoll: active,
  };
}
