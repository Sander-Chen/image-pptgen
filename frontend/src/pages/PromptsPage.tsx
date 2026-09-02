import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Divider,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  message,
} from 'antd';
import { CodeOutlined, CopyOutlined, DeleteOutlined, EditOutlined, FolderAddOutlined, PlusOutlined, SearchOutlined, StarOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { Folder, LifecycleStatus, Prompt, SystemVariable } from '../types';

const { TextArea } = Input;
type AgentType = 'designer' | 'html_agent';
type PromptRole =
  | AgentType
  | 'image_cover_3_1'
  | 'image_1_0'
  | 'image_3_0_seed'
  | 'image_3_0_non_seed'
  | 'image_3_2_seed'
  | 'image_3_2_non_seed'
  | 'image_5_0_unified'
  | 'image_generator'
  | 'evaluation_visual_qa'
  | 'xml_cleanup';
type VariableStatus = 'ready' | 'needs_confirmation' | 'missing' | 'disabled';
type IntegrityStatus = 'passed' | 'failed' | 'warning' | 'skipped';

interface VariableMapping {
  variable: string;
  confidence: number;
  status: VariableStatus;
  target?: string | null;
  source?: string;
}

interface PromptAnalysis {
  required_variables: string[];
  present_variables: string[];
  disabled_variables?: string[];
  mappings: VariableMapping[];
  can_save: boolean;
  can_publish: boolean;
  change_report?: AssistantChangeReport;
  integrity_checks?: IntegrityCheck[];
}

interface IntegrityCheck {
  key: string;
  label: string;
  status: IntegrityStatus;
  severity: 'blocker' | 'warning' | 'info';
  message: string;
}

interface AssistantChangeHunk {
  type: string;
  original: string[];
  updated: string[];
}

interface AssistantChangeReport {
  similarity: number;
  risk_level: 'low' | 'medium' | 'high';
  inserted_variables: string[];
  original_length: number;
  updated_length: number;
  added_line_count: number;
  removed_line_count: number;
  added_lines: string[];
  removed_lines: string[];
  changed_hunks: AssistantChangeHunk[];
  summary: string;
}

interface VariablePickerState {
  open: boolean;
  top: number;
  left: number;
  rangeStart: number;
  rangeEnd: number;
}

const promptRoleOptions: Array<{ value: PromptRole; label: string; short: string; color: string; group: 'HTML' | 'Image' | 'Utility' }> = [
  { value: 'designer', label: 'Designer Agent', short: 'Designer', color: 'blue', group: 'HTML' },
  { value: 'html_agent', label: 'HTML Agent', short: 'HTML', color: 'green', group: 'HTML' },
  { value: 'image_cover_3_1', label: 'Image Cover 3.1', short: 'Cover 3.1', color: 'gold', group: 'Image' },
  { value: 'image_1_0', label: 'Image 1.0', short: 'Img 1.0', color: 'gold', group: 'Image' },
  { value: 'image_3_0_seed', label: 'Image 3.0 Seed', short: '3.0 Seed', color: 'gold', group: 'Image' },
  { value: 'image_3_0_non_seed', label: 'Image 3.0 Non-Seed', short: '3.0 Non', color: 'gold', group: 'Image' },
  { value: 'image_3_2_seed', label: 'Image 3.2 Seed', short: '3.2 Seed', color: 'gold', group: 'Image' },
  { value: 'image_3_2_non_seed', label: 'Image 3.2 Non-Seed', short: '3.2 Non', color: 'gold', group: 'Image' },
  { value: 'image_5_0_unified', label: 'Image 5.0 Unified', short: 'Img 5.0', color: 'gold', group: 'Image' },
  { value: 'image_generator', label: 'Image Generator', short: 'Image', color: 'purple', group: 'Image' },
  { value: 'evaluation_visual_qa', label: 'Evaluation Visual QA', short: 'Visual QA', color: 'geekblue', group: 'Utility' },
  { value: 'xml_cleanup', label: 'XML Cleanup', short: 'XML', color: 'cyan', group: 'Utility' },
];

const agentMeta: Record<PromptRole, { label: string; short: string; color: string; group: 'HTML' | 'Image' | 'Utility' }> = Object.fromEntries(
  promptRoleOptions.map((role) => [role.value, role]),
) as unknown as Record<PromptRole, { label: string; short: string; color: string; group: 'HTML' | 'Image' | 'Utility' }>;

const metaForRole = (role: string) => agentMeta[role as PromptRole] || {
  label: role,
  short: role.replaceAll('_', ' '),
  color: 'default',
  group: 'Utility' as const,
};

const closedVariablePicker: VariablePickerState = {
  open: false,
  top: 0,
  left: 0,
  rangeStart: 0,
  rangeEnd: 0,
};

const caretCoordinates = (textarea: HTMLTextAreaElement, position: number) => {
  const mirror = document.createElement('div');
  const style = window.getComputedStyle(textarea);
  const properties = [
    'box-sizing',
    'width',
    'font-family',
    'font-size',
    'font-weight',
    'font-style',
    'letter-spacing',
    'line-height',
    'padding-top',
    'padding-right',
    'padding-bottom',
    'padding-left',
    'border-top-width',
    'border-right-width',
    'border-bottom-width',
    'border-left-width',
    'word-break',
    'overflow-wrap',
  ];
  mirror.style.position = 'absolute';
  mirror.style.visibility = 'hidden';
  mirror.style.top = '0';
  mirror.style.left = '-9999px';
  mirror.style.height = 'auto';
  mirror.style.minHeight = '0';
  mirror.style.overflow = 'hidden';
  mirror.style.whiteSpace = 'pre-wrap';
  mirror.style.wordWrap = 'break-word';
  properties.forEach((property) => mirror.style.setProperty(property, style.getPropertyValue(property)));
  mirror.textContent = textarea.value.slice(0, position);
  const marker = document.createElement('span');
  marker.textContent = textarea.value.slice(position, position + 1) || '\u200b';
  mirror.appendChild(marker);
  document.body.appendChild(mirror);
  const top = marker.offsetTop - textarea.scrollTop + marker.offsetHeight + 6;
  const left = marker.offsetLeft - textarea.scrollLeft;
  document.body.removeChild(mirror);
  return { top, left };
};

const renderPaginationItem = (_page: number, type: string, originalElement: React.ReactNode) => {
  if (!React.isValidElement(originalElement)) return originalElement;
  if (type === 'prev') {
    return React.cloneElement(originalElement as React.ReactElement<{ 'aria-label'?: string; title?: string }>, {
      'aria-label': 'Previous prompts page',
      title: 'Previous page',
    });
  }
  if (type === 'next') {
    return React.cloneElement(originalElement as React.ReactElement<{ 'aria-label'?: string; title?: string }>, {
      'aria-label': 'Next prompts page',
      title: 'Next page',
    });
  }
  return originalElement;
};

interface PromptsPageProps {
  embedded?: boolean;
}

const PromptsPage: React.FC<PromptsPageProps> = ({ embedded = false }) => {
  const [prompts, setPrompts] = useState<Prompt[]>([]);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [variables, setVariables] = useState<SystemVariable[]>([]);
  const [status, setStatus] = useState<LifecycleStatus>('active');
  const [folderId, setFolderId] = useState<number | null>(null);
  const [agentFilter, setAgentFilter] = useState<PromptRole | 'all'>('all');
  const [searchText, setSearchText] = useState('');
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [folderModalOpen, setFolderModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [folderSaving, setFolderSaving] = useState(false);
  const [analyzing, setAnalyzing] = useState(false);
  const [assisting, setAssisting] = useState(false);
  const [analysis, setAnalysis] = useState<PromptAnalysis | null>(null);
  const [assistantNotice, setAssistantNotice] = useState<string | null>(null);
  const [assistantChangeReport, setAssistantChangeReport] = useState<AssistantChangeReport | null>(null);
  const [assistantReviewOpen, setAssistantReviewOpen] = useState(false);
  const [confirmedVariables, setConfirmedVariables] = useState<Set<string>>(new Set());
  const [variablePicker, setVariablePicker] = useState<VariablePickerState>(closedVariablePicker);
  const [selectedPromptKeys, setSelectedPromptKeys] = useState<React.Key[]>([]);
  const [inspectedPromptId, setInspectedPromptId] = useState<number | null>(null);
  const [bulkFolderModalOpen, setBulkFolderModalOpen] = useState(false);
  const [bulkFolderIds, setBulkFolderIds] = useState<number[]>([]);
  const [form] = Form.useForm();
  const [folderForm] = Form.useForm();
  const editorWrapperRef = useRef<HTMLDivElement | null>(null);
  const editorTextAreaRef = useRef<HTMLTextAreaElement | null>(null);
  const modalAgent = Form.useWatch('agent_type', form) as PromptRole | undefined;
  const modalStatus = Form.useWatch('status', form) as string | undefined;

  const fetchPrompts = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.prompts.list({
        agent_type: agentFilter === 'all' ? undefined : agentFilter,
        status,
        folder_id: folderId,
      });
      setPrompts(data);
      setSelectedPromptKeys([]);
    } catch (err: unknown) {
      message.error(`Failed to load prompts: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [agentFilter, folderId, status]);

  const fetchFolders = useCallback(async () => {
    try {
      setFolders(await api.folders.list('prompt'));
    } catch (err: unknown) {
      message.error(`Failed to load folders: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  const fetchVariables = useCallback(async () => {
    try {
      setVariables(await api.systemVariables.list());
    } catch (err: unknown) {
      message.error(`Failed to load variables: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchPrompts();
      void fetchFolders();
      void fetchVariables();
    });
  }, [fetchFolders, fetchPrompts, fetchVariables]);

  const groupedCounts = useMemo(() => ({
    designer: prompts.filter((prompt) => prompt.agent_type === 'designer').length,
    html_agent: prompts.filter((prompt) => prompt.agent_type === 'html_agent').length,
    image: prompts.filter((prompt) => metaForRole(prompt.agent_type).group === 'Image').length,
  }), [prompts]);

  const displayedPrompts = useMemo(() => {
    const needle = searchText.trim().toLowerCase();
    if (!needle) return prompts;
    return prompts.filter((prompt) => (
      prompt.name.toLowerCase().includes(needle) ||
      prompt.version.toLowerCase().includes(needle) ||
      (prompt.description || '').toLowerCase().includes(needle)
    ));
  }, [prompts, searchText]);

  const inspectedPrompt = displayedPrompts.find((prompt) => prompt.id === inspectedPromptId) || displayedPrompts[0];
  const inspectedPromptMeta = inspectedPrompt ? metaForRole(inspectedPrompt.agent_type) : null;
  const inspectedVariables = inspectedPrompt
    ? variables.filter((variable) => variable.agent_type === inspectedPrompt.agent_type && variable.status === 'active')
    : [];

  const activeVariableOptions = variables
    .filter((variable) => variable.status === 'active' && variable.agent_type === (modalAgent || 'designer'))
    .map((variable) => ({ label: variable.name, value: variable.name }));

  const openAdd = (agentType: PromptRole = 'designer') => {
    setEditingId(null);
    setAnalysis(null);
    setAssistantNotice(null);
    setAssistantChangeReport(null);
    setAssistantReviewOpen(false);
    setConfirmedVariables(new Set());
    setVariablePicker(closedVariablePicker);
    form.resetFields();
    form.setFieldsValue({ agent_type: agentType, status: 'active', folder_ids: folderId ? [folderId] : [] });
    setModalOpen(true);
  };

  const openEdit = (record: Prompt) => {
    setEditingId(record.id);
    setAnalysis(null);
    setAssistantNotice(null);
    setAssistantChangeReport(null);
    setAssistantReviewOpen(false);
    setConfirmedVariables(new Set());
    setVariablePicker(closedVariablePicker);
    form.setFieldsValue(record);
    setModalOpen(true);
  };

  const openCopy = (record: Prompt) => {
    setEditingId(null);
    setAnalysis(null);
    setAssistantNotice(null);
    setAssistantChangeReport(null);
    setAssistantReviewOpen(false);
    setConfirmedVariables(new Set());
    setVariablePicker(closedVariablePicker);
    form.setFieldsValue({
      agent_type: record.agent_type,
      version: '',
      name: `${record.name} (copy)`,
      status: 'active',
      description: record.description,
      content: record.content,
      folder_ids: record.folder_ids || [],
    });
    setModalOpen(true);
    message.info('Copied content into a new editable version. Analyze variables before saving.');
  };

  const mappingIsAccepted = (mapping: VariableMapping) =>
    mapping.status === 'ready' || (mapping.status === 'needs_confirmation' && confirmedVariables.has(mapping.variable));

  const variableBlocked = analysis
    ? (
    (analysis.disabled_variables?.length || 0) > 0 ||
    analysis.mappings.some((mapping) => mapping.status === 'missing' || mapping.status === 'disabled' || !mappingIsAccepted(mapping))
    )
    : true;
  const publishTarget = (modalStatus || 'active') === 'active';
  const saveBlocked = publishTarget && (!analysis || variableBlocked || analysis.can_publish === false);
  const activeChangeReport = assistantChangeReport || analysis?.change_report || null;

  const resetAnalysis = () => {
    setAnalysis(null);
    setAssistantNotice(null);
    setAssistantChangeReport(null);
    setAssistantReviewOpen(false);
    setConfirmedVariables(new Set());
  };

  const handleAnalyze = async () => {
    try {
      const values = await form.validateFields(['agent_type', 'content']);
      setAnalyzing(true);
      setConfirmedVariables(new Set());
      const result = await api.prompts.analyze({
        agent_type: values.agent_type,
        content: values.content,
        baseline_prompt_id: editingId || undefined,
      });
      setAnalysis(result as PromptAnalysis);
      setAssistantChangeReport(null);
      setAssistantReviewOpen(false);
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setAnalyzing(false);
    }
  };

  const analyzePromptRecord = async (prompt: Prompt) => {
    try {
      setAnalyzing(true);
      const result = await api.prompts.analyze({
        agent_type: prompt.agent_type,
        content: prompt.content,
        baseline_prompt_id: prompt.id,
      });
      setAnalysis(result as PromptAnalysis);
      message.success('Prompt variables analyzed');
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setAnalyzing(false);
    }
  };

  const toggleConfirmedVariable = (variable: string, checked: boolean) => {
    setConfirmedVariables((current) => {
      const next = new Set(current);
      if (checked) next.add(variable);
      else next.delete(variable);
      return next;
    });
  };

  const openVariablePickerAtCursor = (textarea: HTMLTextAreaElement, start: number, end: number) => {
    const wrapperRect = editorWrapperRef.current?.getBoundingClientRect();
    const textareaRect = textarea.getBoundingClientRect();
    const caret = caretCoordinates(textarea, end);
    setVariablePicker({
      open: true,
      top: textareaRect.top - (wrapperRect?.top || 0) + caret.top,
      left: Math.min(
        Math.max(textareaRect.left - (wrapperRect?.left || 0) + caret.left, 0),
        Math.max((wrapperRect?.width || textareaRect.width) - 260, 0),
      ),
      rangeStart: start,
      rangeEnd: end,
    });
  };

  const handleEditorChange = (event: React.ChangeEvent<HTMLTextAreaElement>) => {
    editorTextAreaRef.current = event.currentTarget;
    const cursor = event.currentTarget.selectionStart ?? event.currentTarget.value.length;
    if (event.currentTarget.value.slice(Math.max(0, cursor - 2), cursor) === '{{') {
      openVariablePickerAtCursor(event.currentTarget, cursor - 2, cursor);
    } else if (variablePicker.open) {
      setVariablePicker(closedVariablePicker);
    }
  };

  const focusEditorAt = (position: number) => {
    window.setTimeout(() => {
      const textarea = editorTextAreaRef.current;
      if (!textarea) return;
      textarea.focus();
      textarea.setSelectionRange(position, position);
    }, 0);
  };

  const insertVariable = (variable: string) => {
    const content = form.getFieldValue('content') || '';
    const token = `{{${variable}}}`;
    const textarea = editorTextAreaRef.current;
    const fallbackCursor = textarea?.selectionStart ?? content.length;
    const start = variablePicker.open ? variablePicker.rangeStart : fallbackCursor;
    const end = variablePicker.open ? variablePicker.rangeEnd : fallbackCursor;
    const nextContent = `${content.slice(0, start)}${token}${content.slice(end)}`;
    form.setFieldValue('content', nextContent);
    setVariablePicker(closedVariablePicker);
    resetAnalysis();
    focusEditorAt(start + token.length);
  };

  const handleAssistantInsert = async () => {
    try {
      const values = await form.validateFields(['agent_type', 'content']);
      setAssisting(true);
      setConfirmedVariables(new Set());
      const result = await api.prompts.assistVariables({
        agent_type: values.agent_type,
        content: values.content,
        prefer_llm: true,
      });
      form.setFieldValue('content', result.content);
      setAnalysis(result.analysis as PromptAnalysis);
      setAssistantChangeReport(result.change_report);
      setAssistantReviewOpen(Boolean(result.requires_review));
      setVariablePicker(closedVariablePicker);
      const modeLabel = result.mode === 'llm'
        ? 'Prompt Adding Assistant inserted variables with the configured LLM.'
        : result.mode === 'already_ready'
          ? 'Prompt already contains all required variables.'
          : 'LLM assistant was unavailable or inconclusive, so a safe review block was inserted.';
      setAssistantNotice(result.assistant_error ? `${modeLabel} (${result.assistant_error})` : modeLabel);
      message.success(result.inserted_variables.length ? 'Variables inserted for review' : 'Prompt variables already ready');
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setAssisting(false);
    }
  };

  const assistantAlertType = assistantChangeReport?.risk_level === 'medium' ? 'warning' : 'info';

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      if (saveBlocked) {
        message.error('Analyze the prompt and fix publish blockers, or save it as Deprecated draft');
        return;
      }
      setSaving(true);
      const folder_ids = values.folder_ids || [];
      if (editingId) {
        await api.prompts.update(editingId, values);
        await api.prompts.assignFolders(editingId, folder_ids);
        message.success('Prompt updated');
      } else {
        const created = await api.prompts.create(values);
        await api.prompts.assignFolders(created.id, folder_ids);
        message.success('Prompt created');
      }
      setModalOpen(false);
      void fetchPrompts();
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleArchive = async (id: number) => {
    try {
      await api.prompts.delete(id);
      message.success('Prompt archived');
      void fetchPrompts();
    } catch (err: unknown) {
      message.error(`Archive failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleRestore = async (id: number) => {
    try {
      await api.prompts.restore(id);
      message.success('Prompt restored');
      void fetchPrompts();
    } catch (err: unknown) {
      message.error(`Restore failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleSetDefault = async (id: number) => {
    try {
      await api.prompts.setDefault(id);
      message.success('Default prompt updated');
      void fetchPrompts();
    } catch (err: unknown) {
      message.error(`Default update failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const selectedPromptIds = selectedPromptKeys.map(Number);

  const runPromptBulkAction = async (action: 'archive' | 'restore') => {
    if (!selectedPromptIds.length) return;
    try {
      const result = await api.bulkActions.apply({ entity_type: 'prompt', action, ids: selectedPromptIds });
      const failures = result.results.filter((item) => item.status === 'error');
      if (failures.length) message.warning(`${failures.length} prompt(s) could not be updated`);
      else message.success(`${selectedPromptIds.length} prompt(s) updated`);
      void fetchPrompts();
    } catch (err: unknown) {
      message.error(`Bulk action failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleBulkMove = async () => {
    if (!selectedPromptIds.length) return;
    try {
      const result = await api.bulkActions.apply({
        entity_type: 'prompt',
        action: 'move_to_folder',
        ids: selectedPromptIds,
        folder_ids: bulkFolderIds,
      });
      const failures = result.results.filter((item) => item.status === 'error');
      if (failures.length) message.warning(`${failures.length} prompt(s) could not be moved`);
      else message.success(`${selectedPromptIds.length} prompt(s) moved`);
      setBulkFolderModalOpen(false);
      setBulkFolderIds([]);
      void fetchPrompts();
    } catch (err: unknown) {
      message.error(`Move failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const saveFolder = async () => {
    try {
      const values = await folderForm.validateFields();
      setFolderSaving(true);
      await api.folders.create({ scope: 'prompt', name: values.name, parent_id: values.parent_id || null });
      message.success('Prompt folder created');
      setFolderModalOpen(false);
      folderForm.resetFields();
      void fetchFolders();
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setFolderSaving(false);
    }
  };

  const columns = [
    {
      title: 'Role',
      dataIndex: 'agent_type',
      key: 'agent_type',
      width: 110,
      render: (agentType: string) => {
        const meta = metaForRole(agentType);
        return <Tag color={meta.color}>{meta.short}</Tag>;
      },
    },
    {
      title: 'Version',
      dataIndex: 'version',
      key: 'version',
      width: 105,
      sorter: (a: Prompt, b: Prompt) => a.version.localeCompare(b.version),
      ellipsis: true,
      render: (version: string, record: Prompt) => <Tag color={metaForRole(record.agent_type).color}>{version}</Tag>,
    },
    {
      title: 'Name',
      dataIndex: 'name',
      key: 'name',
      width: 220,
      ellipsis: true,
      sorter: (a: Prompt, b: Prompt) => a.name.localeCompare(b.name),
      render: (name: string, record: Prompt) => (
        <Space size="small" wrap>
          <Tooltip title={name}><span className="prompt-name-cell">{name}</span></Tooltip>
          {record.is_default && <Tag color="gold">Default</Tag>}
        </Space>
      ),
    },
    {
      title: 'Lifecycle',
      dataIndex: 'lifecycle_status',
      key: 'lifecycle_status',
      width: 95,
      render: (value: string) => <Tag>{value || 'active'}</Tag>,
    },
    {
      title: 'Variables',
      key: 'variables',
      width: 95,
      render: (_: unknown, record: Prompt) => {
        const roleVariables = variables.filter((variable) => variable.agent_type === record.agent_type && variable.status === 'active');
        const explicit = roleVariables.filter((variable) => record.content.includes(`{{${variable.name}}}`)).length;
        const ready = roleVariables.length > 0 && explicit === roleVariables.length;
        return <Tag color={ready ? 'success' : 'warning'}>{ready ? 'ready' : `${explicit}/${roleVariables.length}`}</Tag>;
      },
    },
    {
      title: 'Folders',
      dataIndex: 'folder_ids',
      key: 'folder_ids',
      width: 120,
      render: (folderIds: number[] = []) => (
        <Space size={4} wrap>
          {folderIds.length ? folderIds.map((id) => <Tag key={id}>{folders.find((folder) => folder.id === id)?.name || id}</Tag>) : '-'}
        </Space>
      ),
    },
    {
      title: 'Description',
      dataIndex: 'description',
      key: 'description',
      width: 180,
      responsive: ['xxl' as const],
      ellipsis: true,
      render: (desc: string) => desc ? <Tooltip title={desc}><span>{desc}</span></Tooltip> : <span style={{ color: '#999' }}>-</span>,
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 150,
      render: (_: unknown, record: Prompt) => (
        <Space size="small">
          {status === 'active' ? (
            <>
              {!record.is_default && (
                <Tooltip title="Set as default">
                  <Button aria-label="Set prompt as default" icon={<StarOutlined />} size="small" onClick={() => handleSetDefault(record.id)} />
                </Tooltip>
              )}
              <Tooltip title="Edit prompt">
                <Button aria-label="Edit prompt" icon={<EditOutlined />} size="small" onClick={() => openEdit(record)} />
              </Tooltip>
              <Tooltip title="Copy prompt into a new version">
                <Button aria-label="Duplicate prompt" icon={<CopyOutlined />} size="small" onClick={() => openCopy(record)} />
              </Tooltip>
              <Popconfirm title="Archive this prompt?" description="Archived prompts remain available to historical runs." onConfirm={() => handleArchive(record.id)}>
                <Button aria-label="Archive prompt" icon={<DeleteOutlined />} size="small" danger />
              </Popconfirm>
            </>
          ) : (
            <Button size="small" onClick={() => handleRestore(record.id)}>Restore</Button>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div className={`work-surface prompt-workspace-surface ${embedded ? 'embedded' : ''}`}>
      <div className="page-toolbar">
        <div>
          <h2>Prompt Management</h2>
          <div className="prompt-role-summary">
            <span className="prompt-role-card designer"><EditOutlined /> <b>Designer</b> <strong>{groupedCounts.designer}</strong></span>
            <span className="prompt-role-card html"><CodeOutlined /> <b>HTML Agent</b> <strong>{groupedCounts.html_agent}</strong></span>
            <span className="prompt-role-card image"><CodeOutlined /> <b>Image</b> <strong>{groupedCounts.image}</strong></span>
          </div>
        </div>
        <Space wrap>
          <Button onClick={() => setFolderModalOpen(true)} icon={<FolderAddOutlined />}>New Folder</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openAdd(agentFilter === 'all' ? 'designer' : agentFilter)}>
            Add Prompt
          </Button>
        </Space>
      </div>

      <div className="prompt-filter-shell" aria-label="Prompt filters">
        <div className="prompt-filter-row">
          <span>Lifecycle</span>
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            value={status}
            onChange={(event) => setStatus(event.target.value)}
            options={[
              { label: 'Active', value: 'active' },
              { label: 'Archived', value: 'archived' },
            ]}
          />
        </div>
        <div className="prompt-filter-row">
          <span>Role family</span>
          <Select
            aria-label="Prompt role filter"
            showSearch
            optionFilterProp="label"
            value={agentFilter}
            onChange={setAgentFilter}
            options={[
              { label: 'All roles', value: 'all' },
              ...promptRoleOptions.map((role) => ({ label: `${role.group} · ${role.label}`, value: role.value })),
            ]}
          />
        </div>
        <div className="prompt-filter-row">
          <span>Location</span>
          <Select
            aria-label="Prompt folder filter"
            allowClear
            placeholder="Filter by prompt folder"
            value={folderId ?? undefined}
            options={folders.map((folder) => ({ label: folder.name, value: folder.id }))}
            onChange={(value) => setFolderId(value ?? null)}
          />
        </div>
        <div className="prompt-filter-row wide">
          <span>Search</span>
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="Search prompt name or version"
            value={searchText}
            onChange={(event) => setSearchText(event.target.value)}
          />
        </div>
      </div>

      {selectedPromptIds.length > 0 && (
        <div className="bulk-action-bar">
          <span>{selectedPromptIds.length} selected</span>
          <Button size="small" onClick={() => setBulkFolderModalOpen(true)}>Move to Folder</Button>
          {status === 'active' ? (
            <Popconfirm title="Archive selected prompts?" onConfirm={() => runPromptBulkAction('archive')}>
              <Button size="small" danger>Archive</Button>
            </Popconfirm>
          ) : (
            <Button size="small" onClick={() => runPromptBulkAction('restore')}>Restore</Button>
          )}
          <Button size="small" onClick={() => setSelectedPromptKeys([])}>Clear</Button>
        </div>
      )}

      <Table
        className="responsive-table"
        dataSource={displayedPrompts}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={{ pageSize: 20, itemRender: renderPaginationItem }}
        rowSelection={{
          selectedRowKeys: selectedPromptKeys,
          onChange: setSelectedPromptKeys,
        }}
        onRow={(record) => ({
          onClick: () => setInspectedPromptId(record.id),
        })}
        rowClassName={(record) => (record.id === inspectedPrompt?.id ? 'prompt-row-inspected' : '')}
        tableLayout="fixed"
        scroll={{ x: 900 }}
      />

      {inspectedPrompt && inspectedPromptMeta && (
        <section className="prompt-inspector-panel" aria-label="Prompt inspector">
          <div className="prompt-inspector-head">
            <div>
              <h3>Prompt Inspector</h3>
              <span>{inspectedPrompt.name}</span>
            </div>
            <Space wrap>
              <Tag color={inspectedPromptMeta.color}>{inspectedPromptMeta.label}</Tag>
              {inspectedPrompt.is_default && <Tag color="gold">Default</Tag>}
              <Button icon={<EditOutlined />} onClick={() => openEdit(inspectedPrompt)}>Edit Prompt</Button>
              <Button loading={analyzing} onClick={() => analyzePromptRecord(inspectedPrompt)}>Analyze Variables</Button>
            </Space>
          </div>
          <div className="prompt-inspector-grid">
            <div className="prompt-inspector-meta">
              <div><span>Version</span><strong>{inspectedPrompt.version}</strong></div>
              <div><span>Lifecycle</span><strong>{inspectedPrompt.lifecycle_status || 'active'}</strong></div>
              <div><span>Variables</span><strong>{inspectedVariables.length}</strong></div>
            </div>
            <div className="prompt-variable-strip">
              {inspectedVariables.length ? inspectedVariables.map((variable) => {
                const present = inspectedPrompt.content.includes(`{{${variable.name}}}`);
                return <Tag key={variable.id} color={present ? 'success' : 'warning'}>{variable.name}</Tag>;
              }) : <Tag>No variables registered</Tag>}
            </div>
          </div>
          <pre className="prompt-content-preview">{inspectedPrompt.content}</pre>
        </section>
      )}

      <Modal
        title={editingId ? 'Edit Prompt' : `Add ${metaForRole(modalAgent || 'designer').short} Prompt`}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleSave}
        okText={publishTarget ? (editingId ? 'Save Prompt' : 'Create Prompt') : 'Save Draft'}
        cancelText="Cancel"
        okButtonProps={{ disabled: saveBlocked }}
        confirmLoading={saving}
        width={860}
        className="prompt-edit-modal"
        wrapClassName="prompt-edit-modal-wrap"
        style={{ top: 16 }}
        styles={{ body: { maxHeight: 'calc(100vh - 180px)', overflowY: 'auto' } }}
        forceRender
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          onValuesChange={(changed) => {
            if ('content' in changed || 'agent_type' in changed) resetAnalysis();
          }}
        >
          <Space wrap style={{ width: '100%' }}>
            <Form.Item name="agent_type" label="Agent Type" rules={[{ required: true, message: 'Select agent type' }]}>
              <Select
                aria-label="Prompt agent type"
                style={{ width: 190 }}
                options={promptRoleOptions.map((role) => ({ label: role.label, value: role.value }))}
                disabled={!!editingId}
              />
            </Form.Item>
            <Form.Item name="version" label="Version" rules={[{ required: true, message: 'Enter version' }]}>
              <Input style={{ width: 150 }} placeholder="v5.4" disabled={!!editingId} />
            </Form.Item>
            <Form.Item name="status" label="Status">
              <Select style={{ width: 150 }} options={[{ label: 'Active', value: 'active' }, { label: 'Deprecated', value: 'deprecated' }]} />
            </Form.Item>
          </Space>
          <Form.Item name="name" label="Name" rules={[{ required: true, message: 'Enter display name' }]}>
            <Input />
          </Form.Item>
          <Form.Item name="folder_ids" label="Folders">
            <Select mode="multiple" options={folders.map((folder) => ({ label: folder.name, value: folder.id }))} />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <TextArea rows={2} />
          </Form.Item>
          <Form.Item label="Prompt Content" required>
            <div ref={editorWrapperRef} className="prompt-editor-wrap">
              <Form.Item name="content" noStyle rules={[{ required: true, message: 'Enter prompt template' }]}>
                <TextArea
                  rows={12}
                  className="prompt-editor"
                  aria-label="Prompt Content"
                  placeholder="Paste the full prompt template here. Typing {{ opens variables at the cursor."
                  onFocus={(event) => {
                    editorTextAreaRef.current = event.currentTarget;
                  }}
                  onClick={(event) => {
                    editorTextAreaRef.current = event.currentTarget;
                  }}
                  onChange={handleEditorChange}
                  onKeyDown={(event) => {
                    if (event.key === 'Escape' && variablePicker.open) {
                      event.preventDefault();
                      setVariablePicker(closedVariablePicker);
                    }
                    if (event.key === 'Enter' && variablePicker.open && activeVariableOptions[0]) {
                      event.preventDefault();
                      insertVariable(activeVariableOptions[0].value);
                    }
                  }}
                />
              </Form.Item>
              {variablePicker.open && (
                <div
                  className="variable-caret-menu"
                  style={{ top: variablePicker.top, left: variablePicker.left }}
                  role="listbox"
                  aria-label="Prompt variables"
                >
                  {activeVariableOptions.length ? activeVariableOptions.map((option) => (
                    <button
                      type="button"
                      key={option.value}
                      className="variable-caret-option"
                      onMouseDown={(event) => {
                        event.preventDefault();
                        insertVariable(option.value);
                      }}
                    >
                      <span className="variable-plus">+</span>
                      <span>{option.label}</span>
                    </button>
                  )) : (
                    <div className="variable-caret-empty">No active variables</div>
                  )}
                </div>
              )}
            </div>
          </Form.Item>
          <Space align="center" wrap style={{ marginBottom: 12 }}>
            <Button loading={assisting} type="primary" onClick={handleAssistantInsert}>
              Auto insert variables
            </Button>
            <Button icon={<SearchOutlined />} loading={analyzing} onClick={handleAnalyze}>
              Analyze variables
            </Button>
            <Button
              onClick={() => {
                const textarea = editorTextAreaRef.current;
                const cursor = textarea?.selectionStart ?? (form.getFieldValue('content') || '').length;
                if (textarea) openVariablePickerAtCursor(textarea, cursor, cursor);
              }}
            >
              Insert variable
            </Button>
            {analysis && (
              <span style={{ color: '#666' }}>
                {analysis.present_variables.length} explicit variable(s), {analysis.required_variables.length} active required
              </span>
            )}
          </Space>
          {assistantNotice && (
            <Alert
              type={assistantNotice.includes('safe review block') ? 'warning' : assistantAlertType}
              showIcon
              style={{ marginBottom: 12 }}
              message={assistantNotice}
              description={assistantChangeReport ? (
                <span>
                  {assistantChangeReport.summary} Similarity {Math.round(assistantChangeReport.similarity * 100)}%.
                </span>
              ) : undefined}
              action={assistantChangeReport ? (
                <Button size="small" onClick={() => setAssistantReviewOpen(true)}>
                  Review changes
                </Button>
              ) : undefined}
            />
          )}
          {analysis && (
            <div>
              <Alert
                type={saveBlocked ? 'warning' : publishTarget ? 'success' : 'info'}
                showIcon
                message={saveBlocked ? 'Publish checks need review' : publishTarget ? 'Prompt can be published' : 'Draft can be saved'}
                description={saveBlocked
                  ? 'Active prompts require variable readiness, valid placeholders, preserved critical instructions, and acceptable diff retention.'
                  : publishTarget
                    ? 'Every required mapping and integrity check is ready for an active prompt.'
                    : 'Deprecated drafts can be saved even when checks fail; run Analyze again before publishing.'}
                action={analysis.change_report ? (
                  <Button size="small" onClick={() => setAssistantReviewOpen(true)}>
                    Review diff
                  </Button>
                ) : undefined}
              />
              <Divider style={{ margin: '16px 0 12px' }} />
              <Space direction="vertical" size="small" style={{ width: '100%' }}>
                {analysis.mappings.map((mapping) => (
                  <div key={mapping.variable} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'center' }}>
                    <Space wrap>
                      <strong>{mapping.variable}</strong>
                      <Tag color={mapping.status === 'ready' ? 'success' : mapping.status === 'missing' || mapping.status === 'disabled' ? 'error' : 'warning'}>
                        {mapping.status}
                      </Tag>
                      {mapping.target && <span style={{ color: '#666' }}>maps to {mapping.target}</span>}
                    </Space>
                    {mapping.status === 'needs_confirmation' && (
                      <Checkbox
                        checked={confirmedVariables.has(mapping.variable)}
                        onChange={(event) => toggleConfirmedVariable(mapping.variable, event.target.checked)}
                      >
                        Confirm
                      </Checkbox>
                    )}
                  </div>
                ))}
              </Space>
              {analysis.integrity_checks && analysis.integrity_checks.length > 0 && (
                <>
                  <Divider style={{ margin: '16px 0 12px' }} />
                  <Space direction="vertical" size="small" style={{ width: '100%' }}>
                    {analysis.integrity_checks.map((check) => (
                      <div key={check.key} style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'flex-start' }}>
                        <div>
                          <strong>{check.label}</strong>
                          <div style={{ color: '#666' }}>{check.message}</div>
                        </div>
                        <Tag color={check.status === 'passed' ? 'success' : check.status === 'failed' ? 'error' : 'default'}>
                          {check.status}
                        </Tag>
                      </div>
                    ))}
                  </Space>
                </>
              )}
              <Divider style={{ margin: '16px 0 12px' }} />
              <div style={{ color: '#666' }}>
                Preview: {analysis.required_variables.map((variable) => (
                  <Tag key={variable}>{`{{${variable}}}`}</Tag>
                ))}
              </div>
            </div>
          )}
        </Form>
      </Modal>

      <Modal
        title="Prompt Assistant Change Review"
        open={assistantReviewOpen}
        onCancel={() => setAssistantReviewOpen(false)}
        footer={[
          <Button key="close" type="primary" onClick={() => setAssistantReviewOpen(false)}>
            Continue reviewing
          </Button>,
        ]}
        width={920}
        destroyOnHidden
      >
        {activeChangeReport && (
          <div className="assistant-diff-review">
            <div className="assistant-diff-summary">
              <Tag color={activeChangeReport.risk_level === 'low' ? 'success' : activeChangeReport.risk_level === 'medium' ? 'warning' : 'error'}>
                {activeChangeReport.risk_level} risk
              </Tag>
              <span>{activeChangeReport.summary}</span>
              <span>{Math.round(activeChangeReport.similarity * 100)}% original similarity</span>
            </div>
            <div className="assistant-diff-vars">
              {activeChangeReport.inserted_variables.map((variable) => (
                <Tag key={variable}>{`{{${variable}}}`}</Tag>
              ))}
            </div>
            <div className="assistant-diff-grid">
              <div className="assistant-diff-head">Before</div>
              <div className="assistant-diff-head">After</div>
              {activeChangeReport.changed_hunks.length ? activeChangeReport.changed_hunks.map((hunk, index) => (
                <React.Fragment key={`${hunk.type}-${index}`}>
                  <pre className="assistant-diff-block removed">
                    {hunk.original.length ? hunk.original.join('\n') : 'No original text removed'}
                  </pre>
                  <pre className="assistant-diff-block added">
                    {hunk.updated.length ? hunk.updated.join('\n') : 'No new text added'}
                  </pre>
                </React.Fragment>
              )) : (
                <>
                  <pre className="assistant-diff-block">No changes</pre>
                  <pre className="assistant-diff-block">No changes</pre>
                </>
              )}
            </div>
            {activeChangeReport.removed_line_count > 0 && (
              <Alert
                type="warning"
                showIcon
                message="Original lines changed or removed"
                description="Review the before/after blocks carefully before saving this prompt version."
              />
            )}
          </div>
        )}
      </Modal>

      <Modal
        title="New Prompt Folder"
        open={folderModalOpen}
        onOk={saveFolder}
        onCancel={() => setFolderModalOpen(false)}
        okText="Create Folder"
        cancelText="Cancel"
        confirmLoading={folderSaving}
        destroyOnHidden
      >
        <Form form={folderForm} layout="vertical">
          <Form.Item name="name" label="Folder name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="parent_id" label="Parent folder">
            <Select allowClear options={folders.map((folder) => ({ label: folder.name, value: folder.id }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Move Selected Prompts"
        open={bulkFolderModalOpen}
        onOk={handleBulkMove}
        onCancel={() => setBulkFolderModalOpen(false)}
        okText="Move Prompts"
        cancelText="Cancel"
        destroyOnHidden
      >
        <Select
          mode="multiple"
          style={{ width: '100%' }}
          placeholder="Select folders"
          value={bulkFolderIds}
          onChange={setBulkFolderIds}
          options={folders.map((folder) => ({ label: folder.name, value: folder.id }))}
        />
      </Modal>
    </div>
  );
};

export default PromptsPage;
