//! Embedded Browser slide: a **child WebView2** inside the main Remedy window
//! (Chromium engine). Not a separate popup unless the UI calls system open.
//!
//! Requires Tauri feature `unstable` (window.add_child / multiwebview).

use serde::{Deserialize, Serialize};
use serde_json::json;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;
use tauri::{
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, State, WebviewUrl,
};
use tauri::webview::{NewWindowResponse, WebviewBuilder};
use tauri::utils::config::Color;
use url::Url;

const LABEL: &str = "remedy-browser-embed";

fn api_url(path: &str) -> String {
    format!("{}{}", crate::api_base_url(), path)
}

/// Shared DOM helpers for every agent scrape/click. Prepended to the snapshot
/// and click JS so Remedy sees the WHOLE page — not just the light DOM.
///
/// Why this exists: modern retail sites (Target, Kroger, Best Buy) render
/// their controls inside Web Component **shadow roots** and same-origin
/// **iframes**, which a plain `document.querySelectorAll` cannot see. Remedy
/// would "find" a store card in page_text yet have no clickable element for
/// its "Make this my store" button. And even in the light DOM, a list of
/// stores gives five identical "Set as store" buttons with nothing to tell
/// them apart — the store name + address live in an ancestor CARD, not on the
/// button. These helpers pierce shadow/iframes AND attach that card context so
/// a generic button becomes identifiable ("Set as store" · "Hueytown …35023").
const REMEDY_DOM_JS: &str = r#"
window.__rmdyRoots=function(){
  const roots=[document];
  const walk=(root)=>{
    let els;
    try{ els=root.querySelectorAll('*'); }catch(e){ return; }
    for(const el of els){
      if(el.shadowRoot){ roots.push(el.shadowRoot); walk(el.shadowRoot); }
    }
  };
  try{ walk(document); }catch(e){}
  // Same-origin iframes only (cross-origin throws → skipped, never leaked).
  try{
    for(const f of document.querySelectorAll('iframe')){
      try{ const d=f.contentDocument; if(d){ roots.push(d); walk(d); } }catch(e){}
    }
  }catch(e){}
  return roots;
};
window.__rmdyDeep=function(sel){
  const out=[]; const seen=new Set();
  for(const root of window.__rmdyRoots()){
    let els; try{ els=root.querySelectorAll(sel); }catch(e){ continue; }
    for(const el of els){ if(!seen.has(el)){ seen.add(el); out.push(el); } }
  }
  return out;
};
window.__rmdyFind=function(ref){
  for(const root of window.__rmdyRoots()){
    let el; try{ el=root.querySelector('[data-remedy-ref="'+ref+'"]'); }catch(e){ continue; }
    if(el) return el;
  }
  return null;
};
window.__rmdyName=function(el){
  // Concatenate aria + visible text + placeholder. Aria-only ("Post text")
  // used to hide the on-screen placeholder ("What's happening?") so
  // click-by-visible-text missed the composer and hit a toolbar control.
  const aria=(el.getAttribute&&(el.getAttribute('aria-label')||el.getAttribute('title')))||'';
  const text=(el.innerText||'').toString();
  const val=(el.value!=null?String(el.value):'');
  const ph=el.placeholder||'';
  const nm=(el.getAttribute&&el.getAttribute('name'))||'';
  const parts=[aria,text,val,ph,nm];
  const seen={}; const uniq=[];
  for(const p of parts){
    const n=String(p||'').trim().replace(/\s+/g,' ');
    const k=n.toLowerCase();
    if(!k||seen[k]) continue;
    seen[k]=1; uniq.push(n);
  }
  return (uniq.join(' ')||(el.tagName||'')).trim();
};
// True TOP-WINDOW rect for any element. Shadow-DOM elements share the top
// document's layout so their getBoundingClientRect is already top-relative;
// but an element inside a same-origin IFRAME reports coordinates relative to
// that frame's own viewport. Walk up each frameElement, adding its offset (and
// border/padding), so snapshot x/y, click, and press_hold all target the real
// page point instead of clicking empty top-window chrome (a silent miss).
window.__rmdyRect=function(el){
  let r; try{ r=el.getBoundingClientRect(); }catch(e){ return null; }
  let x=r.x, y=r.y; const w=r.width, h=r.height;
  try{
    let win=el.ownerDocument&&el.ownerDocument.defaultView;
    let guard=0;
    while(win&&win.frameElement&&guard++<8){
      const fr=win.frameElement.getBoundingClientRect();
      let bl=0,bt=0,pl=0,pt=0;
      try{
        const cs=win.parent.getComputedStyle(win.frameElement);
        bl=parseFloat(cs.borderLeftWidth)||0; bt=parseFloat(cs.borderTopWidth)||0;
        pl=parseFloat(cs.paddingLeft)||0; pt=parseFloat(cs.paddingTop)||0;
      }catch(e){}
      x+=fr.x+bl+pl; y+=fr.y+bt+pt;
      if(win===win.parent) break;
      win=win.parent;
    }
  }catch(e){}
  return {x:x,y:y,width:w,height:h,top:y,left:x,right:x+w,bottom:y+h};
};
window.__rmdyVisible=function(el){
  const r=window.__rmdyRect(el);
  if(!r) return false;
  let st; try{ st=window.getComputedStyle(el); }catch(e){ st=null; }
  if(st&&(st.visibility==='hidden'||st.display==='none'||st.opacity==='0')) return false;
  if(el.disabled) return false;
  return r.width>2&&r.height>2&&r.bottom>0&&r.right>0&&r.top<innerHeight&&r.left<innerWidth;
};
// The disambiguating context for a control: the text of its nearest CARD-like
// ancestor (store tile / search result / product), minus the control's own
// label, capped. Card detection is SIZE-based, not class-name guessing, so it
// generalizes across sites: the smallest ancestor whose text is meaningfully
// larger than the control (it wraps siblings — the store name, address, hours)
// but still bounded (a card, not the whole page). Card-ish tags/roles/classes
// only nudge the bound wider. Crosses shadow boundaries via getRootNode().host.
window.__rmdyCtx=function(el){
  const CARD=/(card|tile|result|store|item|listitem|cell|option|product|location|pod|address)/i;
  const self=((el.innerText||el.value||'')+'').trim().replace(/\s+/g,' ');
  const selfLen=self.length;
  let node=el.parentElement, hops=0;
  const strip=(t)=>{ t=(t||'').trim().replace(/\s+/g,' '); if(self) t=t.split(self).join(' ').replace(/\s+/g,' ').trim(); return t; };
  while(node&&hops<9){
    let t=''; try{ t=(node.innerText||'').trim().replace(/\s+/g,' '); }catch(e){ t=''; }
    const tag=(node.tagName||'').toLowerCase();
    const role=((node.getAttribute&&node.getAttribute('role'))||'').toLowerCase();
    const idcls=((node.className&&node.className.toString&&node.className.toString())||'')+' '+
      ((node.getAttribute&&(node.getAttribute('data-testid')||node.getAttribute('data-test')))||'');
    const cardish=tag==='li'||tag==='article'||role==='listitem'||role==='option'||role==='article'||CARD.test(idcls);
    // A real card: adds ≥12 chars of sibling context beyond the control, and
    // is not the whole page. Card-ish containers may be a bit larger.
    const cap=cardish?420:300;
    if(t.length>=selfLen+12 && t.length<=cap){
      const stripped=strip(t);
      if(stripped.length>=4) return stripped.slice(0,180);
    }
    if(!node.parentElement){
      const rootNode=node.getRootNode&&node.getRootNode();
      node=(rootNode&&rootNode.host)?rootNode.host:null;
    } else { node=node.parentElement; }
    hops++;
  }
  return '';
};
window.__rmdyClickSel='a,button,input,textarea,select,[role=button],[role=link],[role=tab],[role=menuitem],[role=option],[role=radio],[role=checkbox],[contenteditable=true],summary,label,[onclick]';
window.__rmdyHoldSel=window.__rmdyClickSel+',[role=switch],div,span';
window.__rmdyTypeSel='input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=range]):not([type=color]),textarea,[role=textbox],[role=searchbox],[role=combobox],[contenteditable=true],[contenteditable=""],[contenteditable=plaintext-only],label';
window.__rmdyIsField=function(el){
  if(!el) return false;
  const tag=(el.tagName||'').toLowerCase();
  const role=((el.getAttribute&&el.getAttribute('role'))||'').toLowerCase();
  const itype=(el.type||'').toLowerCase();
  const skip={hidden:1,submit:1,button:1,reset:1,image:1,checkbox:1,radio:1,file:1,range:1,color:1};
  if(tag==='input') return !skip[itype];
  return tag==='textarea'||el.isContentEditable||role==='textbox'||role==='searchbox'||role==='combobox';
};
window.__rmdyFieldOf=function(el){
  if(!el) return null;
  if(window.__rmdyIsField(el)) return el;
  const fieldSel='input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]):not([type=image]):not([type=checkbox]):not([type=radio]):not([type=file]):not([type=range]):not([type=color]),textarea,[role=textbox],[role=searchbox],[role=combobox],[contenteditable=true],[contenteditable=""],[contenteditable=plaintext-only]';
  if((el.tagName||'').toLowerCase()==='label'){
    let f=null;
    const id=el.htmlFor||(el.getAttribute&&el.getAttribute('for'))||'';
    if(id){ try{ f=(el.ownerDocument||document).getElementById(id); }catch(e){} }
    if(f && !window.__rmdyIsField(f)) f=null;
    if(!f){ try{ f=el.querySelector(fieldSel); }catch(e){} }
    if(!f){
      try{
        let n=el.nextElementSibling;
        for(let i=0;i<2 && n && !f;i++,n=n.nextElementSibling){
          if(window.__rmdyIsField(n)) f=n;
        }
      }catch(e){}
    }
    return f||null;
  }
  try{
    const id=el.id;
    if(id){
      const hit=window.__rmdyDeep(fieldSel).find(n=>
        ((n.getAttribute&&n.getAttribute('aria-labelledby'))||'').split(/\s+/).indexOf(id)>=0);
      if(hit) return hit;
    }
  }catch(e){}
  return null;
};
window.__rmdySTOP=new Set(['the','a','an','to','of','in','on','at','for','and','or','my','me','it','one','some','this','that','with','from','into','your','their']);
window.__rmdyACTION=new Set(['post','submit','send','tweet','publish','share','continue','next','save','create','reply','comment','search','confirm','go','done','apply','update']);
window.__rmdyScore=function(el,q){
  if(!window.__rmdyVisible(el)) return -1;
  const r=window.__rmdyRect(el)||el.getBoundingClientRect();
  const name=window.__rmdyName(el).toLowerCase();
  if(!name) return -1;
  q=(q||'').toLowerCase().trim();
  if(!q) return -1;
  const tag=(el.tagName||'').toLowerCase();
  const role=((el.getAttribute&&el.getAttribute('role'))||'').toLowerCase();
  const itype=(el.type||'').toLowerCase();
  const STOP=window.__rmdySTOP;
  const qt=q.split(/[^a-z0-9]+/).filter(Boolean);
  const mqt=qt.filter(t=>t.length>=3 && !STOP.has(t));
  let s=0;
  if(name===q) s=100;
  else if(q.length>=3 && name.includes(q)) s=70;
  else if(q.includes(name)&&name.length>=3) s=40;
  else {
    const nt=name.split(/[^a-z0-9]+/).filter(t=>t.length>=3 && !STOP.has(t));
    const use=mqt.length?mqt:qt.filter(t=>t.length>=3 && !STOP.has(t));
    // Exact tokens only — "add" must not hit "address" / "happening" via includes.
    const hit=use.filter(t=>nt.some(n=>n===t)).length;
    s = use.length ? 22*(hit/use.length) : 0;
  }
  let ctx=''; try { ctx=window.__rmdyCtx(el).toLowerCase(); } catch(e) {}
  const nameHasQuery=mqt.some(t=>name.includes(t));
  if(ctx && mqt.length>1 && nameHasQuery){
    const missing=mqt.filter(t=>!name.includes(t));
    const inCtx=missing.filter(t=>ctx.includes(t)).length;
    if(inCtx) s+=20*(inCtx/mqt.length);
  }
  if(ctx && /view in .{0,24}app|get the app|download (the )?app|open (in|the) app|install (the )?app/.test(ctx)) s-=40;
  if(mqt.some(t=>window.__rmdyACTION.has(t))){
    if(tag==='button'||role==='button'||itype==='submit') s+=25;
    else if(tag==='a'||role==='link') s-=20;
  }
  if(/happen|write a|write your|compose|post text|\btitle\b|\bbody\b|caption|what.s on/.test(q)){
    if(tag==='textarea'||el.isContentEditable||role==='textbox'||role==='searchbox'||itype==='text'||itype==='search') s+=30;
    else if(tag==='button'||role==='button') s-=15;
  }
  if(s<=0) return -1;
  if(r.width>8&&r.width<900&&r.height>8&&r.height<220) s+=5;
  return s;
};
window.__rmdyPick=function(q, sel){
  let best=null, bestS=-1, second=null, secondS=-1;
  for(const el of window.__rmdyDeep(sel)){
    const s=window.__rmdyScore(el,q);
    if(s>bestS){ second=best; secondS=bestS; bestS=s; best=el; }
    else if(s>secondS){ secondS=s; second=el; }
  }
  return {best:best, bestS:bestS, second:second, secondS:secondS};
};
"#;

/// Same-window OAuth for redirect-style SSO. GIS / sized IdP windows still
/// need a real popup (`window.opener`) — those pass through to `window.open`.
/// Brand-agnostic: path/query heuristics, not site-specific hosts.
const SAME_WINDOW_OAUTH_JS: &str = r#"(function(){
  if (window.__remedySameWindowOpen) return;
  window.__remedySameWindowOpen = true;
  var SS_RET = '__remedy_oauth_return';
  var SS_TS = '__remedy_oauth_ts';
  var origOpen = window.open;
  var origClose = window.close;

  /** True when this page looks like an auth / SSO hop (any provider). */
  function looksLikeAuthUrl(href, host, path, search){
    var h = (host || '').toLowerCase();
    var p = (path || '').toLowerCase();
    var s = (search || '').toLowerCase();
    var blob = (href || '').toLowerCase();
    if (/[?&](ux_mode|response_type|client_id|redirect_uri|scope)=/.test(s) || /[?&](ux_mode|response_type|client_id)=/.test(blob)) return true;
    if (/\/(oauth2?|oidc|saml|sso|authorize|signin|sign-in|sign_in|login|log-in|log_in|auth|session|connect|gsi)(\/|$|\?)/.test(p)) return true;
    if (/^(login|accounts|account|auth|sso|id|identity|signin)\./.test(h)) return true;
    if (/\.(auth0|okta|onelogin|pingidentity|duosecurity)\./.test(h) || /\.(auth0|okta)\.com$/.test(h)) return true;
    return false;
  }

  function rememberReturn(){
    try {
      var href = String(window.location.href || '');
      if (!href || href.indexOf('about:')===0) return;
      var h = (window.location.hostname || '').toLowerCase();
      var p = (window.location.pathname || '').toLowerCase();
      var s = (window.location.search || '').toLowerCase();
      // Don't overwrite return URL while already on an auth hop
      if (looksLikeAuthUrl(href, h, p, s)) return;
      sessionStorage.setItem(SS_RET, href);
      sessionStorage.setItem(SS_TS, String(Date.now()));
    } catch(e) {}
  }

  function returnUrl(){
    try {
      var r = sessionStorage.getItem(SS_RET) || '';
      var ts = parseInt(sessionStorage.getItem(SS_TS) || '0', 10) || 0;
      if (!r || !ts || (Date.now() - ts) > 20*60*1000) return '';
      return r;
    } catch(e) { return ''; }
  }

  /** Popup OAuth (ux_mode=popup) cannot finish in a single WebView — use redirect UX. */
  function rewritePopupAuthMode(abs){
    try {
      var u = new URL(abs);
      var changed = false;
      if (u.searchParams.get('ux_mode') === 'popup') {
        u.searchParams.set('ux_mode', 'redirect');
        changed = true;
      }
      // display=popup is a common OIDC/OAuth hint — prefer page
      if (u.searchParams.get('display') === 'popup') {
        u.searchParams.set('display', 'page');
        changed = true;
      }
      if (u.searchParams.get('ui_mode') === 'card') {
        u.searchParams.delete('ui_mode');
        changed = true;
      }
      return changed ? u.toString() : abs;
    } catch(e) {}
    return abs;
  }

  function go(u){
    try {
      var abs = new URL(String(u), window.location.href).href;
      abs = rewritePopupAuthMode(abs);
      if (/^https?:/i.test(abs)) {
        rememberReturn();
        window.location.assign(abs);
        return true;
      }
      if (abs.indexOf('about:')===0) {
        return false;
      }
    } catch(e) {}
    try {
      rememberReturn();
      window.location.href = String(u);
      return true;
    } catch(e2) {}
    return false;
  }

  function bounceHomeIfStuck(){
    try {
      var ret = returnUrl();
      if (!ret) return;
      var text = ((document.body && (document.body.innerText || document.body.textContent)) || '').slice(0, 1200).toLowerCase();
      var path = (window.location.pathname || '').toLowerCase();
      var host = (window.location.hostname || '').toLowerCase();
      var search = (window.location.search || '').toLowerCase();
      var href = String(window.location.href || '');
      var stuckClose = /you may now close|you can close this|close this window|return to the app|authentication complete|sign-?in complete|login successful|successfully signed in|returned to the app|you can return to/.test(text);
      var stuckPath = /\/oauth\/(success|complete|done|callback)|\/auth\/(success|complete|callback)|\/signin\/.*\/(approval|complete)/.test(path);
      // GIS /gsi/transform is a popup helper (postMessage to opener). As a
      // full document it never finishes — bounce back to the site.
      var gisTransform = /\/gsi\/transform(\/|$)/.test(path);
      var popupModeStuck = looksLikeAuthUrl(href, host, path, search)
        && (search.indexOf('ux_mode=popup') >= 0 || search.indexOf('display=popup') >= 0 || search.indexOf('ui_mode=card') >= 0);
      if (stuckClose || stuckPath || gisTransform) {
        window.location.assign(ret);
        return;
      }
      // Auth page still open in popup mode after user likely finished
      if (popupModeStuck) {
        var ts0 = parseInt(sessionStorage.getItem(SS_TS) || '0', 10) || 0;
        if (ts0 && (Date.now() - ts0) > 8000) {
          try {
            var sp = new URLSearchParams(window.location.search);
            var cont = sp.get('continue') || sp.get('redirect_uri') || sp.get('return') || sp.get('return_to') || sp.get('next');
            if (cont && /^https?:/i.test(cont)) {
              window.location.assign(cont);
              return;
            }
          } catch(e) {}
          window.location.assign(ret);
          return;
        }
      }
      // Generic account-home dead-end after auth (no active challenge in path)
      if (looksLikeAuthUrl(href, host, path, search)
          && /sign out|log out|manage account|your account|security settings/.test(text)
          && !/oauth|authorize|consent|challenge|password|verify|mfa|2fa|otp/.test(path + text.slice(0, 200))) {
        var ts = parseInt(sessionStorage.getItem(SS_TS) || '0', 10) || 0;
        if (ts && (Date.now() - ts) > 4000) {
          window.location.assign(ret);
        }
      }
    } catch(e) {}
  }

  function blankStub(){
    var closed = false;
    var stub = {
      get closed(){ return closed; },
      close: function(){
        closed = true;
        var ret = returnUrl();
        if (ret) { try { window.location.assign(ret); } catch(e) {} }
      },
      focus: function(){},
      blur: function(){},
      // Sites postMessage to popup; forward onto current window (same-tab OAuth)
      postMessage: function(msg, origin){
        try {
          var o = (origin && origin !== '*') ? origin : window.location.origin;
          window.postMessage(msg, o === '/' ? window.location.origin : origin || '*');
        } catch(e) {
          try { window.postMessage(msg, '*'); } catch(e2) {}
        }
      },
      document: document,
      window: window,
      self: null,
      frames: window.frames,
      parent: window,
      top: window
    };
    stub.self = stub;
    // opener = current window so GIS / OAuth can postMessage back
    try {
      Object.defineProperty(stub, 'opener', {
        get: function(){ return window; },
        set: function(){},
        configurable: true
      });
    } catch(e) { stub.opener = window; }
    var loc = {
      get href(){ return window.location.href; },
      set href(v){ go(v); },
      assign: function(v){ go(v); },
      replace: function(v){ try { rememberReturn(); window.location.replace(String(v)); } catch(e){ go(v); } },
      reload: function(){ try { window.location.reload(); } catch(e){} },
      toString: function(){ return window.location.href; }
    };
    try {
      Object.defineProperty(stub, 'location', {
        get: function(){ return loc; },
        set: function(v){ go(v); },
        configurable: true
      });
    } catch(e) { stub.location = loc; }
    return stub;
  }

  window.open = function(url, name, features){
    try {
      var u = (url==null || url==='') ? '' : String(url);
      var feat = features == null ? '' : String(features);
      var sized = /width\s*=|height\s*=/i.test(feat);
      var blank = !u || u === 'about:blank' || u.indexOf('about:blank')===0;
      var abs = '';
      try { if (u && u.indexOf('about:')!==0) abs = new URL(u, window.location.href).href; } catch(e) {}
      var host='', path='', search='';
      try {
        if (abs) { var parsed = new URL(abs); host=parsed.hostname; path=parsed.pathname; search=parsed.search; }
      } catch(e) {}
      var gis = /\/gsi\//i.test(u) || /\/gsi\//i.test(path) || /\/gsi\//i.test(abs);
      var auth = looksLikeAuthUrl(abs || u, host, path, search);
      // GIS (and other sized IdP windows) need a real popup with window.opener.
      // Navigating the rail to /gsi/transform is what gets the owner stuck.
      if (gis || (blank && sized) || (auth && sized)) {
        try {
          var real = origOpen ? origOpen.apply(window, arguments) : null;
          if (real) return real;
        } catch(e) {}
      }
      if (blank) {
        return blankStub();
      }
      go(u);
      // Never return null (sites treat that as "popup blocked" and hang)
      return blankStub();
    } catch(e) {
      try { return origOpen ? origOpen.apply(window, arguments) : blankStub(); } catch(e2) { return blankStub(); }
    }
  };

  // Popup flows call close() when done — take user back to the app site
  window.close = function(){
    var ret = returnUrl();
    if (ret) {
      try { window.location.assign(ret); return; } catch(e) {}
    }
    try { if (origClose) origClose.call(window); } catch(e2) {}
  };

  // target=_blank login buttons
  document.addEventListener('click', function(ev){
    try {
      var a = ev.target && ev.target.closest ? ev.target.closest('a[target="_blank"], area[target="_blank"]') : null;
      if (!a) return;
      var href = a.getAttribute('href') || '';
      if (!href || href.charAt(0)==='#') return;
      var abs = new URL(href, window.location.href).href;
      if (!/^https?:/i.test(abs)) return;
      // Only force same-window for likely auth links (any provider)
      try {
        var au = new URL(abs);
        if (!looksLikeAuthUrl(abs, au.hostname, au.pathname, au.search)
            && !/oauth|authorize|signin|sign-in|login|sso|saml|oidc/i.test(abs)) return;
      } catch(e) {
        if (!/oauth|authorize|signin|login|sso/i.test(abs)) return;
      }
      ev.preventDefault();
      ev.stopPropagation();
      go(abs);
    } catch(e) {}
  }, true);

  // After load: unstick "close this window" / stranded IdP pages
  if (document.readyState === 'complete' || document.readyState === 'interactive') {
    setTimeout(bounceHomeIfStuck, 400);
    setTimeout(bounceHomeIfStuck, 2000);
  } else {
    document.addEventListener('DOMContentLoaded', function(){
      setTimeout(bounceHomeIfStuck, 400);
      setTimeout(bounceHomeIfStuck, 2000);
    });
  }
  window.addEventListener('load', function(){ setTimeout(bounceHomeIfStuck, 600); });
  rememberReturn();
})();"#;

/// Minimal rail helpers only — no zoom, no aggressive !important chrome overrides.
/// (Those made icons invisible-but-clickable on Gmail and other SPAs.)
const RAIL_LAYOUT_JS: &str = r#"(function(){
  if (window.__remedyRailLayout) return;
  window.__remedyRailLayout = true;
  try {
    try { document.documentElement.style.zoom = ''; } catch(e) {}
    try { if (document.body) document.body.style.zoom = ''; } catch(e) {}
    // Remove prior experimental styles if a long-lived page kept them
    var old = document.getElementById('remedy-rail-layout-css');
    if (old) old.remove();
    var oldShield = document.getElementById('remedy-privacy-shield-css');
    if (oldShield) oldShield.remove();

    var s = document.createElement('style');
    s.id = 'remedy-rail-layout-css';
    (document.documentElement || document.head || document.body).appendChild(s);
    // Scrollbars only — do not touch color/fill/transform/display of app chrome
    s.textContent = [
      'html{scrollbar-gutter:stable both-edges;}',
      '::-webkit-scrollbar{width:12px;height:12px;}',
      '::-webkit-scrollbar-track{background:rgba(127,127,127,0.15);}',
      '::-webkit-scrollbar-thumb{background:rgba(127,127,127,0.55);border-radius:6px;}',
      '::-webkit-scrollbar-thumb:hover{background:rgba(127,127,127,0.75);}',
      'html.remedy-doc-scroll,html.remedy-doc-scroll body{overflow:auto!important;}'
    ].join('');
  } catch(e) {}

  function applyFit(){
    try {
      try { document.documentElement.style.zoom = ''; } catch(e) {}
      var h = window.innerHeight || document.documentElement.clientHeight || 0;
      var sh = Math.max(
        document.documentElement ? document.documentElement.scrollHeight : 0,
        document.body ? document.body.scrollHeight : 0
      );
      if (sh > h + 48) {
        document.documentElement.classList.add('remedy-doc-scroll');
      }
    } catch(e) {}
  }
  applyFit();
  window.addEventListener('resize', applyFit);
  setTimeout(applyFit, 400);
})();"#;

/// Rewrite auth navigations that cannot complete inside a single WebView.
/// Brand-agnostic: query flags + special schemes only.
fn rewrite_oauth_navigation(raw: &str) -> Option<String> {
    let s = raw.trim();
    if s.is_empty() {
        return None;
    }
    let lower = s.to_ascii_lowercase();

    // Popup OAuth (any provider using ux_mode/display=popup) needs redirect/page in-rail.
    if lower.starts_with("http://") || lower.starts_with("https://") {
        if let Ok(mut u) = Url::parse(s) {
            let pairs: Vec<(String, String)> = u
                .query_pairs()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect();
            let needs = pairs.iter().any(|(k, v)| {
                (k == "ux_mode" && v == "popup")
                    || (k == "display" && v == "popup")
                    || (k == "ui_mode" && v == "card")
            });
            if needs {
                let mut ser = url::form_urlencoded::Serializer::new(String::new());
                let mut changed = false;
                for (k, v) in &pairs {
                    if k == "ux_mode" && v == "popup" {
                        ser.append_pair("ux_mode", "redirect");
                        changed = true;
                    } else if k == "display" && v == "popup" {
                        ser.append_pair("display", "page");
                        changed = true;
                    } else if k == "ui_mode" && v == "card" {
                        changed = true; // drop
                    } else {
                        ser.append_pair(k, v);
                    }
                }
                if changed {
                    u.set_query(Some(&ser.finish()));
                    let out = u.to_string();
                    if out != s {
                        log::info!("browser oauth rewrite popup→redirect/page");
                        return Some(out);
                    }
                }
            }
        }
    }

    // Cross-origin storage relay used by some SSO SDKs: storagerelay://https/host?…
    if lower.starts_with("storagerelay://") {
        let rest = s.get("storagerelay://".len()..)?;
        let scheme_host = rest.split('?').next().unwrap_or(rest);
        let mut parts = scheme_host.splitn(2, '/');
        let scheme = parts.next().unwrap_or("https");
        let hostpath = parts.next().unwrap_or("");
        if hostpath.is_empty() {
            return None;
        }
        let host = hostpath.split('/').next().unwrap_or(hostpath);
        if host.is_empty() {
            return None;
        }
        let scheme = if scheme.eq_ignore_ascii_case("http") {
            "http"
        } else {
            "https"
        };
        let out = format!("{scheme}://{host}/");
        log::info!("browser oauth rewrite storagerelay → {out}");
        return Some(out);
    }

    // Android intent://…;S.browser_fallback_url=https%3A%2F%2F…
    if lower.starts_with("intent:") {
        if let Some(idx) = lower.find("s.browser_fallback_url=") {
            let start = idx + "s.browser_fallback_url=".len();
            let rest = s.get(start..)?;
            let end = rest.find(';').unwrap_or(rest.len());
            let enc = rest.get(..end)?.trim();
            if let Ok(dec) = urlencoding_minimal(enc) {
                if dec.starts_with("http://") || dec.starts_with("https://") {
                    log::info!("browser oauth rewrite intent fallback → {dec}");
                    return Some(dec);
                }
            }
        }
    }

    None
}

/// Path/query heuristics for auth flows (no brand host hardcoding).
fn looks_like_auth_url_str(url: &str) -> bool {
    let lower = url.to_ascii_lowercase();
    if lower.contains("ux_mode=")
        || lower.contains("response_type=")
        || lower.contains("client_id=")
        || lower.contains("redirect_uri=")
    {
        return true;
    }
    // Path segments common to OAuth/OIDC/SAML/login
    for token in [
        "/oauth",
        "/oauth2",
        "/oidc",
        "/saml",
        "/sso/",
        "/authorize",
        "/signin",
        "/sign-in",
        "/sign_in",
        "/login",
        "/log-in",
        "/log_in",
        "/auth/",
        "/session",
        "/connect/",
        "/gsi/",
    ] {
        if lower.contains(token) {
            return true;
        }
    }
    false
}

/// Google Identity Services last hop after account pick. It only works as a
/// popup that `postMessage`s to `window.opener` — as a full document it hangs.
fn is_gsi_transformer_url(url: &str) -> bool {
    url.to_ascii_lowercase().contains("/gsi/transform")
}

/// Native `window.open` must be allowed for IdP / GIS popups. Wry denies every
/// new window unless `on_new_window` returns Allow (see wry webview2).
fn should_allow_oauth_popup(url: &str) -> bool {
    let s = url.trim();
    let lower = s.to_ascii_lowercase();
    if s.is_empty()
        || lower == "about:blank"
        || lower.starts_with("about:blank")
        || lower.starts_with("about:")
    {
        return true;
    }
    if lower.contains("/gsi/") {
        return true;
    }
    crate::privacy_shield::is_identity_provider_url(s) || looks_like_auth_url_str(s)
}

fn should_remember_oauth_return(prev: &str, next: &str) -> bool {
    if prev.is_empty() || prev.starts_with("about:") {
        return false;
    }
    let next_auth = crate::privacy_shield::is_identity_provider_url(next)
        || next.to_ascii_lowercase().contains("/gsi/");
    if !next_auth {
        return false;
    }
    // Same-host IdP hops (select → transform) must not overwrite the site URL.
    if let (Ok(p), Ok(n)) = (Url::parse(prev), Url::parse(next)) {
        if p.host_str().is_some() && p.host_str() == n.host_str() {
            return false;
        }
    }
    true
}

fn bounce_target_for_gsi_transformer(current: &str, return_url: Option<&str>) -> Option<String> {
    if !is_gsi_transformer_url(current) {
        return None;
    }
    let ret = return_url
        .map(str::trim)
        .filter(|r| !r.is_empty() && !r.starts_with("about:") && *r != current)?;
    Some(ret.to_string())
}

fn urlencoding_minimal(enc: &str) -> Result<String, ()> {
    let bytes = enc.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() => {
                let h = |c: u8| -> Option<u8> {
                    match c {
                        b'0'..=b'9' => Some(c - b'0'),
                        b'a'..=b'f' => Some(c - b'a' + 10),
                        b'A'..=b'F' => Some(c - b'A' + 10),
                        _ => None,
                    }
                };
                if let (Some(a), Some(b)) = (h(bytes[i + 1]), h(bytes[i + 2])) {
                    out.push((a << 4) | b);
                    i += 3;
                    continue;
                }
                out.push(bytes[i]);
                i += 1;
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            c => {
                out.push(c);
                i += 1;
            }
        }
    }
    String::from_utf8(out).map_err(|_| ())
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct BrowserBounds {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

/// Chrome mobile — sites serve compact / mobile templates suited to the rail.
// UA strings carry the *installed* engine's major version. A frozen
// "Chrome/131" on a 151 runtime disagreed with the engine's own client hints
// (Sec-CH-UA says 151) — the exact mismatch retail anti-bot stacks key on;
// Walmart served "Robot or human?" on the first navigate. Honest = invisible.
const UA_FALLBACK_MAJOR: &str = "131";

fn engine_major() -> String {
    let m = tauri::webview_version()
        .ok()
        .and_then(|v| v.split('.').next().map(str::to_string))
        .filter(|s| !s.is_empty() && s.chars().all(|c| c.is_ascii_digit()))
        .unwrap_or_else(|| UA_FALLBACK_MAJOR.to_string());
    // WebKitGTK reports 2.x — that is not a Chrome major. Using it produces
    // "Chrome/2.0.0.0" which anti-bot walls reject immediately.
    match m.parse::<u32>() {
        Ok(n) if n >= 80 => m,
        _ => UA_FALLBACK_MAJOR.to_string(),
    }
}

fn ua_mobile() -> String {
    let m = engine_major();
    format!(
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{m}.0.0.0 Mobile Safari/537.36"
    )
}

/// Desktop Edge — byte-identical to the WebView2 default UA for this runtime,
/// so desktop mode is not an override at all.
fn ua_desktop() -> String {
    let m = engine_major();
    #[cfg(target_os = "linux")]
    {
        return format!(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{m}.0.0.0 Safari/537.36"
        );
    }
    #[cfg(not(target_os = "linux"))]
    format!(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{m}.0.0.0 Safari/537.36 Edg/{m}.0.0.0"
    )
}

/// CDP `Emulation.setUserAgentOverride` params so the client hints
/// (Sec-CH-UA-Platform / -Mobile / brands) agree with the UA header.
///
/// This is ordinary device emulation — the same pairing Chrome DevTools sends
/// for its device modes, and the same choice a phone browser's "Request
/// desktop site" makes. The rail is a narrow viewport, so it asks for the
/// mobile view by default and the owner can flip it. Without the matching
/// hints the UA says Android while the hints still say Windows, and a site
/// that trusts the hints serves a desktop layout into a phone-width viewport:
/// the page is broken, not the request disguised.
#[cfg(windows)]
fn ua_override_params(desktop: bool) -> String {
    let m = engine_major();
    let ua = rail_user_agent(desktop);
    let brands = json!([
        {"brand": "Chromium", "version": m},
        {"brand": if desktop { "Microsoft Edge" } else { "Google Chrome" }, "version": m},
        {"brand": "Not.A/Brand", "version": "99"},
    ]);
    let meta = if desktop {
        json!({
            "brands": brands,
            "fullVersionList": brands,
            "platform": "Windows",
            "platformVersion": "15.0.0",
            "architecture": "x86",
            "model": "",
            "mobile": false,
            "bitness": "64",
            "wow64": false,
        })
    } else {
        json!({
            "brands": brands,
            "fullVersionList": brands,
            "platform": "Android",
            "platformVersion": "14.0.0",
            "architecture": "",
            "model": "Pixel 8",
            "mobile": true,
            "bitness": "",
            "wow64": false,
        })
    };
    json!({
        "userAgent": ua,
        "acceptLanguage": "en-US,en;q=0.9",
        "platform": if desktop { "Win32" } else { "Linux armv81" },
        "userAgentMetadata": meta,
    })
    .to_string()
}

/// Make the client hints match the UA header (best-effort, Windows CDP).
#[cfg(windows)]
fn apply_ua_client_hints(wv: &tauri::Webview, desktop: bool) {
    let params = ua_override_params(desktop);
    if let Err(e) = cdp_call(wv, "Emulation.setUserAgentOverride", params) {
        log::warn!("browser UA client-hints override failed: {e}");
    }
}

#[cfg(not(windows))]
fn apply_ua_client_hints(_wv: &tauri::Webview, _desktop: bool) {}

fn browser_rail_prefs_path() -> std::path::PathBuf {
    let home = if cfg!(target_os = "windows") {
        std::env::var("USERPROFILE").unwrap_or_else(|_| ".".into())
    } else {
        std::env::var("HOME").unwrap_or_else(|_| ".".into())
    };
    std::path::PathBuf::from(home)
        .join(".remedy")
        .join("browser_rail.json")
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
struct BrowserRailPrefs {
    /// When true (default), the embed is a plain Edge/WebView2 desktop
    /// browser — the UA it really is. Mobile view stays one click away for
    /// narrow rails; it carries an Android UA + matching client hints, but a
    /// desktop engine claiming to be a phone is what retail bot walls flag,
    /// so it is opt-in rather than the default.
    pub desktop_site: bool,
}

impl Default for BrowserRailPrefs {
    fn default() -> Self {
        Self {
            desktop_site: true,
        }
    }
}

fn load_rail_prefs() -> BrowserRailPrefs {
    let p = browser_rail_prefs_path();
    if let Ok(raw) = std::fs::read_to_string(&p) {
        if let Ok(prefs) = serde_json::from_str::<BrowserRailPrefs>(&raw) {
            return prefs;
        }
    }
    BrowserRailPrefs::default()
}

fn save_rail_prefs(prefs: &BrowserRailPrefs) {
    let p = browser_rail_prefs_path();
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(raw) = serde_json::to_string_pretty(prefs) {
        let _ = std::fs::write(p, raw);
    }
}

pub struct BrowserState {
    current_url: Mutex<String>,
    /// Last URL a load event actually committed. Used as prev_url for the
    /// stale-Finished guard — `current_url` may already be the *destination*
    /// (address bar) before on_page_load fires.
    last_committed_url: Mutex<String>,
    last_bounds: Mutex<Option<BrowserBounds>>,
    /// When true, do not show() the embed (Settings/Help/overlays cover the host).
    /// Prevents the native WebView2 HWND from floating above React chrome.
    stack_suppressed: AtomicBool,
    /// Request desktop site (full UA); default mobile for rail formatting.
    desktop_site: AtomicBool,
    /// HTML/video fullscreen active — SPA rail bounds must not shrink the embed.
    page_fullscreen: AtomicBool,
    /// Navigate job waiting for on_page_load (complete after the page is actually open).
    pending_navigate: Mutex<Option<(String, String)>>,
    /// Site URL to restore if GIS dumps `/gsi/transform` into the rail (cross-origin
    /// so sessionStorage on accounts.google.com cannot hold the return URL).
    oauth_return_url: Mutex<Option<String>>,
}

impl Default for BrowserState {
    fn default() -> Self {
        let prefs = load_rail_prefs();
        Self {
            current_url: Mutex::new("https://github.com/AhmiDarrow/RemedyAI".into()),
            last_committed_url: Mutex::new("https://github.com/AhmiDarrow/RemedyAI".into()),
            last_bounds: Mutex::new(None),
            stack_suppressed: AtomicBool::new(false),
            desktop_site: AtomicBool::new(prefs.desktop_site),
            page_fullscreen: AtomicBool::new(false),
            pending_navigate: Mutex::new(None),
            oauth_return_url: Mutex::new(None),
        }
    }
}

fn rail_user_agent(desktop: bool) -> String {
    if desktop {
        ua_desktop()
    } else {
        ua_mobile()
    }
}

/// Ensure mobile pages get a proper viewport (some sites omit it and look huge).
const MOBILE_VIEWPORT_JS: &str = r#"(function(){
  try {
    var m = document.querySelector('meta[name="viewport"]');
    if (!m) {
      m = document.createElement('meta');
      m.setAttribute('name', 'viewport');
      (document.head || document.documentElement).appendChild(m);
    }
    m.setAttribute('content', 'width=device-width, initial-scale=1, maximum-scale=5, viewport-fit=cover');
  } catch(e) {}
})();"#;

/// Desktop mode: wide viewport so sites don't stay stuck in mobile layout after toggle.
const DESKTOP_VIEWPORT_JS: &str = r#"(function(){
  try {
    var m = document.querySelector('meta[name="viewport"]');
    if (!m) {
      m = document.createElement('meta');
      m.setAttribute('name', 'viewport');
      (document.head || document.documentElement).appendChild(m);
    }
    // Wide fixed width — many sites key off this for "desktop" CSS.
    m.setAttribute('content', 'width=1280, initial-scale=1');
  } catch(e) {}
})();"#;

/// Tell the page the WebView (rail host) *is* the screen — no polyfill of
/// requestFullscreen. Sites use screen/inner dimensions; matching them to the
/// rail makes native fullscreen fill this surface only.
const RAIL_AS_SCREEN_JS: &str = r#"(function(){
  if (window.__remedyRailAsScreen) return;
  window.__remedyRailAsScreen = true;
  function wh() {
    var w = window.innerWidth || document.documentElement.clientWidth || 1;
    var h = window.innerHeight || document.documentElement.clientHeight || 1;
    return { w: Math.max(1, Math.round(w)), h: Math.max(1, Math.round(h)) };
  }
  function patch(proto, keys, pick) {
    keys.forEach(function(k) {
      try {
        Object.defineProperty(proto, k, {
          configurable: true,
          enumerable: true,
          get: function() { return pick(wh()); }
        });
      } catch (e) {}
    });
  }
  try {
    patch(Screen.prototype, ['width', 'availWidth'], function(d) { return d.w; });
    patch(Screen.prototype, ['height', 'availHeight'], function(d) { return d.h; });
  } catch (e) {}
  // Minimal CSS: when native :fullscreen fires, fill the WebView (the rail).
  try {
    var s = document.createElement('style');
    s.id = 'remedy-rail-fs';
    s.textContent = [
      ':fullscreen,:-webkit-full-screen{box-sizing:border-box;width:100%!important;height:100%!important;max-width:100vw!important;max-height:100vh!important;background:#000!important;}',
      'video:fullscreen,video:-webkit-full-screen{object-fit:contain;width:100%!important;height:100%!important;}'
    ].join('');
    (document.documentElement || document.head).appendChild(s);
  } catch (e) {}
})();"#;

fn normalize_url(raw: &str) -> Result<String, String> {
    let u = raw.trim();
    if u.is_empty() {
        return Err("empty url".into());
    }
    // Block task-text leaks: "gmail sign in, type user@…" must never become
    // https://gmail sign in… in the address bar.
    if u.contains(' ') || u.contains('\n') || u.contains('\t') {
        return Err("invalid url: spaces (refusing task-text leak)".into());
    }
    if u.contains('@') && !u.starts_with("http://") && !u.starts_with("https://") {
        return Err("invalid url: looks like email, not a page URL".into());
    }
    // Commas in host (before ?) are never valid
    let host_part = u.split('?').next().unwrap_or(u).split('#').next().unwrap_or(u);
    if host_part.contains(',') || host_part.contains(';') || host_part.contains('"') {
        return Err("invalid url: illegal characters in host".into());
    }
    if u.starts_with("javascript:") || u.starts_with("data:") || u.starts_with("file:") {
        return Err("unsupported url scheme".into());
    }
    let candidate = if u.starts_with("http://")
        || u.starts_with("https://")
        || u.starts_with("about:")
    {
        u.to_string()
    } else {
        format!("https://{u}")
    };
    if candidate.starts_with("about:") {
        return Ok(candidate);
    }
    let parsed: Url = candidate
        .parse()
        .map_err(|e: url::ParseError| format!("invalid url: {e}"))?;
    let scheme = parsed.scheme();
    if scheme != "http" && scheme != "https" {
        return Err(format!("unsupported url scheme: {scheme}"));
    }
    // Reject userinfo (user:pass@host or empty @host) — credentials must not
    // land in the rail address bar.
    if let Some(after) = candidate.splitn(2, "://").nth(1) {
        let authority = after.split(['/', '?', '#']).next().unwrap_or(after);
        if authority.contains('@') {
            return Err("blocked url userinfo".into());
        }
    }
    let host = parsed
        .host_str()
        .ok_or_else(|| "invalid url: missing host".to_string())?;
    if host.contains(' ') || host.is_empty() {
        return Err("invalid url: bad host".into());
    }
    // Unwrap IPv4-mapped IPv6 before private-IP / metadata checks.
    let host_for_ip = if let Ok(v6) = host.parse::<std::net::Ipv6Addr>() {
        v6.to_ipv4_mapped()
            .map(|v4| v4.to_string())
            .unwrap_or_else(|| host.to_string())
    } else {
        host.to_string()
    };
    // Require a real domain (has a dot) or localhost / IPv4 / mapped IPv4
    let ok_host = host == "localhost"
        || host.parse::<std::net::Ipv4Addr>().is_ok()
        || host_for_ip.parse::<std::net::Ipv4Addr>().is_ok()
        || host.contains('.');
    if !ok_host {
        return Err(format!("invalid url host: {host}"));
    }
    // Mirror Python is_valid_navigate_url: IMDS / public IPs stay out of the rail.
    let host_lc = host.to_ascii_lowercase();
    let ip_lc = host_for_ip.to_ascii_lowercase();
    if host_lc == "metadata.google.internal"
        || host_lc == "metadata.goog"
        || host_lc == "metadata"
        || host_lc.split('.').any(|label| label == "metadata")
        || host_lc == "instance-data"
        || host_lc.ends_with(".internal")
        || host_lc.ends_with(".nip.io")
        || host_lc.ends_with(".sslip.io")
        || host_lc.ends_with(".xip.io")
        || host_lc.starts_with("169.254.")
        || ip_lc.starts_with("169.254.")
    {
        return Err(format!("blocked metadata / link-local host: {host}"));
    }
    if let Ok(ip) = host_for_ip.parse::<std::net::Ipv4Addr>() {
        let o = ip.octets();
        let loopback = o[0] == 127 || ip.is_unspecified();
        let rfc1918 = o[0] == 10
            || (o[0] == 192 && o[1] == 168)
            || (o[0] == 172 && (16..=31).contains(&o[1]));
        if !loopback && !rfc1918 {
            return Err(format!("blocked non-private IP: {host}"));
        }
    }
    Ok(candidate)
}

#[cfg(test)]
mod normalize_url_tests {
    use super::normalize_url;

    #[test]
    fn rejects_userinfo() {
        assert!(normalize_url("https://user:pass@example.com/").is_err());
        assert!(normalize_url("https://@mail.google.com/").is_err());
    }

    #[test]
    fn unwraps_ipv4_mapped_link_local() {
        assert!(normalize_url("http://[::ffff:169.254.169.254]/").is_err());
        assert!(normalize_url("http://169.254.169.254/").is_err());
    }

    #[test]
    fn rejects_metadata_dns_label() {
        assert!(normalize_url("http://metadata.nicob.net/").is_err());
    }

    #[test]
    fn allows_https_public_host() {
        assert!(normalize_url("https://mail.google.com").is_ok());
    }
}

#[cfg(test)]
mod oauth_popup_tests {
    use super::{
        bounce_target_for_gsi_transformer, is_gsi_transformer_url, looks_like_auth_url_str,
        should_allow_oauth_popup, should_remember_oauth_return,
    };

    #[test]
    fn gsi_transform_is_the_stuck_helper() {
        assert!(is_gsi_transformer_url(
            "https://accounts.google.com/gsi/transform"
        ));
        assert!(!is_gsi_transformer_url(
            "https://accounts.google.com/gsi/select"
        ));
    }

    #[test]
    fn native_popup_allowed_for_gis_and_blank() {
        assert!(should_allow_oauth_popup("about:blank"));
        assert!(should_allow_oauth_popup(""));
        assert!(should_allow_oauth_popup(
            "https://accounts.google.com/gsi/select?client_id=x"
        ));
        assert!(should_allow_oauth_popup(
            "https://accounts.google.com/gsi/transform"
        ));
        assert!(!should_allow_oauth_popup("https://example.com/shop"));
    }

    #[test]
    fn remembers_site_when_entering_idp() {
        assert!(should_remember_oauth_return(
            "https://github.com/login",
            "https://accounts.google.com/gsi/select"
        ));
        assert!(!should_remember_oauth_return(
            "https://accounts.google.com/gsi/select",
            "https://accounts.google.com/gsi/transform"
        ));
        assert!(!should_remember_oauth_return(
            "https://mail.example.com/inbox",
            "https://mail.example.com/inbox?tab=1"
        ));
    }

    #[test]
    fn bounces_transform_to_saved_site() {
        assert_eq!(
            bounce_target_for_gsi_transformer(
                "https://accounts.google.com/gsi/transform",
                Some("https://mail.example.com/inbox")
            )
            .as_deref(),
            Some("https://mail.example.com/inbox")
        );
        assert!(bounce_target_for_gsi_transformer(
            "https://accounts.google.com/gsi/select",
            Some("https://mail.example.com/inbox")
        )
        .is_none());
    }

    #[test]
    fn gsi_path_looks_like_auth() {
        assert!(looks_like_auth_url_str(
            "https://accounts.google.com/gsi/transform"
        ));
    }
}

fn main_window(app: &AppHandle) -> Result<tauri::Window, String> {
    // Prefer explicit Window handle; WebviewWindow apps often only register via webview.
    if let Some(w) = app.get_window("main") {
        return Ok(w);
    }
    if let Some(wv) = app.get_webview("main") {
        return Ok(wv.window());
    }
    if let Some(ww) = app.get_webview_window("main") {
        // WebviewWindow shares label with its primary webview.
        if let Some(wv) = app.get_webview(ww.label()) {
            return Ok(wv.window());
        }
    }
    // Last resort: any window (single-window desktop).
    if let Some((_, w)) = app.windows().into_iter().next() {
        log::warn!("browser: using fallback window label={}", w.label());
        return Ok(w);
    }
    Err("main window not found — cannot embed browser".into())
}

fn clamp_bounds(b: &BrowserBounds) -> BrowserBounds {
    // Never park the embed over the in-app title strip (36px). SPA also clamps
    // against live chrome; this is a hard floor when defaults/stale bounds win.
    const MIN_TOP: f64 = 36.0;
    let mut y = b.y.max(0.0);
    let mut height = b.height.max(80.0);
    if y < MIN_TOP {
        let delta = MIN_TOP - y;
        y = MIN_TOP;
        height = (height - delta).max(80.0);
    }
    BrowserBounds {
        x: b.x.max(0.0),
        y,
        width: b.width.max(80.0),
        height,
    }
}

fn apply_bounds(wv: &tauri::Webview, b: &BrowserBounds, allow_show: bool) -> Result<(), String> {
    wv.set_position(LogicalPosition::new(b.x, b.y))
        .map_err(|e| format!("set_position: {e}"))?;
    wv.set_size(LogicalSize::new(b.width, b.height))
        .map_err(|e| format!("set_size: {e}"))?;
    if allow_show {
        let _ = wv.show();
    } else {
        let _ = wv.hide();
    }
    #[cfg(any(
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    ))]
    linux_place_embed(&wv.window(), b, allow_show);
    Ok(())
}

/// Tauri Linux `add_child` packs the child into the default `gtk::Box`, which
/// splits the window (main webview + browser stacked). Reparent the Browser
/// child onto a `gtk::Overlay` so it floats in the rail slot.
#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_place_embed(window: &tauri::Window, bounds: &BrowserBounds, allow_show: bool) {
    let b = bounds.clone();
    let win = window.clone();
    if let Err(e) = window.run_on_main_thread(move || {
        if let Err(err) = linux_place_embed_gtk(&win, &b, allow_show) {
            log::warn!("linux browser overlay: {err}");
        }
    }) {
        log::warn!("linux browser overlay schedule: {e}");
    }
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
const LINUX_OVERLAY_NAME: &str = "remedy-rail-overlay";
#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
const LINUX_EMBED_NAME: &str = "remedy-browser-embed";

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_widget_is_webkit(w: &gtk::Widget) -> bool {
    use gtk::glib::prelude::ObjectExt;
    w.type_().name().contains("WebKitWebView")
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_find_embed_widget(window: &tauri::Window) -> Result<gtk::Widget, String> {
    use gtk::prelude::*;

    let vbox = window
        .default_vbox()
        .map_err(|e| format!("default_vbox: {e}"))?;
    if let Some(overlay) = vbox
        .children()
        .into_iter()
        .find(|c| c.widget_name() == LINUX_OVERLAY_NAME)
        .and_then(|c| c.downcast::<gtk::Overlay>().ok())
    {
        let base = overlay.child();
        for child in overlay.children() {
            if base.as_ref() == Some(&child) {
                continue;
            }
            if linux_widget_is_webkit(&child) || child.widget_name() == LINUX_EMBED_NAME {
                return Ok(child);
            }
        }
    }
    vbox.children()
        .into_iter()
        .find(|c| c.widget_name() == LINUX_EMBED_NAME || linux_widget_is_webkit(c))
        .ok_or_else(|| "browser embed widget not found".into())
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_on_embed<F, R>(wv: &tauri::Webview, f: F) -> Result<R, String>
where
    F: FnOnce(&gtk::Widget) -> Result<R, String> + Send + 'static,
    R: Send + 'static,
{
    let window = wv.window();
    let (tx, rx) = std::sync::mpsc::sync_channel(1);
    let window_for_cb = window.clone();
    window
        .run_on_main_thread(move || {
            let result = match linux_find_embed_widget(&window_for_cb) {
                Ok(widget) => f(&widget),
                Err(e) => Err(e),
            };
            let _ = tx.send(result);
        })
        .map_err(|e| format!("linux main thread: {e}"))?;
    rx.recv_timeout(Duration::from_secs(4))
        .map_err(|_| "linux embed input timeout".to_string())?
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_dispatch_pointer(
    widget: &gtk::Widget,
    kind: &str,
    x: f64,
    y: f64,
    button: u32,
) -> Result<(), String> {
    use gtk::gdk;
    use gtk::glib::translate::ToGlibPtr;
    use gtk::prelude::*;

    let window = widget.window().ok_or_else(|| "embed GdkWindow missing".to_string())?;
    let display = window.display();
    let seat = display
        .default_seat()
        .ok_or_else(|| "no GDK seat".to_string())?;
    let device = seat
        .pointer()
        .ok_or_else(|| "no pointer device".to_string())?;
    let ty = match kind {
        "mouseMoved" | "motion" => gdk::EventType::MotionNotify,
        "mousePressed" | "press" => gdk::EventType::ButtonPress,
        "mouseReleased" | "release" => gdk::EventType::ButtonRelease,
        _ => return Err(format!("unknown linux pointer kind {kind}")),
    };
    let mut event = gdk::Event::new(ty);
    let (_ok, ox, oy) = window.origin();
    let win_ptr = window.to_glib_full();
    unsafe {
        if ty == gdk::EventType::MotionNotify {
            let ptr = event.as_ptr() as *mut gdk::ffi::GdkEventMotion;
            (*ptr).window = win_ptr;
            (*ptr).send_event = 0;
            (*ptr).x = x;
            (*ptr).y = y;
            (*ptr).x_root = x + f64::from(ox);
            (*ptr).y_root = y + f64::from(oy);
            (*ptr).state = if button == 0 {
                gdk::ModifierType::empty().bits()
            } else {
                gdk::ModifierType::BUTTON1_MASK.bits()
            };
        } else {
            let ptr = event.as_ptr() as *mut gdk::ffi::GdkEventButton;
            (*ptr).window = win_ptr;
            (*ptr).send_event = 0;
            (*ptr).x = x;
            (*ptr).y = y;
            (*ptr).x_root = x + f64::from(ox);
            (*ptr).y_root = y + f64::from(oy);
            (*ptr).button = button.max(1);
            (*ptr).state = if kind.contains("Released") || kind == "release" {
                gdk::ModifierType::empty().bits()
            } else {
                gdk::ModifierType::BUTTON1_MASK.bits()
            };
        }
    }
    event.set_device(Some(&device));
    widget.grab_focus();
    if !widget.event(&event) {
        log::debug!("linux pointer event not handled ({kind})");
    }
    Ok(())
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_dispatch_key(widget: &gtk::Widget, press: bool, keyval: u32) -> Result<(), String> {
    use gtk::gdk;
    use gtk::glib::translate::ToGlibPtr;
    use gtk::prelude::*;

    let window = widget.window().ok_or_else(|| "embed GdkWindow missing".to_string())?;
    let display = window.display();
    let seat = display
        .default_seat()
        .ok_or_else(|| "no GDK seat".to_string())?;
    let device = seat
        .keyboard()
        .or_else(|| seat.pointer())
        .ok_or_else(|| "no keyboard device".to_string())?;
    let ty = if press {
        gdk::EventType::KeyPress
    } else {
        gdk::EventType::KeyRelease
    };
    let mut event = gdk::Event::new(ty);
    let win_ptr = window.to_glib_full();
    unsafe {
        let ptr = event.as_ptr() as *mut gdk::ffi::GdkEventKey;
        (*ptr).window = win_ptr;
        (*ptr).send_event = 0;
        (*ptr).keyval = keyval;
        (*ptr).hardware_keycode = 0;
        (*ptr).state = gdk::ModifierType::empty().bits();
        (*ptr).group = 0;
        (*ptr).is_modifier = 0;
    }
    event.set_device(Some(&device));
    widget.grab_focus();
    let _ = widget.event(&event);
    Ok(())
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_mouse(
    wv: &tauri::Webview,
    kind: &str,
    x: f64,
    y: f64,
    button: &str,
) -> Result<(), String> {
    let kind_s = kind.to_string();
    let btn: u32 = if button == "right" { 3 } else { 1 };
    linux_on_embed(wv, move |widget| {
        linux_dispatch_pointer(widget, &kind_s, x, y, btn)
    })
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_click_trusted(wv: &tauri::Webview, x: f64, y: f64, button: &str) -> Result<(), String> {
    let _ = linux_mouse(wv, "mouseMoved", x, y, "none");
    linux_mouse(wv, "mousePressed", x, y, button)?;
    linux_mouse(wv, "mouseReleased", x, y, button)
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_hover_trusted(wv: &tauri::Webview, x: f64, y: f64) -> Result<(), String> {
    linux_mouse(wv, "mouseMoved", x, y, "none")
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_insert_text(wv: &tauri::Webview, text: &str) -> Result<(), String> {
    use gtk::gdk::keys::Key;

    let chars: Vec<u32> = text
        .chars()
        .map(|ch| {
            if ch == '\n' || ch == '\r' {
                *gtk::gdk::keys::constants::Return
            } else if ch == '\t' {
                *gtk::gdk::keys::constants::Tab
            } else {
                *Key::from_unicode(ch)
            }
        })
        .filter(|k| *k != 0)
        .collect();
    linux_on_embed(wv, move |widget| {
        for keyval in &chars {
            linux_dispatch_key(widget, true, *keyval)?;
            linux_dispatch_key(widget, false, *keyval)?;
        }
        Ok(())
    })
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_named_key(wv: &tauri::Webview, key: &str) -> Option<Result<(), String>> {
    use gtk::gdk::keys::constants as k;
    let keyval: u32 = match key {
        "enter" | "return" | "Enter" => *k::Return,
        "tab" | "Tab" => *k::Tab,
        "esc" | "escape" | "Escape" => *k::Escape,
        "backspace" | "Backspace" => *k::BackSpace,
        "delete" | "del" | "Delete" => *k::Delete,
        "ArrowLeft" => *k::Left,
        "ArrowUp" => *k::Up,
        "ArrowRight" => *k::Right,
        "ArrowDown" => *k::Down,
        "PageUp" => *k::Page_Up,
        "PageDown" => *k::Page_Down,
        "Home" => *k::Home,
        "End" => *k::End,
        _ => return None,
    };
    Some(linux_on_embed(wv, move |widget| {
        linux_dispatch_key(widget, true, keyval)?;
        linux_dispatch_key(widget, false, keyval)
    }))
}

#[cfg(any(
    target_os = "linux",
    target_os = "dragonfly",
    target_os = "freebsd",
    target_os = "netbsd",
    target_os = "openbsd"
))]
fn linux_place_embed_gtk(
    window: &tauri::Window,
    bounds: &BrowserBounds,
    allow_show: bool,
) -> Result<(), String> {
    use gtk::prelude::*;
    use gtk::{Align, Overlay};

    let vbox = window
        .default_vbox()
        .map_err(|e| format!("default_vbox: {e}"))?;

    const OVERLAY_NAME: &str = LINUX_OVERLAY_NAME;
    const EMBED_NAME: &str = LINUX_EMBED_NAME;

    let overlay = if let Some(existing) = vbox
        .children()
        .into_iter()
        .find(|c| c.widget_name() == OVERLAY_NAME)
        .and_then(|c| c.downcast::<Overlay>().ok())
    {
        existing
    } else {
        let overlay = Overlay::new();
        overlay.set_widget_name(OVERLAY_NAME);
        overlay.set_hexpand(true);
        overlay.set_vexpand(true);
        overlay.set_halign(Align::Fill);
        overlay.set_valign(Align::Fill);

        let main_wv = vbox.children().into_iter().find(linux_widget_is_webkit);
        if let Some(mw) = main_wv {
            vbox.remove(&mw);
            mw.set_hexpand(true);
            mw.set_vexpand(true);
            mw.set_halign(Align::Fill);
            mw.set_valign(Align::Fill);
            overlay.add(&mw);
            mw.show();
        }
        vbox.pack_start(&overlay, true, true, 0);
        vbox.reorder_child(&overlay, 0);
        overlay.show();
        overlay.queue_resize();
        overlay
    };

    // add_child packs a new WebKitWebView into the vbox — steal it onto the overlay.
    let extras: Vec<gtk::Widget> = vbox
        .children()
        .into_iter()
        .filter(linux_widget_is_webkit)
        .collect();
    for extra in extras {
        vbox.remove(&extra);
        extra.set_widget_name(EMBED_NAME);
        extra.set_halign(Align::Start);
        extra.set_valign(Align::Start);
        extra.set_hexpand(false);
        extra.set_vexpand(false);
        overlay.add_overlay(&extra);
    }

    let base = overlay.child();
    for child in overlay.children() {
        if base.as_ref() == Some(&child) {
            continue;
        }
        if !(linux_widget_is_webkit(&child) || child.widget_name() == EMBED_NAME) {
            continue;
        }
        child.set_halign(Align::Start);
        child.set_valign(Align::Start);
        child.set_hexpand(false);
        child.set_vexpand(false);
        child.set_margin_start(bounds.x.round().max(0.0) as i32);
        child.set_margin_top(bounds.y.round().max(0.0) as i32);
        child.set_size_request(
            bounds.width.round().max(80.0) as i32,
            bounds.height.round().max(80.0) as i32,
        );
        if allow_show {
            child.show();
        } else {
            child.hide();
        }
    }
    Ok(())
}

fn embed_may_show(state: &BrowserState) -> bool {
    !state.stack_suppressed.load(Ordering::SeqCst)
}

/// WebView2: page/video fullscreen stays inside the rail host.
/// SPA may hide toolbar/header so the host grows to the full rail panel;
/// we never expand past last_bounds into the whole app window.
///
/// Handler is intentionally leaked after attach — COM callbacks must outlive
/// the with_webview closure or events never fire.
#[cfg(windows)]
fn attach_fullscreen_handler(app: AppHandle, wv: tauri::Webview) {
    let app_cb = app.clone();
    let result = wv.with_webview(move |platform| {
        use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2;
        use webview2_com::ContainsFullScreenElementChangedEventHandler;

        let controller = platform.controller();
        let core = match unsafe { controller.CoreWebView2() } {
            Ok(c) => c,
            Err(e) => {
                log::warn!("browser fullscreen: CoreWebView2 failed: {e}");
                return;
            }
        };
        let app_inner = app_cb.clone();
        let handler = ContainsFullScreenElementChangedEventHandler::create(Box::new(
            move |sender: Option<ICoreWebView2>, _args| {
                let Some(sender) = sender else {
                    return Ok(());
                };
                let mut is_fs = windows::core::BOOL(0);
                if unsafe { sender.ContainsFullScreenElement(&mut is_fs) }.is_err() {
                    return Ok(());
                }
                let fullscreen = is_fs.as_bool();
                let app3 = app_inner.clone();
                let _ = app3.clone().run_on_main_thread(move || {
                    apply_page_fullscreen(&app3, fullscreen);
                });
                Ok(())
            },
        ));
        let mut token = 0i64;
        if let Err(e) = unsafe { core.add_ContainsFullScreenElementChanged(&handler, &mut token) }
        {
            log::warn!("browser fullscreen handler attach failed: {e}");
        } else {
            // Keep COM handler alive for the lifetime of the process/embed.
            std::mem::forget(handler);
            log::info!("browser fullscreen handler attached (token={token})");
        }
    });
    if let Err(e) = result {
        log::warn!("browser with_webview for fullscreen failed: {e}");
    }
}

#[cfg(not(windows))]
fn attach_fullscreen_handler(_app: AppHandle, _wv: tauri::Webview) {}

// ---------------------------------------------------------------------------
// Trusted input. Synthetic `el.dispatchEvent(new MouseEvent(...))`
// carries `event.isTrusted === false` — the loudest bot signal retail
// anti-fraud stacks (DataDome / PerimeterX / Akamai / Turnstile) key on, and
// cart/checkout pages are the most guarded pages on the web.
// Windows: WebView2 CDP (`Input.dispatchMouseEvent`) — DevTools/Playwright path.
// Linux: GDK button/key events on the WebKitGTK embed — same OS input pipeline.
// Pages see trusted events. Remedy shops *with* her user present — trusted
// input + human-held checkout is the honest posture.
// ---------------------------------------------------------------------------

/// JS prelude that binds `el` for type/type_text.
///
/// Prefers a snapshot ref, then a visible-text query among editable fields
/// (placeholder / aria / associated <label>), then the focused node. A stale
/// ref with a query relocates in the same pass instead of failing closed.
/// Early `return` strings (`missing-ref:…` / `no-match:…`) are for the
/// wrapping IIFE. Empty ref+query keeps the old activeElement path.
/// Routing tokens (`browser` / `desktop` / ...) are not field locators.
fn type_locate_js(r#ref: Option<&str>, query: Option<&str>) -> String {
    let rf = r#ref.filter(|s| !s.is_empty()).unwrap_or("");
    let q_raw = query.filter(|s| !s.is_empty()).unwrap_or("");
    const ROUTING: &[&str] = &[
        "browser", "desktop", "auto", "system", "grove", "alongside", "studio",
        "chrome", "rail", "web", "os",
    ];
    let q = if ROUTING.iter().any(|r| q_raw.eq_ignore_ascii_case(r)) {
        ""
    } else {
        q_raw
    };
    if rf.is_empty() && q.is_empty() {
        return "const el=document.activeElement||document.body;".into();
    }
    let er = rf.replace('\\', "\\\\").replace('\'', "\\'");
    let eq = q
        .replace('\\', "\\\\")
        .replace('\'', "\\'")
        .replace('\n', " ")
        .replace('\r', " ");
    let pick = if q.is_empty() {
        String::new()
    } else {
        String::from(
            r#"
  if(!el && q){
    const sel=window.__rmdyTypeSel;
    let hit=window.__rmdyPick(q, sel);
    if(!hit.best||hit.bestS<40){
      for(let pass=0; pass<4 && (!hit.best||hit.bestS<40); pass++){
        window.scrollBy(0, Math.floor(innerHeight*0.75));
        hit=window.__rmdyPick(q, sel);
      }
    }
    if(hit.best&&hit.bestS>=40){
      if(hit.second && hit.bestS-hit.secondS<8 && hit.bestS>=70){
        const bn=window.__rmdyName(hit.best).slice(0,40);
        const sn=window.__rmdyName(hit.second).slice(0,40);
        return 'ambiguous:'+bn+' vs '+sn+' -- pass ref= from computer_snapshot';
      }
      el=window.__rmdyFieldOf(hit.best);
    }
    if(!el) return 'no-match:'+q;
  }"#,
        )
    };
    format!(
        r#"{dom}
  const _rf='{er}';
  const q='{eq}'.toLowerCase().trim();
  let el=null;
  if(_rf){{
    el=window.__rmdyFind(_rf);
  }}{pick}
  if(_rf && !el) return 'missing-ref:'+_rf;
  if(!el) el=document.activeElement||document.body;
  try{{ el.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
"#,
        dom = REMEDY_DOM_JS,
        er = er,
        eq = eq,
        pick = pick,
    )
}


#[cfg(test)]
mod type_locate_tests {
    use super::type_locate_js;

    #[test]
    fn empty_uses_focused_node() {
        let js = type_locate_js(None, None);
        assert!(js.contains("activeElement"));
        assert!(!js.contains("__rmdyPick"));
    }

    #[test]
    fn query_picks_an_editable_field() {
        let js = type_locate_js(None, Some("What's happening?"));
        assert!(js.contains("__rmdyPick"));
        assert!(js.contains("__rmdyTypeSel"));
        assert!(js.contains("__rmdyFieldOf"));
        assert!(js.contains("no-match"));
        assert!(js.contains("happening"));
    }

    #[test]
    fn ref_still_finds_by_data_remedy_ref() {
        let js = type_locate_js(Some("e4"), None);
        assert!(js.contains("__rmdyFind"));
        assert!(js.contains("missing-ref"));
        assert!(!js.contains("__rmdyPick"));
    }

    #[test]
    fn stale_ref_falls_through_to_query() {
        let js = type_locate_js(Some("e4"), Some("Email"));
        assert!(js.contains("__rmdyFind"));
        assert!(js.contains("__rmdyPick"));
        assert!(js.contains("Email"));
        assert!(js.contains("no-match"));
    }

    #[test]
    fn routing_hint_does_not_pick() {
        let js = type_locate_js(None, Some("browser"));
        assert!(js.contains("activeElement"));
        assert!(!js.contains("__rmdyPick"));
    }
}

/// Resolve a viewport point for a gesture: explicit x/y if given, else locate
/// the element by text/ref (deep query — shadow DOM + same-origin iframes) and
/// return its center. Used by press-and-hold so the model can target a control
/// by label the same way it clicks.
fn resolve_point(
    wv: &tauri::Webview,
    x: Option<f64>,
    y: Option<f64>,
    text: Option<&str>,
    r#ref: Option<&str>,
) -> Result<(f64, f64), String> {
    // Explicit coordinates win when BOTH are present and on-screen (>=0),
    // including (0,0) — gate on presence, not positivity, so a top-left target
    // is not silently discarded into text/ref resolution.
    if let (Some(px), Some(py)) = (x, y) {
        if px >= 0.0 && py >= 0.0 && px.is_finite() && py.is_finite() {
            return Ok((px, py));
        }
    }
    let locator = if let Some(rf) = r#ref.filter(|s| !s.is_empty()) {
        let escaped = rf.replace('\\', "\\\\").replace('\'', "\\'");
        format!(
            r#"(function(){{
  {dom}
  const el=window.__rmdyFind('{escaped}');
  if(!el) return 'missing-ref';
  try{{ el.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
  const r=window.__rmdyRect(el)||el.getBoundingClientRect();
  return 'xy:'+(r.x+r.width/2)+':'+(r.y+r.height/2);
}})()"#,
            dom = REMEDY_DOM_JS
        )
    } else if let Some(t) = text.filter(|s| !s.is_empty()) {
        let escaped = t
            .replace('\\', "\\\\")
            .replace('\'', "\\'")
            .replace('\n', " ")
            .replace('\r', " ");
        format!(
            r#"(function(){{
  {dom}
  const q='{escaped}'.toLowerCase().trim();
  if(!q) return 'no-match';
  const sel=window.__rmdyHoldSel;
  let hit=window.__rmdyPick(q, sel);
  if(!hit.best||hit.bestS<40){{
    for(let pass=0; pass<4 && (!hit.best||hit.bestS<40); pass++){{
      window.scrollBy(0, Math.floor(innerHeight*0.75));
      hit=window.__rmdyPick(q, sel);
    }}
  }}
  if(!hit.best||hit.bestS<40) return 'no-match';
  try{{ hit.best.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
  const r=window.__rmdyRect(hit.best)||hit.best.getBoundingClientRect();
  return 'xy:'+(r.x+r.width/2)+':'+(r.y+r.height/2);
}})()"#,
            dom = REMEDY_DOM_JS
        )
    } else {
        return Err("press_hold needs x/y, text, or ref".into());
    };
    let (tx, rx) = std::sync::mpsc::channel::<String>();
    wv.eval_with_callback(&locator, move |r| {
        let _ = tx.send(r);
    })
    .map_err(|e| format!("resolve_point: {e}"))?;
    let raw = rx
        .recv_timeout(Duration::from_secs(9))
        .map_err(|_| "resolve_point timed out".to_string())?;
    let unq = raw.trim().trim_matches('"');
    if let Some(rest) = unq.strip_prefix("xy:") {
        let mut it = rest.split(':');
        let px: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(-1.0);
        let py: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(-1.0);
        if px >= 0.0 && py >= 0.0 {
            return Ok((px, py));
        }
    }
    Err(format!("press_hold could not locate target ({unq})"))
}

#[cfg(windows)]
fn cdp_call(wv: &tauri::Webview, method: &str, params_json: String) -> Result<String, String> {
    use webview2_com::CallDevToolsProtocolMethodCompletedHandler;

    let (tx, rx) = std::sync::mpsc::channel::<Result<String, String>>();
    let tx_err = tx.clone();
    let method_owned = method.to_string();
    wv.with_webview(move |platform| {
        let controller = platform.controller();
        let core = match unsafe { controller.CoreWebView2() } {
            Ok(c) => c,
            Err(e) => {
                let _ = tx_err.send(Err(format!("CoreWebView2: {e}")));
                return;
            }
        };
        let handler = CallDevToolsProtocolMethodCompletedHandler::create(Box::new(
            move |error_code: windows::core::Result<()>, result_json: String| {
                let _ = tx.send(match error_code {
                    Ok(()) => Ok(result_json),
                    Err(e) => Err(format!("cdp: {e}")),
                });
                Ok(())
            },
        ));
        let m = windows::core::HSTRING::from(method_owned.as_str());
        let p = windows::core::HSTRING::from(params_json.as_str());
        if let Err(e) = unsafe { core.CallDevToolsProtocolMethod(&m, &p, &handler) } {
            let _ = tx_err.send(Err(format!("cdp start: {e}")));
        }
    })
    .map_err(|e| format!("with_webview: {e}"))?;
    rx.recv_timeout(Duration::from_secs(4))
        .map_err(|_| "cdp timeout".to_string())?
}

#[cfg(windows)]
fn cdp_mouse(
    wv: &tauri::Webview,
    kind: &str,
    x: f64,
    y: f64,
    button: &str,
    click_count: i32,
) -> Result<(), String> {
    let params = json!({
        "type": kind,
        "x": x,
        "y": y,
        "button": button,
        "clickCount": click_count,
        "buttons": if kind == "mousePressed" { 1 } else { 0 },
    });
    cdp_call(wv, "Input.dispatchMouseEvent", params.to_string()).map(|_| ())
}

/// Real click at viewport CSS coords — page sees isTrusted=true events.
#[cfg(windows)]
fn cdp_click_trusted(wv: &tauri::Webview, x: f64, y: f64, button: &str) -> Result<(), String> {
    let btn = if button == "right" { "right" } else { "left" };
    // The pointer arrives before it presses — like a hand does.
    let _ = cdp_mouse(wv, "mouseMoved", x, y, "none", 0);
    cdp_mouse(wv, "mousePressed", x, y, btn, 1)?;
    cdp_mouse(wv, "mouseReleased", x, y, btn, 1)
}

/// Pointer only — menus / CSS :hover need the cursor on the control.
#[cfg(windows)]
fn cdp_hover_trusted(wv: &tauri::Webview, x: f64, y: f64) -> Result<(), String> {
    cdp_mouse(wv, "mouseMoved", x, y, "none", 0)
}

/// Trusted text entry into the focused editable (real input events; React
/// and vanilla listeners both see isTrusted=true).
#[cfg(windows)]
fn cdp_insert_text(wv: &tauri::Webview, text: &str) -> Result<(), String> {
    cdp_call(wv, "Input.insertText", json!({ "text": text }).to_string()).map(|_| ())
}

/// Trusted named-key press (Enter submits forms natively — no requestSubmit
/// shim needed). Returns None for keys we don't map; caller falls back.
#[cfg(windows)]
fn cdp_named_key(wv: &tauri::Webview, key: &str) -> Option<Result<(), String>> {
    let canon: &str = match key {
        "enter" | "return" | "Enter" => "Enter",
        "tab" | "Tab" => "Tab",
        "esc" | "escape" | "Escape" => "Escape",
        "backspace" | "Backspace" => "Backspace",
        "delete" | "del" | "Delete" => "Delete",
        other => other,
    };
    let (vk, text): (i32, &str) = match canon {
        "Enter" => (13, "\r"),
        "Tab" => (9, ""),
        "Escape" => (27, ""),
        "Backspace" => (8, ""),
        "Delete" => (46, ""),
        "ArrowLeft" => (37, ""),
        "ArrowUp" => (38, ""),
        "ArrowRight" => (39, ""),
        "ArrowDown" => (40, ""),
        "PageUp" => (33, ""),
        "PageDown" => (34, ""),
        "Home" => (36, ""),
        "End" => (35, ""),
        _ => return None,
    };
    let down = json!({
        "type": if text.is_empty() { "rawKeyDown" } else { "keyDown" },
        "key": canon,
        "code": canon,
        "windowsVirtualKeyCode": vk,
        "nativeVirtualKeyCode": vk,
        "text": text,
        "unmodifiedText": text,
    });
    let up = json!({
        "type": "keyUp",
        "key": canon,
        "code": canon,
        "windowsVirtualKeyCode": vk,
        "nativeVirtualKeyCode": vk,
    });
    Some(
        cdp_call(wv, "Input.dispatchKeyEvent", down.to_string())
            .and_then(|_| cdp_call(wv, "Input.dispatchKeyEvent", up.to_string()))
            .map(|_| ()),
    )
}

fn apply_page_fullscreen(app: &AppHandle, fullscreen: bool) {
    let Some(state) = app.try_state::<BrowserState>() else {
        return;
    };
    state
        .page_fullscreen
        .store(fullscreen, Ordering::SeqCst);
    let Some(wv) = app.get_webview(LABEL) else {
        return;
    };
    // Stay inside the Browser rail: re-apply last host rect (SPA will grow the
    // host to fill the rail panel after chrome hides). Never cover the whole app.
    let rail = state
        .last_bounds
        .lock()
        .ok()
        .and_then(|g| g.clone())
        .unwrap_or_else(|| default_rail_bounds(app));
    let b = clamp_bounds(&rail);
    // Overlay suppress always wins — page fullscreen must not paint over
    // Settings / Help / the image lightbox (native HWND sits above CSS).
    let may_show = embed_may_show(state.inner());
    if let Err(e) = apply_bounds(&wv, &b, may_show) {
        log::warn!("browser rail fullscreen bounds failed: {e}");
    } else {
        log::info!(
            "browser page fullscreen {} — rail embed {}x{} @ ({},{})",
            if fullscreen { "ON" } else { "OFF" },
            b.width,
            b.height,
            b.x,
            b.y
        );
    }
    let _ = app.emit(
        "browser-page-fullscreen",
        json!({ "fullscreen": fullscreen }),
    );
}

fn destroy_embed(app: &AppHandle) {
    // Child webview (embedded)
    if let Some(state) = app.try_state::<BrowserState>() {
        state.page_fullscreen.store(false, Ordering::SeqCst);
    }
    if let Some(wv) = app.get_webview(LABEL) {
        let _ = wv.hide();
        let _ = wv.close();
        log::info!("browser embed closed ({LABEL})");
    }
    // Legacy popup window from older builds
    if let Some(win) = app.get_webview_window("remedy-browser") {
        let _ = win.destroy();
        log::info!("legacy popup browser destroyed");
    }
}

/// Destroy embedded browser (idempotent).
/// Current rail view mode: mobile (default) or desktop site.
#[tauri::command]
pub fn browser_view_mode(state: State<'_, BrowserState>) -> Result<serde_json::Value, String> {
    let desktop = state.desktop_site.load(Ordering::SeqCst);
    Ok(json!({
        "desktop_site": desktop,
        "mode": if desktop { "desktop" } else { "mobile" },
    }))
}

/// Apply mobile/desktop User-Agent on a live embed (WebView2 Settings2).
#[cfg(windows)]
fn set_embed_user_agent(wv: &tauri::Webview, desktop: bool) -> Result<(), String> {
    let ua = rail_user_agent(desktop);
    let (tx, rx) = std::sync::mpsc::sync_channel::<Result<(), String>>(1);
    wv.with_webview(move |platform| {
        use webview2_com::Microsoft::Web::WebView2::Win32::ICoreWebView2Settings2;
        use windows::core::{Interface, HSTRING};

        let result = (|| {
            let controller = platform.controller();
            let core = unsafe { controller.CoreWebView2() }.map_err(|e| e.to_string())?;
            let settings = unsafe { core.Settings() }.map_err(|e| e.to_string())?;
            let settings2: ICoreWebView2Settings2 =
                settings.cast().map_err(|e| format!("Settings2 cast: {e}"))?;
            let hs = HSTRING::from(ua.as_str());
            unsafe { settings2.SetUserAgent(&hs) }.map_err(|e| format!("SetUserAgent: {e}"))?;
            Ok(())
        })();
        let _ = tx.send(result);
    })
    .map_err(|e| format!("with_webview: {e}"))?;
    rx.recv().map_err(|e| format!("UA channel: {e}"))??;
    apply_ua_client_hints(wv, desktop);
    Ok(())
}

#[cfg(not(windows))]
fn set_embed_user_agent(_wv: &tauri::Webview, _desktop: bool) -> Result<(), String> {
    Err("User-Agent switch requires Windows WebView2".into())
}

/// Toggle desktop vs mobile site for the Browser rail.
///
/// Prefers **in-place** User-Agent change + hard reload (no destroy). Falls back
/// to destroy+recreate if Settings2 is unavailable. SPA should pass current URL
/// + host bounds when available.
#[tauri::command(async)]
pub fn browser_set_desktop_site(
    app: AppHandle,
    state: State<'_, BrowserState>,
    enabled: bool,
    url: Option<String>,
    bounds: Option<BrowserBounds>,
) -> Result<serde_json::Value, String> {
    state.desktop_site.store(enabled, Ordering::SeqCst);
    save_rail_prefs(&BrowserRailPrefs {
        desktop_site: enabled,
    });
    log::info!(
        "browser rail view mode → {}",
        if enabled { "desktop" } else { "mobile" }
    );

    let target = url
        .map(|u| u.trim().to_string())
        .filter(|u| !u.is_empty() && !u.starts_with("about:"))
        .or_else(|| {
            state
                .current_url
                .lock()
                .ok()
                .map(|g| g.clone())
                .filter(|u| !u.is_empty() && !u.starts_with("about:"))
        });

    let mut method = "prefs_only";
    if let Some(ref target_url) = target {
        if let Some(wv) = app.get_webview(LABEL) {
            match set_embed_user_agent(&wv, enabled) {
                Ok(()) => {
                    // Hard reload with new UA (same URL).
                    if let Some(ref b) = bounds {
                        let cb = clamp_bounds(b);
                        if let Ok(mut g) = state.last_bounds.lock() {
                            *g = Some(cb.clone());
                        }
                        let _ = apply_bounds(&wv, &cb, embed_may_show(state.inner()));
                    }
                    match target_url.parse::<Url>() {
                        Ok(parsed) => {
                            if let Err(e) = wv.navigate(parsed) {
                                log::warn!("browser UA toggle navigate failed: {e}");
                            } else {
                                method = "ua_inplace";
                                log::info!("browser UA applied in-place, reloading {target_url}");
                            }
                        }
                        Err(e) => log::warn!("browser UA toggle bad url: {e}"),
                    }
                    // Viewport script after a beat (page load handler also runs).
                    let js = if enabled {
                        DESKTOP_VIEWPORT_JS
                    } else {
                        MOBILE_VIEWPORT_JS
                    };
                    let wv2 = wv.clone();
                    std::thread::spawn(move || {
                        std::thread::sleep(std::time::Duration::from_millis(400));
                        let _ = wv2.eval(js);
                        std::thread::sleep(std::time::Duration::from_millis(600));
                        let _ = wv2.eval(js);
                    });
                }
                Err(e) => {
                    log::warn!("browser set UA failed ({e}); recreate embed");
                    destroy_embed(&app);
                    match navigate_embed(&app, state.inner(), target_url, bounds.clone()) {
                        Ok(_) => method = "recreate",
                        Err(ne) => {
                            log::warn!("browser recreate after UA fail: {ne}");
                            return Err(ne);
                        }
                    }
                }
            }
        } else {
            // No embed yet — flag is set; next navigate creates with correct UA.
            method = "prefs_next_open";
            if let Some(b) = bounds {
                if let Ok(mut g) = state.last_bounds.lock() {
                    *g = Some(clamp_bounds(&b));
                }
            }
        }
    }

    let _ = app.emit(
        "browser-view-mode",
        json!({
            "desktop_site": enabled,
            "mode": if enabled { "desktop" } else { "mobile" },
        }),
    );
    Ok(json!({
        "desktop_site": enabled,
        "mode": if enabled { "desktop" } else { "mobile" },
        "method": method,
        "recreate": method == "recreate",
    }))
}

#[tauri::command]
pub fn browser_close(app: AppHandle) -> Result<(), String> {
    destroy_embed(&app);
    Ok(())
}

#[tauri::command]
pub fn browser_is_open(app: AppHandle) -> bool {
    app.get_webview(LABEL).is_some()
}

#[tauri::command]
pub fn browser_hide(app: AppHandle, state: State<'_, BrowserState>) -> Result<(), String> {
    if let Some(wv) = app.get_webview(LABEL) {
        let b = state
            .last_bounds
            .lock()
            .ok()
            .and_then(|g| g.clone())
            .unwrap_or_else(|| default_rail_bounds(&app));
        apply_bounds(&wv, &clamp_bounds(&b), false)?;
    }
    Ok(())
}

#[tauri::command]
pub fn browser_show(app: AppHandle, state: State<'_, BrowserState>) -> Result<(), String> {
    if !embed_may_show(state.inner()) {
        return Ok(());
    }
    if let Some(wv) = app.get_webview(LABEL) {
        let b = state
            .last_bounds
            .lock()
            .ok()
            .and_then(|g| g.clone())
            .unwrap_or_else(|| default_rail_bounds(&app));
        apply_bounds(&wv, &clamp_bounds(&b), true)?;
    }
    Ok(())
}

/// Suppress (hide) or restore the embed while React overlays cover the host.
/// Does not destroy the webview — navigations may continue while hidden.
#[tauri::command]
pub fn browser_set_stack_suppressed(
    app: AppHandle,
    state: State<'_, BrowserState>,
    suppressed: bool,
) -> Result<(), String> {
    state
        .stack_suppressed
        .store(suppressed, Ordering::SeqCst);
    if suppressed {
        if let Some(wv) = app.get_webview(LABEL) {
            let b = state
                .last_bounds
                .lock()
                .ok()
                .and_then(|g| g.clone())
                .unwrap_or_else(|| default_rail_bounds(&app));
            let _ = apply_bounds(&wv, &clamp_bounds(&b), false);
        }
        log::debug!("browser embed stack suppressed");
    } else if let Some(wv) = app.get_webview(LABEL) {
        // Re-apply last bounds then show — SPA should also push fresh host rect.
        let b = state
            .last_bounds
            .lock()
            .ok()
            .and_then(|g| g.clone())
            .unwrap_or_else(|| default_rail_bounds(&app));
        let _ = apply_bounds(&wv, &clamp_bounds(&b), true);
        let _ = app.emit("browser-stack-restored", json!({ "ok": true }));
        log::debug!("browser embed stack restored");
    }
    Ok(())
}

/// Reposition/resize the embedded webview to match the Browser slide host.
#[tauri::command]
pub fn browser_set_bounds(
    app: AppHandle,
    state: State<'_, BrowserState>,
    bounds: BrowserBounds,
) -> Result<(), String> {
    let b = clamp_bounds(&bounds);
    // Always remember rail host bounds (SPA grows host while video is fullscreen).
    if let Ok(mut g) = state.last_bounds.lock() {
        *g = Some(b.clone());
    }
    let may_show = embed_may_show(state.inner());
    if let Some(wv) = app.get_webview(LABEL) {
        apply_bounds(&wv, &b, may_show)?;
    }
    Ok(())
}

/// Right-rail-ish bounds from main window size when SPA has not pushed host rect yet.
fn default_rail_bounds(app: &AppHandle) -> BrowserBounds {
    if let Some(ww) = app.get_webview_window("main") {
        if let (Ok(size), Ok(scale)) = (ww.inner_size(), ww.scale_factor()) {
            let w = (size.width as f64 / scale).max(800.0);
            let h = (size.height as f64 / scale).max(500.0);
            // Match SPA rail max (~624 body + icon strip); prefer ~40% when smaller
            let rail_w = (w * 0.40).clamp(400.0, 624.0);
            // title (36) + panel header (~30) + URL toolbar (~40)
            let top = 108.0_f64;
            let bottom = 48.0_f64; // app status bar
            let gap = 6.0_f64;
            return BrowserBounds {
                x: (w - rail_w + gap).max(0.0),
                y: top,
                width: (rail_w - gap * 2.0).max(280.0),
                height: (h - top - bottom).max(240.0),
            };
        }
    }
    BrowserBounds {
        x: 420.0,
        y: 108.0,
        width: 480.0,
        height: 640.0,
    }
}

/// Core navigate used by the command and the Rust computer-host poller.
pub fn navigate_embed(
    app: &AppHandle,
    state: &BrowserState,
    url_raw: &str,
    bounds: Option<BrowserBounds>,
) -> Result<String, String> {
    let mut url = normalize_url(url_raw)?;
    // Rewrite Google popup-mode GIS → redirect before navigating (same-window).
    if let Some(fixed) = rewrite_oauth_navigation(&url) {
        url = fixed;
    }
    {
        let mut cur = state.current_url.lock().map_err(|e| e.to_string())?;
        *cur = url.clone();
    }

    let parsed: Url = url.parse().map_err(|e: url::ParseError| e.to_string())?;

    let b = bounds
        .or_else(|| state.last_bounds.lock().ok().and_then(|g| g.clone()))
        .filter(|bb| bb.width >= 200.0 && bb.height >= 160.0)
        .map(|b| clamp_bounds(&b))
        .unwrap_or_else(|| default_rail_bounds(app));
    if let Ok(mut g) = state.last_bounds.lock() {
        *g = Some(b.clone());
    }

    let may_show = embed_may_show(state);

    // Already embedded — navigate + re-bounds; recreate if navigate fails (stale child).
    if let Some(wv) = app.get_webview(LABEL) {
        if let Err(e) = apply_bounds(&wv, &b, may_show) {
            log::warn!("browser bounds on existing embed failed: {e}");
        }
        match wv.navigate(parsed.clone()) {
            Ok(()) => {
                if may_show {
                    let _ = wv.show();
                } else {
                    let _ = wv.hide();
                }
                log::info!("browser embed navigate {url} show={may_show}");
                return Ok(url);
            }
            Err(e) => {
                log::warn!("browser navigate on existing embed failed ({e}); recreating");
                destroy_embed(app);
            }
        }
    }

    // Close any legacy popup first
    if let Some(win) = app.get_webview_window("remedy-browser") {
        let _ = win.destroy();
    }

    let window = main_window(app)?;
    // about:blank first → then navigate: avoids multiwebview white-screen on some GPUs.
    let blank: Url = "about:blank".parse().map_err(|e: url::ParseError| e.to_string())?;
    // Keep SPA address bar in sync when the user clicks links inside the page.
    let app_for_load = app.clone();
    let app_for_nav = app.clone();
    // Dark chrome — pure white reads as a distracting border around the page
    // Do not focus on create — steals focus and worsens z-order over React chrome.
    let desktop = state.desktop_site.load(Ordering::SeqCst);
    let ua = rail_user_agent(desktop);
    log::info!(
        "browser embed create mode={} ua={}",
        if desktop { "desktop" } else { "mobile" },
        if desktop { "desktop-chrome" } else { "mobile-chrome" }
    );
    let builder = WebviewBuilder::new(LABEL, WebviewUrl::External(blank))
        .focused(false)
        .user_agent(&ua)
        .background_color(Color(18, 18, 22, 255))
        // Privacy Shield + OAuth special-scheme rewrite (storagerelay / intent)
        .on_navigation(move |url| {
            let s = url.as_str();
            if let Some(fixed) = rewrite_oauth_navigation(s) {
                let app2 = app_for_nav.clone();
                let fixed2 = fixed.clone();
                let _ = app2.clone().run_on_main_thread(move || {
                    if let Some(wv) = app2.get_webview(LABEL) {
                        if let Ok(u) = fixed2.parse::<Url>() {
                            if let Err(e) = wv.navigate(u) {
                                log::warn!("browser oauth rewrite navigate failed: {e}");
                            }
                        }
                    }
                });
                return false;
            }
            if crate::privacy_shield::should_block_navigation(s) {
                return false;
            }
            true
        })
        // GIS / SSO `window.open` is denied by Wry unless we Allow it. Without a
        // real popup, Google lands the rail on /gsi/transform and hangs.
        .on_new_window(|url, _features| {
            let s = url.as_str();
            if should_allow_oauth_popup(s) {
                log::info!("browser new-window allow {s}");
                NewWindowResponse::Allow
            } else {
                log::debug!("browser new-window deny {s}");
                NewWindowResponse::Deny
            }
        })
        .on_page_load(move |wv, payload| {
            let u = payload.url().as_str().to_string();
            if u.is_empty() || u.starts_with("about:") {
                return;
            }
            let mut prev_url = String::new();
            if let Some(st) = app_for_load.try_state::<BrowserState>() {
                if let Ok(mut g) = st.current_url.lock() {
                    *g = u.clone();
                }
                if let Ok(mut g) = st.last_committed_url.lock() {
                    prev_url = g.clone();
                    *g = u.clone();
                }
                if should_remember_oauth_return(&prev_url, &u) {
                    if let Ok(mut g) = st.oauth_return_url.lock() {
                        *g = Some(prev_url.clone());
                    }
                }
                let saved = st
                    .oauth_return_url
                    .lock()
                    .ok()
                    .and_then(|g| g.clone());
                if let Some(ret) =
                    bounce_target_for_gsi_transformer(&u, saved.as_deref())
                {
                    log::info!("browser oauth bounce gsi/transform → {ret}");
                    let app3 = app_for_load.clone();
                    let _ = app3.clone().run_on_main_thread(move || {
                        if let Some(embed) = app3.get_webview(LABEL) {
                            if let Ok(parsed) = ret.parse::<Url>() {
                                let _ = embed.navigate(parsed);
                            }
                        }
                    });
                    return;
                }
            }
            let _ = app_for_load.emit("browser-url-changed", json!({ "url": u }));
            complete_pending_navigate_if_any(&app_for_load, &u, &prev_url);
            // Same-window OAuth: window.open → location.assign (no popup surface).
            let _ = wv.eval(SAME_WINDOW_OAUTH_JS);
            // Scrollbars only (no zoom / chrome overrides)
            let _ = wv.eval(RAIL_LAYOUT_JS);
            // Screen/viewport = this WebView (the rail) so native fullscreen fills it.
            let _ = wv.eval(RAIL_AS_SCREEN_JS);
            // Viewport meta for mobile vs desktop rail mode (re-applied every load).
            if let Some(st) = app_for_load.try_state::<BrowserState>() {
                if st.desktop_site.load(Ordering::SeqCst) {
                    let _ = wv.eval(DESKTOP_VIEWPORT_JS);
                } else {
                    let _ = wv.eval(MOBILE_VIEWPORT_JS);
                }
            }
            if let Some(js) = crate::privacy_shield::cosmetic_inject_js(&u) {
                let _ = wv.eval(&js);
            }
            let wv2 = wv.clone();
            std::thread::spawn(move || {
                std::thread::sleep(std::time::Duration::from_millis(800));
                let _ = wv2.eval(RAIL_LAYOUT_JS);
                let _ = wv2.eval(RAIL_AS_SCREEN_JS);
            });
        });

    // add_child already runs builder on main thread internally
    let wv = window
        .add_child(
            builder,
            LogicalPosition::new(b.x, b.y),
            LogicalSize::new(b.width, b.height),
        )
        .map_err(|e| {
            log::error!("browser add_child failed: {e}");
            format!(
                "embed browser failed: {e}. Try ↗ system browser, or reinstall WebView2 Runtime."
            )
        })?;
    #[cfg(any(
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    ))]
    linux_place_embed(&window, &b, may_show);

    // Video fullscreen: expand child WebView2 to the app window on request.
    attach_fullscreen_handler(app.clone(), wv.clone());
    // Client hints must agree with the UA header. Off-thread: cdp_call waits
    // on a completion callback that only the main-thread pump can deliver.
    {
        let wv_hints = wv.clone();
        std::thread::spawn(move || apply_ua_client_hints(&wv_hints, desktop));
    }

    if may_show {
        let _ = wv.show();
    } else {
        let _ = wv.hide();
    }
    // Immediate navigate to target (no delayed re-navigate — that wiped SPAs).
    if let Err(e) = wv.navigate(parsed) {
        log::warn!("browser initial navigate failed: {e}");
    }

    log::info!(
        "browser embed created {url} @ ({},{}) {}x{}",
        b.x,
        b.y,
        b.width,
        b.height
    );
    Ok(url)
}

/// Open or navigate the **embedded** WebView2 inside the main window.
/// Pass CSS-pixel bounds of the Browser slide content area (from getBoundingClientRect).
///
/// Must stay `async` on Windows — sync create can deadlock WebView2.
#[tauri::command]
pub async fn browser_navigate(
    app: AppHandle,
    state: State<'_, BrowserState>,
    url: String,
    bounds: Option<BrowserBounds>,
) -> Result<String, String> {
    navigate_embed(&app, state.inner(), &url, bounds)
}

// ── Rust-side computer host (does not depend on SPA JS poller) ──────────────

static COMPUTER_HOST_STARTED: AtomicBool = AtomicBool::new(false);

/// Cached Bearer for host/jobs/ui (API requires auth; a11y push stays job_id-only).
/// Refresh every ~30s so DPAPI is not hit on the 25ms poll hot path.
fn host_bearer_cached() -> String {
    use std::sync::Mutex;
    use std::time::Instant;
    static CACHE: Mutex<Option<(Instant, String)>> = Mutex::new(None);
    const TTL: Duration = Duration::from_secs(30);
    if let Ok(guard) = CACHE.lock() {
        if let Some((t, tok)) = guard.as_ref() {
            if t.elapsed() < TTL && !tok.is_empty() {
                return tok.clone();
            }
        }
    }
    let tok = crate::get_local_api_token().unwrap_or_default();
    if tok.is_empty() {
        // Do not cache empty — sidecar may write the token moments later.
        log::debug!("computer-host: no local API bearer yet — host routes will 401");
        return String::new();
    }
    if let Ok(mut guard) = CACHE.lock() {
        *guard = Some((Instant::now(), tok.clone()));
    }
    tok
}

/// Attach Authorization when we have a token (host/jobs/ui require Bearer).
fn auth_req(req: ureq::Request) -> ureq::Request {
    let tok = host_bearer_cached();
    if tok.is_empty() {
        req
    } else {
        req.set("Authorization", &format!("Bearer {tok}"))
    }
}

/// Background poller: open Browser rail + drive WebView when the agent navigates.
/// Runs in Rust so it works even if the React host hook never mounts.
pub fn start_computer_host_poller(app: AppHandle) {
    if COMPUTER_HOST_STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    log::info!("computer-host: starting Rust poller thread");
    let _ = std::thread::Builder::new()
        .name("computer-host".into())
        .spawn(move || computer_host_loop(app));
}

fn computer_host_loop(app: AppHandle) {
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(2))
        .timeout(Duration::from_secs(8))
        .build();
    // Wait for sidecar API
    for _ in 0..60 {
        if agent.get(&api_url("/api/ping")).call().is_ok() {
            break;
        }
        std::thread::sleep(Duration::from_millis(500));
    }
    // Wait briefly for token file (sidecar may write DPAPI envelope just after ping).
    for _ in 0..40 {
        if !host_bearer_cached().is_empty() {
            break;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    log::info!("computer-host: API reachable, polling ui/command + jobs (Bearer auth)");
    // Track last *completed* navigate job so renudge take of the same id is a
    // real re-navigate (never fake-complete without opening the URL).
    let mut last_completed_nav = String::new();
    // jobs/next already marks poller=True every tick. POSTing /host/hello here
    // was pure spam (no bounds, no session) on top of the SPA 4s bounds hello.
    let mut idle_streak: u32 = 0;
    loop {
        // Busy 50ms. Idle long-polls jobs/next (wake-on-enqueue) instead of
        // sleeping 2s then missing the first click.
        let (sleep_ms, wait_ms): (u64, u64) = if idle_streak == 0 {
            (50, 0)
        } else if idle_streak < 8 {
            (0, 150)
        } else if idle_streak < 16 {
            (0, 800)
        } else {
            (0, 2000)
        };
        if sleep_ms > 0 {
            std::thread::sleep(Duration::from_millis(sleep_ms));
        }
        let mut saw_work = false;

        // take=1 clears command atomically — prevents reloading the same wiki forever
        if let Ok(resp) = auth_req(
            agent.get(&api_url("/api/computer/ui/command?take=1&driver=rust")),
        )
        .call()
        {
            if let Ok(v) = resp.into_json::<serde_json::Value>() {
                if let Some(cmd) = v.get("command").filter(|c| !c.is_null() && c.is_object()) {
                    let jid = cmd
                        .get("job_id")
                        .and_then(|j| j.as_str())
                        .unwrap_or("")
                        .to_string();
                    // Always handle — never drop a take without navigate+complete.
                    // Renudge may re-deliver the same job_id; re-navigate is correct.
                    saw_work = true;
                    handle_ui_command(&app, &agent, cmd);
                    if !jid.is_empty() {
                        last_completed_nav = jid;
                    }
                }
            }
        }

        // One poller. The SPA only opens the rail + sends bounds hello —
        // it no longer claims jobs/next. Two drivers used to race snapshot
        // and dual-spam the API during a fat ReAct turn.
        let url = if wait_ms > 0 {
            api_url(&format!(
                "/api/computer/jobs/next?wait_ms={wait_ms}&driver=rust"
            ))
        } else {
            api_url("/api/computer/jobs/next?driver=rust")
        };
        if let Ok(resp) = auth_req(agent.get(&url)).call()
        {
            if let Ok(v) = resp.into_json::<serde_json::Value>() {
                if let Some(job) = v.get("job").filter(|j| !j.is_null() && j.is_object()) {
                    let action = job
                        .get("action")
                        .and_then(|a| a.as_str())
                        .unwrap_or("");
                    let jid = job
                        .get("id")
                        .and_then(|i| i.as_str())
                        .unwrap_or("")
                        .to_string();
                    saw_work = true;
                    // NEVER fake-complete navigate. If still claimable, navigate for real.
                    if action == "navigate" && !jid.is_empty() && jid == last_completed_nav {
                        log::info!(
                            "computer-host: re-claim navigate {jid} after prior complete — navigating again"
                        );
                    }
                    // DOM jobs can take several seconds (page load + eval).
                    // Run them off the poller thread so navigate stays snappy.
                    if matches!(
                        action,
                        "snapshot" | "a11y" | "page_text" | "ready" | "click"
                            | "type" | "key" | "scroll" | "drag" | "press_hold" | "select"
                            | "hover" | "screenshot"
                    ) {
                        let app2 = app.clone();
                        let agent2 = agent.clone();
                        let job2 = job.clone();
                        let _ = std::thread::Builder::new()
                            .name("computer-dom".into())
                            .spawn(move || handle_job(&app2, &agent2, &job2));
                    } else {
                        handle_job(&app, &agent, job);
                        if action == "navigate" && !jid.is_empty() {
                            last_completed_nav = jid;
                        }
                    }
                }
            }
        }
        idle_streak = if saw_work {
            0
        } else {
            idle_streak.saturating_add(1)
        };
    }
}

fn handle_ui_command(app: &AppHandle, agent: &ureq::Agent, cmd: &serde_json::Value) {
    let action = cmd
        .get("action")
        .and_then(|a| a.as_str())
        .unwrap_or("")
        .to_string();
    let url = cmd
        .get("url")
        .and_then(|u| u.as_str())
        .unwrap_or("")
        .to_string();
    let job_id = cmd
        .get("job_id")
        .and_then(|j| j.as_str())
        .unwrap_or("")
        .to_string();
    let job_action = cmd
        .get("job_action")
        .and_then(|j| j.as_str())
        .unwrap_or("")
        .to_string();

    if action != "open_browser" && job_action != "navigate" {
        return;
    }

    // Tell SPA to expand Browser rail + sync address bar URL
    let _ = app.emit(
        "computer-open-browser",
        json!({ "url": url, "job_id": job_id }),
    );

    if url.is_empty() {
        if !job_id.is_empty() && job_action == "navigate" {
            complete_job(
                agent,
                &job_id,
                false,
                json!({}),
                Some("navigate ui_command missing url".into()),
            );
        }
        return;
    }

    let final_url = url.clone();
    if !job_id.is_empty() {
        arm_pending_navigate(app, job_id.clone(), final_url.clone());
    }
    let _ = app.emit("computer-browser-url", json!({ "url": url }));
    // Fire-and-forget embed navigate — complete from on_page_load (or 8s timeout).
    fire_navigate(app, &url);
}

fn rust_action_ok(raw: &str) -> bool {
    let s = raw.trim();
    if s.is_empty() {
        return false;
    }
    if s.starts_with("missing-ref:")
        || s.starts_with("no-match:")
        || s.starts_with("no-option:")
        || s.starts_with("not-select:")
        || s.starts_with("no element")
        || s.starts_with("ambiguous:")
        || s.starts_with("error:")
        || s.starts_with("browser:")
    {
        return false;
    }
    s == "ok" || s.starts_with("ok:") || s.starts_with("ok-")
}

fn handle_job(app: &AppHandle, agent: &ureq::Agent, job: &serde_json::Value) {
    let id = job.get("id").and_then(|i| i.as_str()).unwrap_or("").to_string();
    let action = job
        .get("action")
        .and_then(|a| a.as_str())
        .unwrap_or("")
        .to_string();
    let payload = job.get("payload").cloned().unwrap_or(json!({}));
    if id.is_empty() {
        return;
    }
    let _ = app.emit("computer-open-browser", json!({ "job_id": id }));

    if action == "navigate" {
        let url = payload
            .get("url")
            .and_then(|u| u.as_str())
            .unwrap_or("")
            .to_string();
        if url.is_empty() {
            complete_job(agent, &id, false, json!({}), Some("url required".into()));
            return;
        }
        arm_pending_navigate(app, id.clone(), url.clone());
        let _ = app.emit("computer-browser-url", json!({ "url": url }));
        fire_navigate(app, &url);
        ack_ui_command(agent, &id);
        return;
    }

    if action == "snapshot" || action == "a11y" {
        // Optimistic navigate completes before paint; wait for ready then
        // retry snapshot — WebView2 eval hangs mid-navigation until load ends.
        let _ = wait_page_ready(app, 4);
        match run_snapshot_with_retry(app, &id, 2) {
            Ok(raw) => log::info!("computer-host snapshot job {id} ok len={}", raw.len()),
            Err(e) => {
                log::warn!("computer-host snapshot job {id}: {e}");
                complete_job(agent, &id, false, json!({}), Some(e));
            }
        }
        ack_ui_command(agent, &id);
        return;
    }

    if action == "page_text" {
        let _ = wait_page_ready(app, 4);
        match run_page_text_with_retry(app, 2) {
            Ok(raw) => {
                let parsed = parse_page_text_raw(&raw);
                let text = parsed
                    .get("text")
                    .and_then(|t| t.as_str())
                    .unwrap_or("")
                    .to_string();
                complete_job(
                    agent,
                    &id,
                    true,
                    json!({
                        "ok": true,
                        "target": "browser",
                        "action": "page_text",
                        "message": format!("Page text {} chars", text.len()),
                        "via": "rust-host",
                        "title": parsed.get("title").cloned().unwrap_or(json!("")),
                        "url": parsed.get("url").cloned().unwrap_or(json!("")),
                        "text": text,
                    }),
                    None,
                );
            }
            Err(e) => {
                log::warn!("computer-host page_text job {id}: {e}");
                complete_job(agent, &id, false, json!({}), Some(e));
            }
        }
        ack_ui_command(agent, &id);
        return;
    }

    if action == "ready" {
        match browser_agent_action(
            app.clone(),
            "ready".into(),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ) {
            Ok(raw) => {
                let parsed: serde_json::Value =
                    serde_json::from_str(&raw).unwrap_or_else(|_| json!({ "raw": raw }));
                complete_job(
                    agent,
                    &id,
                    true,
                    json!({
                        "ok": true,
                        "target": "browser",
                        "action": "ready",
                        "message": "page ready probe",
                        "via": "rust-host",
                        "ready": parsed,
                    }),
                    None,
                );
            }
            Err(e) => complete_job(agent, &id, false, json!({}), Some(e)),
        }
        return;
    }

    if action == "click" || action == "hover" {
        let text = payload
            .get("text")
            .and_then(|t| t.as_str())
            .map(|s| s.to_string());
        let r#ref = payload
            .get("ref")
            .and_then(|t| t.as_str())
            .map(|s| s.to_string());
        let x = payload.get("x").and_then(|v| v.as_f64());
        let y = payload.get("y").and_then(|v| v.as_f64());
        let button = payload
            .get("button")
            .and_then(|t| t.as_str())
            .map(|s| s.to_string());
        let click_text = payload
            .get("click_text")
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
            || (text.as_ref().map(|s| !s.is_empty()).unwrap_or(false)
                && r#ref.as_ref().map(|s| s.is_empty()).unwrap_or(true)
                && x.is_none());
        let hover = action == "hover";
        let act = if click_text {
            if hover {
                "hover_text".to_string()
            } else {
                "click_text".to_string()
            }
        } else if r#ref.as_ref().map(|s| !s.is_empty()).unwrap_or(false) {
            if hover {
                "hover_ref".to_string()
            } else {
                "click_ref".to_string()
            }
        } else if hover {
            "hover".to_string()
        } else {
            "click".to_string()
        };
        match browser_agent_action(
            app.clone(),
            act,
            x,
            y,
            None,
            None,
            text,
            None,
            button,
            None,
            None,
            r#ref.clone(),
        ) {
            Ok(raw) => {
                let ok = rust_action_ok(&raw);
                complete_job(
                    agent,
                    &id,
                    ok,
                    json!({
                        "ok": ok,
                        "target": "browser",
                        "action": "click",
                        "message": if ok {
                            format!("Clicked ({raw})")
                        } else {
                            format!("click failed: {raw}")
                        },
                        "detail": raw,
                        "ref": r#ref,
                        "via": "rust-host",
                    }),
                    if ok { None } else { Some(format!("click failed")) },
                );
            }
            Err(e) => complete_job(agent, &id, false, json!({}), Some(e)),
        }
        ack_ui_command(agent, &id);
        return;
    }

    if action == "press_hold" {
        let text = payload.get("text").and_then(|t| t.as_str()).map(String::from);
        let r#ref = payload.get("ref").and_then(|t| t.as_str()).map(String::from);
        let x = payload.get("x").and_then(|v| v.as_f64());
        let y = payload.get("y").and_then(|v| v.as_f64());
        let hold = payload
            .get("hold_ms")
            .and_then(|v| v.as_i64())
            .map(|v| v as i32);
        match browser_agent_action(
            app.clone(),
            "press_hold".into(),
            x,
            y,
            None,
            None,
            text,
            None,
            None,
            hold,
            None,
            r#ref,
        ) {
            Ok(raw) => {
                let ok = rust_action_ok(&raw);
                complete_job(
                    agent,
                    &id,
                    ok,
                    json!({
                        "ok": ok,
                        "target": "browser",
                        "action": "press_hold",
                        "message": if ok {
                            format!("Pressed and held ({raw})")
                        } else {
                            format!("press_hold failed: {raw}")
                        },
                        "detail": raw,
                        "via": "rust-host",
                    }),
                    if ok { None } else { Some("press_hold failed".to_string()) },
                );
            }
            Err(e) => complete_job(agent, &id, false, json!({}), Some(e)),
        }
        ack_ui_command(agent, &id);
        return;
    }

    // type / key / scroll / drag — Rust drives these too when the SPA is down
    // (desktop minimized). browser_agent_action already implements them.
    if matches!(action.as_str(), "type" | "key" | "scroll" | "drag" | "select") {
        let text = payload.get("text").and_then(|t| t.as_str()).map(String::from);
        // select: the field label travels as `hint`; type: query/label/hint.
        // browser_agent_action has no extra slot, so ride those in `key`
        // (unused by select/type). Routing tokens are stripped in type_locate_js.
        let key = if action == "select" {
            payload.get("hint").and_then(|t| t.as_str()).map(String::from)
        } else if action == "type" {
            ["query", "label", "hint"].iter().find_map(|k| {
                payload
                    .get(*k)
                    .and_then(|t| t.as_str())
                    .filter(|s| !s.is_empty())
                    .map(String::from)
            })
        } else {
            payload.get("key").and_then(|t| t.as_str()).map(String::from)
        };
        let r#ref = payload.get("ref").and_then(|t| t.as_str()).map(String::from);
        let x = payload.get("x").and_then(|v| v.as_f64());
        let y = payload.get("y").and_then(|v| v.as_f64());
        let x2 = payload.get("x2").and_then(|v| v.as_f64());
        let y2 = payload.get("y2").and_then(|v| v.as_f64());
        let dy = payload.get("dy").and_then(|v| v.as_i64()).map(|v| v as i32);
        match browser_agent_action(
            app.clone(),
            action.clone(),
            x,
            y,
            x2,
            y2,
            text,
            key,
            None,
            dy,
            None,
            r#ref,
        ) {
            Ok(raw) => {
                let ok = rust_action_ok(&raw);
                complete_job(
                    agent,
                    &id,
                    ok,
                    json!({
                        "ok": ok,
                        "target": "browser",
                        "action": action,
                        "message": if ok { format!("{action} ok ({raw})") }
                                   else { format!("{action} failed: {raw}") },
                        "detail": raw,
                        "via": "rust-host",
                    }),
                    if ok { None } else { Some(format!("{action} failed")) },
                );
            }
            Err(e) => complete_job(agent, &id, false, json!({}), Some(e)),
        }
        ack_ui_command(agent, &id);
        return;
    }

    if action == "screenshot" {
        let _ = app.emit("computer-open-browser", json!({ "job_id": id }));
        std::thread::sleep(Duration::from_millis(200));
        let scale = main_window(app)
            .ok()
            .and_then(|w| w.scale_factor().ok())
            .unwrap_or(1.0);
        let mut body = json!({ "label": "browser_rail", "scale": scale });
        if let Some(state) = app.try_state::<BrowserState>() {
            if let Ok(g) = state.last_bounds.lock() {
                if let Some(b) = g.clone() {
                    if b.width > 40.0 && b.height > 40.0 {
                        body["x"] = json!(b.x.round() as i64);
                        body["y"] = json!(b.y.round() as i64);
                        body["width"] = json!(b.width.round() as i64);
                        body["height"] = json!(b.height.round() as i64);
                    }
                }
            }
        }
        let page_url = app
            .try_state::<BrowserState>()
            .and_then(|s| s.current_url.lock().ok().map(|g| g.clone()))
            .unwrap_or_default();
        match auth_req(
            agent
                .post(&api_url("/api/computer/capture"))
                .set("Content-Type", "application/json"),
        )
        .send_json(body)
        {
            Ok(resp) => {
                let v: serde_json::Value = resp.into_json().unwrap_or_else(|_| json!({}));
                let capture = v.get("capture").cloned().unwrap_or(json!({}));
                let mut result = json!({
                    "ok": true,
                    "target": "browser",
                    "action": "screenshot",
                    "message": "Browser rail capture",
                    "via": "rust-host",
                    "url": page_url,
                    "scale": scale,
                });
                if let Some(obj) = capture.as_object() {
                    if let Some(map) = result.as_object_mut() {
                        for (k, val) in obj {
                            map.insert(k.clone(), val.clone());
                        }
                    }
                }
                complete_job(agent, &id, true, result, None);
            }
            Err(e) => complete_job(
                agent,
                &id,
                false,
                json!({}),
                Some(format!("screenshot capture failed: {e}")),
            ),
        }
        ack_ui_command(agent, &id);
        return;
    }

    log::warn!("computer-host: job {id} action={action} has no Rust handler");
    complete_job(
        agent,
        &id,
        false,
        json!({}),
        Some(format!("unsupported browser job action: {action}")),
    );
}

/// Poll document.readyState briefly so snapshot/page_text do not eval mid-nav.
fn wait_page_ready(app: &AppHandle, attempts: u32) -> bool {
    for i in 0..attempts {
        match browser_agent_action(
            app.clone(),
            "ready".into(),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ) {
            Ok(raw) => {
                if raw.contains("\"ok\":true")
                    || raw.contains("\"complete\"")
                    || raw.contains("\"interactive\"")
                {
                    return true;
                }
            }
            Err(_) => {
                // WebView may not exist yet right after open_browser.
                if i == 0 {
                    std::thread::sleep(Duration::from_millis(250));
                }
            }
        }
        std::thread::sleep(Duration::from_millis(200 + i as u64 * 100));
    }
    false
}

fn run_snapshot_with_retry(app: &AppHandle, job_id: &str, attempts: u32) -> Result<String, String> {
    let mut last_err = "snapshot failed".to_string();
    for i in 0..attempts {
        match browser_agent_action(
            app.clone(),
            "snapshot".into(),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            Some(job_id.to_string()),
            None,
        ) {
            Ok(raw) => return Ok(raw),
            Err(e) => {
                last_err = e;
                log::warn!(
                    "computer-host snapshot attempt {}/{}: {}",
                    i + 1,
                    attempts,
                    last_err
                );
                std::thread::sleep(Duration::from_millis(350 + i as u64 * 200));
            }
        }
    }
    Err(last_err)
}

fn run_page_text_with_retry(app: &AppHandle, attempts: u32) -> Result<String, String> {
    let mut last_err = "page_text failed".to_string();
    for i in 0..attempts {
        match browser_agent_action(
            app.clone(),
            "page_text".into(),
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
            None,
        ) {
            Ok(raw) => return Ok(raw),
            Err(e) => {
                last_err = e;
                std::thread::sleep(Duration::from_millis(300 + i as u64 * 150));
            }
        }
    }
    Err(last_err)
}

/// WebView2 may return either a JSON object string or a double-encoded JSON
/// string (because page_text JS uses JSON.stringify and the host serializes again).
fn parse_page_text_raw(raw: &str) -> serde_json::Value {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return json!({ "text": "" });
    }
    let mut val: serde_json::Value = match serde_json::from_str(trimmed) {
        Ok(v) => v,
        Err(_) => return json!({ "text": trimmed }),
    };
    // Double-encoded: "\"{...}\"" or "\"plain text\""
    if let Some(s) = val.as_str() {
        if let Ok(inner) = serde_json::from_str::<serde_json::Value>(s) {
            val = inner;
        } else {
            return json!({ "text": s });
        }
    }
    if val.is_object() {
        return val;
    }
    if let Some(s) = val.as_str() {
        return json!({ "text": s });
    }
    json!({ "text": trimmed })
}

/// Schedule embed navigate on the UI thread without blocking the poller.
fn fire_navigate(app: &AppHandle, url: &str) {
    let app2 = app.clone();
    let url2 = url.to_string();
    if let Err(e) = app.run_on_main_thread(move || {
        let state = app2.state::<BrowserState>();
        if let Err(err) = navigate_embed(&app2, state.inner(), &url2, None) {
            log::warn!("fire_navigate failed: {err}");
        }
    }) {
        log::warn!("fire_navigate schedule failed: {e}");
    }
}

fn run_navigate_on_main(app: &AppHandle, url: &str) -> Result<String, String> {
    let (tx, rx) = std::sync::mpsc::channel::<Result<String, String>>();
    let app2 = app.clone();
    let url2 = url.to_string();
    app.run_on_main_thread(move || {
        let state = app2.state::<BrowserState>();
        let r = navigate_embed(&app2, state.inner(), &url2, None);
        let _ = tx.send(r);
    })
    .map_err(|e| format!("run_on_main_thread: {e}"))?;
    rx.recv_timeout(Duration::from_secs(2))
        .map_err(|_| "navigate timed out on main thread".to_string())?
}

fn run_resync_bounds_on_main(app: &AppHandle) -> Result<(), String> {
    // Keep signature for potential future use; prefer browser_set_bounds from SPA.
    let (tx, rx) = std::sync::mpsc::channel::<Result<(), String>>();
    let app2 = app.clone();
    app.run_on_main_thread(move || {
        let state = app2.state::<BrowserState>();
        let b = state
            .last_bounds
            .lock()
            .ok()
            .and_then(|g| g.clone())
            .filter(|bb| bb.width >= 200.0 && bb.height >= 160.0)
            .unwrap_or_else(|| default_rail_bounds(&app2));
        if let Some(wv) = app2.get_webview(LABEL) {
            let may = embed_may_show(state.inner());
            let _ = apply_bounds(&wv, &clamp_bounds(&b), may);
        }
        let _ = tx.send(Ok(()));
    })
    .map_err(|e| format!("run_on_main_thread: {e}"))?;
    rx.recv_timeout(Duration::from_secs(5))
        .map_err(|_| "resync bounds timeout".to_string())?
}

fn arm_pending_navigate(app: &AppHandle, job_id: String, url: String) {
    if let Some(st) = app.try_state::<BrowserState>() {
        if let Ok(mut g) = st.pending_navigate.lock() {
            *g = Some((job_id.clone(), url.clone()));
        }
    }
    let app2 = app.clone();
    let jid = job_id;
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(8));
        if let Some(st) = app2.try_state::<BrowserState>() {
            let leftover = if let Ok(mut g) = st.pending_navigate.lock() {
                if g.as_ref().map(|(id, _)| id == &jid).unwrap_or(false) {
                    g.take()
                } else {
                    None
                }
            } else {
                None
            };
            if let Some((id, dest)) = leftover {
                let agent = ureq::AgentBuilder::new()
                    .timeout_connect(Duration::from_secs(2))
                    .timeout(Duration::from_secs(8))
                    .build();
                // Do not fake SUCCESS — the page never fired on_page_load.
                // The agent must see a failed navigate, not continue as if ready.
                complete_job(
                    &agent,
                    &id,
                    false,
                    json!({
                        "ok": false,
                        "target": "browser",
                        "action": "navigate",
                        "message": format!(
                            "Navigation timed out — the in-app Browser rail did not finish loading {dest}."
                        ),
                        "url": dest,
                        "via": "rust-host-timeout",
                        "user_visible": true,
                    }),
                    Some("navigation timed out".into()),
                );
            }
        }
    });
}

/// Hostname with any leading `www.` dropped (walmart.com == www.walmart.com).
fn nav_host(u: &str) -> String {
    Url::parse(u)
        .ok()
        .and_then(|p| p.host_str().map(|h| h.to_ascii_lowercase()))
        .map(|h| h.trim_start_matches("www.").to_string())
        .unwrap_or_default()
}

fn same_page_url(a: &str, b: &str) -> bool {
    a.trim_end_matches('/') == b.trim_end_matches('/')
}

fn complete_pending_navigate_if_any(app: &AppHandle, loaded_url: &str, prev_url: &str) {
    // Only a load event that is *our* navigation settles the pending job:
    // the requested URL itself, or a new page on the same host (redirect,
    // e.g. /search → /blocked). A late Finished event from the previous
    // page (same URL the rail already showed) used to complete a walmart
    // /search navigate with "URL: https://www.walmart.com/" — the agent
    // read that as "search did not happen" and hunted for the search box.
    // Anything else resolves via the 8s optimistic timer.
    let pending = if let Some(st) = app.try_state::<BrowserState>() {
        st.pending_navigate.lock().ok().and_then(|mut g| {
            let matches = g
                .as_ref()
                .map(|(_, dest)| {
                    if same_page_url(loaded_url, dest) {
                        return true;
                    }
                    let want = nav_host(dest);
                    let got = nav_host(loaded_url);
                    let same_host = want.is_empty() || got.is_empty() || want == got;
                    same_host && !same_page_url(loaded_url, prev_url)
                })
                .unwrap_or(false);
            if matches {
                g.take()
            } else {
                None
            }
        })
    } else {
        None
    };
    let Some((id, dest)) = pending else {
        return;
    };
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(2))
        .timeout(Duration::from_secs(8))
        .build();
    complete_job(
        &agent,
        &id,
        true,
        json!({
            "ok": true,
            "target": "browser",
            "action": "navigate",
            "message": format!(
                "SUCCESS: Page is open in the in-app Browser rail. URL: {loaded_url}."
            ),
            "url": loaded_url,
            "requested_url": dest,
            "via": "rust-host",
            "user_visible": true,
        }),
        None,
    );
}

fn complete_job(
    agent: &ureq::Agent,
    job_id: &str,
    ok: bool,
    result: serde_json::Value,
    error: Option<String>,
) {
    let body = json!({
        "ok": ok,
        "result": result,
        "error": error,
    });
    let url = api_url(&format!("/api/computer/jobs/{job_id}/complete"));
    if let Err(e) = auth_req(
        agent
            .post(&url)
            .set("Content-Type", "application/json"),
    )
    .send_json(body)
    {
        log::warn!("computer-host complete {job_id}: {e}");
    } else {
        log::info!("computer-host completed job {job_id} ok={ok}");
    }
}

fn ack_ui_command(agent: &ureq::Agent, job_id: &str) {
    let url = api_url(&format!("/api/computer/ui/command/ack?job_id={job_id}"));
    let _ = auth_req(agent.post(&url)).call();
}

#[tauri::command]
pub fn browser_reload(app: AppHandle) -> Result<(), String> {
    let wv = app
        .get_webview(LABEL)
        .ok_or_else(|| "browser not open".to_string())?;
    wv.eval("window.location.reload()")
        .map_err(|e| format!("reload: {e}"))
}

#[tauri::command]
pub fn browser_go_back(app: AppHandle) -> Result<(), String> {
    let wv = app
        .get_webview(LABEL)
        .ok_or_else(|| "browser not open".to_string())?;
    wv.eval("window.history.back()")
        .map_err(|e| format!("back: {e}"))
}

#[tauri::command]
pub fn browser_go_forward(app: AppHandle) -> Result<(), String> {
    let wv = app
        .get_webview(LABEL)
        .ok_or_else(|| "browser not open".to_string())?;
    wv.eval("window.history.forward()")
        .map_err(|e| format!("forward: {e}"))
}

/// Live URL of the embed. Prefers WebView2's real location (link clicks / history);
/// falls back to last navigated URL in state.
#[tauri::command(async)]
pub fn browser_current_url(
    app: AppHandle,
    state: State<'_, BrowserState>,
) -> Result<String, String> {
    if let Some(wv) = app.get_webview(LABEL) {
        if let Ok(u) = wv.url() {
            let s = u.as_str().to_string();
            if !s.is_empty() && !s.starts_with("about:") {
                if let Ok(mut g) = state.current_url.lock() {
                    *g = s.clone();
                }
                return Ok(s);
            }
        }
        // Fallback: ask the page (some navigations lag wv.url())
        let (tx, rx) = std::sync::mpsc::channel::<String>();
        if wv
            .eval_with_callback(
                "try{String(location.href||'')}catch(e){''}",
                move |result| {
                    let _ = tx.send(result);
                },
            )
            .is_ok()
        {
            if let Ok(raw) = rx.recv_timeout(Duration::from_millis(400)) {
                let s = raw.trim().trim_matches('"').to_string();
                if !s.is_empty() && !s.starts_with("about:") {
                    if let Ok(mut g) = state.current_url.lock() {
                        *g = s.clone();
                    }
                    return Ok(s);
                }
            }
        }
    }
    state
        .current_url
        .lock()
        .map(|g| g.clone())
        .map_err(|e| e.to_string())
}

/// CSS-pixel bounds of the embed (for host screenshot crop / layout).
#[tauri::command]
pub fn browser_last_bounds(state: State<'_, BrowserState>) -> Result<Option<BrowserBounds>, String> {
    state
        .last_bounds
        .lock()
        .map(|g| g.clone())
        .map_err(|e| e.to_string())
}

/// Agent-driven input into the embed (coordinates relative to the page viewport).
/// Used by the computer-use host poller — not a feature gate, task completion path.
///
/// `async`: this command blocks on `eval_with_callback` / CDP results. A plain
/// sync command runs on the main thread, and WebView2 delivers those callbacks
/// through the main thread's message pump — so every SPA-claimed snapshot /
/// page_text / click deadlocked into the 9s timeout while the Rust poller
/// thread (off-main) ran the very same evals in ~1s. Off-main is mandatory.
#[tauri::command(async)]
pub fn browser_agent_action(
    app: AppHandle,
    action: String,
    x: Option<f64>,
    y: Option<f64>,
    x2: Option<f64>,
    y2: Option<f64>,
    text: Option<String>,
    key: Option<String>,
    button: Option<String>,
    dy: Option<i32>,
    // Snapshot job id — complete via eval result (not page→localhost fetch).
    job_id: Option<String>,
    // Element ref from computer_snapshot (e.g. e3)
    r#ref: Option<String>,
) -> Result<String, String> {
    let wv = app
        .get_webview(LABEL)
        .ok_or_else(|| "browser not open — navigate first".to_string())?;
    let act = action.to_lowercase();
    let jid_owned = job_id.clone().unwrap_or_default();
    let button_cdp = button.clone();
    let text_cdp = text.clone();
    let key_cdp = key.clone();
    let ref_cdp = r#ref.clone();

    // Press-and-hold: Remedy as the owner's authorized hands for an
    // accessibility gesture (press-and-hold verification, hold-to-confirm).
    // Resolve coordinates (x/y direct, or locate by text/ref — including in a
    // challenge iframe/shadow), then hold a REAL trusted mouse press for the
    // duration. Synthetic events never pass these walls; trusted CDP input is
    // the whole point.
    if act == "press_hold" {
        let hold_ms = dy.map(|v| v.clamp(300, 12000)).unwrap_or(2600) as u64;
        let (px, py) = resolve_point(&wv, x, y, text.as_deref(), r#ref.as_deref())?;
        #[cfg(windows)]
        {
            let _ = cdp_mouse(&wv, "mouseMoved", px, py, "none", 0);
            cdp_mouse(&wv, "mousePressed", px, py, "left", 1)?;
            std::thread::sleep(Duration::from_millis(hold_ms));
            cdp_mouse(&wv, "mouseReleased", px, py, "left", 1)?;
            return Ok(format!("ok:press_hold:{}:{}:{}ms", px.round(), py.round(), hold_ms));
        }
        #[cfg(any(
            target_os = "linux",
            target_os = "dragonfly",
            target_os = "freebsd",
            target_os = "netbsd",
            target_os = "openbsd"
        ))]
        {
            let _ = linux_mouse(&wv, "mouseMoved", px, py, "none");
            linux_mouse(&wv, "mousePressed", px, py, "left")?;
            std::thread::sleep(Duration::from_millis(hold_ms));
            linux_mouse(&wv, "mouseReleased", px, py, "left")?;
            return Ok(format!("ok:press_hold:{}:{}:{}ms", px.round(), py.round(), hold_ms));
        }
        #[cfg(not(any(
            windows,
            target_os = "linux",
            target_os = "dragonfly",
            target_os = "freebsd",
            target_os = "netbsd",
            target_os = "openbsd"
        )))]
        {
            // Last-resort synthetic pointer hold (may not pass trusted walls).
            let js = format!(
                r#"(function(){{
  const x={px}, y={py};
  const el=document.elementFromPoint(x,y)||document.body;
  const opt={{bubbles:true,cancelable:true,clientX:x,clientY:y,pointerId:1,button:0,buttons:1}};
  try{{ el.dispatchEvent(new PointerEvent('pointerdown',opt)); }}catch(e){{}}
  try{{ el.dispatchEvent(new MouseEvent('mousedown',opt)); }}catch(e){{}}
  setTimeout(function(){{
    try{{ el.dispatchEvent(new PointerEvent('pointerup',opt)); }}catch(e){{}}
    try{{ el.dispatchEvent(new MouseEvent('mouseup',opt)); }}catch(e){{}}
    try{{ el.dispatchEvent(new MouseEvent('click',opt)); }}catch(e){{}}
  }}, {hold_ms});
  return 'ok:press_hold_synth';
}})()"#
            );
            let (tx, rx) = std::sync::mpsc::channel::<String>();
            wv.eval_with_callback(&js, move |r| {
                let _ = tx.send(r);
            })
            .map_err(|e| format!("press_hold: {e}"))?;
            let _ = rx.recv_timeout(Duration::from_millis(hold_ms + 1500));
            return Ok(format!("ok:press_hold:{}:{}:{}ms", px.round(), py.round(), hold_ms));
        }
    }

    let js = match act.as_str() {
        "snapshot" | "a11y" => {
            // Richer a11y-ish scrape; return array via eval_with_callback.
            // Deep query (shadow DOM + same-origin iframes) + per-element card
            // context so generic controls in a list are distinguishable.
            format!(
                r#"(function(){{
  {dom}
  try {{
    window.__rmdyDeep('[data-remedy-ref]').forEach(el => el.removeAttribute('data-remedy-ref'));
  }} catch(e) {{}}
  const sel='a,button,input,textarea,select,[role=button],[role=link],[role=textbox],[role=tab],[role=menuitem],[role=option],[role=checkbox],[role=switch],[role=radio],[contenteditable=true],summary,label,[onclick]';
  const nodes=window.__rmdyDeep(sel).filter(window.__rmdyVisible).slice(0,160);
  return nodes.map((el,i) => {{
    const r=window.__rmdyRect(el)||el.getBoundingClientRect();
    const ref='e'+(i+1);
    try {{ el.setAttribute('data-remedy-ref', ref); }} catch(e) {{}}
    const text=(el.innerText||'').trim().replace(/\s+/g,' ').slice(0,120);
    const tag=(el.tagName||'').toLowerCase();
    const itype=String(el.type||'').toLowerCase();
    const auto=String((el.getAttribute&&el.getAttribute('autocomplete'))||'').toLowerCase();
    // Never ship password/OTP/secret field values into tool results → LLM.
    const nm=((el.getAttribute&&el.getAttribute('name'))||'').toLowerCase();
    const sensitive = tag==='input' && (
      itype==='password' || itype==='hidden' ||
      auto.includes('password') || auto.includes('one-time') || auto==='one-time-code' ||
      auto.includes('cc-') || auto.includes('card') ||
      nm.match(/pass|otp|cvv|cvc|secret|token/)
    );
    const rawVal = (el.value!=null?String(el.value):'');
    const hasVal = rawVal.length>0;
    const aria=(el.getAttribute&&el.getAttribute('aria-label'))||'';
    const titleAttr=(el.getAttribute&&el.getAttribute('title'))||'';
    const ph=(el.placeholder||'');
    // Prefer labels/placeholder over raw value for name (avoids password in name).
    // Keep the visible placeholder even when aria-label differs (X compose:
    // aria "Post text" + placeholder "What's happening?").
    let name=(aria||titleAttr||text||ph||el.name||(sensitive?'':rawVal)||el.tagName||'').trim().replace(/\s+/g,' ');
    if(ph && name.toLowerCase().indexOf(ph.toLowerCase().slice(0,24))<0) name=(name+' '+ph).trim();
    name=name.slice(0,120);
    // Selected/pressed state (a store already chosen, a tab active, …).
    const state=((el.getAttribute&&(el.getAttribute('aria-pressed')||el.getAttribute('aria-selected')||el.getAttribute('aria-checked')))||'').toString();
    let ctx=''; try {{ ctx=window.__rmdyCtx(el); }} catch(e) {{}}
    return {{
      ref, tag, role:(el.getAttribute&&el.getAttribute('role'))||'',
      name, text,
      context: (ctx===name)?'':ctx,
      state: (state==='true'||state==='false'||state==='mixed')?state:'',
      value: sensitive ? (hasVal ? '[filled]' : '') : rawVal.slice(0,80),
      value_redacted: !!sensitive,
      placeholder:(el.placeholder||'').slice(0,80),
      href:(el.href||(el.getAttribute&&el.getAttribute('href'))||'').slice(0,200),
      title:((el.getAttribute&&el.getAttribute('title'))||'').slice(0,80),
      x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2),
      w:Math.round(r.width), h:Math.round(r.height)
    }};
  }});
}})()"#,
                dom = REMEDY_DOM_JS
            )
        }
        "page_text" => {
            r#"(function(){
  const t=(document.body&&document.body.innerText)||'';
  const title=document.title||'';
  const url=location.href||'';
  // Cap page text so less personal content reaches the agent/LLM by default.
  return JSON.stringify({title,url,text:t.replace(/\s+\n/g,'\n').trim().slice(0,8000)});
})()"#
            .to_string()
        }
        "ready" => {
            // Page settle signal for agents (document + optional network quiet)
            r#"(function(){
  const ready=document.readyState||'';
  const busy=!!(window.__remedy_nav_busy);
  const title=document.title||'';
  const url=location.href||'';
  return JSON.stringify({ready,busy,title,url,ok: ready==='complete'||ready==='interactive'});
})()"#
            .to_string()
        }
        "click_text" | "hover_text" => {
            let needle = text.clone().unwrap_or_default();
            if needle.is_empty() {
                return Err("text required for click_text".into());
            }
            let escaped = needle
                .replace('\\', "\\\\")
                .replace('\'', "\\'")
                .replace('\n', " ")
                .replace('\r', " ");
            format!(
                r#"(function(){{
  {dom}
  const q='{escaped}'.toLowerCase().trim();
  if(!q) return 'missing-text';
  const sel=window.__rmdyClickSel;
  let hit=window.__rmdyPick(q, sel);
  // Scroll passes: find off-screen matches (OSWorld: re-observe after scroll)
  if(!hit.best||hit.bestS<40){{
    for(let pass=0; pass<4 && (!hit.best||hit.bestS<40); pass++){{
      window.scrollBy(0, Math.floor(innerHeight*0.75));
      hit=window.__rmdyPick(q, sel);
    }}
  }}
  const best=hit.best, bestS=hit.bestS, second=hit.second, secondS=hit.secondS;
  if(!best||bestS<40) return 'no-match:'+q;
  if(second && bestS-secondS<8 && bestS>=70){{
    const bn=window.__rmdyName(best).slice(0,40);
    const sn=window.__rmdyName(second).slice(0,40);
    return 'ambiguous:'+bn+' vs '+sn+' — pass ref= from computer_snapshot';
  }}
  try{{ best.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
  try{{ best.focus({{preventScroll:true}}); }}catch(e){{}}
  const r=window.__rmdyRect(best)||best.getBoundingClientRect();
  const x=r.x+r.width/2, y=r.y+r.height/2;
  const name=window.__rmdyName(best).replace(/\s+/g,' ').slice(0,80);
  const tag=(best.tagName||'').toLowerCase();
  const itype=(best.type||'').toLowerCase();
  // Locate only — the host dispatches a TRUSTED click at these coords
  // (synthetic dispatchEvent is isTrusted=false → bot-flagged at checkout).
  return 'okxy:'+Math.round(x)+':'+Math.round(y)+':'+bestS.toFixed(0)+':'+tag+':'+itype+':'+name;
}})()"#,
                dom = REMEDY_DOM_JS
            )
        }
        "click_ref" | "hover_ref" => {
            let rf = r#ref.unwrap_or_default();
            if rf.is_empty() {
                return Err("ref required for click_ref".into());
            }
            let escaped = rf.replace('\\', "\\\\").replace('\'', "\\'");
            format!(
                r#"(function(){{
  {dom}
  const ref='{escaped}';
  const el=window.__rmdyFind(ref);
  if(!el) return 'missing-ref:'+ref;
  try{{ el.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
  try{{ el.focus({{preventScroll:true}}); }}catch(e){{}}
  const r=window.__rmdyRect(el)||el.getBoundingClientRect();
  const x=r.x+r.width/2, y=r.y+r.height/2;
  // Locate only — host clicks these coords via trusted input.
  return 'okxy:'+Math.round(x)+':'+Math.round(y)+':'+ref+':'+(el.tagName||'?');
}})()"#,
                dom = REMEDY_DOM_JS
            )
        }
        "click" | "hover" => {
            // Prefer ref when provided
            if let Some(rf) = r#ref.clone().filter(|s| !s.is_empty()) {
                let escaped = rf.replace('\\', "\\\\").replace('\'', "\\'");
                format!(
                    r#"(function(){{
  {dom}
  const ref='{escaped}';
  const el=window.__rmdyFind(ref);
  if(!el) return 'missing-ref:'+ref;
  try{{ el.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
  try{{ el.focus({{preventScroll:true}}); }}catch(e){{}}
  const r=window.__rmdyRect(el)||el.getBoundingClientRect();
  const x=r.x+r.width/2, y=r.y+r.height/2;
  // Locate only — host clicks these coords via trusted input.
  return 'okxy:'+Math.round(x)+':'+Math.round(y)+':'+ref;
}})()"#,
                    dom = REMEDY_DOM_JS
                )
            } else {
            let cx = x.unwrap_or(0.0);
            let cy = y.unwrap_or(0.0);
            let btn = button.unwrap_or_else(|| "left".into());
            let js_btn = if btn == "right" {
                "contextmenu"
            } else {
                "click"
            };
            // js_btn/btn only picked the synthetic event name — the host now
            // dispatches trusted input; right-click comes from button= param.
            let _ = js_btn;
            format!(
                r#"(function(){{
  const x={cx}, y={cy};
  const el=document.elementFromPoint(x,y)||document.body;
  if(!el) return 'no element';
  try{{ el.focus({{preventScroll:true}}); }}catch(e){{}}
  // Locate only — host clicks these coords via trusted input.
  return 'okxy:'+Math.round(x)+':'+Math.round(y)+':'+(el.tagName||'?');
}})()"#,
                cx = cx,
                cy = cy,
            )
            }
        }
        "type" | "type_text" => {
            let t = text.unwrap_or_default();
            // Escape for JS string
            let escaped = t
                .replace('\\', "\\\\")
                .replace('\'', "\\'")
                .replace('\n', "\\n")
                .replace('\r', "\\r");
            // key= carries query/label/hint (see handle_job). Ref still wins;
            // a stale ref falls through to the visible-text pick.
            let locate = type_locate_js(
                r#ref.as_deref().filter(|s| !s.is_empty()),
                key.as_deref().filter(|s| !s.is_empty()),
            );
            format!(
                r#"(function(){{
  {locate}
  const t='{escaped}';
  try{{ el.focus({{preventScroll:true}}); }}catch(e){{}}
  if(el && (el.isContentEditable || /^(INPUT|TEXTAREA)$/.test(el.tagName))){{
    const start=el.selectionStart??el.value?.length??0;
    const end=el.selectionEnd??start;
    if(typeof el.value==='string'){{
      el.value=el.value.slice(0,start)+t+el.value.slice(end);
      try{{ el.selectionStart=el.selectionEnd=start+t.length; }}catch(e){{}}
      el.dispatchEvent(new Event('input',{{bubbles:true}}));
    }} else {{
      document.execCommand('insertText', false, t);
    }}
    return 'ok';
  }}
  for(const ch of t){{
    document.activeElement?.dispatchEvent(new KeyboardEvent('keydown',{{key:ch,bubbles:true}}));
    document.activeElement?.dispatchEvent(new KeyboardEvent('keypress',{{key:ch,bubbles:true}}));
    document.activeElement?.dispatchEvent(new KeyboardEvent('keyup',{{key:ch,bubbles:true}}));
  }}
  return 'ok-fallback';
}})()"#,
                locate = locate,
                escaped = escaped
            )
        }
        "select" => {
            let want = text.unwrap_or_default();
            if want.trim().is_empty() {
                return Err("select needs the option to choose (value=)".to_string());
            }
            let esc = |s: &str| {
                s.replace('\\', "\\\\")
                    .replace('\'', "\\'")
                    .replace('\n', "\\n")
            };
            let escaped = esc(&want);
            let er = esc(&r#ref.clone().unwrap_or_default());
            // Field label (computer_select hint= / computer_fill text=) rides
            // in `key`; see handle_job.
            let hint = esc(&key.clone().unwrap_or_default());
            format!(
                r#"(function(){{
  {dom}
  const want='{escaped}';
  const w=want.toLowerCase();
  const hint='{hint}'.trim().toLowerCase();
  const hasOpt=(s)=>{{
    for(const opt of s.options){{
      const tv=(opt.text||'').trim();
      if(!tv && !opt.value) continue;
      if(opt.value===want || tv===want || tv.toLowerCase()===w) return opt;
    }}
    return null;
  }};
  const labelOf=(s)=>{{
    let t=(s.getAttribute('aria-label')||'')+' ';
    if(s.id){{ const l=document.querySelector('label[for="'+CSS.escape(s.id)+'"]'); if(l) t+=(l.textContent||'')+' '; }}
    const wrap=s.closest('label'); if(wrap) t+=(wrap.textContent||'')+' ';
    const lb=s.getAttribute('aria-labelledby');
    if(lb){{ for(const i of lb.split(/\s+/)){{ const n=document.getElementById(i); if(n) t+=(n.textContent||'')+' '; }} }}
    t+=(s.name||'')+' '+(s.id||'');
    return t.replace(/\s+/g,' ').trim().toLowerCase();
  }};
  let el=null;
  if('{er}'){{
    el=window.__rmdyFind('{er}');
    if(!el) return 'missing-ref:{er}';
  }} else {{
    const nodes=Array.from(document.querySelectorAll('select'));
    if(hint){{
      el=nodes.find(s=>labelOf(s).indexOf(hint)>=0)||null;
      if(!el) return 'no-match:'+hint;
    }} else if(nodes.length===1){{
      el=nodes[0];
    }} else {{
      const cands=nodes.filter(hasOpt);
      if(cands.length===1) el=cands[0];
      else if(cands.length>1) return 'ambiguous:'+cands.length+' dropdowns offer '+want+' - pass ref or hint';
    }}
  }}
  if(!el) return 'no element';
  const tag=(el.tagName||'').toLowerCase();
  if(tag!=='select') return 'not-select:'+tag;
  const opt=hasOpt(el);
  if(!opt) return 'no-option:'+want;
  el.value=opt.value;
  el.dispatchEvent(new Event('input',{{bubbles:true}}));
  el.dispatchEvent(new Event('change',{{bubbles:true}}));
  return 'ok:'+el.value;
}})()"#,
                dom = REMEDY_DOM_JS,
                escaped = escaped,
                er = er,
                hint = hint
            )
        }
        "key" => {
            let k = key.unwrap_or_else(|| "Enter".into());
            let canon = match k.as_str() {
                "enter" | "return" | "Enter" => "Enter",
                "tab" | "Tab" => "Tab",
                other => other,
            };
            let escaped = canon.replace('\\', "\\\\").replace('\'', "\\'");
            format!(
                r#"(function(){{
  const key='{escaped}';
  const el=document.activeElement||document.body;
  const opts={{key:key,code:key,bubbles:true,cancelable:true}};
  el.dispatchEvent(new KeyboardEvent('keydown', opts));
  el.dispatchEvent(new KeyboardEvent('keyup', opts));
  if(key==='Enter' && el.form) try{{ el.form.requestSubmit(); }}catch(e){{}}
  return 'ok';
}})()"#
            )
        }
        "scroll" => {
            let cx = x.unwrap_or(0.0);
            let cy = y.unwrap_or(0.0);
            // dy notches: negative = scroll down (increase scrollTop)
            let delta_y = -(dy.unwrap_or(-3) * 80);
            format!(
                r#"(function(){{
  const el=document.elementFromPoint({cx},{cy})||document.scrollingElement||document.documentElement;
  el.scrollBy({{top:{delta_y},left:0,behavior:'instant'}});
  return 'ok';
}})()"#,
                cx = cx,
                cy = cy,
                delta_y = delta_y,
            )
        }
        "drag" => {
            let x1 = x.unwrap_or(0.0);
            let y1 = y.unwrap_or(0.0);
            let x2 = x2.unwrap_or(x1);
            let y2 = y2.unwrap_or(y1);
            format!(
                r#"(function(){{
  const el=document.elementFromPoint({x1},{y1})||document.body;
  el.dispatchEvent(new MouseEvent('mousedown',{{bubbles:true,clientX:{x1},clientY:{y1}}}));
  el.dispatchEvent(new MouseEvent('mousemove',{{bubbles:true,clientX:{x2},clientY:{y2}}}));
  el.dispatchEvent(new MouseEvent('mouseup',{{bubbles:true,clientX:{x2},clientY:{y2}}}));
  return 'ok';
}})()"#
            )
        }
        other => return Err(format!("unknown browser agent action: {other}")),
    };

    // Prefer eval_with_callback so snapshot returns elements in-process
    // (no HTTPS→localhost fetch). Other actions also get real JS return values.
    // Snapshot/page_text need a longer budget: after fire-and-forget navigate the
    // WebView2 may still be loading and eval callbacks stall until the load settles.
    let eval_timeout_s: u64 = match act.as_str() {
        // Heavy retail pages (Walmart/Target) stall evals well past 5s while
        // loading — 5s snapshots died 3× in a row and Remedy went blind
        // mid-shop. Budgets sized with executor waits (snapshot 22s, click
        // 18s) at 2 host attempts each.
        "snapshot" | "a11y" | "page_text" => 9,
        "click" | "click_ref" | "click_text" | "hover" | "hover_ref" | "hover_text"
        | "type" | "type_text" | "key" | "scroll" | "drag" | "select" => 8,
        "ready" => 2,
        _ => 4,
    };
    // Trusted typing / named keys first (Windows CDP / Linux GDK — real
    // input pipeline). Any failure falls through to the synthetic JS path.
    #[cfg(any(
        windows,
        target_os = "linux",
        target_os = "dragonfly",
        target_os = "freebsd",
        target_os = "netbsd",
        target_os = "openbsd"
    ))]
    {
        if matches!(act.as_str(), "type" | "type_text") {
            if let Some(txt) = text_cdp.as_deref().filter(|s| !s.is_empty()) {
                let (ftx, frx) = std::sync::mpsc::channel::<String>();
                // Field-targeted type: focus the snapshot ref first (or the
                // field found by visible-text query). Typing into whatever
                // happens to be focused was the old hole — computer_type
                // ref=eN would land in the wrong box and still report
                // ok:trusted-type. query= relocates a stale ref in-process.
                let focus_js = {
                    let locate = type_locate_js(
                        ref_cdp.as_deref().filter(|s| !s.is_empty()),
                        key_cdp.as_deref().filter(|s| !s.is_empty()),
                    );
                    format!(
                        r#"(function(){{
  {locate}
  try{{ el.focus({{preventScroll:true}}); }}catch(e){{}}
  return (el&&(el.isContentEditable||/^(INPUT|TEXTAREA)$/.test(el.tagName)
    ||/^(textbox|searchbox|combobox)$/.test(((el.getAttribute&&el.getAttribute('role'))||'').toLowerCase())))
    ?'rm-editable':'rm-not-editable';
}})()"#,
                        locate = locate
                    )
                };
                if wv
                    .eval_with_callback(&focus_js, move |r| {
                        let _ = ftx.send(r);
                    })
                    .is_ok()
                {
                    if let Ok(chk) = frx.recv_timeout(Duration::from_secs(2)) {
                        let flag = chk.trim().trim_matches('"');
                        if flag.starts_with("no-match")
                            || flag.starts_with("missing-ref")
                            || flag.starts_with("ambiguous")
                        {
                            log::info!("browser agent action {act} -> {flag}");
                            return Ok(flag.to_string());
                        }
                        let typed = {
                            #[cfg(windows)]
                            {
                                cdp_insert_text(&wv, txt).is_ok()
                            }
                            #[cfg(any(
                                target_os = "linux",
                                target_os = "dragonfly",
                                target_os = "freebsd",
                                target_os = "netbsd",
                                target_os = "openbsd"
                            ))]
                            {
                                linux_insert_text(&wv, txt).is_ok()
                            }
                        };
                        if flag == "rm-editable" && typed {
                            log::info!("browser agent action {act} → ok:trusted-type");
                            return Ok("ok:trusted-type".into());
                        }
                    }
                }
            }
        }
        if act == "key" {
            if let Some(k) = key_cdp.as_deref() {
                let named = {
                    #[cfg(windows)]
                    {
                        cdp_named_key(&wv, k)
                    }
                    #[cfg(any(
                        target_os = "linux",
                        target_os = "dragonfly",
                        target_os = "freebsd",
                        target_os = "netbsd",
                        target_os = "openbsd"
                    ))]
                    {
                        linux_named_key(&wv, k)
                    }
                };
                if let Some(Ok(())) = named {
                    log::info!("browser agent action key → ok:trusted-key");
                    return Ok("ok:trusted-key".into());
                }
            }
        }
    }

    let (tx, rx) = std::sync::mpsc::channel::<String>();
    wv.eval_with_callback(js, move |result| {
        let _ = tx.send(result);
    })
    .map_err(|e| format!("browser agent action: {e}"))?;
    let raw = rx
        .recv_timeout(Duration::from_secs(eval_timeout_s))
        .map_err(|_| {
            format!("browser agent action timed out on webview ({eval_timeout_s}s / {act})")
        })?;

    // Trusted click hand-off: the locator JS returned viewport coords; the
    // host dispatches real input there. Fallback (non-Windows rail / CDP
    // error): synthetic events at the same point — the old behavior.
    {
        let raw_unq = raw.trim().trim_matches('"').to_string();
        if let Some(rest) = raw_unq.strip_prefix("okxy:") {
            let mut it = rest.splitn(3, ':');
            let cx: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(-1.0);
            let cy: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(-1.0);
            let meta = it.next().unwrap_or("").to_string();
            if cx >= 0.0 && cy >= 0.0 {
                let hover_only = act.starts_with("hover");
                let btn = button_cdp.clone().unwrap_or_else(|| "left".into());
                #[cfg(windows)]
                let trusted = if hover_only {
                    match cdp_hover_trusted(&wv, cx, cy) {
                        Ok(()) => true,
                        Err(e) => {
                            log::warn!("trusted hover failed — synthetic fallback: {e}");
                            false
                        }
                    }
                } else {
                    match cdp_click_trusted(&wv, cx, cy, &btn) {
                        Ok(()) => true,
                        Err(e) => {
                            log::warn!("trusted click failed — synthetic fallback: {e}");
                            false
                        }
                    }
                };
                #[cfg(any(
                    target_os = "linux",
                    target_os = "dragonfly",
                    target_os = "freebsd",
                    target_os = "netbsd",
                    target_os = "openbsd"
                ))]
                let trusted = if hover_only {
                    match linux_hover_trusted(&wv, cx, cy) {
                        Ok(()) => true,
                        Err(e) => {
                            log::warn!("linux trusted hover failed — synthetic fallback: {e}");
                            false
                        }
                    }
                } else {
                    match linux_click_trusted(&wv, cx, cy, &btn) {
                        Ok(()) => true,
                        Err(e) => {
                            log::warn!("linux trusted click failed — synthetic fallback: {e}");
                            false
                        }
                    }
                };
                #[cfg(not(any(
                    windows,
                    target_os = "linux",
                    target_os = "dragonfly",
                    target_os = "freebsd",
                    target_os = "netbsd",
                    target_os = "openbsd"
                )))]
                let trusted = false;
                if !trusted {
                    let fb = if hover_only {
                        format!(
                            r#"(function(){{
  const x={cx}, y={cy};
  const el=document.elementFromPoint(x,y)||document.body;
  const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window}};
  try{{ el.dispatchEvent(new MouseEvent('mouseover', opts)); }}catch(e){{}}
  try{{ el.dispatchEvent(new MouseEvent('mouseenter', opts)); }}catch(e){{}}
  try{{ el.dispatchEvent(new MouseEvent('mousemove', opts)); }}catch(e){{}}
}})()"#
                        )
                    } else {
                        let ev = if btn == "right" { "contextmenu" } else { "click" };
                        let code = if btn == "right" { 2 } else { 0 };
                        format!(
                            r#"(function(){{
  const x={cx}, y={cy};
  const el=document.elementFromPoint(x,y)||document.body;
  const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window,button:{code}}};
  try{{ el.dispatchEvent(new MouseEvent('mousedown', opts)); }}catch(e){{}}
  try{{ el.dispatchEvent(new MouseEvent('mouseup', opts)); }}catch(e){{}}
  try{{ el.dispatchEvent(new MouseEvent('{ev}', opts)); }}catch(e){{}}
  if({code}===0&&typeof el.click==='function') try{{ el.click(); }}catch(e){{}}
}})()"#
                        )
                    };
                    let _ = wv.eval(&fb);
                }
                let out = format!("ok:{meta}");
                log::info!(
                    "browser agent action {act} → {out} ({} input)",
                    if trusted { "trusted" } else { "synthetic" }
                );
                return Ok(out);
            }
        }
    }

    if act == "snapshot" || act == "a11y" {
        // raw is JSON array of elements (or null/error string)
        let elements_val: serde_json::Value =
            serde_json::from_str(&raw).unwrap_or_else(|_| json!([]));
        let elements: Vec<serde_json::Value> = elements_val
            .as_array()
            .cloned()
            .unwrap_or_default();
        let n = elements.len();
        if !jid_owned.is_empty() {
            // Prefer a11y push first (sets last_elements on bridge for click-by-ref)
            let agent = ureq::AgentBuilder::new()
                .timeout_connect(Duration::from_secs(1))
                .timeout(Duration::from_secs(3))
                .build();
            let push_ok = agent
                .post(&api_url("/api/computer/a11y/push"))
                .set("Content-Type", "application/json")
                .send_json(json!({
                    "job_id": jid_owned,
                    "elements": elements_val,
                }))
                .is_ok();
            if !push_ok {
                complete_job(
                    &agent,
                    &jid_owned,
                    true,
                    json!({
                        "ok": true,
                        "target": "browser",
                        "action": "snapshot",
                        "message": format!("{n} interactive elements"),
                        "elements": elements,
                        "via": "eval-callback",
                    }),
                    None,
                );
            }
        }
        log::info!("browser agent snapshot n={n}");
        return Ok(raw);
    }

    if act == "page_text" {
        // Normalize double-encoded JSON.stringify results so callers always
        // get a JSON object string with title/url/text fields.
        let parsed = parse_page_text_raw(&raw);
        let normalized = parsed.to_string();
        log::info!(
            "browser agent page_text len={} text_len={}",
            normalized.len(),
            parsed
                .get("text")
                .and_then(|t| t.as_str())
                .map(|s| s.len())
                .unwrap_or(0)
        );
        return Ok(normalized);
    }

    log::info!("browser agent action {act} → {raw}");
    // eval_with_callback hands back the JSON encoding of the JS return value:
    // a plain 'ok' arrives as "\"ok\"" (quotes included). The SPA's ok-check
    // compares against bare `ok`, so scroll/type/key read as failures
    // ("browser:scroll failed: \"ok\"") — unquote JSON strings here.
    let raw = serde_json::from_str::<String>(&raw).unwrap_or(raw);
    // Every action script returns an explicit string; an empty/null result
    // means the injected script threw (hostile page, missing helper), so it
    // is a failure — never mint an ok for it.
    Ok(if raw.is_empty() || raw == "null" || raw == "undefined" {
        format!("browser:{act}:no-result")
    } else {
        raw
    })
}

pub fn close_browser_on_quit(app: &AppHandle) {
    destroy_embed(app);
}
