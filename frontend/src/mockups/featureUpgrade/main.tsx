import React from 'react';
import { createRoot } from 'react-dom/client';
import 'antd/dist/reset.css';
import '../../index.css';
import '../../App.css';
import './featureUpgradeMock.css';
import FeatureUpgradeMockApp from './FeatureUpgradeMockApp';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <FeatureUpgradeMockApp />
  </React.StrictMode>,
);
