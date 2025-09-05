// static/js/admin_ux_persona.js
// Injects "User Experience" & "Persona" tabs into existing Admin page, and binds to /api/v1/admin/config

(function(){
  function ready(fn){ document.readyState!=="loading" ? fn() : document.addEventListener("DOMContentLoaded", fn); }

  ready(async function(){
    const tabs = document.querySelector(".tabs");
    const main = document.querySelector("main");
    if (!tabs || !main) return;

    // Create tab buttons
    const bUX = document.createElement("button"); bUX.className="tab"; bUX.textContent="User Experience"; bUX.setAttribute("aria-controls","tab-ux"); bUX.setAttribute("aria-selected","false");
    const bPE = document.createElement("button"); bPE.className="tab"; bPE.textContent="Persona";         bPE.setAttribute("aria-controls","tab-persona"); bPE.setAttribute("aria-selected","false");
    tabs.appendChild(bUX); tabs.appendChild(bPE);

    // Panels
    const pUX = document.createElement("section"); pUX.id="tab-ux"; pUX.className="panel"; pUX.setAttribute("aria-hidden","true");
    pUX.innerHTML = `
      <h2>User Experience</h2>
      <div class="form-grid">
        <label>Show state dots</label><input id="cfg_show_state_dots" type="checkbox">
        <label>Show instruction strip</label><input id="cfg_show_instruction_strip" type="checkbox">
        <label>Suggestions enabled</label><input id="cfg_suggestions_enabled" type="checkbox">
        <label>Suggestions: max items</label><input id="cfg_suggestions_max_items" type="number" min="1" max="4">
        <label>Suggestions: max words</label><input id="cfg_suggestions_max_words" type="number" min="1" max="7">
        <label>Nudge delay (ms)</label><input id="cfg_nudge_delay_ms" type="number" min="500" step="100">
        <label>Nudge backoff after ignored</label><input id="cfg_nudge_backoff_after_ignored" type="number" min="0" max="5">
        <label>Short-term memory window</label><input id="cfg_short_term_window" type="number" min="0" max="12">
        <label>Short-term summary</label><input id="cfg_short_term_summary" type="checkbox">
        <label>Awareness enabled</label><input id="cfg_awareness_enabled" type="checkbox">
        <label>Agenda check every N turns</label><input id="cfg_agenda_check_every_turns" type="number" min="0" max="10">
      </div>
      <div style="margin-top:10px"><button id="btnSaveUX" class="btn primary">Save UX Settings</button></div>
    `;
    const pPE = document.createElement("section"); pPE.id="tab-persona"; pPE.className="panel"; pPE.setAttribute("aria-hidden","true");
    pPE.innerHTML = `
      <h2>Persona</h2>
      <div class="form-grid">
        <label>Pure-first guardrail</label><input id="cfg_pure_guardrail_enabled" type="checkbox">
        <label>Nebraska persona % (0.12–0.15)</label><input id="cfg_nebraska_persona_level" type="number" step="0.01" min="0" max="1">
        <label>Nebraska quotes enabled</label><input id="cfg_nebraska_quotes_enabled" type="checkbox">
        <label>Allowed teacher moves (comma)</label><input id="cfg_policy_teacher_moves" placeholder="check_understanding,offer_steps,deep_dive,compare,visualize,summarize_next_actions">
        <label>Allowed tones (comma)</label><input id="cfg_policy_tones" placeholder="brief,empathetic,energetic">
        <label>LLM Provider</label><input id="cfg_llm_provider" placeholder="auto|openai">
        <label>STT Provider</label><input id="cfg_stt_provider" placeholder="auto|whisper">
        <label>TTS Provider</label><input id="cfg_tts_provider" placeholder="auto|elevenlabs">
      </div>
      <div style="margin-top:10px"><button id="btnSavePersona" class="btn primary">Save Persona Settings</button></div>
    `;
    main.appendChild(pUX); main.appendChild(pPE);

    // Tab switching reuse from existing admin.js behavior
    const allTabs = Array.from(document.querySelectorAll(".tab"));
    const panels = Array.from(document.querySelectorAll(".panel"));
    [bUX,bPE].forEach(t => t.addEventListener("click", () => {
      allTabs.forEach(x => x.setAttribute("aria-selected","false"));
      t.setAttribute("aria-selected","true");
      panels.forEach(p => p.setAttribute("aria-hidden","true"));
      const target = document.getElementById(t.getAttribute("aria-controls"));
      if (target) target.setAttribute("aria-hidden","false");
    }));

    async function getConfig(){
      try { const j = await fetch("/api/v1/admin/config",{credentials:"include"}).then(r=>r.json()); return j.cfg||j; } catch(e){ return {}; }
    }
    async function setConfig(updates){
      let tok = sessionStorage.getItem("csrf");
      if (!tok) { try{ const r = await fetch("/api/v1/auth/csrf",{credentials:"include"}).then(r=>r.json()); tok = r?.csrf; sessionStorage.setItem("csrf", tok||""); }catch(e){} }
      return fetch("/api/v1/admin/config",{method:"POST",credentials:"include",headers:Object.assign({"Content-Type":"application/json"},tok?{"X-CSRF-Token":tok}:{}),body:JSON.stringify(updates)}).then(r=>r.json());
    }
    const idToKey = {
      cfg_show_state_dots:"show_state_dots", cfg_show_instruction_strip:"show_instruction_strip",
      cfg_suggestions_enabled:"suggestions_enabled", cfg_suggestions_max_items:"suggestions_max_items", cfg_suggestions_max_words:"suggestions_max_words",
      cfg_nudge_delay_ms:"nudge_delay_ms", cfg_nudge_backoff_after_ignored:"nudge_backoff_after_ignored",
      cfg_short_term_window:"short_term_window", cfg_short_term_summary:"short_term_summary", cfg_awareness_enabled:"awareness_enabled",
      cfg_agenda_check_every_turns:"agenda_check_every_turns",
      cfg_pure_guardrail_enabled:"pure_guardrail_enabled", cfg_nebraska_persona_level:"nebraska_persona_level", cfg_nebraska_quotes_enabled:"nebraska_quotes_enabled",
      cfg_policy_teacher_moves:"policy_teacher_moves", cfg_policy_tones:"policy_tones",
      cfg_llm_provider:"llm_provider", cfg_stt_provider:"stt_provider", cfg_tts_provider:"tts_provider"
    };
    function setVal(id,val){ const el=document.getElementById(id); if(!el) return; if(el.type==="checkbox") el.checked=!!val; else if(Array.isArray(val)) el.value = val.join(","); else el.value = (val??""); }
    function getVal(id){ const el=document.getElementById(id); if(!el) return undefined; if(el.type==="checkbox") return el.checked; if(id==="cfg_policy_teacher_moves"||id==="cfg_policy_tones"){const v=(el.value||"").trim(); return v? v.split(/\s*,\s*/):[];} const v=el.value; if(el.type==="number") return v===""? null: Number(v); return v; }

    const cfg = await getConfig(); Object.entries(idToKey).forEach(([id,key]) => setVal(id, cfg[key]));
    document.getElementById("btnSaveUX")?.addEventListener("click", async ()=>{
      const keys=["cfg_show_state_dots","cfg_show_instruction_strip","cfg_suggestions_enabled","cfg_suggestions_max_items","cfg_suggestions_max_words","cfg_nudge_delay_ms","cfg_nudge_backoff_after_ignored","cfg_short_term_window","cfg_short_term_summary","cfg_awareness_enabled","cfg_agenda_check_every_turns"];
      const updates={}; keys.forEach(id=>updates[idToKey[id]]=getVal(id)); await setConfig(updates); alert("User Experience settings saved.");
    });
    document.getElementById("btnSavePersona")?.addEventListener("click", async ()=>{
      const keys=["cfg_pure_guardrail_enabled","cfg_nebraska_persona_level","cfg_nebraska_quotes_enabled","cfg_policy_teacher_moves","cfg_policy_tones","cfg_llm_provider","cfg_stt_provider","cfg_tts_provider"];
      const updates={}; keys.forEach(id=>updates[idToKey[id]]=getVal(id)); await setConfig(updates); alert("Persona settings saved.");
    });
  });
})();
