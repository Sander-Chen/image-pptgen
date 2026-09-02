import React, { useMemo, useState } from 'react';
import { Button, Form, Input, Modal, Radio, Select, Space, message } from 'antd';
import { FolderAddOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { Folder, LifecycleStatus } from '../types';

interface Props {
  scope: 'deck' | 'requirement' | 'color';
  folders: Folder[];
  status: LifecycleStatus;
  folderId: number | null;
  onStatusChange: (status: LifecycleStatus) => void;
  onFolderChange: (folderId: number | null) => void;
  onFoldersChanged: () => void;
}

const statusOptions = [
  { label: 'Active', value: 'active' },
  { label: 'Archived', value: 'archived' },
  { label: 'Recycle Bin', value: 'recycle_bin' },
];

const DataFolderControls: React.FC<Props> = ({
  scope,
  folders,
  status,
  folderId,
  onStatusChange,
  onFolderChange,
  onFoldersChanged,
}) => {
  const [modalOpen, setModalOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const folderOptions = useMemo(
    () => folders.map((folder) => ({ label: folder.name, value: folder.id })),
    [folders],
  );

  const saveFolder = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      await api.folders.create({ scope, name: values.name, parent_id: values.parent_id || null });
      message.success('Folder created');
      setModalOpen(false);
      form.resetFields();
      onFoldersChanged();
    } catch (err: unknown) {
      if (err instanceof Error) message.error(err.message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Space wrap className="control-strip">
        <Radio.Group
          aria-label={`${scope} lifecycle filter`}
          optionType="button"
          buttonStyle="solid"
          options={statusOptions}
          value={status}
          onChange={(event) => onStatusChange(event.target.value)}
        />
        <Select
          allowClear
          style={{ minWidth: 220 }}
          placeholder="Filter by folder"
          aria-label={`${scope} folder filter`}
          value={folderId ?? undefined}
          options={folderOptions}
          onChange={(value) => onFolderChange(value ?? null)}
        />
        <Button icon={<FolderAddOutlined />} onClick={() => setModalOpen(true)}>
          New Folder
        </Button>
      </Space>

      <Modal
        title="New Folder"
        open={modalOpen}
        onOk={saveFolder}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="Folder name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="parent_id" label="Parent folder">
            <Select allowClear options={folderOptions} />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );
};

export default DataFolderControls;
