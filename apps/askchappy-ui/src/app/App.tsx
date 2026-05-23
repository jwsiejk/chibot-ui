import React from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { ROUTES } from '../../../../shared/contracts/askchappy';
import { AUTH_ROLES } from '../../../../shared/contracts/auth';
import { AuthProvider, useAuth } from '../auth/authState';
import { AuthGate } from '../components/AuthGate';
import { AppNav } from '../components/AppNav';

const Placeholder = ({ title }: { title: string }) => (
  <main>
    <h1>{title}</h1>
  </main>
);

const NotAuthorized = () => (
  <main>
    <h1>Not authorized</h1>
    <p>This admin area is restricted in MVP/demo mode.</p>
  </main>
);

const AdminRoute = ({ title }: { title: string }) => {
  const { user } = useAuth();
  if (!user || user.role !== AUTH_ROLES[0]) {
    return <NotAuthorized />;
  }
  return <Placeholder title={title} />;
};

const ChappyEntry = () => (
  <AuthGate>
    <main>
      <h1>AskChappy entry placeholder</h1>
      <AppNav />
      <p>Welcome to AskChappy MVP/demo entry.</p>
    </main>
  </AuthGate>
);

const ChappySession = () => (
  <main>
    <h1>AskChappy Zoom-like session placeholder</h1>
  </main>
);

const ChappySummary = () => (
  <main>
    <h1>AskChappy recap placeholder</h1>
  </main>
);

export function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path={ROUTES.home} element={<Navigate to={ROUTES.chappy} replace />} />
        <Route path={ROUTES.chappy} element={<ChappyEntry />} />
        <Route path={ROUTES.chappySession} element={<ChappySession />} />
        <Route path={ROUTES.chappySummary} element={<ChappySummary />} />
        <Route path={ROUTES.dev} element={<Placeholder title="Diagnostics placeholder (hidden from main flow)" />} />
        <Route path={ROUTES.admin} element={<AdminRoute title="Admin placeholder" />} />
        <Route path={ROUTES.adminVoice} element={<AdminRoute title="Admin Voice Studio placeholder" />} />
        <Route path={ROUTES.adminAvatar} element={<AdminRoute title="Admin Avatar placeholder" />} />
        <Route path="*" element={<Placeholder title="Route not found" />} />
      </Routes>
    </AuthProvider>
  );
}
