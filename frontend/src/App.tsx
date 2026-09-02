import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import AppLayout from './components/AppLayout';
import DataPage from './pages/DataPage';
import GeneratePage from './pages/GeneratePage';
import BatchOverviewPage from './pages/BatchOverviewPage';
import HistoryPage from './pages/HistoryPage';
import PresentationPreviewPage from './pages/PresentationPreviewPage';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/history/run/:runId/preview" element={<PresentationPreviewPage />} />
        <Route element={<AppLayout />}>
          <Route path="/data" element={<DataPage />} />
          <Route path="/generate" element={<GeneratePage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/history/batch/:batchId" element={<BatchOverviewPage />} />
          <Route path="/history/run/:id" element={<HistoryPage />} />
          <Route path="/history/:id" element={<HistoryPage />} />
          <Route path="*" element={<Navigate to="/data" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
