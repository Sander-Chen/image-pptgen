import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Select, Space, Tag, message } from 'antd';
import { api } from '../api';
import type { AutoSplitContentMode, AutoSplitSettings, AutoSplitThinkingEffort } from '../types';

const effortOptions: Array<{ label: string; value: AutoSplitThinkingEffort }> = [
  { label: 'Low', value: 'low' },
  { label: 'Medium', value: 'medium' },
  { label: 'High', value: 'high' },
];

const contentModeOptions: Array<{ label: string; value: AutoSplitContentMode }> = [
  { label: 'Faithful Split', value: 'faithful' },
  { label: 'Editorial Restructure', value: 'editorial' },
];

const contentModeDescriptions: Record<AutoSplitContentMode, string> = {
  faithful: 'Preserves the source wording and order, changing only structure and pagination for PPT.',
  editorial: 'May condense, reorder, and rewrite for a clearer PPT narrative while preserving essential facts and evidence.',
};

const AutoSplitSettingsPanel: React.FC = () => {
  const [settings, setSettings] = useState<AutoSplitSettings | null>(null);
  const [modelProfileId, setModelProfileId] = useState<number | null>(null);
  const [thinkingEffort, setThinkingEffort] = useState<AutoSplitThinkingEffort>('high');
  const [contentMode, setContentMode] = useState<AutoSplitContentMode>('faithful');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const data = await api.autoSplitSettings.get();
      setSettings(data);
      setModelProfileId(data.model_profile_id);
      setThinkingEffort(data.thinking_effort);
      setContentMode(data.content_mode);
    } catch (err: unknown) {
      setLoadError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      void loadSettings();
    });
  }, [loadSettings]);

  const selectedProfile = useMemo(
    () => settings?.available_profiles.find((profile) => profile.id === modelProfileId),
    [modelProfileId, settings],
  );
  const dirty = Boolean(
    settings
      && modelProfileId
      && (
        modelProfileId !== settings.model_profile_id
        || thinkingEffort !== settings.thinking_effort
        || contentMode !== settings.content_mode
      ),
  );

  const saveSettings = async () => {
    if (!modelProfileId) return;
    setSaving(true);
    try {
      const saved = await api.autoSplitSettings.update({
        model_profile_id: modelProfileId,
        thinking_effort: thinkingEffort,
        content_mode: contentMode,
      });
      setSettings(saved);
      setModelProfileId(saved.model_profile_id);
      setThinkingEffort(saved.thinking_effort);
      setContentMode(saved.content_mode);
      message.success('AutoSplit settings saved');
    } catch (err: unknown) {
      message.error(`AutoSplit settings could not be saved: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="config-section">
      <div className="section-heading">
        <h3>AutoSplit</h3>
        <span>One global model, Thinking Effort, and Content Mode setting, independent of Combinations.</span>
      </div>
      {loadError ? (
        <Alert
          type="error"
          showIcon
          message="AutoSplit settings could not be loaded"
          description={loadError}
          action={<Button onClick={loadSettings} loading={loading}>Retry</Button>}
        />
      ) : (
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          <div className="filter-field wide">
            <span>Model</span>
            <Select
              aria-label="AutoSplit model"
              loading={loading}
              value={modelProfileId}
              onChange={setModelProfileId}
              options={(settings?.available_profiles || []).map((profile) => ({
                value: profile.id,
                label: `${profile.name} · ${profile.provider}`,
              }))}
            />
          </div>
          <div className="filter-field wide">
            <span>Thinking Effort</span>
            <Select
              aria-label="AutoSplit Thinking Effort"
              value={thinkingEffort}
              onChange={setThinkingEffort}
              options={effortOptions}
            />
          </div>
          <div className="filter-field wide">
            <span>Content Mode</span>
            <Select
              aria-label="AutoSplit Content Mode"
              value={contentMode}
              onChange={setContentMode}
              options={contentModeOptions}
            />
          </div>
          <Alert
            type="info"
            showIcon
            message={contentModeOptions.find((option) => option.value === contentMode)?.label}
            description={contentModeDescriptions[contentMode]}
          />
          {selectedProfile && (
            <Alert
              type={selectedProfile.ready ? 'success' : 'warning'}
              showIcon
              message={
                <Space wrap>
                  <strong>{selectedProfile.model}</strong>
                  <Tag>{selectedProfile.provider}</Tag>
                  <Tag color={selectedProfile.ready ? 'success' : 'warning'}>
                    {selectedProfile.ready ? 'Ready' : 'Not ready'}
                  </Tag>
                </Space>
              }
              description={selectedProfile.readiness_message}
            />
          )}
          <Button
            type="primary"
            onClick={saveSettings}
            loading={saving}
            disabled={loading || saving || !dirty}
          >
            Save AutoSplit Settings
          </Button>
        </Space>
      )}
    </section>
  );
};

export default AutoSplitSettingsPanel;
