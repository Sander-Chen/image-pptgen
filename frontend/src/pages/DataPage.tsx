import React, { useEffect, useState } from 'react';
import { Button, Tag, message } from 'antd';
import { ReloadOutlined } from '@ant-design/icons';
import DeckTab from '../components/DeckTab';
import { api } from '../api';

const DataPage: React.FC = () => {
  const [deckCount, setDeckCount] = useState(0);
  const [loadingCounts, setLoadingCounts] = useState(false);

  const fetchCounts = async () => {
    setLoadingCounts(true);
    try {
      const decks = await api.decks.list({ status: 'active' });
      setDeckCount(decks.length);
    } catch (err: unknown) {
      message.error(`Failed to load data summary: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setLoadingCounts(false);
    }
  };

  useEffect(() => {
    queueMicrotask(() => {
      void fetchCounts();
    });
  }, []);

  return (
    <div className="work-surface">
      <div className="page-toolbar">
        <div>
          <h2>Decks</h2>
          <p className="toolbar-subtitle">Manage source decks and their slides.</p>
        </div>
        <div className="page-toolbar-actions">
          <Button icon={<ReloadOutlined />} loading={loadingCounts} onClick={fetchCounts} aria-label="Refresh data summary">
            Refresh
          </Button>
        </div>
      </div>
      <div className="module-summary-grid data-summary-grid">
        <div className="module-summary-item">
          <span className="module-summary-label">Decks</span>
          <strong>{deckCount}</strong>
          <Tag color="blue">active</Tag>
        </div>
      </div>
      <DeckTab />
    </div>
  );
};

export default DataPage;
