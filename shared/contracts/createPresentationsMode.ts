export const CREATE_PRESENTATIONS_INTRO_MESSAGE =
  "Create Presentations Mode is ready. I’ll walk you through building a professional deck step by step. What kind of presentation are we creating?";

export const createPresentationModeState = () => ({
  active: true as const,
  mode: 'create_presentations' as const,
  step: 'intro' as const,
  deckBrief: {
    schema_version: '1.0' as const,
    mode: 'create_presentations' as const,
    status: 'draft' as const,
  },
  messages: [] as string[],
  awaitingUserInput: true,
});
