import React from 'react';
import { createRoot } from 'react-dom/client';
import 'antd/dist/reset.css';
import '../../index.css';
import '../../App.css';
import './evaluationPrototype.css';
import EvaluationPrototype from './EvaluationPrototype';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <EvaluationPrototype />
  </React.StrictMode>,
);
