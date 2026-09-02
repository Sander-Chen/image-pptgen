import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Button, Card, Descriptions, Divider, Select, Space, Spin, Tag, message } from 'antd';
import { CheckCircleOutlined, ThunderboltOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { Deck, Slide } from '../types';

const PUBLIC_CONFIG_NAMES = [
  'Codex Native Image 3.0',
  'Codex Native Image 3.0 Luna Low Director',
] as const;

type PublicConfigName = (typeof PUBLIC_CONFIG_NAMES)[number];

interface PublicConfig {
  id: number;
  name: PublicConfigName;
  type: 'image';
  route: 'image_3_0';
  timeout_minutes?: number;
  max_concurrent_runs?: number;
  director: { model: string; reasoning_effort: string };
  renderer: { model: string; reasoning_effort: string };
  palette: { model: string };
}

const REFERENCE_INPUT_MAP = [
  {
    input: 'Deck slide content',
    status: 'Required',
    detail: 'The selected deck supplies the ordered slide inputs.',
  },
  {
    input: 'Page 2 seed image',
    status: 'Derived',
    detail: 'The Image 3.0 route seeds the palette from the first content page.',
  },
  {
    input: 'Palette and style context',
    status: 'Forwarded',
    detail: 'The backend route carries bounded seed dependencies to later pages.',
  },
] as const;

const isPublicConfig = (config: PublicConfig): boolean =>
  config.type === 'image'
  && config.route === 'image_3_0'
  && PUBLIC_CONFIG_NAMES.includes(config.name);

const GeneratePage: React.FC = () => {
  const [decks, setDecks] = useState<Deck[]>([]);
  const [configs, setConfigs] = useState<PublicConfig[]>([]);
  const [loading, setLoading] = useState(false);
  const [selectedDeckId, setSelectedDeckId] = useState<number | null>(null);
  const [slideCount, setSlideCount] = useState(0);
  const [selectedConfigId, setSelectedConfigId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [startedBatch, setStartedBatch] = useState<{ batch_id: number; run_ids: number[]; total_runs: number } | null>(null);

  const publicConfigs = useMemo(
    () => configs.filter(isPublicConfig).slice(0, PUBLIC_CONFIG_NAMES.length),
    [configs],
  );

  const selectedDeck = decks.find((deck) => deck.id === selectedDeckId);
  const selectedConfig = publicConfigs.find((config) => config.id === selectedConfigId);
  const canGenerate = selectedDeckId !== null && selectedConfigId !== null && slideCount >= 2 && !submitting;

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [deckRows, configRows] = await Promise.all([
        api.decks.list(),
        api.configs.list(),
      ]);
      setDecks(deckRows);
      const safeConfigs = configRows as unknown as PublicConfig[];
      setConfigs(safeConfigs.filter(isPublicConfig));
      setSelectedConfigId((currentId) => currentId ?? safeConfigs.find(isPublicConfig)?.id ?? null);
    } catch (err: unknown) {
      message.error(`Failed to load public generation data: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchData();
    });
  }, [fetchData]);

  const handleDeckChange = async (deckId: number) => {
    setSelectedDeckId(deckId);
    setSlideCount(0);
    try {
      const slides: Slide[] = await api.decks.getSlides(deckId);
      setSlideCount(slides.length);
    } catch (err: unknown) {
      setSlideCount(0);
      message.error(`Failed to load deck slides: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleGenerate = async () => {
    if (!canGenerate || selectedDeckId === null || selectedConfigId === null) return;
    setSubmitting(true);
    setStartedBatch(null);
    try {
      const payload: Parameters<typeof api.generate.start>[0] = {
        deck_id: selectedDeckId,
        config_id: selectedConfigId,
        engine: 'image',
        strategy: 'image_3_0',
        requirement_ids: [],
        color_ids: [],
      };
      const result = await api.generate.start(payload);
      setStartedBatch({ batch_id: result.batch_id, run_ids: result.run_ids, total_runs: result.total_runs });
      message.success(`Started Image PPT 3.0 batch #${result.batch_id}`);
    } catch (err: unknown) {
      message.error(`Generate failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="generate-page">
      <div className="page-toolbar">
        <div>
          <h2>Generate Image PPT 3.0</h2>
          <p className="toolbar-subtitle">Choose a deck and one of the two public safe configurations.</p>
        </div>
        <Tag color="gold">Image Route (3.0)</Tag>
      </div>

      <Spin spinning={loading}>
        <div className="generate-step-grid">
          <Card title="Deck" className="generate-options-panel">
            <Select
              aria-label="Select deck"
              placeholder="Select a deck"
              style={{ width: '100%' }}
              value={selectedDeckId ?? undefined}
              onChange={handleDeckChange}
              options={decks.map((deck) => ({ label: deck.title, value: deck.id }))}
            />
            <Alert
              style={{ marginTop: 12 }}
              type={selectedDeckId === null ? 'info' : slideCount >= 2 ? 'success' : 'warning'}
              showIcon
              message={selectedDeckId === null
                ? 'Select a deck to load its slides.'
                : `${slideCount} slide(s) loaded`}
              description={selectedDeckId !== null && slideCount < 2
                ? 'At least two slides are required before generation can start.'
                : undefined}
            />
          </Card>

          <Card title="Image Route (3.0)" className="generate-options-panel">
            <p>This public route is fixed to the Image PPT 3.0 seed flow.</p>
            <Tag color="gold">engine: image</Tag>
            <Tag color="gold">strategy: image_3_0</Tag>
          </Card>

          <Card title="Reference Input Map" className="generate-options-panel">
            <Descriptions column={1} size="small" bordered>
              {REFERENCE_INPUT_MAP.map((item) => (
                <Descriptions.Item key={item.input} label={item.input}>
                  <Space direction="vertical" size={0}>
                    <Tag color={item.status === 'Required' ? 'blue' : 'default'}>{item.status}</Tag>
                    <span>{item.detail}</span>
                  </Space>
                </Descriptions.Item>
              ))}
            </Descriptions>
          </Card>

          <Card title="Public Config" className="generate-options-panel">
            <Select
              aria-label="Select public config"
              placeholder="Select a public config"
              style={{ width: '100%' }}
              value={selectedConfigId ?? undefined}
              onChange={setSelectedConfigId}
              options={publicConfigs.map((config) => ({ label: config.name, value: config.id }))}
            />
            {selectedConfig && (
              <Descriptions column={1} size="small" style={{ marginTop: 16 }}>
                <Descriptions.Item label="Route">{selectedConfig.route}</Descriptions.Item>
                <Descriptions.Item label="Director">
                  {selectedConfig.director.model} · {selectedConfig.director.reasoning_effort}
                </Descriptions.Item>
                <Descriptions.Item label="Renderer">
                  {selectedConfig.renderer.model} · {selectedConfig.renderer.reasoning_effort}
                </Descriptions.Item>
                <Descriptions.Item label="Palette">{selectedConfig.palette.model}</Descriptions.Item>
              </Descriptions>
            )}
          </Card>

          <Card title="Confirm & Generate" className="generate-options-panel generate-confirm-panel">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="Deck">{selectedDeck?.title || '-'}</Descriptions.Item>
              <Descriptions.Item label="Slides">{slideCount}</Descriptions.Item>
              <Descriptions.Item label="Route">Image Route (3.0)</Descriptions.Item>
              <Descriptions.Item label="Config">{selectedConfig?.name || '-'}</Descriptions.Item>
              <Descriptions.Item label="Inputs">Deck slides only.</Descriptions.Item>
            </Descriptions>
            <Divider />
            <Button
              type="primary"
              size="large"
              icon={<ThunderboltOutlined />}
              onClick={handleGenerate}
              loading={submitting}
              disabled={!canGenerate}
              aria-label="Generate Image PPT 3.0"
            >
              Generate Image PPT 3.0
            </Button>
            {!canGenerate && (
              <Alert
                style={{ marginTop: 12 }}
                type="warning"
                showIcon
                message="Generation is not ready"
                description={selectedDeckId === null
                  ? 'Select a deck first.'
                  : selectedConfigId === null
                    ? 'Select a public config.'
                    : 'Select a deck with at least two slides.'}
              />
            )}
            {startedBatch && (
              <Alert
                style={{ marginTop: 12 }}
                type="success"
                showIcon
                icon={<CheckCircleOutlined />}
                message={`Batch #${startedBatch.batch_id} started`}
                description={`${startedBatch.total_runs} run(s) submitted.`}
              />
            )}
          </Card>
        </div>
      </Spin>
    </div>
  );
};

export default GeneratePage;
