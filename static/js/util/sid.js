export function getSID(){
  const key = "chip.sid";
  let sid = sessionStorage.getItem(key);
  if (!sid){
    sid = (crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2);
    sessionStorage.setItem(key, sid);
  }
  return sid;
}
