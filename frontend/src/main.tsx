import React from 'react';
import ReactDOM from 'react-dom/client';
import { ConfigProvider } from 'antd';
import zhCN from 'antd/locale/zh_CN';
import App from './App';
import './index.css';
import './App.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          colorPrimary: '#1463ff',
          colorInfo: '#1463ff',
          colorSuccess: '#16a34a',
          colorWarning: '#f97316',
          colorError: '#ef4444',
          colorText: '#0f172a',
          colorTextSecondary: '#64748b',
          colorBgLayout: '#f8fafc',
          borderRadius: 8,
          fontFamily:
            'Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
        },
        components: {
          Button: {
            controlHeight: 38,
            borderRadius: 8,
          },
          Input: {
            controlHeight: 40,
          },
          Select: {
            controlHeight: 40,
          },
          Table: {
            headerBg: '#fbfdff',
            headerColor: '#0f172a',
            rowHoverBg: '#eff6ff',
            borderColor: '#e5e7eb',
          },
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>
);
