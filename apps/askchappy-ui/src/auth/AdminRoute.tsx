import React from 'react';
import { AUTH_ROLES } from '../../../../shared/contracts/auth';
import { useAuth } from './authState';
import { PlaceholderPage } from '../routes/PlaceholderPage';

const NotAuthorized = () => (
  <main>
    <h1>Not authorized</h1>
    <p>This admin area is restricted in local MVP mode.</p>
  </main>
);

export const AdminRoute = ({ title }: { title: string }) => {
  const { user } = useAuth();

  if (!user || user.role !== AUTH_ROLES[0]) {
    return <NotAuthorized />;
  }

  return <PlaceholderPage title={title} />;
};
