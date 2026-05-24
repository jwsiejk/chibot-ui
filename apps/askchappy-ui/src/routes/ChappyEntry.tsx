import React from 'react';
import { useNavigate } from 'react-router-dom';
import { AuthGate } from '../components/AuthGate';
import { AppNav } from '../components/AppNav';
import { ChappyEntryScreen } from '../chappy/ChappyEntryScreen';
import { createLocalSession } from '../../../../services/askchappy-api/src/api/server';

export const ChappyEntry = () => {
  const navigate = useNavigate();

  const onStartOpenQa = () => {
    const session = createLocalSession();
    navigate(`/chappy/session/${session.session_id}`);
  };

  return (
    <AuthGate>
      <main className="app-shell">
        <AppNav />
        <ChappyEntryScreen onStartOpenQa={onStartOpenQa} />
      </main>
    </AuthGate>
  );
};
