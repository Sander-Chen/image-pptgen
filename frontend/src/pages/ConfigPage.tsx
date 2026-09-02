import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Form,
  Image,
  Input,
  InputNumber,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Tooltip,
  message,
} from 'antd';
import {
  ApiOutlined,
  CheckCircleOutlined,
  DeleteOutlined,
  EditOutlined,
  PlusOutlined,
  StarOutlined,
} from '@ant-design/icons';
import { api } from '../api';
import type { Config, ModelProfile, ModelProfileTestResult, ModelRole } from '../types';
import AutoSplitSettingsPanel from '../components/AutoSplitSettingsPanel';
import SystemSettingsPage from './SystemSettingsPage';

const roleLabels: Record<ModelRole, string> = {
  designer: 'Designer Model',
  html_agent: 'HTML Agent Model',
  auto_spill: 'Auto-Spill Model',
  prompt_assistant: 'Prompt Adding Assistant Model',
  evaluation_visual_qa: 'Evaluation Visual QA Model',
  image_designer: 'Image Designer Model',
  image_generator: 'Image Generator Model',
  shared_extraction: 'Shared Extraction Model',
  xml_cleanup: 'XML Cleanup Model',
};

const roleColors: Record<ModelRole, string> = {
  designer: 'blue',
  html_agent: 'green',
  auto_spill: 'purple',
  prompt_assistant: 'orange',
  evaluation_visual_qa: 'geekblue',
  image_designer: 'gold',
  image_generator: 'volcano',
  shared_extraction: 'cyan',
  xml_cleanup: 'geekblue',
};

const roleOptions = (Object.keys(roleLabels) as ModelRole[]).map((role) => ({
  label: roleLabels[role],
  value: role,
}));

const configTypeOptions = [
  { label: 'HTML', value: 'html' },
  { label: 'Image', value: 'image' },
];

const thinkingOptions = [
  { label: 'None', value: null },
  { label: 'Low', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' },
];

const profileApiTypeOptions = [
  { label: 'OpenAI', value: 'openai' },
  { label: 'Gemini', value: 'gemini' },
  { label: 'Codex Exec', value: 'codex_exec' },
];

const profileOption = (profile: ModelProfile) => ({
  label: `${profile.name} · ${profile.model}`,
  value: profile.id,
});

const isGeminiPaletteProfile = (profile: ModelProfile) => (
  profile.role === 'image_generator' && profile.api_type === 'gemini'
);

interface RouteDraft {
  image_designer_profile_id?: number;
  image_generator_profile_id?: number;
  image_palette_extractor_profile_id?: number;
}

type SystemManagedRecord = { system_managed?: boolean };

const isSystemManaged = <T extends object>(record: T | null | undefined) => (
  (record as (T & SystemManagedRecord) | null | undefined)?.system_managed === true
);

type RouteBindingKey = 'image_designer' | 'image_generator' | 'image_palette_extractor';

interface RouteFlow {
  key: string;
  title: string;
  badge: string;
  state: 'current' | 'roadmap';
  steps: string[];
}

const routeFlows: RouteFlow[] = [
  { key: 'html', title: 'HTML', badge: 'current', state: 'current', steps: ['Designer prompt', 'HTML Agent', 'Clean HTML', 'Captured PNG'] },
  { key: 'image10', title: 'Image 1.0', badge: 'conversation', state: 'current', steps: ['Cover prompt', 'Continuation request', 'Image response'] },
  { key: 'image30', title: 'Image 3.0', badge: 'seed', state: 'current', steps: ['Seed slide', 'Designer XML', 'Image request'] },
  { key: 'image32', title: 'Image 3.2', badge: 'cover ref', state: 'current', steps: ['Cover reference', 'Seed dependency', 'Designer XML', 'Image response'] },
  { key: 'image50', title: 'Image 5.0', badge: 'unified', state: 'current', steps: ['Unified Designer', 'Blueprint XML', 'Image request'] },
  { key: 'image53', title: 'Image 5.3', badge: 'roadmap', state: 'roadmap', steps: ['Versioned generation route', 'Model gate proof', 'Persisted stage artifacts', 'Flow diagram output'] },
];

const generationRouteMermaid = `graph TD
  HTML[HTML Default] --> D[Designer prompt]
  D --> H[HTML Agent]
  H --> Clean[Clean HTML]
  Clean --> PNG[Captured PNG]
  B10[Image 1.0] --> Cover[Cover prompt]
  Cover --> Continue[Continuation request]
  Continue --> Image[Image response]
  B30[Image 3.0] --> Seed[Seed slide]
  Seed --> DesignerXML[Designer XML]
  DesignerXML --> ImageRequest[Image request]
  B50[Image 5.0] --> Unified[Unified Designer]
  Unified --> Blueprint[Blueprint XML]
  Blueprint --> UnifiedImage[Image request]
  B53[Image 5.3 roadmap gate] -. gated .-> Contract[Provider and model contract]`;

const bindingProfileId = (bindings: Record<string, unknown> | undefined, role: RouteBindingKey): number | undefined => {
  const value = bindings?.[role];
  if (typeof value === 'number') return value;
  if (typeof value === 'string' && value.trim()) return Number(value);
  if (value && typeof value === 'object' && 'profile_id' in value) {
    const profileId = (value as { profile_id?: number | string }).profile_id;
    return profileId === undefined ? undefined : Number(profileId);
  }
  return undefined;
};

const ConfigPage: React.FC = () => {
  const [profiles, setProfiles] = useState<ModelProfile[]>([]);
  const [configs, setConfigs] = useState<Config[]>([]);
  const [loading, setLoading] = useState(false);
  const [profileModalOpen, setProfileModalOpen] = useState(false);
  const [configModalOpen, setConfigModalOpen] = useState(false);
  const [editingProfileId, setEditingProfileId] = useState<number | null>(null);
  const [editingConfigId, setEditingConfigId] = useState<number | null>(null);
  const [editingProfile, setEditingProfile] = useState<ModelProfile | null>(null);
  const [editingConfig, setEditingConfig] = useState<Config | null>(null);
  const [saving, setSaving] = useState(false);
  const [profileTesting, setProfileTesting] = useState(false);
  const [profileTestResult, setProfileTestResult] = useState<ModelProfileTestResult | null>(null);
  const [routeSaving, setRouteSaving] = useState(false);
  const [routeConfigId, setRouteConfigId] = useState<number | null>(null);
  const [routeDraft, setRouteDraft] = useState<RouteDraft>({});
  const [routeDirty, setRouteDirty] = useState(false);
  const [activeTab, setActiveTab] = useState('combinations');
  const [profileForm] = Form.useForm();
  const [configForm] = Form.useForm();
  const selectedProfileApiType = Form.useWatch('api_type', profileForm);
  const isCodexProfileDraft = selectedProfileApiType === 'codex_exec';

  const fetchAll = useCallback(async () => {
    setLoading(true);
    try {
      const [profileData, configData] = await Promise.all([
        api.modelProfiles.list({ status: 'active' }),
        api.configs.list(),
      ]);
      setProfiles(profileData);
      setConfigs(configData);
    } catch (err: unknown) {
      message.error(`Failed to load configs: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchAll();
    });
  }, [fetchAll]);

  const profilesByRole = useMemo(() => {
    const grouped: Record<ModelRole, ModelProfile[]> = {
      designer: [],
      html_agent: [],
      auto_spill: [],
      prompt_assistant: [],
      evaluation_visual_qa: [],
      image_designer: [],
      image_generator: [],
      shared_extraction: [],
      xml_cleanup: [],
    };
    profiles.forEach((profile) => {
      grouped[profile.role] ||= [];
      grouped[profile.role].push(profile);
    });
    return grouped;
  }, [profiles]);
  const paletteExtractorProfiles = useMemo(
    () => profilesByRole.image_generator.filter(isGeminiPaletteProfile),
    [profilesByRole],
  );
  const profileSections: Array<{ title: string; roles: ModelRole[] }> = [
    { title: 'HTML', roles: ['designer', 'html_agent', 'auto_spill'] },
    { title: 'Image', roles: ['image_designer', 'image_generator', 'xml_cleanup'] },
    { title: 'Assistants', roles: ['prompt_assistant', 'shared_extraction'] },
  ];

  const profileNameById = useMemo(
    () => new Map(profiles.map((profile) => [profile.id, `${profile.name} · ${profile.model}`])),
    [profiles],
  );
  const imageConfigs = useMemo(
    () => configs.filter((config) => (config.type || 'html') === 'image'),
    [configs],
  );
  const selectedRouteConfig = useMemo(
    () => imageConfigs.find((config) => config.id === routeConfigId) || imageConfigs.find((config) => config.is_default) || imageConfigs[0],
    [imageConfigs, routeConfigId],
  );
  const routeDraftFromConfig = useCallback((config: Config | undefined): RouteDraft => ({
    image_designer_profile_id: config ? bindingProfileId(config.route_model_bindings, 'image_designer') : undefined,
    image_generator_profile_id: config ? bindingProfileId(config.route_model_bindings, 'image_generator') : undefined,
    image_palette_extractor_profile_id: config ? bindingProfileId(config.route_model_bindings, 'image_palette_extractor') : undefined,
  }), []);
  const currentRouteDraft = routeDirty ? routeDraft : routeDraftFromConfig(selectedRouteConfig);

  const openAddProfile = () => {
    setEditingProfileId(null);
    setEditingProfile(null);
    profileForm.resetFields();
    profileForm.setFieldsValue({
      role: 'designer',
      api_type: 'openai',
      temperature: 0.7,
      thinking: null,
      status: 'active',
    });
    setProfileTestResult(null);
    setProfileModalOpen(true);
  };

  const openEditProfile = (record: ModelProfile) => {
    if (isSystemManaged(record)) {
      message.info('Managed by Codex login; validate through audited Native preflight');
      return;
    }
    setEditingProfileId(record.id);
    setEditingProfile(record);
    profileForm.setFieldsValue(record);
    setProfileTestResult(null);
    setProfileModalOpen(true);
  };

  const openAddConfig = () => {
    setEditingConfigId(null);
    setEditingConfig(null);
    configForm.resetFields();
    configForm.setFieldsValue({
      type: 'html',
      timeout_minutes: 30,
      is_default: configs.length === 0,
    });
    setConfigModalOpen(true);
  };

  const openEditConfig = (record: Config) => {
    if (isSystemManaged(record)) {
      message.info('Managed by Codex login; validate through audited Native preflight');
      return;
    }
    setEditingConfigId(record.id);
    setEditingConfig(record);
    configForm.setFieldsValue({
      name: record.name,
      type: record.type || 'html',
      designer_profile_id: record.designer_profile_id,
      html_agent_profile_id: record.html_agent_profile_id,
      image_designer_profile_id: bindingProfileId(record.route_model_bindings, 'image_designer'),
      image_generator_profile_id: bindingProfileId(record.route_model_bindings, 'image_generator'),
      image_palette_extractor_profile_id: bindingProfileId(record.route_model_bindings, 'image_palette_extractor'),
      timeout_minutes: record.timeout_minutes || 30,
      is_default: Boolean(record.is_default),
    });
    setConfigModalOpen(true);
  };

  const saveProfile = async () => {
    if (isSystemManaged(editingProfile)) {
      message.info('Managed by Codex login; validate through audited Native preflight');
      return;
    }
    try {
      const values = await profileForm.validateFields();
      if (!profileTestResult?.ok || !profileTestResult.test_token) {
        message.error('Run Test successfully before saving this model');
        return;
      }
      setSaving(true);
      const payload = { ...values, test_token: profileTestResult.test_token };
      if (editingProfileId) {
        await api.modelProfiles.update(editingProfileId, payload);
        message.success('Role model updated');
      } else {
        await api.modelProfiles.create(payload);
        message.success('Role model created');
      }
      setActiveTab('model-profiles');
      setProfileModalOpen(false);
      await fetchAll();
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const testProfile = async () => {
    if (isSystemManaged(editingProfile)) {
      message.info('Managed by Codex login; validate through audited Native preflight');
      return;
    }
    try {
      const values = await profileForm.validateFields();
      setProfileTesting(true);
      setProfileTestResult(null);
      const result = await api.modelProfiles.test(values);
      setProfileTestResult(result);
      if (result.ok) {
        message.success('Model test passed');
      } else {
        message.error('Model test failed');
      }
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setProfileTesting(false);
    }
  };

  const saveConfig = async () => {
    if (isSystemManaged(editingConfig)) {
      message.info('Managed by Codex login; validate through audited Native preflight');
      return;
    }
    try {
      const values = await configForm.validateFields();
      const { image_designer_profile_id, image_generator_profile_id, image_palette_extractor_profile_id, ...configValues } = values;
      const route_model_bindings: Record<string, { profile_id: number }> = {};
      if (image_designer_profile_id) {
        route_model_bindings.image_designer = { profile_id: image_designer_profile_id };
      }
      if (image_generator_profile_id) {
        route_model_bindings.image_generator = { profile_id: image_generator_profile_id };
      }
      if (image_palette_extractor_profile_id) {
        route_model_bindings.image_palette_extractor = { profile_id: image_palette_extractor_profile_id };
      }
      const payload = {
        ...configValues,
        route_model_bindings,
      };
      setSaving(true);
      if (editingConfigId) {
        await api.configs.update(editingConfigId, payload);
        if (payload.is_default) await api.configs.setDefault(editingConfigId);
        message.success('Combination updated');
      } else {
        await api.configs.create(payload);
        message.success('Combination created');
      }
      setActiveTab('combinations');
      setConfigModalOpen(false);
      await fetchAll();
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  const selectRouteConfig = (configId: number) => {
    const config = imageConfigs.find((item) => item.id === configId);
    setRouteConfigId(configId);
    setRouteDraft(routeDraftFromConfig(config));
    setRouteDirty(false);
  };

  const cancelRouteDraft = () => {
    setRouteDraft(routeDraftFromConfig(selectedRouteConfig));
    setRouteDirty(false);
    message.info('Route map changes discarded');
  };

  const saveRouteDraft = async () => {
    if (!selectedRouteConfig) return;
    if (isSystemManaged(selectedRouteConfig)) {
      message.info('Managed by Codex login; validate through audited Native preflight');
      return;
    }
    const draft = currentRouteDraft;
    const currentBindings = selectedRouteConfig.route_model_bindings && typeof selectedRouteConfig.route_model_bindings === 'object'
      ? { ...(selectedRouteConfig.route_model_bindings as Record<string, unknown>) }
      : {};
    if (draft.image_designer_profile_id) {
      currentBindings.image_designer = { profile_id: draft.image_designer_profile_id };
    } else {
      delete currentBindings.image_designer;
    }
    if (draft.image_generator_profile_id) {
      currentBindings.image_generator = { profile_id: draft.image_generator_profile_id };
    } else {
      delete currentBindings.image_generator;
    }
    if (draft.image_palette_extractor_profile_id) {
      currentBindings.image_palette_extractor = { profile_id: draft.image_palette_extractor_profile_id };
    } else {
      delete currentBindings.image_palette_extractor;
    }
    try {
      setRouteSaving(true);
      await api.configs.update(selectedRouteConfig.id, { route_model_bindings: currentBindings });
      message.success('Route map bindings saved');
      setRouteDirty(false);
      await fetchAll();
    } catch (err: unknown) {
      message.error(`Route map save failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setRouteSaving(false);
    }
  };

  const setDefaultConfig = async (id: number) => {
    try {
      await api.configs.setDefault(id);
      message.success('Default combination updated');
      await fetchAll();
    } catch (err: unknown) {
      message.error(`Failed to set default: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const deleteConfig = async (id: number) => {
    const config = configs.find((item) => item.id === id);
    if (isSystemManaged(config)) {
      message.info('Managed by Codex login; validate through audited Native preflight');
      return;
    }
    try {
      await api.configs.delete(id);
      message.success('Combination deleted');
      await fetchAll();
    } catch (err: unknown) {
      message.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const profileColumns = [
    {
      title: 'Role',
      dataIndex: 'role',
      key: 'role',
      width: 170,
      render: (role: ModelRole) => <Tag color={roleColors[role]}>{roleLabels[role]}</Tag>,
    },
    { title: 'Profile', dataIndex: 'name', key: 'name', width: 220 },
    {
      title: 'Model',
      key: 'model',
      render: (_: unknown, record: ModelProfile) => (
        <div className="model-cell">
          <strong>{record.model}</strong>
          <span>{record.api_type} · {record.thinking || 'no thinking'}</span>
        </div>
      ),
    },
    {
      title: 'Endpoint',
      dataIndex: 'endpoint',
      key: 'endpoint',
      ellipsis: true,
      responsive: ['lg' as const],
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 90,
      render: (_: unknown, record: ModelProfile) => (
        <Tooltip title="Edit role model">
          <Button
            aria-label={`Edit ${record.name}`}
            icon={<EditOutlined />}
            size="small"
            disabled={isSystemManaged(record)}
            onClick={() => openEditProfile(record)}
          />
        </Tooltip>
      ),
    },
  ];

  const configColumns = [
    {
      title: 'Combination',
      key: 'name',
      width: 160,
      render: (_: unknown, record: Config) => (
        <Space size="small" wrap>
          <strong>{record.name}</strong>
          <Tag color={(record.type || 'html') === 'image' ? 'gold' : 'blue'}>{(record.type || 'html').toUpperCase()}</Tag>
          {record.is_default && <Tag icon={<CheckCircleOutlined />} color="success">Default</Tag>}
        </Space>
      ),
    },
    {
      title: 'HTML Route',
      key: 'html_route',
      width: 280,
      render: (_: unknown, record: Config) => (
        <div className="route-binding-cell">
          <span><Tag color={roleColors.designer}>Designer</Tag>{record.designer?.model || '-'}</span>
          <span><Tag color={roleColors.html_agent}>HTML</Tag>{record.html_agent?.model || '-'}</span>
        </div>
      ),
    },
    {
      title: 'Image Route',
      key: 'image_route',
      width: 280,
      render: (_: unknown, record: Config) => {
        const directorId = bindingProfileId(record.route_model_bindings, 'image_designer');
        const imageId = bindingProfileId(record.route_model_bindings, 'image_generator');
        const paletteId = bindingProfileId(record.route_model_bindings, 'image_palette_extractor');
        return (
          <div className="route-binding-cell">
            <span><Tag color={roleColors.image_designer}>Designer</Tag>{directorId ? profileNameById.get(directorId) || directorId : 'Default fallback'}</span>
            <span><Tag color={roleColors.image_generator}>Image</Tag>{imageId ? profileNameById.get(imageId) || imageId : 'Default fallback'}</span>
            <span><Tag color="cyan">Palette</Tag>{paletteId ? profileNameById.get(paletteId) || paletteId : 'Default fallback'}</span>
          </div>
        );
      },
    },
    {
      title: 'Execution',
      key: 'execution',
      width: 120,
      render: (_: unknown, record: Config) => `${record.timeout_minutes || 30}m timeout`,
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 120,
      render: (_: unknown, record: Config) => (
        <Space size="small">
          {!record.is_default && (
            <Tooltip title="Set as default">
              <Button
                aria-label={`Set ${record.name} as default`}
                icon={<StarOutlined />}
                size="small"
                onClick={() => setDefaultConfig(record.id)}
              />
            </Tooltip>
          )}
          <Tooltip title="Edit combination">
            <Button
              aria-label={`Edit ${record.name}`}
              icon={<EditOutlined />}
              size="small"
              disabled={isSystemManaged(record)}
              onClick={() => openEditConfig(record)}
            />
          </Tooltip>
          <Popconfirm title="Delete this combination?" disabled={isSystemManaged(record)} onConfirm={() => deleteConfig(record.id)}>
            <Button aria-label={`Delete ${record.name}`} icon={<DeleteOutlined />} size="small" danger disabled={isSystemManaged(record)} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div className="config-page">
      <div className="page-toolbar">
        <div>
          <h2>Config</h2>
          <p className="toolbar-subtitle">Manage combinations, AutoSplit, model profiles, variables, runtime, system settings, and Generation Routes. Managed by Codex login; validate through audited Native preflight.</p>
        </div>
        <Space className="page-toolbar-actions" wrap>
          <Button icon={<ApiOutlined />} onClick={openAddProfile}>
            Add Role Model
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openAddConfig}>
            Add Combination
          </Button>
        </Space>
      </div>

      <Tabs
        className="config-workbench-tabs"
        activeKey={activeTab}
        onChange={setActiveTab}
        items={[
          {
            key: 'combinations',
            label: 'Combinations',
            children: (
              <>
                <Alert
                  type="info"
                  showIcon
                  className="config-hint"
                  message="Combinations are the production runtime bundles."
                  description="Each combination keeps model bindings, timeout, route metadata, and default selection. Queue-wide concurrency lives in System Settings."
                />
                <section className="config-section">
                  <div className="section-heading">
                    <h3>Combinations</h3>
                    <span>Generation uses these model and route combinations.</span>
                  </div>
                  <Table
                    className="responsive-table"
                    dataSource={configs}
                    columns={configColumns}
                    rowKey="id"
                    loading={loading}
                    pagination={false}
                    tableLayout="fixed"
                    scroll={{ x: 960 }}
                  />
                </section>
              </>
            ),
          },
          {
            key: 'auto-split',
            label: 'AutoSplit',
            children: <AutoSplitSettingsPanel />,
          },
          {
            key: 'model-profiles',
            label: 'Model Profiles',
            children: (
              <section className="config-section">
                <div className="section-heading">
                  <h3>Model Profiles</h3>
                  <span>Grouped by role. Provider keys remain redacted in browser evidence and API payload logs.</span>
                </div>
                <div className="role-profile-sections">
                  {profileSections.map((section) => (
                    <div className="role-profile-section" key={section.title}>
                      <div className="role-profile-section-heading">
                        <strong>{section.title}</strong>
                        <span>{section.roles.reduce((count, role) => count + profilesByRole[role].length, 0)} profiles</span>
                      </div>
                      <div className="role-profile-groups">
                        {section.roles.map((role) => (
                          <div className="role-profile-group" key={role}>
                            <div className="role-profile-heading" aria-label={`${roleLabels[role]} group`}>
                              <Tag color={roleColors[role]}>{roleLabels[role]}</Tag>
                              <span>{profilesByRole[role].length}</span>
                            </div>
                            <Table
                              dataSource={profilesByRole[role]}
                              columns={profileColumns}
                              rowKey="id"
                              loading={loading}
                              pagination={false}
                              size="small"
                              scroll={{ x: 820 }}
                              locale={{ emptyText: 'No active model profiles' }}
                            />
                          </div>
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            ),
          },
          {
            key: 'variables-runtime',
            label: 'Variables & Runtime',
            children: <SystemSettingsPage embedded section="variables" />,
          },
          {
            key: 'routes',
            label: 'Generation Routes',
            children: (
                <section className="config-section route-map-editor">
                  <div className="section-heading">
                    <h3>Generation Routes</h3>
                    <span>Workflow templates shown in product language; stored field remains route_model_bindings.</span>
                  </div>
                  <div className="route-map-controls" aria-label="Route map editor">
                    <div className="filter-field wide">
                      <span>Combination</span>
                      <Select
                        aria-label="Generation Routes combination"
                        value={selectedRouteConfig?.id}
                        onChange={selectRouteConfig}
                        options={imageConfigs.map((config) => ({ label: `${config.name}${config.is_default ? ' · default' : ''}`, value: config.id }))}
                      />
                    </div>
                    <div className="filter-field">
                      <span>Designer</span>
                      <Select
                        aria-label="Generation Routes designer model"
                        allowClear
                        placeholder="Use default fallback"
                        value={currentRouteDraft.image_designer_profile_id}
                        disabled={isSystemManaged(selectedRouteConfig)}
                        onChange={(value) => {
                          setRouteDraft((current) => ({ ...current, image_designer_profile_id: value }));
                          setRouteDirty(true);
                        }}
                        options={profilesByRole.image_designer.map(profileOption)}
                      />
                    </div>
                    <div className="filter-field">
                      <span>Image</span>
                      <Select
                        aria-label="Generation Routes image model"
                        allowClear
                        placeholder="Use default fallback"
                        value={currentRouteDraft.image_generator_profile_id}
                        disabled={isSystemManaged(selectedRouteConfig)}
                        onChange={(value) => {
                          setRouteDraft((current) => ({ ...current, image_generator_profile_id: value }));
                          setRouteDirty(true);
                        }}
                        options={profilesByRole.image_generator.map(profileOption)}
                      />
                    </div>
                    <div className="filter-field">
                      <span>Palette</span>
                      <Select
                        aria-label="Generation Routes palette extractor model"
                        allowClear
                        placeholder="Use default fallback"
                        value={currentRouteDraft.image_palette_extractor_profile_id}
                        disabled={isSystemManaged(selectedRouteConfig)}
                        onChange={(value) => {
                          setRouteDraft((current) => ({ ...current, image_palette_extractor_profile_id: value }));
                          setRouteDirty(true);
                        }}
                        options={paletteExtractorProfiles.map(profileOption)}
                      />
                    </div>
                    <Space wrap>
                      <Button onClick={cancelRouteDraft} disabled={!routeDirty || routeSaving || isSystemManaged(selectedRouteConfig)}>
                        Cancel Changes
                      </Button>
                      <Button type="primary" onClick={saveRouteDraft} loading={routeSaving} disabled={!selectedRouteConfig || !routeDirty || isSystemManaged(selectedRouteConfig)}>
                        Save Generation Routes
                      </Button>
                    </Space>
                  </div>
                  {selectedRouteConfig && (
                    <div className="route-map-binding-summary" aria-live="polite">
                      <span><Tag color="gold">Designer</Tag>{currentRouteDraft.image_designer_profile_id ? profileNameById.get(currentRouteDraft.image_designer_profile_id) : 'Default fallback'}</span>
                      <span><Tag color="volcano">Image</Tag>{currentRouteDraft.image_generator_profile_id ? profileNameById.get(currentRouteDraft.image_generator_profile_id) : 'Default fallback'}</span>
                      <span><Tag color="cyan">Palette</Tag>{currentRouteDraft.image_palette_extractor_profile_id ? profileNameById.get(currentRouteDraft.image_palette_extractor_profile_id) : 'Default fallback'}</span>
                      <span><Tag color={routeDirty ? 'warning' : 'success'}>{routeDirty ? 'unsaved changes' : 'saved state'}</Tag></span>
                    </div>
                  )}
                <Table
                  className="responsive-table route-map-table"
                  pagination={false}
                  rowKey="id"
                  scroll={{ x: 760 }}
                  dataSource={[
                    { id: 'html', route: 'HTML Default', prompts: 'Designer + HTML Agent', models: 'Designer / HTML Agent', evidence: 'HTML, captured PNG, raw response' },
                    { id: 'cover', route: 'Image Cover 3.1', prompts: 'Cover Prompt 3.1', models: 'Image Designer + Image Generator', evidence: 'Cover image prompt, request, response, final PNG' },
                    { id: 'image10', route: 'Image 1.0', prompts: 'Cover 3.1 + Continuation', models: 'Image generator conversation', evidence: 'Conversation history, request, response, final PNG' },
                    { id: 'image30', route: 'Image 3.0', prompts: 'Seed + Non-seed Designer', models: 'Image Designer + Image Generator', evidence: 'Seed dependency, XML blueprint, final PNG' },
                    { id: 'image32', route: 'Image 3.2', prompts: 'Cover ref + Seed + Non-seed', models: 'Image Designer + Image Generator', evidence: 'Cover reference, seed dependency, final PNG' },
                    { id: 'image50', route: 'Image 5.0', prompts: 'Unified Designer', models: 'Image Designer + Image Generator + Palette Extractor', evidence: 'Unified XML blueprint, request, response, final PNG' },
                    { id: 'image53', route: 'Image 5.3 Roadmap Gate', prompts: 'Not executable in this Goal', models: 'Roadmap only', evidence: 'Gated until provider/model contract is approved' },
                  ]}
                  columns={[
                    { title: 'Route / Version', dataIndex: 'route', key: 'route', width: 180 },
                    { title: 'Prompt Roles', dataIndex: 'prompts', key: 'prompts', width: 220 },
                    { title: 'Model Roles', dataIndex: 'models', key: 'models', width: 220 },
                    { title: 'Run Detail Evidence', dataIndex: 'evidence', key: 'evidence' },
                  ]}
                />
                <section className="config-section nested route-flow-section">
                  <div className="section-heading">
                    <h3>Workflow Template Steps</h3>
                    <span>Global route coverage lives here, separate from a single run evidence tray.</span>
                  </div>
                  <Tabs
                    className="route-flow-tabs"
                    items={[
                      {
                        key: 'cards',
                        label: 'Workflow Cards',
                        children: (
                          <div className="route-flow-grid">
                            {routeFlows.map((route) => (
                              <article className={`route-card ${route.state}`} key={route.key}>
                                <header>
                                  <strong>{route.title}</strong>
                                  <Tag color={route.state === 'roadmap' ? 'blue' : 'gold'}>{route.badge}</Tag>
                                </header>
                                <div className="flow-steps">
                                  {route.steps.map((step) => (
                                    <div className="flow-step" key={step}>{step}</div>
                                  ))}
                                </div>
                              </article>
                            ))}
                          </div>
                        ),
                      },
                      {
                        key: 'diagram',
                        label: 'Mermaid Diagram',
                        children: (
                          <div className="route-mermaid-panel" aria-label="Mermaid route flow diagram">
                            <div className="route-mermaid-render" aria-label="Rendered route flow">
                              {routeFlows.map((route) => (
                                <article className={`route-card ${route.state}`} key={route.key}>
                                  <header>
                                    <strong>{route.title}</strong>
                                    <Tag color={route.state === 'roadmap' ? 'blue' : 'gold'}>{route.badge}</Tag>
                                  </header>
                                  <div className="route-mermaid-line">
                                    {route.steps.map((step, index) => (
                                      <React.Fragment key={step}>
                                        {index > 0 && <span className="route-mermaid-arrow" aria-hidden="true">→</span>}
                                        <span>{step}</span>
                                      </React.Fragment>
                                    ))}
                                  </div>
                                </article>
                              ))}
                            </div>
                            <pre aria-label="Mermaid source">{generationRouteMermaid}</pre>
                          </div>
                        ),
                      },
                    ]}
                  />
                </section>
              </section>
            ),
          },
          {
            key: 'system-settings',
            label: 'System Settings',
            children: <SystemSettingsPage embedded section="settings" />,
          },
        ]}
      />

      <Modal
        title={editingProfileId ? 'Edit Role Model' : 'Add Role Model'}
        open={profileModalOpen}
        onOk={saveProfile}
        onCancel={() => setProfileModalOpen(false)}
        okText={editingProfileId ? 'Save Role Model' : 'Create Role Model'}
        cancelText="Cancel"
        confirmLoading={saving}
        okButtonProps={{ disabled: !profileTestResult?.ok || profileTesting || isSystemManaged(editingProfile) }}
        width={680}
        destroyOnHidden
        forceRender
      >
        <Form form={profileForm} layout="vertical" onValuesChange={() => setProfileTestResult(null)}>
          <Space className="form-grid" align="start">
            <Form.Item name="role" label="Role" rules={[{ required: true }]}>
              <Select aria-label="Role model role" options={roleOptions} />
            </Form.Item>
            <Form.Item name="name" label="Profile Name" rules={[{ required: true }]}>
              <Input aria-label="Role model profile name" />
            </Form.Item>
          </Space>
          <Space className="form-grid" align="start">
            <Form.Item name="api_type" label="API Type" rules={[{ required: true }]}>
              <Select
                aria-label="Role model API type"
                options={profileApiTypeOptions}
                onChange={(value) => {
                  if (value === 'codex_exec') {
                    profileForm.setFieldsValue({ endpoint: 'codex://exec', api_key: '' });
                  }
                }}
              />
            </Form.Item>
            <Form.Item name="model" label="Model" rules={[{ required: true }]}>
              <Input aria-label="Role model model" />
            </Form.Item>
          </Space>
          <Form.Item name="endpoint" label="Endpoint" rules={[{ required: true }]}>
            <Input aria-label="Role model endpoint" />
          </Form.Item>
          <Form.Item
            name="api_key"
            label="API Key"
            rules={isCodexProfileDraft ? [] : [{ required: true }]}
          >
            <Input.Password aria-label="Role model API key" disabled={isCodexProfileDraft} />
          </Form.Item>
          <Space className="form-grid" align="start">
            <Form.Item name="temperature" label="Temperature">
              <InputNumber min={0} max={2} step={0.1} />
            </Form.Item>
            <Form.Item name="thinking" label="Thinking">
              <Select aria-label="Role model thinking" allowClear placeholder="None" options={thinkingOptions} />
            </Form.Item>
          </Space>
          <div className="model-test-panel">
            <Button icon={<CheckCircleOutlined />} loading={profileTesting} disabled={isSystemManaged(editingProfile)} onClick={testProfile}>
              Test Model
            </Button>
            {profileTestResult && (
              <Alert
                type={profileTestResult.ok ? 'success' : 'error'}
                showIcon
                message={profileTestResult.ok ? 'Test passed' : 'Test failed'}
                description={
                  <Space direction="vertical" size={8} style={{ width: '100%' }}>
                    {profileTestResult.response_preview && <span>{profileTestResult.response_preview}</span>}
                    {profileTestResult.response_detail && <pre className="model-test-response">{profileTestResult.response_detail}</pre>}
                    {profileTestResult.temporary_image_preview && (
                      <Image
                        width={180}
                        src={profileTestResult.temporary_image_preview}
                        alt="Temporary model test preview"
                      />
                    )}
                    {profileTestResult.temporary_image_deleted && <Tag color="success">temporary image deleted</Tag>}
                  </Space>
                }
              />
            )}
          </div>
        </Form>
      </Modal>

      <Modal
        title={editingConfigId ? 'Edit Combination' : 'Add Combination'}
        open={configModalOpen}
        onOk={saveConfig}
        onCancel={() => setConfigModalOpen(false)}
        okText={editingConfigId ? 'Save Combination' : 'Create Combination'}
        cancelText="Cancel"
        confirmLoading={saving}
        okButtonProps={{ disabled: isSystemManaged(editingConfig) }}
        width={700}
        destroyOnHidden
        forceRender
      >
        <Form form={configForm} layout="vertical">
          <Form.Item name="name" label="Combination Name" rules={[{ required: true }]}>
            <Input aria-label="Combination name" />
          </Form.Item>
          <Form.Item name="type" label="Type" rules={[{ required: true }]}>
            <Select aria-label="Combination type" options={configTypeOptions} />
          </Form.Item>
          <Form.Item name="designer_profile_id" label="Designer Model" rules={[{ required: true }]}>
            <Select aria-label="Combination designer model" options={profilesByRole.designer.map(profileOption)} placeholder="Select Designer Model" />
          </Form.Item>
          <Form.Item name="html_agent_profile_id" label="HTML Agent Model" rules={[{ required: true }]}>
            <Select aria-label="Combination HTML agent model" options={profilesByRole.html_agent.map(profileOption)} placeholder="Select HTML Agent Model" />
          </Form.Item>
          <div className="form-section-label">Image Route Models</div>
          <Form.Item name="image_designer_profile_id" label="Image Designer Model">
            <Select
              aria-label="Combination image designer model"
              allowClear
              options={profilesByRole.image_designer.map(profileOption)}
              placeholder="Use default fallback"
            />
          </Form.Item>
          <Form.Item name="image_generator_profile_id" label="Image Generator Model">
            <Select
              aria-label="Combination image generator model"
              allowClear
              options={profilesByRole.image_generator.map(profileOption)}
              placeholder="Use default fallback"
            />
          </Form.Item>
          <Form.Item name="image_palette_extractor_profile_id" label="Image Palette Extractor Model">
            <Select
              aria-label="Combination image palette extractor model"
              allowClear
              options={paletteExtractorProfiles.map(profileOption)}
              placeholder="Use default fallback"
            />
          </Form.Item>
          <Form.Item name="timeout_minutes" label="Timeout (minutes)" rules={[{ required: true }]}>
            <InputNumber min={1} max={240} />
          </Form.Item>
          <Form.Item name="is_default" label="Default">
            <Select
              aria-label="Combination default flag"
              options={[
                { label: 'No', value: false },
                { label: 'Yes', value: true },
              ]}
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
};

export default ConfigPage;
