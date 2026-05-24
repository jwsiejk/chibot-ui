import React from 'react';
import { Link } from 'react-router-dom';
import { ROUTES } from '../../../../shared/contracts/askchappy';
import { AUTH_ROLES } from '../../../../shared/contracts/auth';
import { useAuth } from '../auth/authState';

export const AppNav = () => {
  const { user } = useAuth();
  const isAdmin = user?.role === AUTH_ROLES[0];

  return (
    <nav className="card app-nav" aria-label="app navigation">
      <Link to={ROUTES.chappy} className="brand">AskChappy</Link>
      <div className="nav-links">
        <Link to={ROUTES.chappy} className="nav-link">Chappy Room</Link>
        {isAdmin ? (
          <>
            <Link to={ROUTES.admin} className="nav-link">Admin</Link>
            <Link to={ROUTES.adminVoice} className="nav-link">Voice Studio</Link>
            <Link to={ROUTES.adminAvatar} className="nav-link">Avatar</Link>
          </>
        ) : null}
      </div>
    </nav>
  );
};
