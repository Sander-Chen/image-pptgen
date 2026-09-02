import type {
  Batch,
  BatchDetail,
  Config,
  Deck,
  Slide,
  Requirement,
  Color,
  Run,
  RunDetail,
  RunFailStats,
  Prompt,
  DeckSplitDraft,
  Folder,
  SystemSettings,
  SystemVariable,
  ModelProfile,
  ModelProfileTestResult,
  AutoSplitContentMode,
  AutoSplitSettings,
  AutoSplitThinkingEffort,
  ArtifactVersion,
  BulkActionResult,
  GenerationActionResult,
  RouteEngine,
  RouteStrategy,
  EvaluationListItem,
  EvaluationDetail,
  EvaluationHistoryCreatePayload,
  EvaluationHistoryRunCandidate,
  EvaluationBlankCreatePayload,
  EvaluationExportPayload,
  CodexAuditInvocationDetail,
  CodexAuditEventPage,
  CodexSessionEnvelope,
} from './types';

const BASE = '';
type RunFailParams = {
  route_type?: 'all' | 'html' | 'image';
  date_preset?: 'today' | 'yesterday' | 'last_7_days' | 'last_month' | 'this_year' | 'custom';
  start_date?: string;
  end_date?: string;
};

function queryString(params?: object): string {
  const search = new URLSearchParams();
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== '') {
      search.set(key, String(value));
    }
  });
  const query = search.toString();
  return query ? `?${query}` : '';
}

export class ApiError extends Error {
  status: number;
  payload: Record<string, unknown>;

  constructor(message: string, status: number, payload: Record<string, unknown>) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.payload = payload;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const isFormData = options?.body instanceof FormData;
  const res = await fetch(`${BASE}${path}`, {
    headers: isFormData ? undefined : { 'Content-Type': 'application/json' },
    ...options,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({})) as Record<string, unknown>;
    const message = typeof err.error === 'string' ? err.error : res.statusText;
    throw new ApiError(message, res.status, err);
  }
  return res.json();
}

async function download(
  path: string,
  options: ({ requireZip?: boolean } & RequestInit) = { requireZip: true },
): Promise<{ blob: Blob; filename: string }> {
  const { requireZip = true, ...requestOptions } = options;
  const isFormData = requestOptions.body instanceof FormData;
  const res = await fetch(`${BASE}${path}`, {
    headers: isFormData ? undefined : { 'Content-Type': 'application/json', ...(requestOptions.headers || {}) },
    ...requestOptions,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.error || res.statusText);
  }
  const contentType = (res.headers.get('Content-Type') || '').toLowerCase();
  const disposition = res.headers.get('Content-Disposition') || '';
  const filenameMatch = disposition.match(/filename\*=UTF-8''([^;]+)|filename="?([^";]+)"?/);
  const filename = filenameMatch ? decodeURIComponent(filenameMatch[1] || filenameMatch[2]) : 'download.zip';
  const blob = await res.blob();
  if (requireZip !== false) {
    const header = new Uint8Array(await blob.slice(0, 2).arrayBuffer());
    const looksLikeZip = header[0] === 0x50 && header[1] === 0x4b;
    const declaresZip = (
      contentType.includes('application/zip') ||
      contentType.includes('application/x-zip') ||
      contentType.includes('application/octet-stream') ||
      filename.toLowerCase().endsWith('.zip')
    );
    if (!declaresZip || !looksLikeZip) {
      throw new Error(`Expected ZIP download but received ${contentType || filename || 'unexpected response'}`);
    }
  }
  return {
    blob,
    filename,
  };
}

export const api = {
  systemSettings: {
    get: () => request<SystemSettings>('/api/system-settings'),
    update: (data: Partial<SystemSettings>) =>
      request<SystemSettings>('/api/system-settings', {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
  },

  folders: {
    list: (scope: string) => request<Folder[]>(`/api/folders?scope=${encodeURIComponent(scope)}`),
    create: (data: Partial<Folder>) =>
      request<Folder>('/api/folders', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: number, data: Partial<Folder>) =>
      request<Folder>(`/api/folders/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
  },

  systemVariables: {
    list: (agentType?: string) =>
      request<SystemVariable[]>(`/api/system-variables${agentType ? `?agent_type=${agentType}` : ''}`),
    create: (data: Partial<SystemVariable>) =>
      request<SystemVariable>('/api/system-variables', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: number, data: Partial<SystemVariable>) =>
      request<SystemVariable>(`/api/system-variables/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    references: (id: number) =>
      request<{ references: Array<{ prompt_id: number; version: string; snippet: string }> }>(
        `/api/system-variables/${id}/references`,
      ),
  },

  configs: {
    list: () => request<Config[]>('/api/configs'),
    get: (id: number) => request<Config>(`/api/configs/${id}`),
    create: (data: Partial<Config>) =>
      request<Config>('/api/configs', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: number, data: Partial<Config>) =>
      request<Config>(`/api/configs/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    delete: (id: number) =>
      request<void>(`/api/configs/${id}`, { method: 'DELETE' }),
    setDefault: (id: number) =>
      request<Config>(`/api/configs/${id}/default`, { method: 'POST' }),
  },

  autoSplitSettings: {
    get: () => request<AutoSplitSettings>('/api/auto-split-settings'),
    update: (data: {
      model_profile_id: number;
      thinking_effort: AutoSplitThinkingEffort;
      content_mode: AutoSplitContentMode;
    }) =>
      request<AutoSplitSettings>('/api/auto-split-settings', {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
  },

  modelProfiles: {
    list: (params?: { role?: string; status?: string }) => {
      const search = new URLSearchParams();
      if (params?.role) search.set('role', params.role);
      if (params?.status) search.set('status', params.status);
      const suffix = search.toString() ? `?${search.toString()}` : '';
      return request<ModelProfile[]>(`/api/model-profiles${suffix}`);
    },
    test: (data: Partial<ModelProfile>) =>
      request<ModelProfileTestResult>('/api/model-profiles/test', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    create: (data: Partial<ModelProfile>) =>
      request<ModelProfile>('/api/model-profiles', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: number, data: Partial<ModelProfile>) =>
      request<ModelProfile>(`/api/model-profiles/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
  },

  bulkActions: {
    apply: (data: { entity_type: string; action: string; ids: number[]; folder_ids?: number[] }) =>
      request<{ results: BulkActionResult[] }>('/api/bulk-actions', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },

  decks: {
    list: (params?: { status?: string; folder_id?: number | null }) => {
      const search = new URLSearchParams();
      if (params?.status) search.set('status', params.status);
      if (params?.folder_id) search.set('folder_id', String(params.folder_id));
      const suffix = search.toString() ? `?${search.toString()}` : '';
      return request<Deck[]>(`/api/decks${suffix}`);
    },
    get: (id: number) => request<Deck>(`/api/decks/${id}`),
    create: (data: { title: string; content: string }) =>
      request<Deck>('/api/decks', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: number, data: { title: string; content: string }) =>
      request<Deck>(`/api/decks/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    delete: (id: number) =>
      request<Deck>(`/api/decks/${id}`, { method: 'DELETE' }),
    archive: (id: number) =>
      request<Deck>(`/api/decks/${id}/archive`, { method: 'POST' }),
    restore: (id: number) =>
      request<Deck>(`/api/decks/${id}/restore`, { method: 'POST' }),
    forceDelete: (id: number) =>
      request<Deck & { historical_export_path?: string }>(`/api/decks/${id}/force-delete`, { method: 'POST' }),
    assignFolders: (id: number, folder_ids: number[]) =>
      request<{ id: number; folder_ids: number[] }>(`/api/decks/${id}/folders`, {
        method: 'PUT',
        body: JSON.stringify({ folder_ids }),
      }),
    split: (id: number) =>
      request<{ slides: Slide[] }>(`/api/decks/${id}/split`, {
        method: 'POST',
      }),
    createSplitDraft: (id: number, data: { mode?: 'llm' | 'deterministic' } = { mode: 'llm' }) =>
      request<DeckSplitDraft>(`/api/decks/${id}/split-drafts`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    confirmSplitDraft: (draftId: number) =>
      request<{ slide_ids: number[]; slides: Slide[] }>(`/api/deck-split-drafts/${draftId}/confirm`, {
        method: 'POST',
      }),
    deleteSplitDraft: (draftId: number) =>
      request<void>(`/api/deck-split-drafts/${draftId}`, { method: 'DELETE' }),
    retrySplitDraft: (draftId: number) =>
      request<DeckSplitDraft>(`/api/deck-split-drafts/${draftId}/retry`, {
        method: 'POST',
        body: JSON.stringify({}),
      }),
    getSlides: (id: number) => request<Slide[]>(`/api/decks/${id}/slides`),
  },

  slides: {
    create: (deckId: number, data: { title: string; content: string; position: number }) =>
      request<Slide>(`/api/decks/${deckId}/slides`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: number, data: Partial<Slide>) =>
      request<Slide>(`/api/slides/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    delete: (id: number) =>
      request<void>(`/api/slides/${id}`, { method: 'DELETE' }),
  },

  requirements: {
    list: (params?: { status?: string; folder_id?: number | null }) => {
      const search = new URLSearchParams();
      if (params?.status) search.set('status', params.status);
      if (params?.folder_id) search.set('folder_id', String(params.folder_id));
      const suffix = search.toString() ? `?${search.toString()}` : '';
      return request<Requirement[]>(`/api/requirements${suffix}`);
    },
    create: (data: { title: string; content: string }) =>
      request<Requirement>('/api/requirements', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: number, data: { title: string; content: string }) =>
      request<Requirement>(`/api/requirements/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    delete: (id: number) =>
      request<Requirement>(`/api/requirements/${id}`, { method: 'DELETE' }),
    archive: (id: number) =>
      request<Requirement>(`/api/requirements/${id}/archive`, { method: 'POST' }),
    restore: (id: number) =>
      request<Requirement>(`/api/requirements/${id}/restore`, { method: 'POST' }),
    forceDelete: (id: number) =>
      request<Requirement & { historical_export_path?: string }>(`/api/requirements/${id}/force-delete`, { method: 'POST' }),
    assignFolders: (id: number, folder_ids: number[]) =>
      request<{ id: number; folder_ids: number[] }>(`/api/requirements/${id}/folders`, {
        method: 'PUT',
        body: JSON.stringify({ folder_ids }),
      }),
  },

  colors: {
    list: (params?: { status?: string; folder_id?: number | null }) => {
      const search = new URLSearchParams();
      if (params?.status) search.set('status', params.status);
      if (params?.folder_id) search.set('folder_id', String(params.folder_id));
      const suffix = search.toString() ? `?${search.toString()}` : '';
      return request<Color[]>(`/api/colors${suffix}`);
    },
    create: (data: { title: string; content: string }) =>
      request<Color>('/api/colors', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    update: (id: number, data: { title: string; content: string }) =>
      request<Color>(`/api/colors/${id}`, {
        method: 'PUT',
        body: JSON.stringify(data),
      }),
    delete: (id: number) =>
      request<Color>(`/api/colors/${id}`, { method: 'DELETE' }),
    archive: (id: number) =>
      request<Color>(`/api/colors/${id}/archive`, { method: 'POST' }),
    restore: (id: number) =>
      request<Color>(`/api/colors/${id}/restore`, { method: 'POST' }),
    forceDelete: (id: number) =>
      request<Color & { historical_export_path?: string }>(`/api/colors/${id}/force-delete`, { method: 'POST' }),
    assignFolders: (id: number, folder_ids: number[]) =>
      request<{ id: number; folder_ids: number[] }>(`/api/colors/${id}/folders`, {
        method: 'PUT',
        body: JSON.stringify({ folder_ids }),
      }),
    extractFromImage: (data: FormData) =>
      request<Color>('/api/colors/extract-from-image', {
        method: 'POST',
        body: data,
      }),
  },

  prompts: {
    list: (agentTypeOrParams?: string | { agent_type?: string; status?: string; folder_id?: number | null }) => {
      const params = typeof agentTypeOrParams === 'string'
        ? { agent_type: agentTypeOrParams }
        : (agentTypeOrParams || {});
      const search = new URLSearchParams();
      if (params.agent_type) search.set('agent_type', params.agent_type);
      if (params.status) search.set('status', params.status);
      if (params.folder_id) search.set('folder_id', String(params.folder_id));
      const suffix = search.toString() ? `?${search.toString()}` : '';
      return request<Prompt[]>(`/api/prompts${suffix}`);
    },
    get: (id: number) => request<Prompt>(`/api/prompts/${id}`),
    create: (data: Partial<Prompt>) =>
      request<Prompt>('/api/prompts', { method: 'POST', body: JSON.stringify(data) }),
    update: (id: number, data: Partial<Prompt>) =>
      request<Prompt>(`/api/prompts/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
    delete: (id: number) =>
      request<Prompt>(`/api/prompts/${id}`, { method: 'DELETE' }),
    restore: (id: number) =>
      request<Prompt>(`/api/prompts/${id}/restore`, { method: 'POST' }),
    setDefault: (id: number) =>
      request<Prompt>(`/api/prompts/${id}/default`, { method: 'POST' }),
    assignFolders: (id: number, folder_ids: number[]) =>
      request<{ id: number; folder_ids: number[] }>(`/api/prompts/${id}/folders`, {
        method: 'PUT',
        body: JSON.stringify({ folder_ids }),
      }),
    duplicate: (id: number, data?: { version?: string; name?: string; status?: string }) =>
      request<Prompt>(`/api/prompts/${id}/duplicate`, {
        method: 'POST',
        body: JSON.stringify(data || {}),
      }),
    analyze: (data: { agent_type: string; content: string; baseline_prompt_id?: number }) =>
      request<{
        agent_type?: string;
        required_variables: string[];
        present_variables: string[];
        disabled_variables?: string[];
        mappings: Array<{ variable: string; confidence: number; status: string; target?: string }>;
        can_save: boolean;
        can_publish: boolean;
        integrity_checks?: Array<{ key: string; label: string; status: string; severity: string; message: string }>;
        change_report?: {
          similarity: number;
          risk_level: 'low' | 'medium' | 'high';
          inserted_variables: string[];
          original_length: number;
          updated_length: number;
          added_line_count: number;
          removed_line_count: number;
          added_lines: string[];
          removed_lines: string[];
          changed_hunks: Array<{ type: string; original: string[]; updated: string[] }>;
          summary: string;
        };
      }>('/api/prompts/analyze', { method: 'POST', body: JSON.stringify(data) }),
    assistVariables: (data: { agent_type: string; content: string; prefer_llm?: boolean }) =>
      request<{
        agent_type: string;
        content: string;
        inserted_variables: string[];
        mode: 'llm' | 'deterministic_review_block' | 'already_ready';
        requires_review: boolean;
        change_report: {
          similarity: number;
          risk_level: 'low' | 'medium' | 'high';
          inserted_variables: string[];
          original_length: number;
          updated_length: number;
          added_line_count: number;
          removed_line_count: number;
          added_lines: string[];
          removed_lines: string[];
          changed_hunks: Array<{ type: string; original: string[]; updated: string[] }>;
          summary: string;
        };
        assistant_error?: string | null;
        analysis: {
          required_variables: string[];
          present_variables: string[];
          disabled_variables?: string[];
          mappings: Array<{ variable: string; confidence: number; status: string; target?: string }>;
          can_save: boolean;
          can_publish: boolean;
          integrity_checks?: Array<{ key: string; label: string; status: string; severity: string; message: string }>;
          change_report?: {
            similarity: number;
            risk_level: 'low' | 'medium' | 'high';
            inserted_variables: string[];
            original_length: number;
            updated_length: number;
            added_line_count: number;
            removed_line_count: number;
            added_lines: string[];
            removed_lines: string[];
            changed_hunks: Array<{ type: string; original: string[]; updated: string[] }>;
            summary: string;
          };
        };
      }>('/api/prompts/assist-variables', { method: 'POST', body: JSON.stringify(data) }),
  },

  generate: {
    start: (data: {
      deck_id: number;
      requirement_ids?: number[];
      color_ids?: number[];
      config_id: number;
      designer_prompt_id?: number;
      html_prompt_id?: number;
      mode?: 'manual' | 'auto';
      auto_candidate_count?: number;
      auto_color_id?: number | null;
      auto_color_ids?: number[];
      engine?: RouteEngine;
      strategy?: RouteStrategy;
      route_metadata?: Record<string, unknown>;
    }) =>
      request<{ batch_id: number; run_ids: number[]; total_runs: number; slides_per_run: number }>('/api/generate', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    status: (runId: number) => request<Run>(`/api/runs/${runId}/status`),
  },

  batches: {
    list: () => request<Batch[]>('/api/batches'),
    active: () => request<Batch | Record<string, never>>('/api/batches/active'),
    get: (id: number) => request<BatchDetail>(`/api/batches/${id}`),
    download: (id: number) => download(`/api/batches/${id}/download`),
  },

  generationActions: {
    apply: (data: {
      action: 'retry' | 'force_regenerate';
      scope: 'batch' | 'run' | 'slide' | 'image';
      target_id: number;
      force_mode?: 'overwrite_current' | 'new_run' | 'new_batch';
    }) =>
      request<GenerationActionResult>('/api/generation-actions', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    pollAutoRetry: () =>
      request<GenerationActionResult>('/api/generation-actions/auto-retry-poll', {
        method: 'POST',
      }),
  },

  runs: {
    list: () => request<Run[]>('/api/runs'),
    get: (id: number) => request<RunDetail>(`/api/runs/${id}`),
    codexAuditDetail: (runId: number, invocationId: number) =>
      request<CodexAuditInvocationDetail>(`/api/runs/${runId}/codex-audit/invocations/${invocationId}`),
    codexAuditEvents: (runId: number, invocationId: number, cursor?: string) =>
      request<CodexAuditEventPage>(
        `/api/runs/${runId}/codex-audit/invocations/${invocationId}/events${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`,
      ),
    download: (id: number) => download(`/api/runs/${id}/download`),
    delete: (id: number) =>
      request<void>(`/api/runs/${id}`, { method: 'DELETE' }),
  },

  artifactVersions: {
    activate: (id: number) =>
      request<{ ok: boolean; active_version: ArtifactVersion }>(`/api/artifact-versions/${id}/activate`, {
        method: 'POST',
      }),
  },

  runSlides: {
    evidenceDownload: (id: number) => download(`/api/run-slides/${id}/evidence-download`),
  },

  runFail: {
    stats: (params?: RunFailParams) => request<RunFailStats>(`/api/runfail/stats${queryString(params)}`),
    export: (format: 'json' | 'csv', params?: RunFailParams) =>
      download(`/api/runfail/export${queryString({ format, ...params })}`, { requireZip: false }),
  },

  codexSessions: {
    summary: (sessionId: string) =>
      request<CodexSessionEnvelope>(`/api/codex-sessions/${encodeURIComponent(sessionId)}/summary`),
    index: (sessionId: string, cursor?: string) =>
      request<CodexSessionEnvelope>(`/api/codex-sessions/${encodeURIComponent(sessionId)}/index${queryString({ cursor })}`),
    detail: (sessionId: string, sequence: number) =>
      request<CodexSessionEnvelope>(
        `/api/codex-sessions/${encodeURIComponent(sessionId)}/detail${queryString({ sequence })}`,
      ),
    raw: (sessionId: string, cursor?: string) =>
      request<CodexSessionEnvelope>(`/api/codex-sessions/${encodeURIComponent(sessionId)}/raw${queryString({ cursor })}`),
  },

  evaluations: {
    list: () => request<EvaluationListItem[]>('/api/evaluations'),
    get: (id: number) => request<EvaluationDetail>(`/api/evaluations/${id}`),
    historyRuns: () => request<EvaluationHistoryRunCandidate[]>('/api/evaluations/history-runs'),
    createHistory: (data: EvaluationHistoryCreatePayload) =>
      request<EvaluationDetail>('/api/evaluations/history', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    createBlank: (data: EvaluationBlankCreatePayload) =>
      request<EvaluationDetail>('/api/evaluations/blank', {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    updateVariant: (evaluationId: number, variantId: number, data: { label?: string; goal?: string; comparison_variable?: string | null }) =>
      request<EvaluationDetail>(`/api/evaluations/${evaluationId}/variants/${variantId}`, {
        method: 'PATCH',
        body: JSON.stringify(data),
      }),
    setRepresentative: (evaluationId: number, variantId: number, attemptId: number | null) =>
      request<EvaluationDetail>(`/api/evaluations/${evaluationId}/variants/${variantId}/representative`, {
        method: 'PATCH',
        body: JSON.stringify({ attempt_id: attemptId }),
      }),
    addNote: (evaluationId: number, data: { variant_id?: number; attempt_id?: number; slide_position?: number; note: string }) =>
      request<EvaluationDetail>(`/api/evaluations/${evaluationId}/notes`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    addSlideTag: (evaluationId: number, data: { attempt_id?: number; run_slide_id?: number; slide_position: number; label: string; source?: string }) =>
      request<EvaluationDetail>(`/api/evaluations/${evaluationId}/slide-tags`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    runMachineQa: (
      evaluationId: number,
      data: {
        attempt_id?: number;
        run_slide_id?: number;
        slide_position?: number;
        scope?: 'all_slides' | 'selected_slides';
        attempt_ids?: number[];
        slide_positions?: number[];
      },
    ) =>
      request<EvaluationDetail>(`/api/evaluations/${evaluationId}/machine-qa/run`, {
        method: 'POST',
        body: JSON.stringify(data),
      }),
    export: (evaluationId: number, data: EvaluationExportPayload) =>
      download(`/api/evaluations/${evaluationId}/export`, {
        requireZip: true,
        method: 'POST',
        body: JSON.stringify(data),
      }),
  },
};
