let _current=null;export function playStream(chunks){try{if(!chunks||!chunks.length)return;const size=chunks.reduce((a,c)=>a+c.length,0);
const buf=new Uint8Array(size);let off=0;for(const c of chunks){buf.set(c,off);off+=c.length;}const blob=new Blob([buf],{type:'audio/mpeg'});
if(_current){try{_current.pause();}catch{}}_current=new Audio(URL.createObjectURL(blob));_current.play().catch(()=>{});}catch(e){console.warn('playStream error',e);}}
export function stopPlayback(){try{_current?.pause();}catch{}}
export function setVisemeCallback(fn){} export function isPlaying(){return !!(_current&&!_current.paused);}
