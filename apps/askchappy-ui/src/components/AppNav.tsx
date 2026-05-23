import React from 'react';
import { Link } from 'react-router-dom';
import { ROUTES } from '../../../../shared/contracts/askchappy';
import { AUTH_ROLES } from '../../../../shared/contracts/auth';
import { useAuth } from '../auth/authState';

export const AppNav = () => {
  const { user } = useAuth();
  const isAdmin = user?.role === AUTH_ROLES[0];

  return (
    <nav aria-label="app navigation">
      <Link to={ROUTES.chappy}>Chappy</Link>
      {isAdmin ? (
        <>
          <Link to={ROUTES.admin}>Admin</Link>
          <Link to={ROUTES.adminVoice}>Voice Studio</Link>
          <Link to={ROUTES.adminAvatar}>Avatar</Link>
        </>
      ) : null}
    </nav>
  );
};
