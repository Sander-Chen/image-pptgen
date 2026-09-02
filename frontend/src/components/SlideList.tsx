import React, { useState, useEffect, useCallback } from 'react';
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  InputNumber,
  Space,
  Tooltip,
  Popconfirm,
  message,
} from 'antd';
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons';
import { api } from '../api';
import type { Slide } from '../types';

const { TextArea } = Input;

interface SlideListProps {
  deckId: number;
}

const SlideList: React.FC<SlideListProps> = ({ deckId }) => {
  const [slides, setSlides] = useState<Slide[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingSlide, setEditingSlide] = useState<Slide | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm();

  const fetchSlides = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.decks.getSlides(deckId);
      setSlides(data);
    } catch (err: unknown) {
      message.error(`Failed to load slides: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoading(false);
    }
  }, [deckId]);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchSlides();
    });
  }, [fetchSlides]);

  const openAdd = () => {
    setEditingSlide(null);
    form.resetFields();
    form.setFieldsValue({ position: slides.length + 1 });
    setModalOpen(true);
  };

  const openEdit = (slide: Slide) => {
    setEditingSlide(slide);
    form.setFieldsValue({
      title: slide.title,
      content: slide.content,
      position: slide.position,
    });
    setModalOpen(true);
  };

  const handleSave = async () => {
    try {
      const values = await form.validateFields();
      setSaving(true);
      if (editingSlide) {
        await api.slides.update(editingSlide.id, values);
        message.success('Slide updated');
      } else {
        await api.slides.create(deckId, values);
        message.success('Slide created');
      }
      setModalOpen(false);
      fetchSlides();
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
      await api.slides.delete(id);
      message.success('Slide deleted');
      fetchSlides();
    } catch (err: unknown) {
      message.error(`Delete failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  };

  const columns = [
    {
      title: 'Position',
      dataIndex: 'position',
      key: 'position',
      width: 80,
      sorter: (a: Slide, b: Slide) => a.position - b.position,
    },
    { title: 'Title', dataIndex: 'title', key: 'title' },
    {
      title: 'Content',
      dataIndex: 'content',
      key: 'content',
      ellipsis: true,
      render: (v: string) => (v && v.length > 100 ? v.slice(0, 100) + '...' : v),
    },
    {
      title: 'Actions',
      key: 'actions',
      width: 120,
      render: (_: unknown, record: Slide) => (
        <Space>
          <Tooltip title="Edit slide">
            <Button
              aria-label="Edit slide"
              icon={<EditOutlined />}
              size="small"
              onClick={() => openEdit(record)}
            />
          </Tooltip>
          <Popconfirm
            title="Delete this slide?"
            onConfirm={() => handleDelete(record.id)}
          >
            <Button aria-label="Delete slide" icon={<DeleteOutlined />} size="small" danger />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div>
      <div style={{ marginBottom: 8 }}>
        <Button size="small" icon={<PlusOutlined />} onClick={openAdd}>
          Add Slide
        </Button>
      </div>
      <Table
        dataSource={slides}
        columns={columns}
        rowKey="id"
        loading={loading}
        pagination={false}
        size="small"
      />

      <Modal
        title={editingSlide ? 'Edit Slide' : 'Add Slide'}
        open={modalOpen}
        onOk={handleSave}
        onCancel={() => setModalOpen(false)}
        confirmLoading={saving}
        width={640}
        forceRender
        destroyOnHidden
      >
        <Form form={form} layout="vertical">
          <Form.Item name="position" label="Position">
            <InputNumber min={1} />
          </Form.Item>
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
        </Form>
      </Modal>
    </div>
  );
};

export default SlideList;
