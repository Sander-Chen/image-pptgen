import React from 'react';
import { createRoot } from 'react-dom/client';
import 'antd/dist/reset.css';
import '../../index.css';
import '../../App.css';
import './mockup.css';
import CreateBlankAntdNativeMockup from './CreateBlankAntdNativeMockup';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <CreateBlankAntdNativeMockup />
  </React.StrictMode>,
);
