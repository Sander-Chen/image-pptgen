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
  Upload,
  Select,
  Tag,
} from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined, UploadOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { Color, Folder, LifecycleStatus } from '../types';
import type { UploadFile } from 'antd/es/upload/interface';
import DataFolderControls from './DataFolderControls';

const { TextArea } = Input;

const ColorTab: React.FC = () => {
  const [items, setItems] = useState<Color[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [extractModalOpen, setExtractModalOpen] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [fileList, setFileList] = useState<UploadFile[]>([]);
  const [extractedColor, setExtractedColor] = useState<Color | null>(null);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [status, setStatus] = useState<LifecycleStatus>('active');
  const [folderId, setFolderId] = useState<number | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [bulkFolderModalOpen, setBulkFolderModalOpen] = useState(false);
  const [bulkFolderIds, setBulkFolderIds] = useState<number[]>([]);
  const [form] = Form.useForm();
  const [extractForm] = Form.useForm();

  const fetchItems = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.colors.list({ status, folder_id: folderId });
      setItems(data);
      setSelectedRowKeys([]);
    } catch (err: unknown) {
      message.error(`Failed to load colors: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [folderId, status]);

  const fetchFolders = useCallback(async () => {
    try {
      setFolders(await api.folders.list('color'));
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

  const openEdit = (record: Color) => {
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
        await api.colors.update(editingId, values);
        await api.colors.assignFolders(editingId, folder_ids);
        message.success('Color palette updated');
      } else {
        const created = await api.colors.create(values);
        await api.colors.assignFolders(created.id, folder_ids);
        message.success('Color palette created');
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
      await api.colors.delete(id);
      message.success('Color palette moved to Recycle Bin');
      fetchItems();
    } catch (err: unknown) {
      message.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleArchive = async (id: number) => {
    try {
      await api.colors.archive(id);
      message.success('Color palette archived');
      fetchItems();
    } catch (err: unknown) {
      message.error(`Archive failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleRestore = async (id: number) => {
    try {
      await api.colors.restore(id);
      message.success('Color palette restored');
      fetchItems();
    } catch (err: unknown) {
      message.error(`Restore failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleForceDelete = async (id: number) => {
    try {
      await api.colors.forceDelete(id);
      message.success('Color palette exported to historical data');
      fetchItems();
    } catch (err: unknown) {
      message.error(`Force delete failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const selectedIds = selectedRowKeys.map(Number);

  const runBulkAction = async (action: 'archive' | 'delete' | 'restore' | 'force_delete') => {
    if (!selectedIds.length) return;
    try {
      const result = await api.bulkActions.apply({ entity_type: 'color', action, ids: selectedIds });
      const failures = result.results.filter((item) => item.status === 'error');
      if (failures.length) message.warning(`${failures.length} color palette(s) could not be updated`);
      else message.success(`${selectedIds.length} color palette(s) updated`);
      fetchItems();
    } catch (err: unknown) {
      message.error(`Bulk action failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleBulkMove = async () => {
    if (!selectedIds.length) return;
    try {
      await api.bulkActions.apply({
        entity_type: 'color',
        action: 'move_to_folder',
        ids: selectedIds,
        folder_ids: bulkFolderIds,
      });
      message.success(`${selectedIds.length} color palette(s) moved`);
      setBulkFolderModalOpen(false);
      setBulkFolderIds([]);
      fetchItems();
    } catch (err: unknown) {
      message.error(`Move failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const openExtract = () => {
    extractForm.resetFields();
    setFileList([]);
    setExtractedColor(null);
    setExtractModalOpen(true);
  };

  const handleExtract = async () => {
    try {
      const values = await extractForm.validateFields();
      const uploadFile = fileList[0]?.originFileObj;
      if (!uploadFile) {
        message.error('Choose an image first');
        return;
      }
      const formData = new FormData();
      formData.append('title', values.title);
      formData.append('image', uploadFile);
      setExtracting(true);
      const color = await api.colors.extractFromImage(formData);
      setExtractedColor(color);
      message.success('Color palette extracted');
      fetchItems();
    } catch (err: unknown) {
      if (err instanceof Error) {
        message.error(err.message);
      }
    } finally {
      setExtracting(false);
    }
  };

  const columns = [
    {
      title: 'Title',
      dataIndex: 'title',
      key: 'title',
      width: 200,
      sorter: (a: Color, b: Color) => a.title.localeCompare(b.title),
    },
    {
      title: 'Content',
      dataIndex: 'content',
      key: 'content',
      render: (v: string) => (
        <Tooltip title={v && v.length > 500 ? v.slice(0, 500) + '...' : v}>
          <pre
            style={{
              fontFamily: 'monospace',
              fontSize: 12,
              margin: 0,
              maxHeight: 100,
              overflow: 'auto',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {v && v.length > 200 ? v.slice(0, 200) + '...' : v}
          </pre>
        </Tooltip>
      ),
    },
    {
      title: 'Source',
      dataIndex: 'source_type',
      key: 'source_type',
      width: 100,
      render: (v: string | null | undefined) => v || 'manual',
    },
    {
      title: 'Created',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      sorter: (a: Color, b: Color) =>
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
      render: (_: unknown, record: Color) => (
        <Space>
          {status === 'active' ? (
            <>
              <Tooltip title="Edit color palette">
                <Button aria-label="Edit color palette" icon={<EditOutlined />} size="small" onClick={() => openEdit(record)} />
              </Tooltip>
              <Button size="small" onClick={() => handleArchive(record.id)}>Archive</Button>
              <Popconfirm title="Move this color palette to Recycle Bin?" onConfirm={() => handleDelete(record.id)}>
                <Button aria-label="Move color palette to Recycle Bin" icon={<DeleteOutlined />} size="small" danger />
              </Popconfirm>
            </>
          ) : (
            <>
              <Button size="small" onClick={() => handleRestore(record.id)}>Restore</Button>
              {status === 'recycle_bin' ? (
                <Popconfirm title="Force delete this color palette?" description="It will be exported to historical_data and hidden from the product UI." onConfirm={() => handleForceDelete(record.id)}>
                  <Button size="small" danger>Force Delete</Button>
                </Popconfirm>
              ) : (
                <Popconfirm title="Move archived color palette to Recycle Bin?" onConfirm={() => handleDelete(record.id)}>
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
        scope="color"
        folders={folders}
        status={status}
        folderId={folderId}
        onStatusChange={setStatus}
        onFolderChange={setFolderId}
        onFoldersChanged={fetchFolders}
      />

      <div style={{ marginBottom: 16 }}>
        <Space>
          <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
            Add Color Palette
          </Button>
          <Button icon={<UploadOutlined />} onClick={openExtract}>
            Extract from image
          </Button>
        </Space>
      </div>

      {selectedIds.length > 0 && (
        <div className="bulk-action-bar">
          <span>{selectedIds.length} selected</span>
          <Button size="small" onClick={() => setBulkFolderModalOpen(true)}>Move to Folder</Button>
          {status === 'active' && (
            <>
              <Button size="small" onClick={() => runBulkAction('archive')}>Archive</Button>
              <Popconfirm title="Move selected color palettes to Recycle Bin?" onConfirm={() => runBulkAction('delete')}>
                <Button size="small" danger>Delete</Button>
              </Popconfirm>
            </>
          )}
          {status === 'archived' && (
            <>
              <Button size="small" onClick={() => runBulkAction('restore')}>Restore</Button>
              <Popconfirm title="Move selected archived color palettes to Recycle Bin?" onConfirm={() => runBulkAction('delete')}>
                <Button size="small" danger>Delete</Button>
              </Popconfirm>
            </>
          )}
          {status === 'recycle_bin' && (
            <>
              <Button size="small" onClick={() => runBulkAction('restore')}>Restore</Button>
              <Popconfirm title="Force delete selected color palettes?" onConfirm={() => runBulkAction('force_delete')}>
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
        scroll={{ x: 980 }}
      />

      <Modal
        title={editingId ? 'Edit Color Palette' : 'Add Color Palette'}
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
            label="Content (XML palette data)"
            rules={[{ required: true }]}
          >
            <TextArea rows={10} style={{ fontFamily: 'monospace' }} />
          </Form.Item>
          <Form.Item name="folder_ids" label="Folders">
            <Select mode="multiple" options={folders.map((folder) => ({ label: folder.name, value: folder.id }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Move Selected Color Palettes"
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

      <Modal
        title="Extract Color Palette"
        open={extractModalOpen}
        onCancel={() => setExtractModalOpen(false)}
        onOk={extractedColor ? () => setExtractModalOpen(false) : handleExtract}
        okText={extractedColor ? 'Close' : 'Extract'}
        confirmLoading={extracting}
        width={680}
        forceRender
        destroyOnHidden
      >
        <Form form={extractForm} layout="vertical">
          <Form.Item
            name="title"
            label="Title"
            rules={[{ required: true }]}
          >
            <Input />
          </Form.Item>
          <Form.Item label="Image" required>
            <Upload
              accept="image/*"
              beforeUpload={() => false}
              maxCount={1}
              fileList={fileList}
              onChange={({ fileList: nextFileList }) => setFileList(nextFileList)}
            >
              <Button icon={<UploadOutlined />}>Choose Image</Button>
            </Upload>
          </Form.Item>
          {extractedColor && (
            <Form.Item label="XML Preview">
              <TextArea
                value={extractedColor.content}
                rows={10}
                readOnly
                style={{ fontFamily: 'monospace' }}
              />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  );
};

export default ColorTab;
