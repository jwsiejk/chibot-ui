
// addons-email-intents.js
// Non-invasive overlays: email triggers, account-team intent, transcript emailing,
// and gentle follow-ups. No redeclarations of existing variables.

(function(){
  // --- State (namespaced) ---
  const AC = {
    userEmail: "",
    lastAccountTeam: null, // {name, owner, type}
    conv: [],              // [{role:'user'|'assistant', text:string}]
  };

  // --- Utilities ---
  function lower(s){ return (s||"").toLowerCase(); }
  function trim(s){ return (s||"").trim(); }
  function naturalize(s){
    // sparse, human-sounding disfluencies
    const t = trim(s);
    if (!t) return t;
    const introChances = Math.random();
    let prefix = "";
    if (introChances < 0.12) prefix = "Hmm, ";
    else if (introChances < 0.22) prefix = "Okay— ";
    else if (introChances < 0.28) prefix = "Uh, ";
    return prefix + t;
  }

  // Hook user email from /api/me on load
  (async () => {
    try {
      const res = await fetch("/api/me").then(r=>r.json());
      if (res && res.ok && res.logged_in && res.user) {
        AC.userEmail = res.user.email || "";
      }
    } catch(_) {}
  })();

  // Hook UI.setUser and UI.appendBubble to track state
  try {
    if (window.UI) {
      const _origSetUser = UI.setUser;
      UI.setUser = function(email){
        AC.userEmail = email || AC.userEmail;
        return _origSetUser.apply(UI, arguments);
      };

      const _origAppend = UI.appendBubble;
      UI.appendBubble = function(role, text){
        try { AC.conv.push({role, text: String(text||"")}); } catch(_){}
        return _origAppend.apply(UI, arguments);
      };

      const _origShow = UI.show;
      UI.show = async function(section){
        const res = await _origShow.apply(UI, arguments);
        if (section === "profile"){
          try {
            const r = await fetch("/api/profile").then(x=>x.json());
            if (r && r.ok && r.user){
              const u = r.user;
              const emailEl = document.getElementById("profileEmail");
              const nameEl  = document.getElementById("profileName");
              const titleEl = document.getElementById("profileTitle");
              const regEl   = document.getElementById("profileRegion");
              if (emailEl) emailEl.value = u.email || "";
              if (nameEl)  nameEl.value  = u.name  || "";
              if (titleEl) titleEl.value = u.title || "";
              if (regEl)   regEl.value   = u.region|| "";
            }
          } catch(_) {}
        }
        return res;
      };


      // Also expose a way to export transcript (simple text)
      UI.__ac_exportTranscript = function(limit){
        const rows = (limit ? AC.conv.slice(-limit) : AC.conv);
        return rows.map(r => (r.role === "user" ? "You: " : "Chip: ") + r.text).join("\n");
      };
    }
  } catch(_) {}

  // --- Intent matching ---
  const ACCOUNT_PATTERNS = [
    /^\s*account\s+team\s+(?:for|at)\s+(.+)\s*$/i,
    /^\s*who\s+(?:covers|owns)\s+(.+)\s*$/i,
    /^\s*who\s+is\s+the\s+(?:pure\s+rep|account\s+owner)\s+(?:for|at)\s+(.+)\s*$/i,
    /^\s*(?:team|owner|rep)\s+(.+)\s*$/i
  ];

  function matchAccount(text){
    const t = trim(text);
    for (const rx of ACCOUNT_PATTERNS){
      const m = t.match(rx);
      if (m && m[1]) return trim(m[1]);
    }
    return null;
  }

  const EMAIL_CONV_PATTERNS = [
    /email\s+me\s+(?:this\s+)?(conversation|chat|transcript|history)\b/i,
    /send\s+me\s+(?:the\s+)?(conversation|chat|transcript|history)\b/i
  ];
  const EMAIL_THAT_PATTERNS = [
    /^(?:can\s+you\s+)?email\s+(?:that|this)\s+to\s+me\??$/i,
    /^(?:please\s+)?email\s+(?:that|this)\.?$/i,
    /^send\s+(?:that|this)\s+to\s+me\??$/i
  ];

  function isEmailConversation(text){
    const t = trim(text);
    return EMAIL_CONV_PATTERNS.some(rx => rx.test(t));
  }
  function isEmailThat(text){
    const t = trim(text);
    return EMAIL_THAT_PATTERNS.some(rx => rx.test(t));
  }

  // --- Email helpers ---
  async function sendEmail(to, subject, body, html){
    try {
      const res = await fetch("/api/email/send", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({to, subject, body, html})
      }).then(r=>r.json());
      return !!(res && res.ok);
    } catch(e){
      console.error("email send error", e);
      return false;
    }
  }

  function renderAccountTeamEmailBody(team){
    const lines = [];
    lines.push(`Account team for ${team.name}:`);
    if (team.owner) lines.push(`- Account Owner: ${team.owner}`);
    if (team.pure_rep) lines.push(`- Pure Rep: ${team.pure_rep}`);
    if (team.type) lines.push(`- Type: ${team.type}`);
    lines.push("");
    lines.push("— Sent by Ask Chip");
    return lines.join("\n");
  }

  function htmlEscape(s){ return (s||"").replace(/[&<>]/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[m])); }

  function renderTranscriptEmail(conv){
    const rows = conv.map(r => `<p><strong>${r.role === "user" ? "You" : "Chip"}:</strong> ${htmlEscape(r.text)}</p>`).join("");
    return `<div><h3>Ask Chip Conversation</h3>${rows}<hr><p style="color:#777">Sent by Ask Chip</p></div>`;
  }

  // --- Account team fetch using existing endpoint ---
  async function lookupAccountTeam(name){
    try {
      const res = await fetch(`/api/accounts/search?q=${encodeURIComponent(name)}`).then(r=>r.json());
      if (res && res.ok && Array.isArray(res.results) && res.results.length){
        // choose first result; structure: {id, name, owner, type} (pure_rep optional)
        const r = res.results[0];
        const team = { name: r.name || name, owner: r.owner || "", type: r.type || "", pure_rep: r.pure_rep || "" };
        AC.lastAccountTeam = team;
        return team;
      }
    } catch(e){ console.warn("lookupAccountTeam failed", e); }
    return null;
  }

  // --- Wrap API.chat to intercept intents (typed + STT) ---
  
if (window.API && typeof API.chat === "function") {
  const _origChat = API.chat;

  API.chat = async function(prompt){
    const text = String(prompt || "");

    // 0) Accept simple confirmations after an email offer
    if (AC.pendingEmailOffer && /^(yes|yep|sure|please|do\s+it|go\s+ahead|send\s+it)$/i.test(text)){
      AC.pendingEmailOffer = false;
      if (AC.lastAccountTeam && AC.userEmail){
        const body = renderAccountTeamEmailBody(AC.lastAccountTeam);
        const ok = await sendEmail(AC.userEmail, `Account team for ${AC.lastAccountTeam.name}`, body, null);
        return { ok: true, reply: ok ? "Done—emailed." : "I couldn’t send the email just now." };
      }
      return { ok: true, reply: "I don’t have details to email yet. Ask me for an account team first." };
    }

    // 1) Email conversation?
    if (isEmailConversation(text)){
      if (!AC.userEmail) {
        return { ok: true, reply: "I can email the conversation after you log in." };
      }
      const html = renderTranscriptEmail(AC.conv);
      const ok = await sendEmail(AC.userEmail, "Your Ask Chip Conversation", "", html);
      return { ok: true, reply: ok ? "I’ve emailed the conversation to you." : "I couldn’t send the email right now." };
    }

    // 2) Email 'that' (contextual)
    if (isEmailThat(text)){
      if (!AC.userEmail) return { ok: true, reply: "I can email that after you log in." };
      if (AC.lastAccountTeam){
        const body = renderAccountTeamEmailBody(AC.lastAccountTeam);
        const ok = await sendEmail(AC.userEmail, `Account team for ${AC.lastAccountTeam.name}`, body, null);
        return { ok: true, reply: ok ? "Sent. I emailed that to you." : "I couldn’t send the email just now." };
      }
      // fallback
      const html = renderTranscriptEmail(AC.conv.slice(-20));
      const ok = await sendEmail(AC.userEmail, "Details from Ask Chip", "", html);
      return { ok: true, reply: ok ? "I emailed the recent details to you." : "I couldn’t send the email just now." };
    }

    // 3) Account team intent?
    const acct = matchAccount(text);
    if (acct){
      const team = await lookupAccountTeam(acct);
      if (team){
        AC.pendingEmailOffer = true;
        const reply = naturalize(`Account team for ${team.name}: ${team.owner ? "Owner — " + team.owner + "; " : ""}${team.pure_rep ? "Pure Rep — " + team.pure_rep + "; " : ""}${team.type ? "Type — " + team.type + ". " : ""}Want me to email that to you?`);
        return { ok: true, reply };
      } else {
        return { ok: true, reply: naturalize(`I couldn’t find the account team for ${acct}. Want me to check another name?`) };
      }
    }

    // Default behavior
    return await _origChat.apply(API, arguments);
  };
}
// --- Voice-only continuation: if user says nothing, main.js handles idle; we only enhance 'email that' ---
  // No further changes needed here.

  // --- Provide a small global for debugging ---
  window.__AC_DEBUG__ = AC;
})();
