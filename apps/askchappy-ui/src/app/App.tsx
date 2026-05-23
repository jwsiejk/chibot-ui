import React from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { ROUTES } from '../../../../shared/contracts/askchappy';
import { AuthProvider } from '../auth/authState';
import { AdminRoute } from '../auth/AdminRoute';
import { AdminDashboardPage } from '../admin/AdminDashboardPage';
import { VoiceStudioPage } from '../admin/voice/VoiceStudioPage';
import { AvatarAdminPage } from '../admin/avatar/AvatarAdminPage';
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
        <Route path={ROUTES.admin} element={<AdminRoute><AdminDashboardPage /></AdminRoute>} />
        <Route path={ROUTES.adminVoice} element={<AdminRoute><VoiceStudioPage /></AdminRoute>} />
        <Route path={ROUTES.adminAvatar} element={<AdminRoute><AvatarAdminPage /></AdminRoute>} />
        <Route path="*" element={<PlaceholderPage title="Route not found" />} />
      </Routes>
    </AuthProvider>
  );
}
