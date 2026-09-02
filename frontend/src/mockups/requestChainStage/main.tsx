import React from 'react';
import { createRoot } from 'react-dom/client';
import 'antd/dist/reset.css';
import '../../index.css';
import '../../App.css';
import './requestChainStageMockup.css';
import RequestChainStageMockup from './RequestChainStageMockup';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RequestChainStageMockup />
  </React.StrictMode>,
);
