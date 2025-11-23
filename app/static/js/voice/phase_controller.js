export const PHASE = {
  Boot: "boot",
  Greet: "greet",
  ConversationReady: "conversation_ready",
  UserTurn: "user_turn",
  Closing: "closing",
  Closed: "closed",
};

export function createVoicePhaseController({ log } = {}) {
  let phase = PHASE.Boot;

  const logEvent = (event, reason, uttId) => {
    try {
      if (typeof log === "function") {
        log("voice.phase", {
          phase,
          event,
          reason: typeof reason === "undefined" ? null : reason,
          uttId: typeof uttId === "undefined" ? null : uttId,
        });
      }
    } catch (_) {}
  };

  return {
    getPhase() {
      return phase;
    },

    markGreetStart(uttId) {
      phase = PHASE.Greet;
      logEvent("mark_greet_start", undefined, uttId);
    },

    markGreetEnd(uttId) {
      phase = PHASE.ConversationReady;
      logEvent("mark_greet_end", undefined, uttId);
    },

    enterConversation(reason) {
      phase = PHASE.UserTurn;
      logEvent("enter_conversation", reason);
    },

    endUserTurn(reason) {
      phase = PHASE.ConversationReady;
      logEvent("end_user_turn", reason);
    },

    beginClosing(reason) {
      phase = PHASE.Closing;
      logEvent("begin_closing", reason);
    },

    markClosed(reason) {
      phase = PHASE.Closed;
      logEvent("mark_closed", reason);
    },
  };
}
