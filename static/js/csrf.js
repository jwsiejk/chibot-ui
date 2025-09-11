let _token=null,_inflight=null;
export async function ensureCSRF(force=false){
  if(!force&&_token)return _token;
  if(_inflight&&!force)return _inflight;
  _inflight=(async()=>{const r=await fetch('/api/v1/csrf',{credentials:'include'});const t=r.headers.get('X-CSRF-Token')||'';
    _token=t;let m=document.head.querySelector('meta[name=csrf]');if(!m){m=document.createElement('meta');m.name='csrf';document.head.appendChild(m);}m.content=t;_inflight=null;return t;})();
  return _inflight;
}
export function installFetchInterceptor(){
  const orig=window.fetch;
  window.fetch=async(input,init={})=>{
    try{const url=(typeof input==='string')?input:(input?.url||'');const same=!/^https?:\/\//i.test(url)||url.startsWith(location.origin);
      const m=(init.method||'GET').toUpperCase();
      if(same&&['POST','PUT','PATCH','DELETE'].includes(m)){const h=new Headers(init.headers||{});if(!h.get('X-CSRF-Token')){const t=await ensureCSRF().catch(()=>'');
        if(t)h.set('X-CSRF-Token',t);}init={...init,headers:h,credentials:init.credentials||'include'};}
      let res=await orig(input,init);if(res.status===403){await ensureCSRF(true).catch(()=>{});const h=new Headers(init.headers||{});const t=await ensureCSRF().catch(()=>'');
        if(t)h.set('X-CSRF-Token',t);res=await orig(input,{...init,headers:h,credentials:init.credentials||'include'});}return res;
    }catch(e){return Promise.reject(e);}}
}
