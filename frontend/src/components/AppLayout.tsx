import React, { useState } from 'react';
import { Button, Layout, Menu, Tooltip } from 'antd';
import {
  DatabaseOutlined,
  ThunderboltOutlined,
  HistoryOutlined,
  MenuFoldOutlined,
  MenuUnfoldOutlined,
} from '@ant-design/icons';
import { useNavigate, useLocation, Outlet } from 'react-router-dom';

const { Sider, Content } = Layout;

const menuItems = [
  { key: '/data', icon: <DatabaseOutlined />, label: 'Data' },
  { key: '/generate', icon: <ThunderboltOutlined />, label: 'Generate' },
  { key: '/history', icon: <HistoryOutlined />, label: 'History' },
];

const AppLayout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const [collapsed, setCollapsed] = useState(true);

  const selectedKey = menuItems.find((item) =>
    location.pathname.startsWith(item.key)
  )?.key || '/data';

  return (
    <Layout className="app-shell">
      <Sider
        className="app-sidebar"
        width={208}
        breakpoint="lg"
        collapsedWidth={72}
        collapsed={collapsed}
        onCollapse={setCollapsed}
      >
        <div className="app-brand">
          <span className="app-brand-full">Image PPT 3.0</span>
          <span className="app-brand-short">Image</span>
        </div>
        <Tooltip title={collapsed ? 'Expand navigation' : 'Collapse navigation'} placement="right">
          <Button
            className="sidebar-collapse-button"
            aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'}
            icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
            onClick={() => setCollapsed((value) => !value)}
          />
        </Tooltip>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[selectedKey]}
          items={menuItems}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout style={{ minWidth: 0 }}>
        <Content className="app-content">
          <Outlet />
        </Content>
      </Layout>
    </Layout>
  );
};

export default AppLayout;
