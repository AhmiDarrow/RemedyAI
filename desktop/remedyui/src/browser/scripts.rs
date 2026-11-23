//! Page scripts copied from the Tauri browser host.
//!
//! These strings are the in-page half of computer use: shadow-DOM
//! snapshots, same-window OAuth, and the rail's viewport. They stay
//! byte-for-byte with `desktop/src-tauri/src/browser_host.rs`.

pub const REMEDY_DOM_JS: &str = r#"
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

pub const SAME_WINDOW_OAUTH_JS: &str = r#"
(function(){
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
})();
"#;

pub const RAIL_LAYOUT_JS: &str = r#"
(function(){
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
})();
"#;

pub const MOBILE_VIEWPORT_JS: &str = r#"
(function(){
  try {
    var m = document.querySelector('meta[name="viewport"]');
    if (!m) {
      m = document.createElement('meta');
      m.setAttribute('name', 'viewport');
      (document.head || document.documentElement).appendChild(m);
    }
    m.setAttribute('content', 'width=device-width, initial-scale=1, maximum-scale=5, viewport-fit=cover');
  } catch(e) {}
})();
"#;

pub const DESKTOP_VIEWPORT_JS: &str = r#"
(function(){
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
})();
"#;

pub const RAIL_AS_SCREEN_JS: &str = r#"
(function(){
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
})();
"#;

