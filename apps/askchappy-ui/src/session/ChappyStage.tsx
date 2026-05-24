import React from 'react';
import type { SessionState } from '../../../../shared/contracts/session';
import { getChappyAvatarRuntimeStatus, getChappyAvatarStateConfig } from '../avatar/avatarState';

export const ChappyStage = ({ state }: { state: SessionState }) => {
  const stateConfig = getChappyAvatarStateConfig(state);
  const avatarStatus = getChappyAvatarRuntimeStatus(state);

  return (
    <section className="card stage-card" aria-label="chappy stage">
      <h2>Chappy Stage</h2>
      <p>Chappy avatar stage placeholder</p>
      <p>{stateConfig.label}</p>
      <p>{stateConfig.description}</p>
      <p>State: {stateConfig.state}</p>
      <p>Avatar asset status: {avatarStatus.avatar_asset_status}</p>
      <p>Supports visemes: {String(avatarStatus.supports_visemes)}</p>
      <p>Supports speaking animation: {String(avatarStatus.supports_speaking_animation)}</p>
    </section>
  );
};
