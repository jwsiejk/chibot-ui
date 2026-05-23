import React from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { ROUTES } from '../../../../shared/contracts/askchappy';
import { AuthProvider } from '../auth/authState';
import { AdminRoute } from '../auth/AdminRoute';
import { ChappyEntry } from '../routes/ChappyEntry';
import { ChappySession } from '../routes/ChappySession';
import { ChappySummary } from '../routes/ChappySummary';
import { PlaceholderPage } from '../routes/PlaceholderPage';

export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path={ROUTES.home} element={<Navigate to={ROUTES.chappy} replace />} />
        <Route path={ROUTES.chappy} element={<ChappyEntry />} />
        <Route path={ROUTES.chappySession} element={<ChappySession />} />
        <Route path={ROUTES.chappySummary} element={<ChappySummary />} />
        <Route path={ROUTES.dev} element={<PlaceholderPage title="Diagnostics placeholder (hidden from main flow)" />} />
        <Route path={ROUTES.admin} element={<AdminRoute title="Admin placeholder" />} />
        <Route path={ROUTES.adminVoice} element={<AdminRoute title="Admin Voice Studio placeholder" />} />
        <Route path={ROUTES.adminAvatar} element={<AdminRoute title="Admin Avatar placeholder" />} />
        <Route path="*" element={<PlaceholderPage title="Route not found" />} />
      </Routes>
    </AuthProvider>
  );
}
