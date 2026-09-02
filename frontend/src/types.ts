export interface AgentConfig {
  api_type: string;
  endpoint: string;
  model: string;
  api_key: string;
  temperature: number;
  thinking: string | null;
}

export type ModelRole = 'designer' | 'html_agent' | 'auto_spill' | 'prompt_assistant' | 'evaluation_visual_qa' | 'image_designer' | 'image_generator' | 'shared_extraction' | 'xml_cleanup';
export type RouteEngine = 'html' | 'image';
export type RouteStrategy = 'html_default' | 'codex_html' | 'image_1_0' | 'image_3_0' | 'image_3_2' | 'image_5_0' | 'image_direct';
export type NativeRouteStrategy = 'codex_native' | 'image_direct' | 'image_3_0';
export type ConfigType = 'html' | 'image';
export type ImageRenderer = 'banana' | 'gpt_image_2';
export type ProviderChannel = 'gemini_generate_content' | 'zenmux_images_api';
export type ImageRequestMode = 'system_instruction_split' | 'blueprint_first';

export interface ModelProfile {
  id: number;
  role: ModelRole;
  name: string;
  api_type: string;
  endpoint: string;
  model: string;
  api_key: string;
  temperature: number;
  thinking: string | null;
  status: 'active' | 'disabled';
  created_at?: string;
  updated_at?: string;
}

export interface ModelProfileTestResult {
  ok: boolean;
  test_token?: string;
  tested_model?: string;
  tested_role?: string;
  test_mode?: string;
  tested_effort?: string;
  elapsed_ms?: number;
  response_preview?: string;
  response_detail?: string;
  temporary_image_preview?: string;
  temporary_image_deleted?: boolean;
}

export type AutoSplitThinkingEffort = 'low' | 'medium' | 'high';
export type AutoSplitContentMode = 'faithful' | 'editorial';

export interface AutoSplitProfileOption {
  id: number;
  name: string;
  model: string;
  api_type: 'gemini' | 'codex_exec';
  provider: 'Gemini Native' | 'Local Codex Exec';
  ready: boolean;
  readiness_message: string;
}

export interface AutoSplitSettings {
  model_profile_id: number;
  thinking_effort: AutoSplitThinkingEffort;
  content_mode: AutoSplitContentMode;
  selected_profile: AutoSplitProfileOption;
  available_profiles: AutoSplitProfileOption[];
  updated_at: string;
}

export interface Config {
  id: number;
  name: string;
  type: ConfigType;
  designer: AgentConfig;
  html_agent: AgentConfig;
  designer_profile_id?: number | null;
  html_agent_profile_id?: number | null;
  is_default?: boolean;
  timeout_minutes: number;
  max_concurrent_runs: number;
  route_model_bindings?: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export type LifecycleStatus = 'active' | 'archived' | 'recycle_bin' | 'purged';

export interface Folder {
  id: number;
  scope: 'deck' | 'requirement' | 'color' | 'prompt';
  name: string;
  parent_id?: number | null;
  created_at: string;
  updated_at?: string;
}

export interface SystemVariable {
  id: number;
  agent_type: string;
  name: string;
  description?: string | null;
  status: 'active' | 'disabled';
  created_at: string;
  updated_at?: string;
}

export interface SystemSettings {
  provider_concurrency: Record<string, number>;
  run_queue_concurrency: number;
}

export interface Deck {
  id: number;
  title: string;
  content: string;
  lifecycle_status?: LifecycleStatus;
  folder_ids?: number[];
  slide_count?: number;
  created_at: string;
}

export interface Requirement {
  id: number;
  title: string;
  content: string;
  lifecycle_status?: LifecycleStatus;
  folder_ids?: number[];
  created_at: string;
}

export interface Color {
  id: number;
  title: string;
  content: string;
  source_type?: string | null;
  source_image_path?: string | null;
  source_metadata?: string | null;
  lifecycle_status?: LifecycleStatus;
  folder_ids?: number[];
  created_at: string;
}

export interface Slide {
  id: number;
  deck_id: number;
  position: number;
  title: string;
  content: string;
  split_mode: string;
}

export interface Run {
  id: number;
  batch_id?: number;
  deck_id: number;
  deck_title?: string;
  deck_snapshot_fingerprint?: string | null;
  deck_snapshot_label?: string | null;
  deck_snapshot_slide_count?: number | null;
  requirement_id: number;
  requirement_title?: string;
  color_id: number;
  color_title?: string;
  config_id: number;
  config_name?: string;
  auto_candidate_index?: number | null;
  status: string;
  output_dir?: string;
  design_principle_raw?: string;
  design_principle_json?: string;
  error_message?: string;
  started_at?: string;
  completed_at?: string;
  created_at: string;
  progress?: RunProgress;
  engine?: RouteEngine;
  strategy?: RouteStrategy;
  route_metadata?: Record<string, unknown>;
  stage_artifacts?: Record<string, unknown>;
  model_call_metadata?: Record<string, unknown>;
}

export interface CodexAuditEvent {
  id?: number;
  sequence?: number;
  event_type?: string | null;
  item_type?: string | null;
  is_error?: number | boolean;
  observed_at?: string | null;
  event_timestamp?: string | null;
  usage?: Record<string, unknown> | null;
  payload?: Record<string, unknown> | null;
}

export type NativeTerminalState = 'result_received' | 'completed' | 'normalization_failed' | 'failed' | 'timed_out' | 'skipped';
export type NativeFailureCode =
  | 'business_output_path_invalid'
  | 'canonical_session_unavailable'
  | 'common_audit_failed'
  | 'image_call_binding_failed'
  | 'native_runner_error'
  | 'normalization_failed'
  | 'private_evidence_path_invalid';

export interface NativeImageRecord {
  png_valid: boolean | null;
  width: number | null;
  height: number | null;
  bytes: number | null;
  sha256: string | null;
}

export interface NativeImageDerivation {
  background: string | null;
  foreground: string | null;
  parent_dimensions: readonly [number, number] | null;
  child_dimensions: readonly [number, number] | null;
  parent_bytes: number | null;
  child_bytes: number | null;
  parent_sha256: string | null;
  child_sha256: string | null;
}

export interface NativeImageNormalization {
  normalized: boolean | null;
  algorithm: string | null;
  operation: string | null;
  pillow_version: string | null;
  parent_dimensions: readonly [number, number] | null;
  child_dimensions: readonly [number, number] | null;
  parent_bytes: number | null;
  child_bytes: number | null;
  parent_sha256: string | null;
  child_sha256: string | null;
  derivation?: NativeImageDerivation;
}

/** The path-free Native evidence whitelist emitted by `/api/runs/:id`. */
export interface NativeImageAuditProjection {
  requested_model: string | null;
  actual_model: string | null;
  requested_reasoning_effort: string | null;
  actual_reasoning_effort: string | null;
  cli_version: string | null;
  binary_sha256: string | null;
  attempt: number | null;
  terminal_state: NativeTerminalState | null;
  retry: boolean | null;
  timeout: boolean | null;
  skip: boolean | null;
  fallback_used: boolean | null;
  failure_code: NativeFailureCode | null;
  business_image?: NativeImageRecord;
  normalization?: NativeImageNormalization;
}

export interface NativeCodexAuditEvent {
  sequence: number | null;
  event_type: string | null;
  item_type: string | null;
  is_error: number | boolean | null;
  observed_at: string | null;
  event_timestamp: string | null;
  usage?: Record<string, unknown> | null;
}

export interface NativeCodexAuditInvocation {
  id: number;
  run_id: number | null;
  run_slide_id: number | null;
  stage_id: string | null;
  role: string | null;
  attempt: number | null;
  status: string | null;
  sandbox: string | null;
  model: string | null;
  reasoning_effort: string | null;
  event_count: number | null;
  error_event_count: number | null;
  usage: Record<string, unknown> | null;
  exit_code: number | null;
  error_message: string | null;
  started_at: string | null;
  ended_at: string | null;
  elapsed_ms: number | null;
  events: NativeCodexAuditEvent[];
  native_image: NativeImageAuditProjection;
}

export interface NativeCodexRunAudit {
  run_id: number;
  status: string | null;
  failure_count: number | null;
  attempt_count: number | null;
  per_slide_statuses: Array<Record<string, unknown>>;
  invocation_count: number;
  event_count: number;
  error_event_count: number;
  invocations: NativeCodexAuditInvocation[];
}

export interface CodexAuditInvocation {
  id: number;
  run_id?: number | null;
  run_slide_id?: number | null;
  stage_id?: string | null;
  role?: string | null;
  attempt?: number | null;
  command?: unknown;
  cwd?: string | null;
  sandbox?: string | null;
  model?: string | null;
  reasoning_effort?: string | null;
  prompt_sha256?: string | null;
  raw_jsonl_path?: string | null;
  raw_jsonl_sha256?: string | null;
  observed_jsonl_path?: string | null;
  observed_jsonl_sha256?: string | null;
  output_path?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  elapsed_ms?: number | null;
  exit_code?: number | null;
  status?: string | null;
  error_message?: string | null;
  usage?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
  event_count?: number | null;
  error_event_count?: number | null;
  events?: CodexAuditEvent[];
  native_image?: NativeImageAuditProjection;
}

export interface CodexMachineQaSummary {
  status?: string;
  total?: number;
  pass_count?: number;
  fail_count?: number;
  skipped_count?: number;
}

export interface CodexMachineQaRow {
  id?: number;
  evaluation_id?: number;
  evaluation_title?: string | null;
  evaluation_status?: string | null;
  attempt_id?: number | null;
  attempt_label?: string | null;
  attempt_status?: string | null;
  attempt_batch_id?: number | null;
  variant_label?: string | null;
  run_id?: number | null;
  run_slide_id?: number | null;
  slide_position?: number | null;
  verdict?: string | null;
  issues?: Array<Record<string, unknown>>;
  model_profile_id?: number | null;
  prompt_id?: number | null;
  raw_response?: string | null;
  created_at?: string | null;
}

export interface CodexRunAudit {
  run_id: number;
  status?: string | null;
  failure_count?: number | null;
  attempt_count?: number | null;
  per_slide_statuses?: Array<Record<string, unknown>>;
  invocation_count?: number;
  event_count?: number;
  error_event_count?: number;
  raw_jsonl_paths?: string[];
  observed_jsonl_paths?: string[];
  invocations?: CodexAuditInvocation[];
  machine_qa_summary?: CodexMachineQaSummary | null;
  machine_qa?: CodexMachineQaRow[];
}

/** Full, credential-redacted detail loaded only after an explicit audit expansion. */
export interface CodexAuditDetailLineage {
  run_id: number;
  run_slide_id: number | null;
  stage_id: string | null;
  attempt: number | null;
  invocation_id: number;
  session: {
    bytes: number | null;
    sha256: string | null;
  };
  call: {
    id: string | null;
    arguments_sha256: string | null;
  };
}

export interface CodexAuditDetailJsonlReference {
  sha256: string | null;
}

export interface CodexAuditDetailEvent extends Omit<CodexAuditEvent, 'payload'> {
  payload: Record<string, unknown> | null;
}

export interface CodexAuditDetailCall {
  event_sequence: number | null;
  kind: string | null;
  name: string | null;
  call_id: string | null;
  payload: Record<string, unknown>;
}

export interface CodexAuditInvocationDetail {
  run_id: number;
  invocation_id: number;
  lineage: CodexAuditDetailLineage;
  prompt: string | null;
  assistant_output: string | null;
  tool_calls: CodexAuditDetailCall[];
  imagegen_calls: CodexAuditDetailCall[];
  events?: CodexAuditDetailEvent[];
  errors: {
    invocation_error: string | null;
    metadata_error: string | null;
    event_errors: CodexAuditDetailEvent[];
  };
  jsonl: {
    raw: CodexAuditDetailJsonlReference;
    observed: CodexAuditDetailJsonlReference;
    canonical_session: {
      bytes: number | null;
      sha256: string | null;
    };
  };
  metadata: Record<string, unknown>;
}

/** One explicitly requested, bounded page of an invocation event timeline. */
export interface CodexAuditEventPage {
  run_id: number;
  invocation_id: number;
  items: CodexAuditDetailEvent[];
  next_cursor: string | null;
}

export interface RunSlide {
  id: number;
  run_id: number;
  slide_id: number;
  position: number;
  slide_title?: string;
  slide_content?: string;
  slide_title_snapshot?: string | null;
  slide_content_snapshot?: string | null;
  raw_response?: string;
  clean_html?: string;
  html_path?: string;
  screenshot_path?: string;
  screenshot_path_source?: string;
  slide_type?: 'cover' | 'content';
  xml_raw?: string;
  xml_clean?: string;
  final_image_path?: string;
  stage_artifacts?: Record<string, unknown>;
  artifact_contents?: Record<string, unknown>;
  seed_dependency?: Record<string, unknown>;
  conversation_id?: string;
  active_version?: ArtifactVersion | null;
  versions?: ArtifactVersion[];
  generation_history?: GenerationHistoryItem[];
  has_displayable_artifact?: boolean;
  status: string;
  error_message?: string;
}

export interface NativePublicEvidenceContainer {
  native_image: NativeImageAuditProjection;
}

/** Native `/api/runs/:id` response shape used by the Run Detail fixture contract. */
export interface NativeRunSlide {
  id: number;
  run_id: number;
  slide_id: number;
  position: number;
  status: string;
  final_image_path: string | null;
  conversation_id: null;
  raw_response: null;
  clean_html: null;
  html_path: null;
  screenshot_path: null;
  screenshot_path_source: null;
  xml_raw: null;
  xml_clean: null;
  error_message: null;
  stage_artifacts: NativePublicEvidenceContainer;
  seed_dependency?: NativePublicEvidenceContainer;
}

export interface ArtifactVersion {
  id: number;
  target_run_slide_id: number;
  artifact_run_slide_id: number;
  source_run_id?: number | null;
  source_batch_id?: number | null;
  slide_id?: number | null;
  position?: number | null;
  version_number: number;
  status: 'active' | 'available' | 'failed' | string;
  html_path?: string | null;
  screenshot_path?: string | null;
  screenshot_path_source?: string | null;
  final_image_path?: string | null;
  clean_html?: string | null;
  xml_raw?: string | null;
  xml_clean?: string | null;
  raw_response?: string | null;
  evidence_snapshot?: Record<string, unknown>;
  is_active?: boolean;
  created_at?: string;
}

export interface GenerationHistoryItem {
  id: number;
  action: 'retry' | 'force_regenerate' | 'version_restored' | 'initial_generation' | 'auto_retry' | 'continue' | string;
  scope: 'batch' | 'run' | 'slide' | 'image' | string;
  target_id?: number | null;
  target_run_slide_id?: number | null;
  artifact_run_slide_id?: number | null;
  source_run_id?: number | null;
  source_batch_id?: number | null;
  created_run_id?: number | null;
  created_batch_id?: number | null;
  created_batch_status?: string | null;
  version_id?: number | null;
  status: 'queued' | 'running' | 'success' | 'failed' | 'legacy' | string;
  force_mode?: 'overwrite_current' | 'new_run' | 'new_batch' | string | null;
  summary?: string | null;
  error_message?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string;
}

export interface RunDetail extends Run {
  slides: RunSlide[];
  codex_audit?: CodexRunAudit;
  deck_content?: string;
  requirement_content?: string;
  color_content?: string;
  designer_prompt_version?: string;
  html_prompt_version?: string;
}

export interface NativeRunDetail extends Omit<
  RunDetail,
  | 'engine'
  | 'strategy'
  | 'status'
  | 'output_dir'
  | 'design_principle_raw'
  | 'design_principle_json'
  | 'error_message'
  | 'route_metadata'
  | 'stage_artifacts'
  | 'model_call_metadata'
  | 'slides'
  | 'codex_audit'
> {
  engine: 'image';
  strategy: NativeRouteStrategy;
  status: 'completed' | 'failed';
  output_dir: null;
  design_principle_raw: null;
  design_principle_json: null;
  error_message: null;
  stage_artifacts: NativePublicEvidenceContainer;
  model_call_metadata: NativePublicEvidenceContainer;
  slides: NativeRunSlide[];
  codex_audit: NativeCodexRunAudit;
}

export interface RunProgress {
  total: number;
  completed: number;
  failed: number;
  pending: number;
  running: number;
  displayable?: number;
  missing_displayable?: number;
}

export interface BatchProgress {
  total_runs: number;
  queued_runs: number;
  running_runs: number;
  completed_runs: number;
  completed_with_failures_runs?: number;
  failed_runs: number;
  timed_out_runs: number;
  failure_rate: number;
}

export interface Batch extends BatchProgress {
  id: number;
  deck_id: number;
  deck_title?: string;
  config_id: number;
  config_name?: string;
  designer_prompt_id?: number | null;
  html_prompt_id?: number | null;
  designer_prompt_version?: string | null;
  html_prompt_version?: string | null;
  generation_mode?: string | null;
  engine?: RouteEngine;
  strategy?: RouteStrategy;
  representative_run_id?: number | null;
  requirements?: Array<{ id: number; title: string }>;
  colors?: Array<{ id: number; title: string }>;
  status: string;
  error_message?: string | null;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
}

export interface BatchDetail extends Batch {
  runs: Run[];
  generation_history?: GenerationHistoryItem[];
}

export interface RunFailBreakdownItem {
  count: number;
  percent: number;
  route_type?: string;
  route?: string;
  mode?: string;
  status?: string;
  error_class?: string;
  model?: string;
  retry_signal?: string;
}

export interface RunFailTrendItem {
  window: string;
  total_runs: number;
  failed_or_timed_out: number;
  failure_rate: number;
}

export interface RunFailDiagnosticItem {
  key: string;
  count: number;
  percent: number;
  insight: string;
  recommended_action: string;
  raw_messages: string[];
}

export interface RunFailStats {
  window: string;
  filters: {
    route_type: 'all' | 'html' | 'image';
    date_preset: 'today' | 'yesterday' | 'last_7_days' | 'last_month' | 'this_year' | 'custom';
    start_date: string;
    end_date: string;
    timezone: string;
  };
  total_runs: number;
  failed_or_timed_out: number;
  failure_rate: number;
  by_route_type: Array<RunFailBreakdownItem & { route_type: string }>;
  by_route: Array<RunFailBreakdownItem & { route: string }>;
  by_mode: Array<RunFailBreakdownItem & { mode: string }>;
  by_status: Array<RunFailBreakdownItem & { status: string }>;
  by_error_class: Array<RunFailBreakdownItem & { error_class: string }>;
  by_model: Array<RunFailBreakdownItem & { model: string }>;
  by_retry_signal: Array<RunFailBreakdownItem & { retry_signal: string }>;
  trend: RunFailTrendItem[];
  diagnostics: RunFailDiagnosticItem[];
}

export interface Prompt {
  id: number;
  agent_type: string;
  version: string;
  name: string;
  content: string;
  status: string;
  description?: string;
  lifecycle_status?: LifecycleStatus;
  folder_ids?: number[];
  is_default?: boolean;
  created_at: string;
}

export interface BulkActionResult {
  id: number;
  status: 'ok' | 'error';
  error?: string;
  record?: unknown;
}

export interface GenerationActionResult {
  ok: boolean;
  action: 'retry' | 'force_regenerate';
  scope: 'batch' | 'run' | 'slide' | 'image';
  target_id: number;
  force_mode?: 'overwrite_current' | 'new_run' | 'new_batch' | null;
  source_batch_id?: number | null;
  created_batch_id?: number | null;
  created_run_ids: number[];
  affected_slide_ids: number[];
  launched_run_ids: number[];
  skipped: Array<{ run_id?: number; reason: string; retry_signal?: string }>;
}

export interface DeckSplitDraftSlide {
  title: string;
  content: string;
  split_mode?: string;
}

export interface DeckSplitDraft {
  id: number;
  deck_id: number;
  status: string;
  mode: string;
  model?: string | null;
  model_profile_id?: number | null;
  thinking_effort?: AutoSplitThinkingEffort | null;
  content_mode?: AutoSplitContentMode | null;
  attempt_count: number;
  last_error_code?: 'configuration' | 'timeout' | 'provider_rejected' | 'parse' | 'integrity' | null;
  error_message?: string | null;
  slides: DeckSplitDraftSlide[];
  created_at: string;
  confirmed_at?: string | null;
}

export type EvaluationStatus = 'draft' | 'running' | 'reviewing' | 'reviewed' | 'archived' | string;

export interface EvaluationSnapshot {
  deck?: {
    id?: number;
    title?: string;
    snapshot_fingerprint?: string;
    snapshot_slide_count?: number;
    snapshot_label?: string;
  };
  config?: { id?: number; name?: string; type?: ConfigType | string; model?: string };
  requirement?: { id?: number; title?: string; content?: string };
  color?: { id?: number; title?: string; content?: string };
  engine?: RouteEngine | string;
  strategy?: RouteStrategy | string;
  route_metadata?: Record<string, unknown>;
  prompts?: Record<string, { id?: number; agent_type?: string; version?: string; name?: string; content?: string } | null>;
  run?: { id?: number; batch_id?: number | null; status?: string; created_at?: string };
  model_call_metadata?: Record<string, unknown>;
  variant_index?: number;
}

export interface EvaluationListItem {
  id: number;
  deck_id: number;
  deck_title?: string;
  title: string;
  goal: string;
  status: EvaluationStatus;
  export_config?: Record<string, unknown>;
  variant_count?: number;
  attempt_count?: number;
  created_at: string;
  updated_at?: string;
}

export interface EvaluationAttempt {
  id: number;
  evaluation_id: number;
  variant_id: number;
  run_id?: number | null;
  batch_id?: number | null;
  label: string;
  attempt_index: number;
  status?: string | null;
  snapshot?: EvaluationSnapshot;
  run_missing?: boolean;
  run_status?: string;
  deck_id?: number;
  deck_title?: string;
  engine?: RouteEngine | string;
  strategy?: RouteStrategy | string;
  slides: RunSlide[];
  created_at?: string;
  updated_at?: string;
}

export interface EvaluationVariant {
  id: number;
  evaluation_id: number;
  label: string;
  goal: string;
  comparison_variable?: string | null;
  generation_plan_snapshot?: EvaluationSnapshot;
  representative_attempt_id?: number | null;
  sort_order?: number;
  attempts: EvaluationAttempt[];
  created_at?: string;
  updated_at?: string;
}

export interface EvaluationDetail extends EvaluationListItem {
  variants: EvaluationVariant[];
  representative_attempts?: EvaluationAttempt[];
  run_ids?: number[];
  batch_ids?: number[];
  total_attempts?: number;
  notes?: EvaluationNote[];
  issue_tags?: EvaluationIssueTag[];
  slide_tags?: EvaluationSlideTag[];
  machine_qa?: EvaluationMachineQa[];
}

export interface EvaluationNote {
  id: number;
  evaluation_id: number;
  variant_id?: number | null;
  attempt_id?: number | null;
  slide_position?: number | null;
  note: string;
  created_at?: string;
  updated_at?: string;
}

export interface EvaluationIssueTag {
  id: number;
  evaluation_id: number;
  label: string;
  source: 'human' | 'machine' | string;
  created_at?: string;
}

export interface EvaluationSlideTag {
  id: number;
  evaluation_id: number;
  attempt_id?: number | null;
  run_slide_id?: number | null;
  slide_position: number;
  tag_id: number;
  label: string;
  source: 'human' | 'machine' | string;
  created_at?: string;
}

export interface EvaluationMachineQa {
  id: number;
  evaluation_id: number;
  attempt_id?: number | null;
  run_slide_id?: number | null;
  slide_position: number;
  verdict: 'pass' | 'fail' | 'skipped' | string;
  issues: Array<Record<string, unknown>>;
  model_profile_id?: number | null;
  prompt_id?: number | null;
  raw_response?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface EvaluationHistoryCreatePayload {
  title: string;
  goal: string;
  variants: Array<{
    run_id: number;
    label: string;
    goal: string;
  }>;
}

export interface EvaluationHistoryRunCandidate {
  id: number;
  deck_id: number;
  deck_title?: string;
  deck_snapshot_fingerprint?: string | null;
  deck_snapshot_label?: string | null;
  deck_snapshot_slide_count?: number | null;
  requirement_id: number;
  requirement_title?: string;
  color_id: number;
  color_title?: string;
  config_id: number;
  config_name?: string;
  auto_candidate_index?: number | null;
  status: string;
  started_at?: string | null;
  completed_at?: string | null;
  created_at: string;
  progress?: RunProgress;
  engine?: RouteEngine;
  strategy?: RouteStrategy;
  route_metadata?: Record<string, unknown>;
}

export interface EvaluationBlankCreatePayload {
  title: string;
  goal: string;
  deck_id: number;
  repeat_count: number;
  variants: Array<{
    label: string;
    goal: string;
    comparison_variable?: string | null;
    mode?: 'manual' | 'auto';
    requirement_id?: number;
    color_id?: number | null;
    config_id: number;
    engine: RouteEngine;
    strategy: RouteStrategy;
    auto_candidate_count?: number;
    auto_color_id?: number | null;
    auto_color_ids?: number[];
    route_metadata?: Record<string, unknown>;
    designer_prompt_id?: number;
    html_prompt_id?: number;
  }>;
}

export interface EvaluationExportPayload {
  scope: 'current_slide' | 'all_slides';
  slide_position?: number;
  metadata_fields: string[];
}

export type CodexSessionLevel = 'L1' | 'L2' | 'L3' | 'L4';

export interface CodexSessionSource {
  bytes: number;
  mtime_ns: number;
  sha256: string;
  projection_version: string;
}

export interface CodexSessionItem {
  kind: string;
  sequence?: number;
  turn_id?: string;
  role?: string | null;
  phase?: string | null;
  tool_name?: string | null;
  timestamp?: string | null;
  range_start?: number;
  range_end?: number;
  truncated?: boolean;
  output_chars?: number;
  preview?: string;
  preview_source?: 'text' | 'output' | 'input' | 'none';
  preview_reason?: 'no_persisted_fragment';
  text?: string;
  input?: string;
  output?: string;
  sha256?: string;
  raw_cursor?: string;
  message_count?: number;
  tool_count?: number;
  indexed_events?: number;
  core_conclusion?: CodexSessionCoreConclusion | null;
  metadata?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface CodexSessionCoreConclusion {
  kind: 'message';
  sequence: number;
  role: string | null;
  phase: string | null;
  timestamp: string | null;
  text: string;
  truncated: boolean;
}

export interface CodexSessionEnvelope {
  schema_version: 'codex_session_reader_v1';
  session_id: string;
  level: CodexSessionLevel;
  source: CodexSessionSource;
  items: CodexSessionItem[];
  next_cursor: string | null;
  truncated: boolean;
  filters: {
    effective_only: boolean;
  };
}
