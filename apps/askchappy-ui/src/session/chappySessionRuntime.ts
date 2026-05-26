import * as serverApi from '../../../../services/askchappy-api/src/api/server';

export const chappySessionRuntime = {
  appendLocalUserTextMessage: serverApi.appendLocalUserTextMessage,
  generateLocalAssistantMessage: serverApi.generateLocalAssistantMessage,
  getLocalSession: serverApi.getLocalSession,
  getLocalTranscript: serverApi.getLocalTranscript,
  getLocalVoiceStatus: serverApi.getLocalVoiceStatus,
  setLocalSessionMode: serverApi.setLocalSessionMode,
  synthesizeLocalAssistantMessage: serverApi.synthesizeLocalAssistantMessage,
  transcribeLocalVoiceInput: serverApi.transcribeLocalVoiceInput,
};
