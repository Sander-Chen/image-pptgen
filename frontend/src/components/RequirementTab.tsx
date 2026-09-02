import React, { useState, useEffect, useCallback } from 'react';
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  Space,
  Tooltip,
  Popconfirm,
  message,
  Select,
  Tag,
} from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { Folder, LifecycleStatus, Requirement } from '../types';
import DataFolderControls from './DataFolderControls';

const { TextArea } = Input;

const RequirementTab: React.FC = () => {
  const [items, setItems] = useState<Requirement[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [status, setStatus] = useState<LifecycleStatus>('active');
  const [folderId, setFolderId] = useState<number | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [bulkFolderModalOpen, setBulkFolderModalOpen] = useState(false);
  const [bulkFolderIds, setBulkFolderIds] = useState<number[]>([]);
  const [form] = Form.useForm();

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.requirements.list({ status, folder_id: folderId });
      setItems(data);
      setSelectedRowKeys([]);
    } catch (err: unknown) {
      message.error(`Failed to load requirements: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [folderId, status]);

  const fetchFolders = useCallback(async () => {
    try {
      setFolders(await api.folders.list('requirement'));
    } catch (err: unknown) {
      message.error(`Failed to load folders: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchItems();
      void fetchFolders();
    });
  }, [fetchFolders, fetchItems]);

  const openAdd = () => {
    setEditingId(null);
    form.resetFields();
    form.setFieldsValue({ folder_ids: folderId ? [folderId] : [] });
    setModalOpen(true);
  };

  const openEdit = (record: Requirement) => {
    setEditingId(record.id);
    form.setFieldsValue({ title: record.title, content: record.content, folder_ids: record.folder_ids || [] });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      const folder_ids = values.folder_ids || [];
      if (editingId) {
        await api.requirements.update(editingId, values);
        await api.requirements.assignFolders(editingId, folder_ids);
        message.success('Requirement updated');
      } else {
        const created = await api.requirements.create(values);
        await api.requirements.assignFolders(created.id, folder_ids);
        message.success('Requirement created');
      }
      setModalOpen(false);
      fetchItems();
    } catch (err: unknown) {
      if (err instanceof Error) {
        message.error(err.message);
      }
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (id: number) => {
    try {
      await api.requirements.delete(id);
      message.success('Requirement moved to Recycle Bin');
      fetchItems();
    } catch (err: unknown) {
      message.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleArchive = async (id: number) => {
    try {
      await api.requirements.archive(id);
      message.success('Requirement archived');
      fetchItems();
    } catch (err: unknown) {
      message.error(`Archive failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleRestore = async (id: number) => {
    try {
      await api.requirements.restore(id);
      message.success('Requirement restored');
      fetchItems();
    } catch (err: unknown) {
      message.error(`Restore failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleForceDelete = async (id: number) => {
    try {
      await api.requirements.forceDelete(id);
      message.success('Requirement exported to historical data');
      fetchItems();
    } catch (err: unknown) {
      message.error(`Force delete failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const selectedIds = selectedRowKeys.map(Number);

  const runBulkAction = async (action: 'archive' | 'delete' | 'restore' | 'force_delete') => {
    if (!selectedIds.length) return;
    try {
      const result = await api.bulkActions.apply({ entity_type: 'requirement', action, ids: selectedIds });
      const failures = result.results.filter((item) => item.status === 'error');
      if (failures.length) message.warning(`${failures.length} requirement(s) could not be updated`);
      else message.success(`${selectedIds.length} requirement(s) updated`);
      fetchItems();
    } catch (err: unknown) {
      message.error(`Bulk action failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleBulkMove = async () => {
    if (!selectedIds.length) return;
    try {
      await api.bulkActions.apply({
        entity_type: 'requirement',
        action: 'move_to_folder',
        ids: selectedIds,
        folder_ids: bulkFolderIds,
      });
      message.success(`${selectedIds.length} requirement(s) moved`);
      setBulkFolderModalOpen(false);
      setBulkFolderIds([]);
      fetchItems();
    } catch (err: unknown) {
      message.error(`Move failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const columns = [
    {
      title: 'Title',
      dataIndex: 'title',
      key: 'title',
      width: 200,
      sorter: (a: Requirement, b: Requirement) => a.title.localeCompare(b.title),
    },
    {
      title: 'Content',
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
      render: (v: string) => (
        <Tooltip title={v && v.length > 500 ? v.slice(0, 500) + '...' : v}>
          <span>{v && v.length > 100 ? v.slice(0, 100) + '...' : v}</span>
        </Tooltip>
      ),
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      sorter: (a: Requirement, b: Requirement) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      render: (v: string) => v ? new Date(v).toLocaleString() : '-',
    },
    {
      title: 'Status',
      dataIndex: 'lifecycle_status',
      key: 'lifecycle_status',
      width: 110,
      render: (value: string) => <Tag>{value || 'active'}</Tag>,
    },
    {
      title: 'Folders',
      dataIndex: 'folder_ids',
      key: 'folder_ids',
      width: 180,
      render: (folderIds: number[] = []) => (
        <Space size={4} wrap>
          {folderIds.length ? folderIds.map((id) => <Tag key={id}>{folders.find((folder) => folder.id === id)?.name || id}</Tag>) : '-'}
        </Space>
      ),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 210,
      render: (_: unknown, record: Requirement) => (
        <Space>
          {status === 'active' ? (
            <>
              <Tooltip title="Edit requirement">
                <Button aria-label="Edit requirement" icon={<EditOutlined />} size="small" onClick={() => openEdit(record)} />
              </Tooltip>
              <Button size="small" onClick={() => handleArchive(record.id)}>Archive</Button>
              <Popconfirm title="Move this requirement to Recycle Bin?" onConfirm={() => handleDelete(record.id)}>
                <Button aria-label="Move requirement to Recycle Bin" icon={<DeleteOutlined />} size="small" danger />
              </Popconfirm>
            </>
          ) : (
            <>
              <Button size="small" onClick={() => handleRestore(record.id)}>Restore</Button>
              {status === 'recycle_bin' ? (
                <Popconfirm title="Force delete this requirement?" description="It will be exported to historical_data and hidden from the product UI." onConfirm={() => handleForceDelete(record.id)}>
                  <Button size="small" danger>Force Delete</Button>
                </Popconfirm>
              ) : (
                <Popconfirm title="Move archived requirement to Recycle Bin?" onConfirm={() => handleDelete(record.id)}>
                  <Button size="small" danger>Delete</Button>
                </Popconfirm>
              )}
            </>
          )}
        </Space>
      ),
    },
  ];

  return (
    <div>
      <DataFolderControls
        scope="requirement"
        folders={folders}
        status={status}
        folderId={folderId}
        onStatusChange={setStatus}
        onFolderChange={setFolderId}
        onFoldersChanged={fetchFolders}
      />

      <div style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
          Add Requirement
        </Button>
      </div>

      {selectedIds.length > 0 && (
        <div className="bulk-action-bar">
          <span>{selectedIds.length} selected</span>
          <Button size="small" onClick={() => setBulkFolderModalOpen(true)}>Move to Folder</Button>
          {status === 'active' && (
            <>
              <Button size="small" onClick={() => runBulkAction('archive')}>Archive</Button>
              <Popconfirm title="Move selected requirements to Recycle Bin?" onConfirm={() => runBulkAction('delete')}>
                <Button size="small" danger>Delete</Button>
              </Popconfirm>
            </>
          )}
          {status === 'archived' && (
            <>
              <Button size="small" onClick={() => runBulkAction('restore')}>Restore</Button>
              <Popconfirm title="Move selected archived requirements to Recycle Bin?" onConfirm={() => runBulkAction('delete')}>
                <Button size="small" danger>Delete</Button>
              </Popconfirm>
            </>
          )}
          {status === 'recycle_bin' && (
            <>
              <Button size="small" onClick={() => runBulkAction('restore')}>Restore</Button>
              <Popconfirm title="Force delete selected requirements?" onConfirm={() => runBulkAction('force_delete')}>
                <Button size="small" danger>Force Delete</Button>
              </Popconfirm>
            </>
          )}
          <Button size="small" onClick={() => setSelectedRowKeys([])}>Clear</Button>
        </div>
      )}

      <Table
        className="responsive-table"
        dataSource={items}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={false}
        rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
        scroll={{ x: 960 }}
      />

      <Modal
        title={editingId ? 'Edit Requirement' : 'Add Requirement'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        width={640}
        forceRender
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="title"
            label="Title"
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            name="content"
            label="Content"
            rules={[{ required: true }]}
          >
            <TextArea rows={8} />
          </Form.Item>
          <Form.Item name="folder_ids" label="Folders">
            <Select mode="multiple" options={folders.map((folder) => ({ label: folder.name, value: folder.id }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Move Selected Requirements"
        open={bulkFolderModalOpen}
        onOk={handleBulkMove}
        onCancel={() => setBulkFolderModalOpen(false)}
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

export default RequirementTab;
