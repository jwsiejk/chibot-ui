export type ApiHealth = { service: 'askchappy-api'; status: 'placeholder' };

export function getHealth(): ApiHealth {
  return { service: 'askchappy-api', status: 'placeholder' };
}
