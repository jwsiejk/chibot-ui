
/**
 * recorder-lite.js — Minimal MediaRecorder wrapper driven by VAD events.
 */
let _rec=null, _chunks=[], _cfg={ getStream:null, onBlob:null, mimeType:'audio/webm;codecs=opus' };
export function init(cfg={}){ _cfg=Object.assign(_cfg,cfg||{}); }
export function isRecording(){ return !!_rec; }
export async function start(){ if (_rec) return; if(!_cfg.getStream) throw new Error('recorder-lite: getStream() not provided'); const s=_cfg.getStream(); if(!s) throw new Error('recorder-lite: no mic stream; call VAD.arm() first'); _chunks=[]; const opts={ mimeType:_cfg.mimeType }; _rec=new MediaRecorder(s,opts); _rec.ondataavailable=e=>{ if(e.data&&e.data.size) _chunks.push(e.data); }; _rec.onstop=async()=>{ const blob=new Blob(_chunks,{type:'audio/webm'}); _rec=null; try{ await _cfg.onBlob?.(blob);}catch(e){ console.warn('onBlob error', e); } }; _rec.start(); }
export async function stop(){ if(_rec){ try{ _rec.stop(); }catch{} } }
