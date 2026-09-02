import React, { useState, useEffect, useCallback } from 'react';
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  Select,
  Space,
  Tooltip,
  Popconfirm,
  message,
  Tag,
} from 'antd';
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  ScissorOutlined,
} from '@ant-design/icons';
import { api } from '../api';
import type { Deck, Folder, LifecycleStatus } from '../types';
import SlideList from './SlideList';
import DataFolderControls from './DataFolderControls';

const { TextArea } = Input;

const DeckTab: React.FC = () => {
  const [decks, setDecks] = useState<Deck[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [splitLoadingId, setSplitLoadingId] = useState<number | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [folders, setFolders] = useState<Folder[]>([]);
  const [status, setStatus] = useState<LifecycleStatus>('active');
  const [folderId, setFolderId] = useState<number | null>(null);
  const [selectedRowKeys, setSelectedRowKeys] = useState<React.Key[]>([]);
  const [bulkFolderModalOpen, setBulkFolderModalOpen] = useState(false);
  const [bulkFolderIds, setBulkFolderIds] = useState<number[]>([]);
  const [form] = Form.useForm();

  const fetchDecks = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.decks.list({ status, folder_id: folderId });
      setDecks(data);
      setSelectedRowKeys([]);
    } catch (err: unknown) {
      message.error(`Failed to load decks: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [folderId, status]);

  const fetchFolders = useCallback(async () => {
    try {
      setFolders(await api.folders.list('deck'));
    } catch (err: unknown) {
      message.error(`Failed to load folders: ${err instanceof Error ? err.message : String(err)}`);
    }
  }, []);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchDecks();
      void fetchFolders();
    });
  }, [fetchDecks, fetchFolders]);

  const openAdd = () => {
    setEditingId(null);
    form.resetFields();
    form.setFieldsValue({ folder_ids: folderId ? [folderId] : [] });
    setModalOpen(true);
  };

  const openEdit = (record: Deck) => {
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
        await api.decks.update(editingId, values);
        await api.decks.assignFolders(editingId, folder_ids);
        message.success('Deck updated');
      } else {
        const created = await api.decks.create(values);
        await api.decks.assignFolders(created.id, folder_ids);
        message.success('Deck created');
      }
      setModalOpen(false);
      fetchDecks();
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
      await api.decks.delete(id);
      message.success('Deck moved to Recycle Bin');
      fetchDecks();
    } catch (err: unknown) {
      message.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleArchive = async (id: number) => {
    try {
      await api.decks.archive(id);
      message.success('Deck archived');
      fetchDecks();
    } catch (err: unknown) {
      message.error(`Archive failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleRestore = async (id: number) => {
    try {
      await api.decks.restore(id);
      message.success('Deck restored');
      fetchDecks();
    } catch (err: unknown) {
      message.error(`Restore failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleForceDelete = async (id: number) => {
    try {
      await api.decks.forceDelete(id);
      message.success('Deck exported to historical data');
      fetchDecks();
    } catch (err: unknown) {
      message.error(`Force delete failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const selectedIds = selectedRowKeys.map(Number);

  const runBulkAction = async (action: 'archive' | 'delete' | 'restore' | 'force_delete') => {
    if (!selectedIds.length) return;
    try {
      const result = await api.bulkActions.apply({ entity_type: 'deck', action, ids: selectedIds });
      const failures = result.results.filter((item) => item.status === 'error');
      if (failures.length) message.warning(`${failures.length} deck(s) could not be updated`);
      else message.success(`${selectedIds.length} deck(s) updated`);
      fetchDecks();
    } catch (err: unknown) {
      message.error(`Bulk action failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleBulkMove = async () => {
    if (!selectedIds.length) return;
    try {
      await api.bulkActions.apply({
        entity_type: 'deck',
        action: 'move_to_folder',
        ids: selectedIds,
        folder_ids: bulkFolderIds,
      });
      message.success(`${selectedIds.length} deck(s) moved`);
      setBulkFolderModalOpen(false);
      setBulkFolderIds([]);
      fetchDecks();
    } catch (err: unknown) {
      message.error(`Move failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const handleSplit = async (id: number) => {
    setSplitLoadingId(id);
    try {
      const result = await api.decks.split(id);
      message.success(`Split into ${result.slides.length} slides`);
      setRefreshKey((k) => k + 1);
      fetchDecks();
    } catch (err: unknown) {
      message.error(`Split failed: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setSplitLoadingId(null);
    }
  };

  const columns = [
    {
      title: 'Title',
      dataIndex: 'title',
      key: 'title',
      sorter: (a: Deck, b: Deck) => a.title.localeCompare(b.title),
    },
    { title: 'Slides', dataIndex: 'slide_count', key: 'slide_count', width: 80 },
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
      title: 'Created',
      dataIndex: 'created_at',
      key: 'created_at',
      width: 180,
      sorter: (a: Deck, b: Deck) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
      render: (v: string) => v ? new Date(v).toLocaleString() : '-',
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 200,
      render: (_: unknown, record: Deck) => (
        <Space>
          {status === 'active' ? (
            <>
              <Tooltip title="Edit deck">
                <Button aria-label="Edit deck" icon={<EditOutlined />} size="small" onClick={() => openEdit(record)} />
              </Tooltip>
              <Tooltip title="Split deck into slides">
                <Button aria-label="Split deck into slides" icon={<ScissorOutlined />} size="small" loading={splitLoadingId === record.id} onClick={() => handleSplit(record.id)} />
              </Tooltip>
              <Button size="small" onClick={() => handleArchive(record.id)}>Archive</Button>
              <Popconfirm title="Move this deck to Recycle Bin?" onConfirm={() => handleDelete(record.id)}>
                <Button aria-label="Move deck to Recycle Bin" icon={<DeleteOutlined />} size="small" danger />
              </Popconfirm>
            </>
          ) : (
            <>
              <Button size="small" onClick={() => handleRestore(record.id)}>Restore</Button>
              {status === 'recycle_bin' ? (
                <Popconfirm title="Force delete this deck?" description="It will be exported to historical_data and hidden from the product UI." onConfirm={() => handleForceDelete(record.id)}>
                  <Button size="small" danger>Force Delete</Button>
                </Popconfirm>
              ) : (
                <Popconfirm title="Move archived deck to Recycle Bin?" onConfirm={() => handleDelete(record.id)}>
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
        scope="deck"
        folders={folders}
        status={status}
        folderId={folderId}
        onStatusChange={setStatus}
        onFolderChange={setFolderId}
        onFoldersChanged={fetchFolders}
      />

      <div style={{ marginBottom: 16 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={openAdd}>
          Add Deck
        </Button>
      </div>

      {selectedIds.length > 0 && (
        <div className="bulk-action-bar">
          <span>{selectedIds.length} selected</span>
          <Button size="small" onClick={() => setBulkFolderModalOpen(true)}>Move to Folder</Button>
          {status === 'active' && (
            <>
              <Button size="small" onClick={() => runBulkAction('archive')}>Archive</Button>
              <Popconfirm title="Move selected decks to Recycle Bin?" onConfirm={() => runBulkAction('delete')}>
                <Button size="small" danger>Delete</Button>
              </Popconfirm>
            </>
          )}
          {status === 'archived' && (
            <>
              <Button size="small" onClick={() => runBulkAction('restore')}>Restore</Button>
              <Popconfirm title="Move selected archived decks to Recycle Bin?" onConfirm={() => runBulkAction('delete')}>
                <Button size="small" danger>Delete</Button>
              </Popconfirm>
            </>
          )}
          {status === 'recycle_bin' && (
            <>
              <Button size="small" onClick={() => runBulkAction('restore')}>Restore</Button>
              <Popconfirm title="Force delete selected decks?" onConfirm={() => runBulkAction('force_delete')}>
                <Button size="small" danger>Force Delete</Button>
              </Popconfirm>
            </>
          )}
          <Button size="small" onClick={() => setSelectedRowKeys([])}>Clear</Button>
        </div>
      )}

      <Table
        className="responsive-table"
        dataSource={decks}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={false}
        rowSelection={{ selectedRowKeys, onChange: setSelectedRowKeys }}
        scroll={{ x: 980 }}
        expandable={{
          expandedRowRender: (record) => status === 'active' ? <SlideList key={refreshKey} deckId={record.id} /> : null,
        }}
      />

      <Modal
        title={editingId ? 'Edit Deck' : 'Add Deck'}
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
            <TextArea rows={10} />
          </Form.Item>
          <Form.Item name="folder_ids" label="Folders">
            <Select mode="multiple" options={folders.map((folder) => ({ label: folder.name, value: folder.id }))} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="Move Selected Decks"
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

export default DeckTab;
