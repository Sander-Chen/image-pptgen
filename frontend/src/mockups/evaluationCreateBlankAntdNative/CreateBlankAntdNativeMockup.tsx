import { useMemo, useState } from 'react';
import { Button, Input, InputNumber, Layout, Menu, Segmented, Select, Space, Tag } from 'antd';
import {
  ArrowLeftOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FileTextOutlined,
  HistoryOutlined,
  MenuFoldOutlined,
  SettingOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';

const { Sider, Content } = Layout;

type Engine = 'html' | 'image';
type Mode = 'auto' | 'manual';

type Variant = {
  key: string;
  label: string;
  goal: string;
  engine: Engine;
  mode: Mode;
  config: string;
  strategy: string;
  requirement: string | null;
  color: string | null;
  comparisonVariable: string;
};

const navItems = [
  { key: '/data', icon: <DatabaseOutlined />, label: 'Data' },
  { key: '/generate', icon: <ThunderboltOutlined />, label: 'Generate' },
  { key: '/history', icon: <HistoryOutlined />, label: 'History' },
  { key: '/evaluations', icon: <FileSearchOutlined />, label: 'Evaluations' },
  { key: '/prompts', icon: <FileTextOutlined />, label: 'Prompts' },
  { key: '/config', icon: <SettingOutlined />, label: 'Config' },
];

const letters = ['A', 'B', 'C', 'D'];
const accentColors = ['blue', 'gold', 'purple', 'cyan'] as const;

function makeVariant(index: number): Variant {
  const letter = letters[index];
  return {
    key: `variant-${letter.toLowerCase()}`,
    label: `${letter} · Variant`,
    goal: index === 0
      ? 'Baseline generation plan for comparison.'
      : 'Alternative generation plan for side-by-side review.',
    engine: 'html',
    mode: 'auto',
    config: 'HTML Production Pro · default',
    strategy: 'HTML Default',
    requirement: null,
    color: null,
    comparisonVariable: index === 0 ? 'Baseline' : 'Prompt / route change',
  };
}

function routeTag(variant: Variant) {
  return variant.engine === 'html' ? 'HTML Default' : `Image ${variant.strategy}`;
}

function CreateBlankAntdNativeMockup() {
  const [variantCount, setVariantCount] = useState(4);
  const [repeat, setRepeat] = useState(1);
  const [variants, setVariants] = useState<Variant[]>(() => [0, 1, 2, 3].map(makeVariant));

  const visibleVariants = useMemo(() => variants.slice(0, variantCount), [variantCount, variants]);

  const patchVariant = (index: number, patch: Partial<Variant>) => {
    setVariants((current) => current.map((variant, variantIndex) => (
      variantIndex === index ? { ...variant, ...patch } : variant
    )));
  };

  const setEngine = (index: number, engine: Engine) => {
    patchVariant(index, engine === 'html'
      ? {
        engine,
        mode: 'auto',
        config: 'HTML Production Pro · default',
        strategy: 'HTML Default',
        requirement: null,
        color: null,
      }
      : {
        engine,
        mode: 'manual',
        config: 'Image Test',
        strategy: '5.0',
        requirement: '奢华',
        color: '可爱小动物--黄',
      });
  };

  const setMode = (index: number, mode: Mode) => {
    patchVariant(index, mode === 'auto'
      ? { mode, requirement: null, color: null }
      : { mode, requirement: '奢华', color: '可爱小动物--黄' });
  };

  return (
    <Layout className="create-blank-mockup">
      <Sider width={224} className="mockup-sider">
        <div className="mockup-brand">HTML-PPT-Gen</div>
        <Button className="mockup-collapse" icon={<MenuFoldOutlined />} />
        <Menu selectedKeys={['/evaluations']} items={navItems} mode="inline" />
        <div className="mockup-user">admin<br /><span>Administrator</span></div>
      </Sider>
      <Content className="mockup-content">
        <div className="mockup-page-head">
          <div>
            <span className="mockup-kicker">Evaluation</span>
            <h1>Create Blank Evaluation</h1>
            <p>Start a shared-deck multi-variant evaluation with one normalized generation plan per variant.</p>
          </div>
          <Button icon={<ArrowLeftOutlined />}>Evaluations</Button>
        </div>

        <section className="mockup-panel">
          <div className="mockup-section-head">
            <div>
              <h2>Blank setup</h2>
              <p>Accepted Ant Design-native mockup. Engine is fixed first; dependent controls follow.</p>
            </div>
            <Button type="primary" icon={<ThunderboltOutlined />}>Start Evaluation Runs</Button>
          </div>

          <div className="mockup-plan-strip">
            <div>
              <span>Planned runs</span>
              <strong>{variantCount * repeat}</strong>
              <p>{variantCount} variants · {repeat} attempt{repeat > 1 ? 's' : ''} each</p>
            </div>
            <div>
              <span>Generation contract</span>
              <p>One shared deck. HTML Auto has empty palette/runtime inputs. Image is manual-only.</p>
            </div>
            <Tag color="blue">User accepted</Tag>
          </div>

          <div className="mockup-form-grid">
            <label>
              <span>Title</span>
              <Input defaultValue="Evaluation 2026/6/10" />
            </label>
            <label>
              <span>Shared deck</span>
              <Select value="中国历史" options={[{ value: '中国历史', label: '中国历史' }]} />
            </label>
            <label className="wide">
              <span>Evaluation goal</span>
              <Input.TextArea rows={3} defaultValue="Compare variants page by page and keep the strongest representative attempt from each variant." />
            </label>
            <label>
              <span>Variants</span>
              <Segmented block value={variantCount} options={[2, 3, 4]} onChange={(value) => setVariantCount(Number(value))} />
            </label>
            <label>
              <span>Repeat</span>
              <InputNumber min={1} max={5} value={repeat} onChange={(value) => setRepeat(Number(value || 1))} />
            </label>
          </div>

          <div className={`mockup-variant-grid variant-count-${variantCount}`}>
            {visibleVariants.map((variant, index) => (
              <article className="mockup-variant-card" key={variant.key}>
                <header className="mockup-variant-card-head">
                  <div className="mockup-variant-title">
                    <Tag color={accentColors[index]}>{letters[index]}</Tag>
                    <Input value={variant.label} onChange={(event) => patchVariant(index, { label: event.target.value })} />
                  </div>
                  <label className="mockup-engine-row">
                    <span>Engine</span>
                    <Segmented
                      block
                      value={variant.engine}
                      options={[
                        { value: 'html', label: 'HTML' },
                        { value: 'image', label: 'Image' },
                      ]}
                      onChange={(value) => setEngine(index, value as Engine)}
                    />
                  </label>
                  <Space size={4} wrap>
                    <Tag color={variant.engine === 'html' ? 'blue' : 'gold'}>{routeTag(variant)}</Tag>
                    {variant.engine === 'html' && variant.mode === 'auto'
                      ? <Tag color="green">Palette empty</Tag>
                      : <Tag>Manual inputs</Tag>}
                  </Space>
                </header>

                <label>
                  <span>Variant goal</span>
                  <Input.TextArea rows={2} value={variant.goal} onChange={(event) => patchVariant(index, { goal: event.target.value })} />
                </label>

                <div className="mockup-variant-fields">
                  {variant.engine === 'html' ? (
                    <>
                      <label className="full-row">
                        <span>Generation mode</span>
                        <Segmented
                          block
                          value={variant.mode}
                          options={[
                            { value: 'auto', label: 'Auto (Recommended)' },
                            { value: 'manual', label: 'Manual' },
                          ]}
                          onChange={(value) => setMode(index, value as Mode)}
                        />
                      </label>
                      {variant.mode === 'auto' ? (
                        <div className="mockup-auto-summary">
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
                            <Select value={variant.requirement || undefined} options={[{ value: '奢华', label: '奢华' }]} />
                          </label>
                          <label>
                            <span>Color (optional)</span>
                            <Select value={variant.color || undefined} options={[{ value: '可爱小动物--黄', label: '可爱小动物--黄' }]} />
                          </label>
                        </>
                      )}
                      <label>
                        <span>Config</span>
                        <Select value={variant.config} options={[{ value: variant.config, label: variant.config }]} />
                      </label>
                      <label>
                        <span>Default Designer Prompt</span>
                        <Select value="5.3.20-designer (v5.3.20)" options={[{ value: '5.3.20-designer (v5.3.20)', label: '5.3.20-designer (v5.3.20)' }]} />
                      </label>
                      <label>
                        <span>Default HTML Agent Prompt</span>
                        <Select value="Default · v5.3.20-HTML" options={[{ value: 'Default · v5.3.20-HTML', label: 'Default · v5.3.20-HTML' }]} />
                      </label>
                    </>
                  ) : (
                    <>
                      <div className="mockup-manual-summary">
                        <span>Generation mode</span>
                        <Space size={4} wrap>
                          <Tag color="gold">Manual only</Tag>
                          <Tag>Image Auto disabled</Tag>
                        </Space>
                      </div>
                      <label>
                        <span>Config</span>
                        <Select value={variant.config} options={[{ value: 'Image Test', label: 'Image Test' }]} />
                      </label>
                      <label>
                        <span>Image strategy</span>
                        <Select value={variant.strategy} options={['1.0', '3.0', '3.2', '5.0'].map((value) => ({ value, label: value }))} />
                      </label>
                      <label>
                        <span>Requirement</span>
                        <Select value={variant.requirement || undefined} options={[{ value: '奢华', label: '奢华' }]} />
                      </label>
                      <label>
                        <span>Color (optional)</span>
                        <Select value={variant.color || undefined} options={[{ value: '可爱小动物--黄', label: '可爱小动物--黄' }]} />
                      </label>
                      <div className="mockup-image-roles">
                        <span>Image prompt roles</span>
                        <Space size={4} wrap>
                          <Tag color="gold">image cover 3 1 · image-l4-default</Tag>
                          <Tag color="gold">image 5 0 unified · image-l4-default</Tag>
                          <Tag color="gold">image generator · image-l4-default</Tag>
                        </Space>
                      </div>
                    </>
                  )}
                </div>

                <label>
                  <span>Comparison variable</span>
                  <Input value={variant.comparisonVariable} onChange={(event) => patchVariant(index, { comparisonVariable: event.target.value })} />
                </label>
              </article>
            ))}
          </div>
        </section>
      </Content>
    </Layout>
  );
}

export default CreateBlankAntdNativeMockup;
