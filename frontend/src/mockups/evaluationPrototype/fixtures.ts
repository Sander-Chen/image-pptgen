export type EvaluationStatus = 'Draft' | 'Running' | 'Reviewing' | 'Reviewed' | 'Archived';
export type IssueSource = 'human' | 'machine';

export interface IssueTag {
  id: string;
  label: string;
  source: IssueSource;
}

export interface SlideVisual {
  position: number;
  title: string;
  image: string;
  machineVerdict: 'pass' | 'fail' | 'not_run';
  issueTags: IssueTag[];
  note?: string;
}

export interface Attempt {
  id: string;
  runId: number;
  batchId: number;
  label: string;
  engine: 'html' | 'image';
  strategy: string;
  promptVersion: string;
  model: string;
  requirement: string;
  color: string;
  status: 'completed' | 'failed';
  qaSummary: string;
  slides: SlideVisual[];
}

export interface Variant {
  id: string;
  label: string;
  objective: string;
  comparisonVariable: string;
  attempts: Attempt[];
}

export interface Evaluation {
  id: string;
  title: string;
  deck: string;
  objective: string;
  status: EvaluationStatus;
  summary: string;
  variants: Variant[];
}

const path = '/evaluation-prototype/';

const issueTags = {
  overlap: { id: 'overlap', label: 'Overlap', source: 'human' as const },
  clipping: { id: 'clipping', label: 'Overflow', source: 'human' as const },
  text: { id: 'text', label: 'Text clarity', source: 'human' as const },
  balance: { id: 'balance', label: 'Balance', source: 'human' as const },
  machineOverflow: { id: 'machine:overflow', label: 'Machine overflow', source: 'machine' as const },
};

function slides(prefix: string, verdicts: Array<'pass' | 'fail' | 'not_run'>, tags: IssueTag[][] = []): SlideVisual[] {
  return [1, 2, 3, 4].map((position) => ({
    position,
    title: `中国历史 ${position}`,
    image: `${path}${prefix}-slide-${position}.png`,
    machineVerdict: verdicts[position - 1],
    issueTags: tags[position - 1] || [],
    note: position === 2 && tags[position - 1]?.length ? 'Needs manual review before this attempt can be the representative output.' : undefined,
  }));
}

export const evaluations: Evaluation[] = [
  {
    id: 'EV-2026-0606-001',
    title: '中国历史 · HTML vs Image Route',
    deck: '中国历史',
    objective: 'Check whether the newer visual route improves layout strength without losing the balanced structure of the baseline.',
    status: 'Reviewing',
    summary: 'Image route is stronger visually, but the baseline still has more stable density on text-heavy pages.',
    variants: [
      {
        id: 'variant-a',
        label: 'A · HTML baseline',
        objective: 'Preserve current HTML prompt stability and content completeness.',
        comparisonVariable: 'Prompt + route baseline',
        attempts: [
          {
            id: 'attempt-a1',
            runId: 93,
            batchId: 35,
            label: 'A1 · Run 93',
            engine: 'html',
            strategy: 'html_default',
            promptVersion: 'V5.3.20',
            model: 'Gemini 3.1 Pro',
            requirement: '专业极简',
            color: 'System Empty Color',
            status: 'completed',
            qaSummary: 'Machine QA pass on all sampled slides.',
            slides: slides('html-baseline-a1', ['pass', 'pass', 'pass', 'pass']),
          },
          {
            id: 'attempt-a2',
            runId: 91,
            batchId: 34,
            label: 'A2 · Run 91',
            engine: 'html',
            strategy: 'html_default',
            promptVersion: 'V5.3.20',
            model: 'Gemini 3.1 Pro',
            requirement: 'AutoSkill System Requirement',
            color: 'System Empty Color',
            status: 'completed',
            qaSummary: 'One page marked for density review.',
            slides: slides('html-baseline-a2', ['pass', 'fail', 'pass', 'pass'], [[], [issueTags.balance], [], []]),
          },
        ],
      },
      {
        id: 'variant-b',
        label: 'B · Image V5.4',
        objective: 'Improve visual hierarchy and page polish with Image route while avoiding hard layout failures.',
        comparisonVariable: 'Image strategy + model route',
        attempts: [
          {
            id: 'attempt-b1',
            runId: 94,
            batchId: 36,
            label: 'B1 · Run 94',
            engine: 'image',
            strategy: 'image_5_0',
            promptVersion: 'V5.4',
            model: 'Gemini 3 Flash Image',
            requirement: '专业极简',
            color: 'System Empty Color',
            status: 'completed',
            qaSummary: 'Machine QA pass; representative candidate.',
            slides: slides('image-v54-b1', ['pass', 'pass', 'pass', 'pass']),
          },
          {
            id: 'attempt-b2',
            runId: 92,
            batchId: 36,
            label: 'B2 · Run 92',
            engine: 'image',
            strategy: 'image_5_0',
            promptVersion: 'V5.4',
            model: 'Gemini 3 Flash Image',
            requirement: '专业极简',
            color: 'System Empty Color',
            status: 'completed',
            qaSummary: 'Machine flagged a possible overflow on slide 3.',
            slides: slides('image-v54-b2', ['pass', 'pass', 'fail', 'pass'], [[], [], [issueTags.machineOverflow, issueTags.clipping], []]),
          },
        ],
      },
    ],
  },
  {
    id: 'EV-2026-0606-002',
    title: '中国历史 · Repeat Attempt Stability',
    deck: '中国历史',
    objective: 'Review whether repeated attempts converge on the same visual structure.',
    status: 'Draft',
    summary: 'Draft waiting for run selection.',
    variants: [],
  },
];

export const availableHistoryRuns = [
  { key: '93', runId: 93, batchId: 35, deck: '中国历史', route: 'HTML', status: 'completed' },
  { key: '94', runId: 94, batchId: 36, deck: '中国历史', route: 'Image 5.0', status: 'completed' },
  { key: '88', runId: 88, batchId: 31, deck: '中国历史', route: 'Image 5.0', status: 'completed' },
  { key: '22', runId: 22, batchId: 12, deck: '农业大学', route: 'HTML', status: 'completed' },
  { key: '19', runId: 19, batchId: 9, deck: '技术栈', route: 'HTML', status: 'failed' },
];

export const defaultIssueTags = [
  issueTags.overlap,
  issueTags.clipping,
  issueTags.text,
  issueTags.balance,
  { id: 'garbled', label: 'Garbled text', source: 'human' as const },
  { id: 'style', label: 'Style drift', source: 'human' as const },
];
