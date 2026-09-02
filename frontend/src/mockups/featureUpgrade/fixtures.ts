export type ActionScope = 'Batch' | 'Run' | 'Slide' | 'Image';
export type RetryClass = 'auto-retryable' | 'terminal' | 'manual-only' | 'none';

export interface HistoryRow {
  id: number;
  batchId: number;
  title: string;
  createdAt: string;
  createdDate: string;
  route: string;
  mode: string;
  requirement: string;
  color: string;
  config: string;
  promptSet: string;
  status: 'completed' | 'failed' | 'running' | 'queued' | 'pending' | 'timed_out';
  retryClass: RetryClass;
  progress: {
    done: number;
    total: number;
  };
  failureRate: number;
  errorSummary: string;
  nextAction: string;
  representativeRunId: number;
  runs: BatchRun[];
}

export interface BatchRun {
  id: number;
  label: string;
  status: 'completed' | 'failed' | 'queued' | 'pending' | 'timed_out';
  retryClass: RetryClass;
  summary: string;
  deckName: string;
  mode: string;
  config: string;
  candidateLabel: string;
  requirement: string;
  color: string;
  slideSummary: string;
  imageSummary: string;
  statusHelp: string;
  errorPreview: string;
}

export interface SlideEvidence {
  id: number;
  position: number;
  title: string;
  status: 'completed' | 'failed' | 'pending' | 'skipped';
  route: string;
  visualLabel: string;
  imageStatus: string;
  artifactVersion: string;
  mode: string;
  config: string;
}

export interface VersionRecord {
  version: string;
  summary: string;
  status: 'current' | 'kept' | 'rotates next';
}

export interface RouteFlow {
  key: string;
  title: string;
  badge: string;
  state: 'current' | 'roadmap';
  steps: string[];
}

export interface ModelGateRow {
  key: string;
  role: string;
  env: string;
  targetModel: string;
  effort: string;
  temperature: string;
  gate: 'needs request' | 'verified' | 'blocked';
}

export interface PromptRow {
  key: string;
  role: string;
  roleFamily: string;
  version: string;
  name: string;
  lifecycle: 'active' | 'archived' | 'draft';
  folders: string[];
  description: string;
  isDefault: boolean;
  variables: string[];
  variableState: 'ready' | 'missing' | 'disabled' | 'needs confirmation';
  createdAt: string;
  contentPreview: string;
}

export interface VariableRow {
  key: string;
  role: string;
  token: string;
  description: string;
  status: 'active' | 'disabled';
  references: number;
  sampleReference: string;
}

export interface CombinationRow {
  key: string;
  name: string;
  designer: string;
  htmlAgent: string;
  autoSpill: string;
  imageDesigner: string;
  imageGenerator: string;
  timeoutMinutes: number;
  maxConcurrentRuns: number;
  isDefault: boolean;
}

export interface RoleModelProfile {
  key: string;
  role: string;
  environment: string;
  provider: string;
  model: string;
  apiType: 'openai' | 'gemini';
  endpoint: string;
  effort: string;
  temperature: string;
  status: 'active' | 'needs request' | 'blocked';
}

export interface FolderRow {
  key: string;
  scope: 'deck' | 'requirement' | 'color';
  name: string;
  parent?: string;
}

export interface DataDeckRow {
  key: string;
  title: string;
  content: string;
  slideCount: number;
  lifecycle: 'active' | 'archived' | 'recycle_bin';
  folders: string[];
  createdAt: string;
  slides: string[];
}

export interface DataRequirementRow {
  key: string;
  title: string;
  content: string;
  lifecycle: 'active' | 'archived' | 'recycle_bin';
  folders: string[];
  createdAt: string;
}

export interface DataColorRow {
  key: string;
  title: string;
  content: string;
  sourceType: 'manual' | 'image_extract';
  lifecycle: 'active' | 'archived' | 'recycle_bin';
  folders: string[];
  createdAt: string;
}

export const historyRows: HistoryRow[] = [
  {
    id: 128,
    batchId: 128,
    title: 'Feature Upgrade Deck',
    createdAt: 'Today 10:13',
    createdDate: '2026-05-29',
    route: 'Image 5.0',
    mode: 'Batch · Image route',
    requirement: 'Image route review',
    color: 'Yellow Image',
    config: 'Production image',
    promptSet: 'Unified director + image request v5',
    status: 'failed',
    retryClass: 'auto-retryable',
    progress: { done: 4, total: 10 },
    failureRate: 60,
    errorSummary: 'No inline image bytes, empty provider text',
    nextAction: 'Network/provider/empty-image class',
    representativeRunId: 801,
    runs: [],
  },
  {
    id: 127,
    batchId: 127,
    title: 'Route Roadmap Smoke',
    createdAt: 'Today 09:52',
    createdDate: '2026-05-29',
    route: 'Image 5.3',
    mode: 'Gate · roadmap smoke',
    requirement: 'flow coverage',
    color: 'Target route',
    config: 'target model gate',
    promptSet: 'Route binding proof',
    status: 'pending',
    retryClass: 'manual-only',
    progress: { done: 0, total: 6 },
    failureRate: 0,
    errorSummary: 'Model connectivity not yet proven',
    nextAction: 'No coding until model gate passes',
    representativeRunId: 901,
    runs: [],
  },
  {
    id: 126,
    batchId: 126,
    title: 'External Review Packet',
    createdAt: 'Yesterday 18:40',
    createdDate: '2026-05-28',
    route: 'HTML',
    mode: 'HTML · review packet',
    requirement: 'HTML review',
    color: 'Blue HTML',
    config: 'Review route',
    promptSet: 'Designer + HTML Agent',
    status: 'completed',
    retryClass: 'none',
    progress: { done: 5, total: 5 },
    failureRate: 0,
    errorSummary: 'Ready for download',
    nextAction: 'No retry needed',
    representativeRunId: 701,
    runs: [],
  },
  {
    id: 125,
    batchId: 125,
    title: 'Legacy Image 1.0 Debug',
    createdAt: 'May 28 22:08',
    createdDate: '2026-05-28',
    route: 'Image 1.0',
    mode: 'Legacy · conversation',
    requirement: 'content prompt split',
    color: 'Yellow Image',
    config: 'Legacy route',
    promptSet: 'Cover prompt + continuation',
    status: 'failed',
    retryClass: 'terminal',
    progress: { done: 2, total: 4 },
    failureRate: 50,
    errorSummary: '400 Bad Request from image endpoint',
    nextAction: 'Correct config, then Continue or Force',
    representativeRunId: 601,
    runs: [],
  },
  {
    id: 124,
    batchId: 124,
    title: 'Timeout Recovery Batch',
    createdAt: 'May 28 20:14',
    createdDate: '2026-05-28',
    route: 'HTML',
    mode: 'HTML · timed out',
    requirement: 'endpoint timeout proof',
    color: 'Blue HTML',
    config: 'Review route',
    promptSet: 'Designer + HTML Agent',
    status: 'timed_out',
    retryClass: 'terminal',
    progress: { done: 1, total: 3 },
    failureRate: 67,
    errorSummary: 'Run exceeded configured timeout',
    nextAction: 'Download evidence, then Retry or Force after endpoint fix',
    representativeRunId: 501,
    runs: [],
  },
];

export const batchRuns: BatchRun[] = [
  { id: 801, label: 'Run 801', status: 'failed', retryClass: 'auto-retryable', summary: 'slide 2 failed', deckName: 'Feature Upgrade Deck', mode: 'Image 5.0', config: 'Production image', candidateLabel: 'Candidate 1', requirement: 'Image route review', color: 'Yellow Image', slideSummary: '4 slides, slide 2 failed', imageSummary: 'image v5 empty response', statusHelp: 'Failed after image stage; retryable empty response.', errorPreview: 'No inline image bytes returned.' },
  { id: 802, label: 'Run 802', status: 'failed', retryClass: 'auto-retryable', summary: 'image failed', deckName: 'Feature Upgrade Deck', mode: 'Image 5.0', config: 'Production image', candidateLabel: 'Candidate 2', requirement: 'Image route review', color: 'Yellow Image', slideSummary: '4 slides generated', imageSummary: 'image request failed', statusHelp: 'Image request failed after XML artifact persisted.', errorPreview: 'Provider response omitted image bytes.' },
  { id: 803, label: 'Run 803', status: 'completed', retryClass: 'none', summary: 'complete', deckName: 'Feature Upgrade Deck', mode: 'Image 5.0', config: 'Production image', candidateLabel: 'Candidate 3', requirement: 'Image route review', color: 'Yellow Image', slideSummary: '4 slides complete', imageSummary: 'all images ready', statusHelp: 'Terminal successful run; download enabled.', errorPreview: 'none' },
  { id: 804, label: 'Run 804', status: 'failed', retryClass: 'terminal', summary: '400 Bad Request', deckName: 'Feature Upgrade Deck', mode: 'Image 5.0', config: 'Production image', candidateLabel: 'Candidate 4', requirement: 'Image route review', color: 'Yellow Image', slideSummary: '2 slides complete', imageSummary: 'bad request terminal', statusHelp: 'Failed with terminal request/config class.', errorPreview: '400 Bad Request from image endpoint.' },
  { id: 805, label: 'Run 805', status: 'queued', retryClass: 'manual-only', summary: 'queued', deckName: 'Feature Upgrade Deck', mode: 'Image 5.0', config: 'Production image', candidateLabel: 'Candidate 5', requirement: 'Image route review', color: 'Yellow Image', slideSummary: 'waiting for slot', imageSummary: 'not started', statusHelp: 'Queued work has no retry target yet.', errorPreview: 'none' },
  { id: 806, label: 'Run 806', status: 'pending', retryClass: 'manual-only', summary: 'pending', deckName: 'Feature Upgrade Deck', mode: 'Image 5.0', config: 'Production image', candidateLabel: 'Candidate 6', requirement: 'Image route review', color: 'Yellow Image', slideSummary: 'pending slides', imageSummary: 'not requested', statusHelp: 'Pending work has not entered provider execution.', errorPreview: 'none' },
  { id: 807, label: 'Run 807', status: 'failed', retryClass: 'auto-retryable', summary: 'empty image', deckName: 'Feature Upgrade Deck', mode: 'Image 5.0', config: 'Production image', candidateLabel: 'Candidate 7', requirement: 'Image route review', color: 'Yellow Image', slideSummary: '3 slides complete', imageSummary: 'empty inline bytes', statusHelp: 'Shares retryable empty-image failure class.', errorPreview: 'Response text empty; inline image null.' },
  { id: 808, label: 'Run 808', status: 'completed', retryClass: 'none', summary: 'complete', deckName: 'Feature Upgrade Deck', mode: 'Image 5.0', config: 'Production image', candidateLabel: 'Candidate 8', requirement: 'Image route review', color: 'Yellow Image', slideSummary: '4 slides complete', imageSummary: 'all images ready', statusHelp: 'Terminal successful run; download enabled.', errorPreview: 'none' },
  { id: 809, label: 'Run 809', status: 'failed', retryClass: 'auto-retryable', summary: 'empty image', deckName: 'Feature Upgrade Deck', mode: 'Image 5.0', config: 'Production image', candidateLabel: 'Candidate 9', requirement: 'Image route review', color: 'Yellow Image', slideSummary: '4 slides generated', imageSummary: 'empty inline bytes', statusHelp: 'Retryable empty-image response after generation.', errorPreview: 'No image part in provider response.' },
  { id: 810, label: 'Run 810', status: 'pending', retryClass: 'manual-only', summary: 'pending', deckName: 'Feature Upgrade Deck', mode: 'Image 5.0', config: 'Production image', candidateLabel: 'Candidate 10', requirement: 'Image route review', color: 'Yellow Image', slideSummary: 'pending slides', imageSummary: 'not requested', statusHelp: 'Pending work has not entered provider execution.', errorPreview: 'none' },
];

historyRows[0].runs = batchRuns;
historyRows[1].runs = batchRuns.slice(0, 6).map((run, index) => ({
  ...run,
  id: 901 + index,
  label: `Run ${901 + index}`,
  status: index === 0 ? 'pending' : 'queued',
  summary: index === 0 ? 'blocked by model gate' : 'queued',
  mode: 'Image 5.3',
  config: 'target model gate',
  slideSummary: 'not generated',
  imageSummary: 'model gate pending',
}));
historyRows[2].runs = batchRuns.slice(0, 5).map((run, index) => ({
  ...run,
  id: 701 + index,
  label: `Run ${701 + index}`,
  status: 'completed',
  summary: 'complete',
  mode: 'HTML',
  config: 'Review route',
  slideSummary: 'HTML captured PNG ready',
  imageSummary: 'captured PNG ready',
}));
historyRows[3].runs = batchRuns.slice(0, 4).map((run, index) => ({
  ...run,
  id: 601 + index,
  label: `Run ${601 + index}`,
  status: index < 2 ? 'completed' : 'failed',
  retryClass: index < 2 ? 'none' : 'terminal',
  summary: index < 2 ? 'complete' : '400 Bad Request',
  mode: 'Image 1.0',
  config: 'Legacy route',
  candidateLabel: `Legacy candidate ${index + 1}`,
  requirement: 'content prompt split',
  color: 'Yellow Image',
  slideSummary: index < 2 ? 'conversation image ready' : 'stopped',
  imageSummary: index < 2 ? 'final image ready' : 'bad request terminal',
  statusHelp: index < 2 ? 'Terminal successful legacy run.' : 'Terminal bad request; fix config before retry.',
  errorPreview: index < 2 ? 'none' : '400 Bad Request from image endpoint.',
}));
historyRows[4].runs = batchRuns.slice(0, 3).map((run, index) => ({
  ...run,
  id: 501 + index,
  label: `Run ${501 + index}`,
  status: index === 0 ? 'timed_out' : 'pending',
  summary: index === 0 ? 'timed out' : 'waiting',
  mode: 'HTML',
  config: 'Review route',
  slideSummary: index === 0 ? '1 / 3 slides before timeout' : 'pending slides',
  imageSummary: index === 0 ? 'captured PNG incomplete' : 'not captured',
}));

export const slides: SlideEvidence[] = [
  { id: 1, position: 1, title: 'Cover Reference', status: 'completed', route: 'Image 5.0', visualLabel: 'Cover Reference', imageStatus: 'final image ready', artifactVersion: 'v5', mode: 'Image 5.0', config: 'Production image' },
  { id: 2, position: 2, title: 'Market Signals', status: 'failed', route: 'Image 5.0', visualLabel: 'Market Signals', imageStatus: 'empty image response', artifactVersion: 'v5', mode: 'Image 5.0', config: 'Production image' },
  { id: 3, position: 3, title: 'Milestones', status: 'pending', route: 'Image 5.0', visualLabel: 'Milestones', imageStatus: 'waiting for retry', artifactVersion: 'v4', mode: 'Image 5.0', config: 'Production image' },
  { id: 4, position: 4, title: 'Recovery Plan', status: 'skipped', route: 'Image 5.0', visualLabel: 'Recovery Plan', imageStatus: 'skipped after failure', artifactVersion: 'v3', mode: 'Image 5.0', config: 'Production image' },
];

export const htmlSlides: SlideEvidence[] = [
  { id: 101, position: 1, title: 'Executive Summary', status: 'completed', route: 'HTML', visualLabel: 'Captured PNG', imageStatus: 'captured PNG ready', artifactVersion: 'html-v3', mode: 'HTML', config: 'Review route' },
  { id: 102, position: 2, title: 'Feature Matrix', status: 'completed', route: 'HTML', visualLabel: 'Live HTML', imageStatus: 'live HTML and clean HTML ready', artifactVersion: 'html-v3', mode: 'HTML', config: 'Review route' },
  { id: 103, position: 3, title: 'Implementation Plan', status: 'completed', route: 'HTML', visualLabel: 'Clean HTML', imageStatus: 'raw response retained', artifactVersion: 'html-v3', mode: 'HTML', config: 'Review route' },
  { id: 104, position: 4, title: 'Review Notes', status: 'completed', route: 'HTML', visualLabel: 'Original Content', imageStatus: 'original content and design principle retained', artifactVersion: 'html-v3', mode: 'HTML', config: 'Review route' },
];

export const versionHistory: VersionRecord[] = [
  { version: 'v5', summary: 'current failed image request', status: 'current' },
  { version: 'v4', summary: 'layout drift', status: 'kept' },
  { version: 'v3', summary: 'missing reference', status: 'kept' },
  { version: 'v2', summary: 'prompt tweak', status: 'kept' },
  { version: 'v1', summary: 'oldest retained', status: 'rotates next' },
];

export const routeFlows: RouteFlow[] = [
  { key: 'html', title: 'HTML', badge: 'current', state: 'current', steps: ['Designer prompt', 'HTML Agent', 'Clean HTML', 'Captured PNG'] },
  { key: 'image10', title: 'Image 1.0', badge: 'conversation', state: 'current', steps: ['Cover prompt', 'Continuation request', 'Image response'] },
  { key: 'image30', title: 'Image 3.0', badge: 'seed', state: 'current', steps: ['Seed slide', 'Designer XML', 'Image request'] },
  { key: 'image32', title: 'Image 3.2', badge: 'cover ref', state: 'current', steps: ['Cover reference', 'Seed dependency', 'Designer XML', 'Image response'] },
  { key: 'image50', title: 'Image 5.0', badge: 'unified', state: 'current', steps: ['Unified Designer', 'Blueprint XML', 'Image request'] },
  { key: 'image53', title: 'Image 5.3', badge: 'roadmap', state: 'roadmap', steps: ['Versioned generation route', 'Model gate proof', 'Persisted stage artifacts', 'Flow diagram output'] },
];

export const modelGateRows: ModelGateRow[] = [
  { key: 'designer-test', role: 'Designer', env: 'Test', targetModel: 'Gemini 3.1 Flash-Lite Preview', effort: 'default', temperature: 'default', gate: 'needs request' },
  { key: 'designer-mini', role: 'Designer', env: 'Production Mini', targetModel: 'GPT-5.4 mini', effort: 'low', temperature: '1', gate: 'needs request' },
  { key: 'designer-pro', role: 'Designer', env: 'Production Pro', targetModel: 'GPT-5.4', effort: 'high', temperature: 'default', gate: 'needs request' },
  { key: 'html-test', role: 'HTML Agent', env: 'Test', targetModel: 'Gemini 3.1 Flash-Lite Preview', effort: 'default', temperature: 'default', gate: 'needs request' },
  { key: 'html-mini', role: 'HTML Agent', env: 'Production Mini', targetModel: 'Gemini 3 Flash', effort: 'high', temperature: '1', gate: 'needs request' },
  { key: 'html-pro', role: 'HTML Agent', env: 'Production Pro', targetModel: 'Gemini 3.1 Pro high', effort: 'high', temperature: 'default', gate: 'needs request' },
  { key: 'image-designer-test', role: 'Image Designer', env: 'Test', targetModel: 'Gemini 3.1 Flash-Lite Preview', effort: 'default', temperature: 'default', gate: 'needs request' },
  { key: 'image-designer-legacy', role: 'Image Designer', env: 'Production Legacy', targetModel: 'GPT 5.1', effort: 'high', temperature: '1', gate: 'needs request' },
  { key: 'image-designer-prod', role: 'Image Designer', env: 'Production', targetModel: 'GPT 5.4', effort: 'high', temperature: '1', gate: 'needs request' },
  { key: 'image-generator-test', role: 'Image Generator', env: 'Test', targetModel: 'gemini-3.1-flash-image', effort: 'low thinking', temperature: '1', gate: 'needs request' },
  { key: 'image-generator-mini', role: 'Image Generator', env: 'Production Mini', targetModel: 'gemini-3.1-flash-image', effort: 'high thinking', temperature: '1', gate: 'needs request' },
  { key: 'image-generator-prod', role: 'Image Generator', env: 'Production', targetModel: 'gemini-3.1-pro-image', effort: 'high thinking', temperature: '1', gate: 'needs request' },
];

export const promptRoles = [
  'Designer',
  'HTML Agent',
  'Image Cover 3.1',
  'Image 1.0',
  'Image 3.0 Seed',
  'Image 3.0 Non-Seed',
  'Image 3.2 Seed',
  'Image 3.2 Non-Seed',
  'Image 5.0 Unified',
  'Image Generator',
  'XML Cleanup',
  'Image 5.3 Route Gate',
];

export const dataFolders: FolderRow[] = [
  { key: 'deck-prod', scope: 'deck', name: 'Production Decks' },
  { key: 'deck-review', scope: 'deck', name: 'External Review' },
  { key: 'req-feature', scope: 'requirement', name: 'Feature Upgrade' },
  { key: 'req-route', scope: 'requirement', name: 'Route QA' },
  { key: 'color-brand', scope: 'color', name: 'Brand Palettes' },
  { key: 'color-image', scope: 'color', name: 'Image Routes' },
];

export const dataDeckRows: DataDeckRow[] = [
  {
    key: 'deck-feature-upgrade',
    title: 'Feature Upgrade Deck',
    content: 'Current branch feature upgrade alignment packet with history, batch, run detail, and config changes.',
    slideCount: 4,
    lifecycle: 'active',
    folders: ['Production Decks', 'External Review'],
    createdAt: '2026-05-29 10:05',
    slides: ['Cover Reference', 'Market Signals', 'Milestones', 'Recovery Plan'],
  },
  {
    key: 'deck-external-review',
    title: 'External Review Packet',
    content: 'HTML route review packet with captured PNG and clean HTML output.',
    slideCount: 5,
    lifecycle: 'active',
    folders: ['External Review'],
    createdAt: '2026-05-28 18:35',
    slides: ['Executive Summary', 'Feature Matrix', 'Implementation Plan', 'Review Notes', 'Appendix'],
  },
  {
    key: 'deck-legacy-image',
    title: 'Legacy Image 1.0 Debug',
    content: 'Archived debugging packet for conversation-style Image generation.',
    slideCount: 4,
    lifecycle: 'archived',
    folders: ['Production Decks'],
    createdAt: '2026-05-27 21:12',
    slides: ['Cover', 'Conversation Prompt', 'Image Response', 'Repair Notes'],
  },
  {
    key: 'deck-old-test',
    title: 'Old Endpoint Smoke',
    content: 'Recycle-bin deck retained until historical export completes.',
    slideCount: 2,
    lifecycle: 'recycle_bin',
    folders: [],
    createdAt: '2026-05-25 09:44',
    slides: ['Endpoint', 'Result'],
  },
];

export const dataRequirementRows: DataRequirementRow[] = [
  {
    key: 'req-image-route-review',
    title: 'Image route review',
    content: 'Show route dependencies, retries, regenerated versions, and model gates without hiding generated results.',
    lifecycle: 'active',
    folders: ['Feature Upgrade', 'Route QA'],
    createdAt: '2026-05-29 09:58',
  },
  {
    key: 'req-html-review',
    title: 'HTML review',
    content: 'Preserve captured PNG, live HTML, clean HTML, raw response, original content, and design principle evidence.',
    lifecycle: 'active',
    folders: ['Route QA'],
    createdAt: '2026-05-28 17:50',
  },
  {
    key: 'req-content-prompt-split',
    title: 'content prompt split',
    content: 'Legacy Image 1.0 request used for conversation continuation review.',
    lifecycle: 'archived',
    folders: ['Route QA'],
    createdAt: '2026-05-27 12:16',
  },
];

export const dataColorRows: DataColorRow[] = [
  {
    key: 'color-yellow-image',
    title: 'Yellow Image',
    content: '<palette><primary>#f7c948</primary><accent>#1463ff</accent><surface>#fff8d8</surface></palette>',
    sourceType: 'manual',
    lifecycle: 'active',
    folders: ['Image Routes'],
    createdAt: '2026-05-29 09:40',
  },
  {
    key: 'color-blue-html',
    title: 'Blue HTML',
    content: '<palette><primary>#1463ff</primary><surface>#eff6ff</surface><ink>#0f172a</ink></palette>',
    sourceType: 'image_extract',
    lifecycle: 'active',
    folders: ['Brand Palettes'],
    createdAt: '2026-05-28 18:05',
  },
  {
    key: 'color-target-route',
    title: 'Target route',
    content: '<palette><primary>#0e7490</primary><warning>#b7791f</warning><surface>#f8fafc</surface></palette>',
    sourceType: 'manual',
    lifecycle: 'archived',
    folders: ['Brand Palettes'],
    createdAt: '2026-05-27 16:20',
  },
];

export const promptRows: PromptRow[] = [
  {
    key: 'designer-default',
    role: 'Designer',
    roleFamily: 'HTML',
    version: 'designer-default-v4',
    name: 'Designer system prompt',
    lifecycle: 'active',
    folders: ['Production', 'HTML'],
    description: 'Creates slide structure and content instructions before HTML generation.',
    isDefault: true,
    variables: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}'],
    variableState: 'ready',
    createdAt: '2026-05-27 14:20',
    contentPreview: 'Use the deck content, user requirement, and required color to produce a faithful slide plan.',
  },
  {
    key: 'html-agent-default',
    role: 'HTML Agent',
    roleFamily: 'HTML',
    version: 'html-agent-v6',
    name: 'HTML Agent build prompt',
    lifecycle: 'active',
    folders: ['Production', 'HTML'],
    description: 'Builds clean HTML and capture-ready assets from the Designer prompt.',
    isDefault: true,
    variables: ['{{Deck-Design-principle}}', '{{Slide-Content}}'],
    variableState: 'ready',
    createdAt: '2026-05-27 14:23',
    contentPreview: 'Render the slide as HTML, preserve text hierarchy, and keep capture constraints explicit.',
  },
  {
    key: 'image-cover-31',
    role: 'Image Cover 3.1',
    roleFamily: 'Image legacy',
    version: 'image-cover-3-1-v2',
    name: 'Image cover reference prompt',
    lifecycle: 'active',
    folders: ['Image', 'Legacy'],
    description: 'Creates the first cover image reference used by legacy Image flows.',
    isDefault: false,
    variables: ['{{Deck-Full-Content}}', '{{Deck-Required-color}}'],
    variableState: 'ready',
    createdAt: '2026-05-26 18:10',
    contentPreview: 'Generate the cover reference image from full deck context and color constraints.',
  },
  {
    key: 'image-10',
    role: 'Image 1.0',
    roleFamily: 'Image legacy',
    version: 'image-1-0-v5',
    name: 'Image 1.0 conversation prompt',
    lifecycle: 'active',
    folders: ['Image', 'Legacy'],
    description: 'Legacy cover/content conversation prompt split.',
    isDefault: true,
    variables: ['{{Deck-Full-Content}}', '{{Slide-Content}}'],
    variableState: 'missing',
    createdAt: '2026-05-26 19:41',
    contentPreview: 'Continue the Image conversation for each slide while retaining cover context.',
  },
  {
    key: 'image-30-seed',
    role: 'Image 3.0 Seed',
    roleFamily: 'Image 3.x',
    version: 'image-3-0-seed-v3',
    name: 'Image 3.0 seed slide prompt',
    lifecycle: 'active',
    folders: ['Image', 'Seed'],
    description: 'Generates the seed slide before designer XML is requested.',
    isDefault: true,
    variables: ['{{Deck-Full-Content}}', '{{Deck-Required-color}}'],
    variableState: 'ready',
    createdAt: '2026-05-27 09:12',
    contentPreview: 'Create a seed image that constrains the downstream Designer XML and image request.',
  },
  {
    key: 'image-30-non-seed',
    role: 'Image 3.0 Non-Seed',
    roleFamily: 'Image 3.x',
    version: 'image-3-0-non-seed-v3',
    name: 'Image 3.0 Designer XML prompt',
    lifecycle: 'active',
    folders: ['Image', 'XML'],
    description: 'Produces Designer XML for non-seed slides.',
    isDefault: true,
    variables: ['{{Slide-Content}}', '{{Deck-User-Requirement}}'],
    variableState: 'needs confirmation',
    createdAt: '2026-05-27 09:20',
    contentPreview: 'Write a designer XML payload that references the seed slide and current slide content.',
  },
  {
    key: 'image-32-seed',
    role: 'Image 3.2 Seed',
    roleFamily: 'Image 3.x',
    version: 'image-3-2-seed-v2',
    name: 'Image 3.2 cover reference prompt',
    lifecycle: 'active',
    folders: ['Image', 'Cover Reference'],
    description: 'Adds explicit cover reference and seed dependency handling.',
    isDefault: true,
    variables: ['{{Deck-Full-Content}}', '{{Deck-Required-color}}'],
    variableState: 'ready',
    createdAt: '2026-05-27 10:03',
    contentPreview: 'Create a seed image that uses the cover reference as the visual anchor.',
  },
  {
    key: 'image-32-non-seed',
    role: 'Image 3.2 Non-Seed',
    roleFamily: 'Image 3.x',
    version: 'image-3-2-non-seed-v2',
    name: 'Image 3.2 Designer XML prompt',
    lifecycle: 'archived',
    folders: ['Image', 'XML'],
    description: 'Archived prompt retained for old prompt versions and evidence playback.',
    isDefault: false,
    variables: ['{{Slide-Content}}', '{{Deck-User-Requirement}}'],
    variableState: 'disabled',
    createdAt: '2026-05-27 10:09',
    contentPreview: 'Archived XML prompt; disabled variables remain valid for old prompt versions only.',
  },
  {
    key: 'image-50-unified',
    role: 'Image 5.0 Unified',
    roleFamily: 'Image 5.x',
    version: 'image-5-0-unified-v5',
    name: 'Unified Image Designer',
    lifecycle: 'active',
    folders: ['Image', 'Production'],
    description: 'One Designer prompt emits blueprint XML before image generation.',
    isDefault: true,
    variables: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Slide-Content}}'],
    variableState: 'ready',
    createdAt: '2026-05-28 16:32',
    contentPreview: 'Produce a complete blueprint XML for the current slide, preserving cover references and route instructions.',
  },
  {
    key: 'image-generator-generator',
    role: 'Image Generator',
    roleFamily: 'Image generator',
    version: 'image-generator-v4',
    name: 'Image generator request',
    lifecycle: 'active',
    folders: ['Image', 'Image'],
    description: 'Sends blueprint XML and image references to the image model profile.',
    isDefault: true,
    variables: ['{{Slide-Content}}', '{{Deck-Required-color}}'],
    variableState: 'ready',
    createdAt: '2026-05-28 16:41',
    contentPreview: 'Render a 16:9 PPT slide image from the Designer XML and retained references.',
  },
  {
    key: 'xml-cleanup',
    role: 'XML Cleanup',
    roleFamily: 'Shared',
    version: 'xml-cleanup-v1',
    name: 'XML cleanup prompt',
    lifecycle: 'active',
    folders: ['Shared'],
    description: 'Repairs malformed XML while preserving current route semantics.',
    isDefault: false,
    variables: ['{{Slide-Content}}'],
    variableState: 'ready',
    createdAt: '2026-05-28 17:05',
    contentPreview: 'Normalize XML syntax and preserve all semantic stage labels before parsing.',
  },
  {
    key: 'image-53-gate',
    role: 'Image 5.3 Route Gate',
    roleFamily: 'Roadmap',
    version: 'image-5-3-gate-draft',
    name: 'Image 5.3 route proof',
    lifecycle: 'draft',
    folders: ['Roadmap'],
    description: 'Additive roadmap route; not a replacement for production prompt roles.',
    isDefault: false,
    variables: ['{{Deck-Full-Content}}'],
    variableState: 'missing',
    createdAt: '2026-05-29 10:13',
    contentPreview: 'Prove model gate, route binding, and artifact persistence before coding the new route.',
  },
];

const exactVariableDescriptions: Record<string, string> = {
  '{{Deck-Full-Content}}': 'Full source deck content for prompt rendering.',
  '{{Deck-User-Requirement}}': 'User requirement selected for the run.',
  '{{Deck-Required-color}}': 'Required color or theme instruction selected for the run.',
  '{{Deck-Design-principle}}': 'Designer planning or design principle passed into HTML generation.',
  '{{Slide-Content}}': 'Current slide content for the active slide position.',
  '{{Deck-Title}}': 'Deck title used by the Image cover prompt.',
};

const exactVariableMatrix: Array<{ role: string; tokens: string[]; sampleReference: string }> = [
  { role: 'Designer', tokens: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}'], sampleReference: 'Designer system prompt v4' },
  { role: 'HTML Agent', tokens: ['{{Deck-Design-principle}}', '{{Deck-User-Requirement}}', '{{Slide-Content}}'], sampleReference: 'HTML Agent build prompt v6' },
  { role: 'Image Cover 3.1', tokens: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}', '{{Slide-Content}}', '{{Deck-Title}}'], sampleReference: 'Image cover prompt 3.1' },
  { role: 'Image 1.0', tokens: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}', '{{Slide-Content}}'], sampleReference: 'Image 1.0 continuation prompt' },
  { role: 'Image 3.0 Seed', tokens: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}', '{{Slide-Content}}'], sampleReference: 'Image 3.0 seed prompt' },
  { role: 'Image 3.0 Non-Seed', tokens: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}', '{{Slide-Content}}'], sampleReference: 'Image 3.0 non-seed prompt' },
  { role: 'Image 3.2 Seed', tokens: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}', '{{Slide-Content}}'], sampleReference: 'Image 3.2 seed prompt' },
  { role: 'Image 3.2 Non-Seed', tokens: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}', '{{Slide-Content}}'], sampleReference: 'Image 3.2 non-seed prompt' },
  { role: 'Image 5.0 Unified', tokens: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}', '{{Slide-Content}}'], sampleReference: 'Unified Image Designer v5' },
  { role: 'Image Generator', tokens: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}', '{{Slide-Content}}'], sampleReference: 'Image generator request v4' },
  { role: 'XML Cleanup', tokens: ['{{Deck-Full-Content}}', '{{Deck-User-Requirement}}', '{{Deck-Required-color}}', '{{Slide-Content}}'], sampleReference: 'XML cleanup prompt v1' },
];

export const variableRows: VariableRow[] = exactVariableMatrix.flatMap((group, groupIndex) => group.tokens.map((token, tokenIndex) => ({
  key: `${group.role.toLowerCase().replaceAll(' ', '-').replaceAll('.', '')}-${token.replace(/[{}]/g, '').toLowerCase()}`,
  role: group.role,
  token,
  description: exactVariableDescriptions[token],
  status: group.role === 'Image 3.2 Non-Seed' && token === '{{Deck-User-Requirement}}' ? 'disabled' : 'active',
  references: Math.max(1, 8 - groupIndex + tokenIndex),
  sampleReference: group.sampleReference,
})));

export const combinationRows: CombinationRow[] = [
  {
    key: 'image-prod',
    name: 'Image Production Image',
    designer: 'Designer / GPT-5.4',
    htmlAgent: 'Not used',
    autoSpill: 'Auto-Spill / GPT-5.4 mini',
    imageDesigner: 'Image 5.0 Unified / GPT-5.4',
    imageGenerator: 'Image Generator / gemini-3.1-pro-image',
    timeoutMinutes: 30,
    maxConcurrentRuns: 10,
    isDefault: true,
  },
  {
    key: 'html-review',
    name: 'HTML Review Route',
    designer: 'Designer / Gemini 3.1 Flash-Lite Preview',
    htmlAgent: 'HTML Agent / Gemini 3 Flash',
    autoSpill: 'Auto-Spill / GPT-5.4 mini',
    imageDesigner: 'Not used',
    imageGenerator: 'Captured PNG',
    timeoutMinutes: 20,
    maxConcurrentRuns: 5,
    isDefault: true,
  },
  {
    key: 'image10-legacy',
    name: 'Image 1.0 Legacy Debug',
    designer: 'Designer / GPT-5.1',
    htmlAgent: 'Not used',
    autoSpill: 'Auto-Spill / GPT-5.1 mini',
    imageDesigner: 'Image 1.0 / GPT-5.1',
    imageGenerator: 'Image Generator / legacy compatible',
    timeoutMinutes: 45,
    maxConcurrentRuns: 4,
    isDefault: false,
  },
  {
    key: 'image53-gate',
    name: 'Image 5.3 Roadmap Gate',
    designer: 'pending gate',
    htmlAgent: 'pending gate',
    autoSpill: 'pending gate',
    imageDesigner: 'pending gate',
    imageGenerator: 'pending gate',
    timeoutMinutes: 30,
    maxConcurrentRuns: 1,
    isDefault: false,
  },
];

export const roleModelProfiles: RoleModelProfile[] = [
  { key: 'designer-test', role: 'Designer', environment: 'Test', provider: 'Gemini compatible', model: 'Gemini 3.1 Flash-Lite Preview', apiType: 'gemini', endpoint: 'masked test endpoint', effort: 'default', temperature: 'default', status: 'needs request' },
  { key: 'designer-prod', role: 'Designer', environment: 'Production Pro', provider: 'OpenAI compatible', model: 'GPT-5.4', apiType: 'openai', endpoint: 'masked production endpoint', effort: 'high', temperature: 'default', status: 'active' },
  { key: 'html-test', role: 'HTML Agent', environment: 'Test', provider: 'Gemini compatible', model: 'Gemini 3.1 Flash-Lite Preview', apiType: 'gemini', endpoint: 'masked test endpoint', effort: 'default', temperature: 'default', status: 'needs request' },
  { key: 'html-prod', role: 'HTML Agent', environment: 'Production Mini', provider: 'Gemini compatible', model: 'Gemini 3 Flash', apiType: 'gemini', endpoint: 'masked production endpoint', effort: 'high', temperature: '1', status: 'active' },
  { key: 'auto-spill-prod', role: 'Auto-Spill', environment: 'Production', provider: 'OpenAI compatible', model: 'GPT-5.4 mini', apiType: 'openai', endpoint: 'masked auto-spill endpoint', effort: 'low', temperature: 'default', status: 'active' },
  { key: 'prompt-assistant-prod', role: 'Prompt Adding Assistant', environment: 'Production', provider: 'OpenAI compatible', model: 'GPT-5.4 mini', apiType: 'openai', endpoint: 'masked assistant endpoint', effort: 'low', temperature: 'default', status: 'active' },
  { key: 'image-designer-test', role: 'Image Designer', environment: 'Test', provider: 'Gemini compatible', model: 'Gemini 3.1 Flash-Lite Preview', apiType: 'gemini', endpoint: 'masked director test endpoint', effort: 'default', temperature: 'default', status: 'needs request' },
  { key: 'image-designer-legacy', role: 'Image Designer', environment: 'Production Legacy', provider: 'OpenAI compatible', model: 'GPT 5.1', apiType: 'openai', endpoint: 'masked legacy endpoint', effort: 'high', temperature: '1', status: 'active' },
  { key: 'image-designer-prod', role: 'Image Designer', environment: 'Production', provider: 'OpenAI compatible', model: 'GPT 5.4', apiType: 'openai', endpoint: 'masked production endpoint', effort: 'high', temperature: '1', status: 'active' },
  { key: 'image-generator-test', role: 'Image Generator', environment: 'Test', provider: 'Gemini image compatible', model: 'gemini-3.1-flash-image', apiType: 'gemini', endpoint: 'masked image test endpoint', effort: 'low thinking', temperature: '1', status: 'needs request' },
  { key: 'image-generator-mini', role: 'Image Generator', environment: 'Production Mini', provider: 'Gemini image compatible', model: 'gemini-3.1-flash-image', apiType: 'gemini', endpoint: 'masked image mini endpoint', effort: 'high thinking', temperature: '1', status: 'needs request' },
  { key: 'image-generator-prod', role: 'Image Generator', environment: 'Production', provider: 'Gemini image compatible', model: 'gemini-3.1-pro-image', apiType: 'gemini', endpoint: 'masked image endpoint', effort: 'high thinking', temperature: '1', status: 'needs request' },
  { key: 'shared-extraction-prod', role: 'Shared Extraction', environment: 'Production', provider: 'OpenAI compatible', model: 'GPT 5.1', apiType: 'openai', endpoint: 'masked shared endpoint', effort: 'low', temperature: 'default', status: 'active' },
  { key: 'xml-cleanup-prod', role: 'XML Cleanup', environment: 'Production', provider: 'OpenAI compatible', model: 'GPT 5.1', apiType: 'openai', endpoint: 'masked cleanup endpoint', effort: 'low', temperature: 'default', status: 'active' },
];
