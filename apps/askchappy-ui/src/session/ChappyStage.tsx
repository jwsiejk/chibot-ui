import React from 'react';
import type { SessionState } from '../../../../shared/contracts/session';
import { getChappyAvatarStateConfig } from '../avatar/avatarState';

export const ChappyStage = ({ state }: { state: SessionState }) => {
  const stateConfig = getChappyAvatarStateConfig(state);

  return (
    <section className="stage-card chappy-video-tile" aria-label="chappy stage">
      <div className="stage-state-row">
        <span className={`state-dot stage-status-pill state-${stateConfig.state}`}>
          {stateConfig.label}
        </span>
      </div>

      <div className="chappy-stage-center">
        <div className="chappy-avatar-placeholder" aria-label="chappy avatar placeholder">C</div>
        <h2>Chappy</h2>
        <p>{stateConfig.description}</p>
      </div>
    </section>
  );
};
