import React from 'react';
import { createRoot } from 'react-dom/client';
import 'antd/dist/reset.css';
import '../../index.css';
import '../../App.css';
import './feedbackRoundPrototype.css';
import FeedbackRoundPrototype from './FeedbackRoundPrototype';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <FeedbackRoundPrototype />
  </React.StrictMode>,
);
