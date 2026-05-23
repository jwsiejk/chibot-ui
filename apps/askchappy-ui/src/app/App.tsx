import React from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { ROUTES } from '../../../../shared/contracts/askchappy';

const Placeholder = ({ title }: { title: string }) => <main><h1>{title}</h1></main>;

export function App() {
  return (
    <Routes>
      <Route path={ROUTES.home} element={<Navigate to={ROUTES.chappy} replace />} />
      <Route path={ROUTES.chappy} element={<Placeholder title="AskChappy entry placeholder" />} />
      <Route path={ROUTES.chappySession} element={<Placeholder title="AskChappy Zoom-like session placeholder" />} />
      <Route path={ROUTES.chappySummary} element={<Placeholder title="AskChappy recap placeholder" />} />
      <Route path={ROUTES.dev} element={<Placeholder title="Diagnostics placeholder (hidden from main flow)" />} />
      <Route path={ROUTES.admin} element={<Placeholder title="Admin placeholder" />} />
      <Route path={ROUTES.adminVoice} element={<Placeholder title="Admin Voice Studio placeholder" />} />
      <Route path={ROUTES.adminAvatar} element={<Placeholder title="Admin Avatar placeholder" />} />
      <Route path="*" element={<Placeholder title="Route not found" />} />
    </Routes>
  );
}
