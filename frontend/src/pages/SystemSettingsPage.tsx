import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  List,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd';
import { EditOutlined, EyeOutlined, PlusOutlined, StopOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { SystemSettings, SystemVariable } from '../types';

const { Text } = Typography;
type SystemSettingsSection = 'all' | 'settings' | 'variables';

const agentOptions = [
  { label: 'Designer Agent', value: 'designer' },
  { label: 'HTML Agent', value: 'html_agent' },
  { label: 'Image Cover', value: 'image_cover' },
  { label: 'Image Designer', value: 'image_designer' },
  { label: 'Image Generator', value: 'image_generator' },
  { label: 'XML Cleanup', value: 'xml_cleanup' },
];

const agentLabel = (agentType: string) => agentOptions.find((option) => option.value === agentType)?.label || agentType;
const agentColor = (agentType: string) => {
  if (agentType === 'designer') return 'blue';
  if (agentType === 'html_agent') return 'green';
  if (agentType.startsWith('image_')) return 'gold';
  return 'geekblue';
};

const SystemSettingsPage: React.FC<{ embedded?: boolean; section?: SystemSettingsSection }> = ({ embedded = false, section = 'all' }) => {
  const [settings, setSettings] = useState<SystemSettings | null>(null);
  const [variables, setVariables] = useState<SystemVariable[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [variableSaving, setVariableSaving] = useState(false);
  const [variableModalOpen, setVariableModalOpen] = useState(false);
  const [editingVariable, setEditingVariable] = useState<SystemVariable | null>(null);
  const [referenceDrawerOpen, setReferenceDrawerOpen] = useState(false);
  const [referenceTitle, setReferenceTitle] = useState('');
  const [references, setReferences] = useState<Array<{ prompt_id: number; version: string; snippet: string; agent_type?: string; prompt_name?: string }>>([]);
  const [referenceLoading, setReferenceLoading] = useState(false);
  const [settingsForm] = Form.useForm<SystemSettings>();
  const [variableForm] = Form.useForm();

  const fetchData = useCallback(async () => {
    setLoading(true);
    try {
      const [settingsData, variableData] = await Promise.all([
        api.systemSettings.get(),
        api.systemVariables.list(),
      ]);
      setSettings(settingsData);
      settingsForm.setFieldsValue(settingsData);
      setVariables(variableData);
    } catch (err: unknown) {
      message.error(`Failed to load system settings: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [settingsForm]);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchData();
    });
  }, [fetchData]);

  const saveSettings = async () => {
    try {
      const values = await settingsForm.validateFields();
      setSavingSettings(true);
      const updated = await api.systemSettings.update(values);
      setSettings(updated);
      settingsForm.setFieldsValue(updated);
      message.success('System settings saved');
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setSavingSettings(false);
    }
  };

  const openAddVariable = () => {
    setEditingVariable(null);
    variableForm.resetFields();
    variableForm.setFieldsValue({ agent_type: 'designer', status: 'active' });
    setVariableModalOpen(true);
  };

  const openEditVariable = (variable: SystemVariable) => {
    setEditingVariable(variable);
    variableForm.setFieldsValue(variable);
    setVariableModalOpen(true);
  };

  const saveVariable = async () => {
    try {
      const values = await variableForm.validateFields();
      setVariableSaving(true);
      if (editingVariable) {
        await api.systemVariables.update(editingVariable.id, values);
        message.success('System variable updated');
      } else {
        await api.systemVariables.create(values);
        message.success('System variable created');
      }
      setVariableModalOpen(false);
      await fetchData();
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setVariableSaving(false);
    }
  };

  const disableVariable = async (variable: SystemVariable) => {
    try {
      await api.systemVariables.update(variable.id, { status: 'disabled' });
      message.success('Variable disabled for new prompts');
      await fetchData();
    } catch (err: unknown) {
      message.error(`Disable failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const openReferences = async (variable: SystemVariable) => {
    setReferenceTitle(`${variable.name} references`);
    setReferenceDrawerOpen(true);
    setReferenceLoading(true);
    try {
      const payload = await api.systemVariables.references(variable.id);
      setReferences(payload.references);
    } catch (err: unknown) {
      message.error(`Failed to load references: ${err instanceof Error ? err.message : String(err)}`);
      setReferences([]);
    } finally {
      setReferenceLoading(false);
    }
  };

  const summary = useMemo(() => {
    const active = variables.filter((item) => item.status === 'active').length;
    const disabled = variables.length - active;
    return { active, disabled };
  }, [variables]);

  const variableColumns = [
    {
      title: 'Role',
      dataIndex: 'agent_type',
      key: 'agent_type',
      width: 150,
      render: (agentType: string) => (
        <Tag color={agentColor(agentType)}>{agentLabel(agentType)}</Tag>
      ),
      filters: agentOptions.map((option) => ({ text: option.label, value: option.value })),
      onFilter: (value: React.Key | boolean, record: SystemVariable) => record.agent_type === value,
    },
    {
      title: 'Variable',
      dataIndex: 'name',
      key: 'name',
      render: (name: string, record: SystemVariable) => (
        <Space direction="vertical" size={0}>
          <Text code>{`{{${name}}}`}</Text>
          {record.description ? <Text type="secondary">{record.description}</Text> : null}
        </Space>
      ),
    },
    {
      title: 'Status',
      dataIndex: 'status',
      key: 'status',
      width: 120,
      render: (status: string) => (
        <Tag color={status === 'active' ? 'success' : 'default'}>{status}</Tag>
      ),
      filters: [
        { text: 'Active', value: 'active' },
        { text: 'Disabled', value: 'disabled' },
      ],
      onFilter: (value: React.Key | boolean, record: SystemVariable) => record.status === value,
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 190,
      render: (_: unknown, record: SystemVariable) => (
        <Space size="small">
          <Tooltip title="Edit variable">
            <Button aria-label={`Edit variable ${record.name}`} size="small" icon={<EditOutlined />} onClick={() => openEditVariable(record)} />
          </Tooltip>
          <Tooltip title="View references">
            <Button aria-label={`View references for ${record.name}`} size="small" icon={<EyeOutlined />} onClick={() => openReferences(record)} />
          </Tooltip>
          <Tooltip title="Disable for new prompts">
            <Button
              aria-label={`Disable variable ${record.name}`}
              size="small"
              icon={<StopOutlined />}
              disabled={record.status === 'disabled'}
              onClick={() => disableVariable(record)}
            />
          </Tooltip>
        </Space>
      ),
    },
  ];

  return (
    <div className="work-surface system-settings-page">
      {!embedded && (
        <div className="page-toolbar">
          <div>
            <h2>System Settings</h2>
            <Text type="secondary">Global controls shared by all configs, runs, and batches.</Text>
          </div>
        </div>
      )}

      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        {section !== 'variables' && (
          <Alert
            type="info"
            showIcon
            message="Concurrency limits apply to run queue launches and provider requests."
            description="Run queue concurrency limits active Runs globally. Provider limits bound external API pressure by endpoint host."
          />
        )}

        {section !== 'variables' && (
        <Card title="Global Concurrency" loading={loading}>
          <Form form={settingsForm} layout="vertical" onFinish={saveSettings}>
            <div className="provider-concurrency-grid">
              <Form.Item
                name="run_queue_concurrency"
                label="Run queue concurrency"
                rules={[{ required: true, message: 'Enter run queue concurrency' }]}
                extra="Maximum active Runs launched from the batch queue."
              >
                <InputNumber min={1} max={100} />
              </Form.Item>
              <Form.Item
                name={['provider_concurrency', 'openai:zenmux.ai']}
                label="ZenMux provider concurrency"
                rules={[{ required: true, message: 'Enter ZenMux provider concurrency' }]}
                extra="Applies to openai:zenmux.ai requests."
              >
                <InputNumber min={1} max={200} />
              </Form.Item>
              <Form.Item
                name={['provider_concurrency', 'gemini:generativelanguage.googleapis.com']}
                label="Gemini provider concurrency"
                rules={[{ required: true, message: 'Enter Gemini provider concurrency' }]}
                extra="Applies to gemini:generativelanguage.googleapis.com requests."
              >
                <InputNumber min={1} max={50} />
              </Form.Item>
              <Form.Item label=" " colon={false}>
                <Button type="primary" htmlType="submit" loading={savingSettings}>
                  Save Concurrency
                </Button>
              </Form.Item>
            </div>
          </Form>
          {settings ? (
            <Descriptions className="provider-concurrency-summary" size="small" column={{ xs: 1, sm: 2 }} style={{ marginTop: 8 }}>
              <Descriptions.Item label="Run queue limit">{settings.run_queue_concurrency}</Descriptions.Item>
              <Descriptions.Item label="ZenMux provider limit">{settings.provider_concurrency['openai:zenmux.ai']}</Descriptions.Item>
              <Descriptions.Item label="Gemini provider limit">{settings.provider_concurrency['gemini:generativelanguage.googleapis.com']}</Descriptions.Item>
            </Descriptions>
          ) : null}
        </Card>
        )}

        {section !== 'settings' && (
        <Card
          title="System Variables"
          loading={loading}
          extra={
            <Button type="primary" icon={<PlusOutlined />} onClick={openAddVariable}>
              Add Variable
            </Button>
          }
        >
          <Space size={8} wrap style={{ marginBottom: 12 }}>
            <Tag color="success">Active {summary.active}</Tag>
            <Tag>Disabled {summary.disabled}</Tag>
          </Space>
          <Table
            className="responsive-table"
            dataSource={variables}
            columns={variableColumns}
            rowKey="id"
            pagination={{ pageSize: 8, showSizeChanger: false }}
            scroll={{ x: 720 }}
          />
        </Card>
        )}
      </Space>

      <Drawer
        title={referenceTitle}
        open={referenceDrawerOpen}
        onClose={() => setReferenceDrawerOpen(false)}
        width={560}
      >
        <List
          loading={referenceLoading}
          dataSource={references}
          locale={{ emptyText: 'No prompt references found' }}
          renderItem={(item) => (
            <List.Item>
              <List.Item.Meta
                title={
                  <Space wrap>
                    <Tag color={agentColor(item.agent_type || '')}>{agentLabel(item.agent_type || '')}</Tag>
                    <Text>{item.prompt_name || `Prompt ${item.prompt_id}`}</Text>
                    <Text type="secondary">{item.version}</Text>
                  </Space>
                }
                description={<Text code>{item.snippet}</Text>}
              />
            </List.Item>
          )}
        />
      </Drawer>

      <Drawer
        title={editingVariable ? 'Edit System Variable' : 'Add System Variable'}
        open={variableModalOpen}
        onClose={() => setVariableModalOpen(false)}
        width={480}
        extra={
          <Space>
            <Button onClick={() => setVariableModalOpen(false)}>Cancel</Button>
            <Button type="primary" loading={variableSaving} onClick={saveVariable}>Save</Button>
          </Space>
        }
      >
        <Form form={variableForm} layout="vertical">
          <Form.Item name="agent_type" label="Role" rules={[{ required: true }]}>
            <Select disabled={!!editingVariable} options={agentOptions} />
          </Form.Item>
          <Form.Item name="name" label="Variable Name" rules={[{ required: true, message: 'Enter a variable name' }]}>
            <Input placeholder="Deck-Full-Content" />
          </Form.Item>
          <Form.Item name="description" label="Description">
            <Input.TextArea rows={3} />
          </Form.Item>
          <Form.Item name="status" label="Status" rules={[{ required: true }]}>
            <Select
              options={[
                { label: 'Active', value: 'active' },
                { label: 'Disabled', value: 'disabled' },
              ]}
            />
          </Form.Item>
          <Alert
            type="warning"
            showIcon
            message="Disabled variables remain valid for old prompt versions."
            description="They are hidden from new autocomplete and block saving new prompt versions until replaced."
          />
        </Form>
      </Drawer>
    </div>
  );
};

export default SystemSettingsPage;
