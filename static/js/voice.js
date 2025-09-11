import { ensureCSRF } from './csrf.js';
let mediaStream=null,ctx=null,rec=null,chunks=[],analyser=null,vadOn=false,silenceMs=0,speechMs=0,vadBoost=1.0;let vadCalibrating=false, vadBase=0.025;
export async function initMic(){mediaStream=await navigator.mediaDevices.getUserMedia({audio:{sampleRate:48000,channelCount:1,echoCancellation:true,noiseSuppression:true},video:false});
ctx=new (window.AudioContext||window.webkitAudioContext)();const src=ctx.createMediaStreamSource(mediaStream);analyser=ctx.createAnalyser();analyser.fftSize=2048;src.connect(analyser);return mediaStream;}
function rms(){const b=new Uint8Array(analyser.fftSize);analyser.getByteTimeDomainData(b);let s=0;for(let i=0;i<b.length;i++){const v=(b[i]-128)/128;s+=v*v;}return Math.sqrt(s/b.length);}

export function armVAD(){
  if(!mediaStream)return;
  if(vadOn)return;
  vadOn=true;
  // Calibrate baseline for ~800ms
  vadCalibrating=true;
  let acc=0, n=0;
  const calibEnd=Date.now()+800;
  (function calib(){
    try{
      const b=new Uint8Array(analyser.fftSize); analyser.getByteTimeDomainData(b);
      let s=0; for(let i=0;i<b.length;i++){ const v=(b[i]-128)/128; s+=v*v; }
      const level = Math.sqrt(s/b.length);
      acc += level; n++;
    }catch{}
    if(Date.now()<calibEnd){ return setTimeout(calib, 40); }
    const avg = (n? acc/n : 0.02);
    // ensure a sane baseline
    vadBase = Math.min(Math.max(avg*1.8, 0.01), 0.04); // 1.8x ambient, clamped
    vadCalibrating=false;
  })();

  try{ if(rec&&rec.state!=='inactive') rec.stop(); }catch{}
  rec = new MediaRecorder(mediaStream, {mimeType:'audio/webm;codecs=opus', audioBitsPerSecond:128000});
  rec.ondataavailable = e=>{ if(e.data.size) chunks.push(e.data) };
  rec.onstop = ()=> postSTT();
  rec.start();
  chunks = []; silenceMs=0; speechMs=0;
  loop();
}
catch{};rec=new MediaRecorder(mediaStream,{mimeType:'audio/webm;codecs=opus',audioBitsPerSecond:128000});rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};rec.onstop=()=>postSTT();rec.start();silenceMs=0;speechMs=0;loop();});
rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data)};rec.onstop=()=>postSTT();rec.start();silenceMs=0;speechMs=0;loop();}
export function disarmVAD(){vadOn=false;try{if(rec&&rec.state!=='inactive')rec.stop();}catch{} chunks=[]; silenceMs=0; speechMs=0;}
async function loop(){if(!vadOn)return;try{await ctx.resume();}catch{}const level=rms();const ms=60;const thr=(vadBase||0.025)*vadBoost; window.dispatchEvent(new CustomEvent('chip:vad', {detail:{level,thr,speechMs,silenceMs,armed:vadOn,boost:vadBoost}})); if(level>thr)speechMs+=ms;else silenceMs+=ms;
if(speechMs>=300&&silenceMs>=400){vadOn=false;try{rec?.stop();}catch{}return;}setTimeout(loop,ms);}
async function postSTT(){try{const sid=localStorage.getItem('chip.sid');const fd=new FormData();const blob=new Blob(chunks,{type:'audio/webm'});
fd.append('file',blob,'turn.webm');fd.append('meta',JSON.stringify({session_id:sid,language:'en'}));const hdr={'X-CSRF-Token':await ensureCSRF()};
await fetch(`/api/v1/voice/stt?session_id=${encodeURIComponent(sid)}`,{method:'POST',credentials:'include',headers:hdr,body:fd});}catch(e){console.warn('STT error',e);}}

export function setVadBoost(mult){ try{ vadBoost = Math.max(1.0, Number(mult)||1.0); }catch{} }
