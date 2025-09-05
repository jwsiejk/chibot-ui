// static/js/layout_editor.js
(function(){
  const root = document.getElementById("layoutRoot");
  if (!root) return;

  // Elements you can edit
  const items = [
    { key: "chipStage",       el: document.getElementById("chipStage") },
    { key: "chatPane",        el: document.getElementById("chatPane") },
    { key: "instructionStrip",el: document.getElementById("instructionStrip") },
    { key: "stateDots",       el: document.getElementById("stateDots") },
    { key: "toolbar",         el: document.querySelector(".toolbar") }
  ].filter(x => x.el);

  // style helpers
  const css = (el, m) => Object.entries(m).forEach(([k,v]) => el.style[k] = v);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  function rect(el){ return el.getBoundingClientRect(); }
  function pctFromPx(px, total){ return (px/Math.max(1,total))*100; }
  function pxFromPct(pct, total){ return (pct/100)*total; }

  // current layout model (percentages)
  let layout = null;     // { mode, stage_side, show_instruction_strip, show_state_dots, nodes: {key:{x,y,w,h}} }
  let design = false;

  // Apply a published layout (for all users)
  async function applyPublishedLayout(){
    try{
      const r = await fetch("/api/v1/admin/layouts?variant=published&breakpoint=desktop", {credentials:"include"});
      if (!r.ok) return;
      const j = await r.json();
      layout = j.layout || {};
    }catch{ /* ignore */ }

    // defaults if missing
    layout = Object.assign({ mode: "grid", nodes: {} }, layout);

    // Show/hide strip/dots
    const strip = document.getElementById("instructionStrip");
    const dots = document.getElementById("stateDots");
    if (strip) strip.classList.toggle("hidden", !layout.show_instruction_strip);
    if (dots)  dots.classList.toggle("hidden", !layout.show_state_dots);

    if (layout.mode !== "free" || !layout.nodes){
      // grid mode: ensure normal flow
      root.classList.remove("free-layout");
      items.forEach(({el}) => { css(el, { position:"", left:"", top:"", width:"", height:"" }); });
      return;
    }

    // free mode → absolute positions
    const R = rect(root);
    root.classList.add("free-layout");
    items.forEach(({key, el})=>{
      const n = layout.nodes[key];
      if (!n) return;
      css(el, {
        position: "absolute",
        left:  pxFromPct(n.x, R.width) + "px",
        top:   pxFromPct(n.y, R.height) + "px",
        width: pxFromPct(n.w, R.width) + "px",
        height: pxFromPct(n.h, R.height) + "px"
      });
    });
  }

  // Design handles (one per element)
  function addHandles(el){
    const box = document.createElement("div");
    box.className = "design-box";
    const hBR = document.createElement("div"); hBR.className = "handle br"; hBR.title = "Resize (Shift+Drag)";
    box.appendChild(hBR);
    el.appendChild(box);
    return { box, hBR };
  }

  // Enter/exit design mode
  function enterDesign(){
    if (design) return;
    design = true;
    root.classList.add("design-mode");
    // Initialize layout nodes from current visual if missing
    const R = rect(root);
    layout = layout || { mode:"free", nodes:{} };
    layout.mode = "free";
    layout.nodes = layout.nodes || {};
    items.forEach(({key, el})=>{
      const E = rect(el);
      if (!layout.nodes[key]){
        layout.nodes[key] = {
          x: pctFromPx(E.left - R.left, R.width),
          y: pctFromPx(E.top - R.top, R.height),
          w: pctFromPx(E.width, R.width),
          h: pctFromPx(E.height, R.height),
        };
      }
      css(el, { position:"absolute" }); // ensure absolute in design
      // add handles if not present
      if (!el.querySelector(".design-box")) addHandles(el);
    });
    // hint
    console.info("Design mode: Shift+Drag to move; Shift+Drag handle to resize; Ctrl+S save draft; Ctrl+P publish; Esc exit.");
  }
  function exitDesign(){
    if (!design) return;
    design = false;
    root.classList.remove("design-mode");
    // Keep absolute while in free mode; remove boxes
    items.forEach(({el})=>{
      const b = el.querySelector(".design-box"); if (b) b.remove();
    });
  }

  // Drag/resize logic (Shift must be held)
  let drag = null; // { key, kind:"move"|"resize", startX,startY, startRect, rootRect }
  function onDown(e, key, kind){
    if (!design || !e.shiftKey) return;
    e.preventDefault(); e.stopPropagation();
    const el = items.find(i=>i.key===key).el;
    drag = {
      key, kind,
      startX: e.clientX, startY: e.clientY,
      startRect: rect(el),
      rootRect: rect(root)
    };
    document.addEventListener("mousemove", onMove);
    document.addEventListener("mouseup", onUp, { once:true });
  }
  function onMove(e){
    if (!drag) return;
    const {key, kind, startX, startY, startRect, rootRect} = drag;
    const dx = e.clientX - startX;
    const dy = e.clientY - startY;
    const W = rootRect.width, H = rootRect.height;
    const node = layout.nodes[key];
    if (kind === "move"){
      const nx = pctFromPx((startRect.left - rootRect.left + dx), W);
      const ny = pctFromPx((startRect.top  - rootRect.top  + dy), H);
      node.x = clamp(nx, 0, 100 - node.w);
      node.y = clamp(ny, 0, 100 - node.h);
    } else { // resize from bottom-right
      const nw = pctFromPx(startRect.width  + dx, W);
      const nh = pctFromPx(startRect.height + dy, H);
      node.w = clamp(nw, 10, 100 - node.x);
      node.h = clamp(nh, 10, 100 - node.y);
    }
    applyNode(key);
  }
  function onUp(){
    document.removeEventListener("mousemove", onMove);
    drag = null;
  }
  function applyNode(key){
    const el = items.find(i=>i.key===key).el;
    const R = rect(root);
    const n = layout.nodes[key];
    css(el, {
      left:  pxFromPct(n.x, R.width) + "px",
      top:   pxFromPct(n.y, R.height) + "px",
      width: pxFromPct(n.w, R.width) + "px",
      height:pxFromPct(n.h, R.height) + "px"
    });
  }

  // Wire element listeners (move) + resize handle
  function wireDesignEvents(){
    items.forEach(({key, el})=>{
      // move (Shift+Drag anywhere inside element)
      el.addEventListener("mousedown", (ev)=> onDown(ev, key, "move"));
      // resize handle (appear only in design)
      el.addEventListener("mousedown", (ev)=>{
        const h = ev.target.closest(".handle.br");
        if (h) onDown(ev, key, "resize");
      });
    });
  }

  // Save/publish
  async function save(variant){
    const body = {
      variant,
      breakpoint: "desktop",
      layout: Object.assign({}, layout, {
        // keep current visibility toggles in the payload
        show_instruction_strip: !document.getElementById("instructionStrip")?.classList.contains("hidden"),
        show_state_dots: !document.getElementById("stateDots")?.classList.contains("hidden")
      })
    };
    const r = await fetch("/api/v1/admin/layouts", {
      method: "POST",
      headers: { "Content-Type":"application/json" },
      credentials: "include",
      body: JSON.stringify(body)
    });
    if (!r.ok) throw new Error(`POST /api/v1/admin/layouts → ${r.status}`);
    const j = await r.json(); console.info(`${variant} saved (v${j.version})`);
  }

  // Keyboard shortcuts
  document.addEventListener("keydown", async (e)=>{
    if (e.key === "Escape") { exitDesign(); }
    if (e.key.toLowerCase() === "s" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault(); await save("draft");
    }
    if (e.key.toLowerCase() === "p" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault(); await save("published");
    }
  });

  // Double-click anywhere toggles design mode
  document.addEventListener("dblclick", (e)=>{
    if (!design) { enterDesign(); } else { exitDesign(); }
  });

  // Listen for layout publishes to live-apply without reload
  try {
    const es = new EventSource("/api/v1/admin/logs");
    es.addEventListener("layout_updated", applyPublishedLayout);
  } catch {}

  // Boot
  wireDesignEvents();
  applyPublishedLayout();
})();
