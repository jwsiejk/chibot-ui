(function () {
  const E = {};
  E.on = (n, fn) => document.addEventListener(n, fn);
  E.emit = (n, d={}) => document.dispatchEvent(new CustomEvent(n, { detail: d }));
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  let rec = null, listening = false, abortCurrent = null;
  function setup() {
    if (!SR) return null;
    const r = new SR();
    r.lang="en-US"; r.interimResults=true; r.continuous=true; r.maxAlternatives=1;
    r.onstart=()=>{listening=true;E.emit("chip:sr-start")};
    r.onend=()=>{listening=false; if(window.__chip_auto_vad) try{r.start()}catch{}};
    r.onresult=(ev)=>{
      let final=""; for(let i=ev.resultIndex;i<ev.results.length;i++){const res=ev.results[i]; if(res.isFinal) final+=res[0].transcript;}
      const interim=ev.results[ev.results.length-1]&&!ev.results[ev.results.length-1].isFinal?ev.results[ev.results.length-1][0].transcript:"";
      if(interim) E.emit("chip:interim",{text:interim});
      if(final.trim()){E.emit("chip:bargein");E.emit("chip:final",{text:final.trim()});}
    };
    return r;
  }
  window.ChipRealtime = {
    start(){ if(!rec) rec=setup(); if(rec&&!listening){window.__chip_auto_vad=true; try{rec.start()}catch{}} },
    stop(){ window.__chip_auto_vad=false; if(rec) try{rec.stop()}catch{} },
    attachAbortController(ac){ abortCurrent = ac; },
    interrupt(){ if(abortCurrent) try{abortCurrent.abort()}catch{}; const a=document.getElementById("chipAudio"); if(a&&!a.paused){try{a.pause();a.currentTime=0}catch{}}; E.emit("chip:bargein"); },
    bus:E
  };
})();
