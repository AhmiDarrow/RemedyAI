//! Computer-use actions. The page scripts match `browser_host.rs`.
//!
//! Trusted clicks, typing, and keys are dispatched by the host through
//! DevTools (`Input.*`) after these scripts locate the target. A locator
//! returns `okxy:x:y:...`. Anything else (`missing-ref:`, `no-match:`,
//! `ambiguous:`) is the result and is not a click.

use serde_json::{Value, json};

use super::scripts::REMEDY_DOM_JS;

/// SPA and Rust agree on what a browser action may call success.
pub fn rust_action_ok(raw: &str) -> bool {
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

/// `ExecuteScript` JSON-encodes a string return (`ok` arrives as `"ok"`).
pub fn unquote_eval(raw: &str) -> String {
    serde_json::from_str::<String>(raw).unwrap_or_else(|_| raw.to_string())
}

/// Empty or null means the injected script threw. Never mint an ok for that.
pub fn finish_action_result(act: &str, raw: &str) -> String {
    let raw = unquote_eval(raw.trim());
    if raw.is_empty() || raw == "null" || raw == "undefined" {
        format!("browser:{act}:no-result")
    } else {
        raw
    }
}

pub fn eval_timeout_secs(act: &str) -> u64 {
    match act {
        "snapshot" | "a11y" | "page_text" => 9,
        "click" | "click_ref" | "click_text" | "hover" | "hover_ref" | "hover_text" | "type" | "type_text"
        | "key" | "scroll" | "drag" | "select" => 8,
        "ready" => 2,
        _ => 4,
    }
}

/// Page text may be double-encoded because the script `JSON.stringify`s and
/// the host serializes the string again.
pub fn parse_page_text_raw(raw: &str) -> Value {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return json!({ "text": "" });
    }
    let mut val: Value = match serde_json::from_str(trimmed) {
        Ok(v) => v,
        Err(_) => return json!({ "text": trimmed }),
    };
    if let Some(s) = val.as_str() {
        if let Ok(inner) = serde_json::from_str::<Value>(s) {
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

pub fn page_ready(raw: &str) -> bool {
    raw.contains("\"ok\":true") || raw.contains("\"complete\"") || raw.contains("\"interactive\"")
}

/// Identity of a rail-open command. `ts` is left out on purpose: the server
/// re-publishes the same command with a fresh timestamp, and keying on it
/// reopened the rail on every poll.
pub fn ui_command_identity(cmd: &Value) -> Option<String> {
    let action = cmd.get("action").and_then(|a| a.as_str()).unwrap_or("");
    let job_action = cmd.get("job_action").and_then(|a| a.as_str()).unwrap_or("");
    if action != "open_browser" && job_action != "navigate" {
        return None;
    }
    let job_id = cmd.get("job_id").and_then(|j| j.as_str()).unwrap_or("");
    let url = cmd.get("url").and_then(|u| u.as_str()).unwrap_or("");
    Some([job_id, action, job_action, url].join("|"))
}

fn js_sq(s: &str) -> String {
    s.replace('\\', "\\\\").replace('\'', "\\'").replace('\n', "\\n").replace('\r', "\\r")
}

fn js_sq_space(s: &str) -> String {
    s.replace('\\', "\\\\").replace('\'', "\\'").replace(['\n', '\r'], " ")
}

const ROUTING: &[&str] = &[
    "browser", "desktop", "auto", "system", "grove", "alongside", "studio", "chrome", "rail", "web", "os",
];

/// Focus the snapshot ref, or the field found by visible text. Routing tokens
/// (`browser`, `desktop`, …) are not field locators. An empty ref and query
/// keeps the focused node.
pub fn type_locate_js(reference: Option<&str>, query: Option<&str>) -> String {
    let rf = reference.unwrap_or("");
    let q_raw = query.unwrap_or("");
    let q = if ROUTING.iter().any(|r| q_raw.eq_ignore_ascii_case(r)) { "" } else { q_raw };
    if rf.is_empty() && q.is_empty() {
        return "const el=document.activeElement||document.body;".into();
    }
    let er = js_sq(rf);
    let eq = js_sq_space(q);
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

/// What the host asks the page to do. Coordinates are CSS pixels in the page.
#[derive(Clone, Debug)]
pub struct AgentRequest {
    pub action: String,
    pub x: Option<f64>,
    pub y: Option<f64>,
    pub x2: Option<f64>,
    pub y2: Option<f64>,
    pub text: Option<String>,
    pub key: Option<String>,
    pub button: Option<String>,
    pub dy: Option<i32>,
    pub job_id: Option<String>,
    pub reference: Option<String>,
}

impl AgentRequest {
    pub fn new(action: impl Into<String>) -> Self {
        Self {
            action: action.into(),
            x: None,
            y: None,
            x2: None,
            y2: None,
            text: None,
            key: None,
            button: None,
            dy: None,
            job_id: None,
            reference: None,
        }
    }
}

/// Click / hover action name from a job payload. Mirrors `handle_job`.
pub fn pointer_action_name(action: &str, payload: &Value) -> String {
    let text = payload.get("text").and_then(|t| t.as_str()).unwrap_or("");
    let reference = payload.get("ref").and_then(|t| t.as_str()).unwrap_or("");
    let x = payload.get("x").and_then(|v| v.as_f64());
    let click_text = payload.get("click_text").and_then(|v| v.as_bool()).unwrap_or(false)
        || (!text.is_empty() && reference.is_empty() && x.is_none());
    let hover = action == "hover";
    if click_text {
        if hover { "hover_text" } else { "click_text" }
    } else if !reference.is_empty() {
        if hover { "hover_ref" } else { "click_ref" }
    } else if hover {
        "hover"
    } else {
        "click"
    }
    .to_string()
}

fn opt_str(v: &Value, key: &str) -> Option<String> {
    v.get(key).and_then(|t| t.as_str()).filter(|s| !s.is_empty()).map(|s| s.to_string())
}

fn opt_f64(v: &Value, key: &str) -> Option<f64> {
    v.get(key).and_then(|t| t.as_f64()).or_else(|| v.get(key).and_then(|t| t.as_i64()).map(|n| n as f64))
}

/// Build an agent request for type / key / scroll / drag / select / pointer.
pub fn request_from_job(action: &str, payload: &Value) -> AgentRequest {
    let mut req = AgentRequest::new(action);
    req.text = opt_str(payload, "text");
    req.reference = opt_str(payload, "ref");
    req.x = opt_f64(payload, "x");
    req.y = opt_f64(payload, "y");
    req.x2 = opt_f64(payload, "x2");
    req.y2 = opt_f64(payload, "y2");
    req.dy = payload.get("dy").and_then(|v| v.as_i64()).map(|v| v as i32);
    req.button = opt_str(payload, "button");
    req.key = if action == "select" {
        opt_str(payload, "hint")
    } else if action == "type" {
        ["query", "label", "hint"].iter().find_map(|k| opt_str(payload, k))
    } else {
        opt_str(payload, "key")
    };
    if action == "click" || action == "hover" {
        req.action = pointer_action_name(action, payload);
    }
    req
}

/// The script for one action. Press-and-hold is located separately.
pub fn action_script(req: &AgentRequest) -> Result<String, String> {
    let act = req.action.to_ascii_lowercase();
    let dom = REMEDY_DOM_JS;
    let js = match act.as_str() {
        "snapshot" | "a11y" => format!(
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
    let name=(aria||titleAttr||text||ph||el.name||(sensitive?'':rawVal)||el.tagName||'').trim().replace(/\s+/g,' ');
    if(ph && name.toLowerCase().indexOf(ph.toLowerCase().slice(0,24))<0) name=(name+' '+ph).trim();
    name=name.slice(0,120);
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
}})()"#
        ),
        "page_text" => r#"(function(){
  const t=(document.body&&document.body.innerText)||'';
  const title=document.title||'';
  const url=location.href||'';
  return JSON.stringify({title,url,text:t.replace(/\s+\n/g,'\n').trim().slice(0,8000)});
})()"#
            .to_string(),
        "ready" => r#"(function(){
  const ready=document.readyState||'';
  const busy=!!(window.__remedy_nav_busy);
  const title=document.title||'';
  const url=location.href||'';
  return JSON.stringify({ready,busy,title,url,ok: ready==='complete'||ready==='interactive'});
})()"#
            .to_string(),
        "click_text" | "hover_text" => {
            let needle = req.text.clone().unwrap_or_default();
            if needle.is_empty() {
                return Err("text required for click_text".into());
            }
            let escaped = js_sq_space(&needle);
            format!(
                r#"(function(){{
  {dom}
  const q='{escaped}'.toLowerCase().trim();
  if(!q) return 'missing-text';
  const sel=window.__rmdyClickSel;
  let hit=window.__rmdyPick(q, sel);
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
  return 'okxy:'+Math.round(x)+':'+Math.round(y)+':'+bestS.toFixed(0)+':'+tag+':'+itype+':'+name;
}})()"#
            )
        }
        "click_ref" | "hover_ref" => {
            let rf = req.reference.clone().unwrap_or_default();
            if rf.is_empty() {
                return Err("ref required for click_ref".into());
            }
            let escaped = js_sq(&rf);
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
  return 'okxy:'+Math.round(x)+':'+Math.round(y)+':'+ref+':'+(el.tagName||'?');
}})()"#
            )
        }
        "click" | "hover" => {
            if let Some(rf) = req.reference.clone().filter(|s| !s.is_empty()) {
                let escaped = js_sq(&rf);
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
  return 'okxy:'+Math.round(x)+':'+Math.round(y)+':'+ref;
}})()"#
                )
            } else {
                let cx = req.x.unwrap_or(0.0);
                let cy = req.y.unwrap_or(0.0);
                format!(
                    r#"(function(){{
  const x={cx}, y={cy};
  const el=document.elementFromPoint(x,y)||document.body;
  if(!el) return 'no element';
  try{{ el.focus({{preventScroll:true}}); }}catch(e){{}}
  return 'okxy:'+Math.round(x)+':'+Math.round(y)+':'+(el.tagName||'?');
}})()"#
                )
            }
        }
        "type" | "type_text" => {
            let t = req.text.clone().unwrap_or_default();
            let escaped = js_sq(&t);
            let locate = type_locate_js(req.reference.as_deref().filter(|s| !s.is_empty()), req.key.as_deref().filter(|s| !s.is_empty()));
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
}})()"#
            )
        }
        "select" => {
            let want = req.text.clone().unwrap_or_default();
            if want.trim().is_empty() {
                return Err("select needs the option to choose (value=)".into());
            }
            let escaped = js_sq(&want);
            let er = js_sq(req.reference.as_deref().unwrap_or(""));
            let hint = js_sq(req.key.as_deref().unwrap_or(""));
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
}})()"#
            )
        }
        "key" => {
            let k = req.key.clone().unwrap_or_else(|| "Enter".into());
            let canon = match k.as_str() {
                "enter" | "return" | "Enter" => "Enter",
                "tab" | "Tab" => "Tab",
                other => other,
            };
            let escaped = js_sq(canon);
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
            let cx = req.x.unwrap_or(0.0);
            let cy = req.y.unwrap_or(0.0);
            let delta_y = -(req.dy.unwrap_or(-3) * 80);
            format!(
                r#"(function(){{
  const el=document.elementFromPoint({cx},{cy})||document.scrollingElement||document.documentElement;
  el.scrollBy({{top:{delta_y},left:0,behavior:'instant'}});
  return 'ok';
}})()"#
            )
        }
        "drag" => {
            let x1 = req.x.unwrap_or(0.0);
            let y1 = req.y.unwrap_or(0.0);
            let x2 = req.x2.unwrap_or(x1);
            let y2 = req.y2.unwrap_or(y1);
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
        "type_focus" => {
            let locate = type_locate_js(req.reference.as_deref().filter(|s| !s.is_empty()), req.key.as_deref().filter(|s| !s.is_empty()));
            format!(
                r#"(function(){{
  {locate}
  try{{ el.focus({{preventScroll:true}}); }}catch(e){{}}
  return (el&&(el.isContentEditable||/^(INPUT|TEXTAREA)$/.test(el.tagName)
    ||/^(textbox|searchbox|combobox)$/.test(((el.getAttribute&&el.getAttribute('role'))||'').toLowerCase())))
    ?'rm-editable':'rm-not-editable';
}})()"#
            )
        }
        other => return Err(format!("unknown browser agent action: {other}")),
    };
    Ok(js)
}

/// Locate a press-and-hold target. Explicit finite coordinates win, including (0, 0).
pub fn press_hold_explicit(x: Option<f64>, y: Option<f64>) -> Option<(f64, f64)> {
    match (x, y) {
        (Some(px), Some(py)) if px >= 0.0 && py >= 0.0 && px.is_finite() && py.is_finite() => Some((px, py)),
        _ => None,
    }
}

pub fn press_hold_locator(reference: Option<&str>, text: Option<&str>) -> Result<String, String> {
    if let Some(rf) = reference.filter(|s| !s.is_empty()) {
        let escaped = js_sq(rf);
        return Ok(format!(
            r#"(function(){{
  {dom}
  const el=window.__rmdyFind('{escaped}');
  if(!el) return 'missing-ref';
  try{{ el.scrollIntoView({{block:'center',inline:'center',behavior:'instant'}}); }}catch(e){{}}
  const r=window.__rmdyRect(el)||el.getBoundingClientRect();
  return 'xy:'+(r.x+r.width/2)+':'+(r.y+r.height/2);
}})()"#,
            dom = REMEDY_DOM_JS
        ));
    }
    if let Some(t) = text.filter(|s| !s.is_empty()) {
        let escaped = js_sq_space(t);
        return Ok(format!(
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
        ));
    }
    Err("press_hold needs x/y, text, or ref".into())
}

pub fn parse_xy(raw: &str) -> Option<(f64, f64)> {
    let unq = raw.trim().trim_matches('"');
    let rest = unq.strip_prefix("xy:")?;
    let mut it = rest.split(':');
    let px: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(-1.0);
    let py: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(-1.0);
    if px >= 0.0 && py >= 0.0 { Some((px, py)) } else { None }
}

pub fn parse_okxy(raw: &str) -> Option<(f64, f64, String)> {
    let unq = raw.trim().trim_matches('"').to_string();
    let rest = unq.strip_prefix("okxy:")?;
    let mut it = rest.splitn(3, ':');
    let cx: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(-1.0);
    let cy: f64 = it.next().and_then(|s| s.parse().ok()).unwrap_or(-1.0);
    let meta = it.next().unwrap_or("").to_string();
    if cx >= 0.0 && cy >= 0.0 { Some((cx, cy, meta)) } else { None }
}

pub fn synthetic_pointer_js(hover: bool, x: f64, y: f64, button: &str) -> String {
    if hover {
        format!(
            r#"(function(){{
  const x={x}, y={y};
  const el=document.elementFromPoint(x,y)||document.body;
  const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window}};
  try{{ el.dispatchEvent(new MouseEvent('mouseover', opts)); }}catch(e){{}}
  try{{ el.dispatchEvent(new MouseEvent('mouseenter', opts)); }}catch(e){{}}
  try{{ el.dispatchEvent(new MouseEvent('mousemove', opts)); }}catch(e){{}}
}})()"#
        )
    } else {
        let ev = if button == "right" { "contextmenu" } else { "click" };
        let code = if button == "right" { 2 } else { 0 };
        format!(
            r#"(function(){{
  const x={x}, y={y};
  const el=document.elementFromPoint(x,y)||document.body;
  const opts={{bubbles:true,cancelable:true,clientX:x,clientY:y,view:window,button:{code}}};
  try{{ el.dispatchEvent(new MouseEvent('mousedown', opts)); }}catch(e){{}}
  try{{ el.dispatchEvent(new MouseEvent('mouseup', opts)); }}catch(e){{}}
  try{{ el.dispatchEvent(new MouseEvent('{ev}', opts)); }}catch(e){{}}
  if({code}===0&&typeof el.click==='function') try{{ el.click(); }}catch(e){{}}
}})()"#
        )
    }
}

pub fn synthetic_press_hold_js(x: f64, y: f64, hold_ms: u64) -> String {
    format!(
        r#"(function(){{
  const x={x}, y={y};
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
    )
}

/// Named keys the Windows CDP path knows. `None` falls through to the page script.
pub fn named_key(key: &str) -> Option<(&'static str, i32, &'static str)> {
    let canon = match key {
        "enter" | "return" | "Enter" => "Enter",
        "tab" | "Tab" => "Tab",
        "esc" | "escape" | "Escape" => "Escape",
        "backspace" | "Backspace" => "Backspace",
        "delete" | "del" | "Delete" => "Delete",
        "ArrowLeft" => "ArrowLeft",
        "ArrowUp" => "ArrowUp",
        "ArrowRight" => "ArrowRight",
        "ArrowDown" => "ArrowDown",
        "PageUp" => "PageUp",
        "PageDown" => "PageDown",
        "Home" => "Home",
        "End" => "End",
        _ => return None,
    };
    let (vk, text) = match canon {
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
    Some((canon, vk, text))
}

pub fn key_down_params(canon: &str, vk: i32, text: &str) -> String {
    json!({
        "type": if text.is_empty() { "rawKeyDown" } else { "keyDown" },
        "key": canon,
        "code": canon,
        "windowsVirtualKeyCode": vk,
        "nativeVirtualKeyCode": vk,
        "text": text,
        "unmodifiedText": text,
    })
    .to_string()
}

pub fn key_up_params(canon: &str, vk: i32) -> String {
    json!({
        "type": "keyUp",
        "key": canon,
        "code": canon,
        "windowsVirtualKeyCode": vk,
        "nativeVirtualKeyCode": vk,
    })
    .to_string()
}

pub fn mouse_params(kind: &str, x: f64, y: f64, button: &str, click_count: i32) -> String {
    json!({
        "type": kind,
        "x": x,
        "y": y,
        "button": button,
        "clickCount": click_count,
        "buttons": if kind == "mousePressed" { 1 } else { 0 },
    })
    .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ok_matches_the_spa_check() {
        assert!(rust_action_ok("ok:clicked"));
        assert!(rust_action_ok("ok"));
        assert!(rust_action_ok("ok-fallback"));
        assert!(rust_action_ok("ok:CA"));
        assert!(!rust_action_ok("browser:type:no-result"));
        assert!(!rust_action_ok("ambiguous:2 dropdowns offer CA - pass ref or hint"));
        assert!(!rust_action_ok("missing-ref:e1"));
        assert!(!rust_action_ok("no-match:Membership"));
        assert!(!rust_action_ok("no-option:Oregon"));
        assert!(!rust_action_ok("not-select:input"));
        assert!(!rust_action_ok("no element"));
        assert!(!rust_action_ok(""));
    }

    #[test]
    fn unquote_and_empty_is_a_failure() {
        assert_eq!(finish_action_result("scroll", "\"ok\""), "ok");
        assert_eq!(finish_action_result("type", "null"), "browser:type:no-result");
        assert_eq!(finish_action_result("key", ""), "browser:key:no-result");
    }

    #[test]
    fn ui_command_ignores_a_fresh_timestamp() {
        let a = json!({"job_id":"j1","action":"open_browser","job_action":"navigate","url":"https://example.com","ts":"1"});
        let b = json!({"job_id":"j1","action":"open_browser","job_action":"navigate","url":"https://example.com","ts":"2"});
        assert_eq!(ui_command_identity(&a), ui_command_identity(&b));
        assert!(ui_command_identity(&json!({"action":"noop"})).is_none());
    }

    #[test]
    fn type_locate_matches_the_tauri_cases() {
        let js = type_locate_js(None, None);
        assert!(js.contains("activeElement"));
        assert!(!js.contains("__rmdyPick"));
        let js = type_locate_js(None, Some("What's happening?"));
        assert!(js.contains("__rmdyPick"));
        assert!(js.contains("__rmdyTypeSel"));
        assert!(js.contains("__rmdyFieldOf"));
        assert!(js.contains("no-match"));
        assert!(js.contains("happening"));
        let js = type_locate_js(Some("e4"), None);
        assert!(js.contains("__rmdyFind"));
        assert!(js.contains("missing-ref"));
        assert!(!js.contains("window.__rmdyPick(q, sel)"));
        let js = type_locate_js(Some("e4"), Some("Email"));
        assert!(js.contains("__rmdyFind"));
        assert!(js.contains("__rmdyPick"));
        assert!(js.contains("Email"));
        let js = type_locate_js(None, Some("browser"));
        assert!(js.contains("activeElement"));
        assert!(!js.contains("__rmdyPick"));
    }

    #[test]
    fn pointer_jobs_pick_text_ref_or_point() {
        assert_eq!(pointer_action_name("click", &json!({"text":"Add to cart"})), "click_text");
        assert_eq!(pointer_action_name("hover", &json!({"ref":"e3"})), "hover_ref");
        assert_eq!(pointer_action_name("click", &json!({"x":12,"y":4})), "click");
    }

    #[test]
    fn page_text_unwraps_a_double_encoded_object() {
        let inner = r#"{"title":"Hi","url":"https://example.com","text":"Hello"}"#;
        let raw = serde_json::to_string(inner).unwrap();
        let parsed = parse_page_text_raw(&raw);
        assert_eq!(parsed["text"], "Hello");
        assert_eq!(parsed["title"], "Hi");
    }

    #[test]
    fn snapshot_script_redacts_secrets_and_uses_the_deep_helper() {
        let js = action_script(&AgentRequest::new("snapshot")).unwrap();
        assert!(js.contains("value_redacted"));
        assert!(js.contains("__rmdyDeep"));
        assert!(js.contains("[filled]"));
        assert!(action_script(&AgentRequest::new("nope")).is_err());
    }

    #[test]
    fn press_hold_keeps_the_origin() {
        assert_eq!(press_hold_explicit(Some(0.0), Some(0.0)), Some((0.0, 0.0)));
        assert!(press_hold_explicit(Some(-1.0), Some(2.0)).is_none());
        assert!(press_hold_locator(None, None).is_err());
        assert!(press_hold_locator(Some("e2"), None).unwrap().contains("__rmdyFind"));
    }

    #[test]
    fn named_enter_is_a_real_key() {
        let (canon, vk, text) = named_key("return").unwrap();
        assert_eq!(canon, "Enter");
        assert_eq!(vk, 13);
        assert_eq!(text, "\r");
        assert!(named_key("a").is_none());
    }
}
