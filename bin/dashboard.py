#!/usr/bin/env python3
"""Claude-on-AIR dashboard — stdlib only.
Usage: dashboard.py [port]   (port defaults to HTTP_PORT in station.conf, then 8074)

Mostly read-only display. A small set of local-only control endpoints (Actions
widget) let the OPERATOR start/stop RX, start/stop the chaser, request a target/
skip, and hit STOP+UNKEY. Every action is logged to data/actions.log. Set
COA_DRYRUN=1 to log intended commands without executing them (used for testing).
"""
import http.server, json, os, socketserver, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import station_config
import world_map                      # embedded coastline path (no network at runtime)

_C = station_config.load()
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BIN = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.expanduser(_C.get("DATA", os.path.join(_ROOT, "data")))
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else int(_C.get("HTTP_PORT", 8074))
MYCALL = _C.get("MYCALL", "N0CALL")
MYGRID = _C.get("MYGRID", "AA00")
CHASELOG = os.path.join(DATA, "chase.log")
ACTIONS_LOG = os.path.join(DATA, "actions.log")
LAYOUT_JSON = os.path.join(DATA, "ui-layout.json")
TARGET_REQ = os.path.join(DATA, "target-request.json")
SKIP_REQ = os.path.join(DATA, "skip-request.json")
EVENT_LINES = 20
MAX_POST_BODY = 65536

DRYRUN = os.environ.get("COA_DRYRUN", "") not in ("", "0", "false", "False")
QSO_PY = os.path.join(_BIN, "qso.py")
RXLOOP_SH = os.path.join(_BIN, "rx-loop.sh")
RIG_MODEL = _C.get("RIG_MODEL", "3060")
CAT_PORT = _C.get("CAT_PORT", "/dev/ttyUSB0")
CAT_BAUD = _C.get("CAT_BAUD", "19200")

CONFIG = {"mycall": MYCALL, "mygrid": MYGRID, "band": _C.get("BAND", ""),
          "dial_hz": int(_C.get("DIAL_HZ", "0") or 0),
          "tx_pwr": _C.get("TX_PWR", ""), "mode": "FT8"}

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>FT8-Claude — __MYCALL__</title>
<style>
 body{background:#0d1117;color:#c9d1d9;font-family:system-ui,sans-serif;margin:0;padding:14px}
 h1{font-size:18px;margin:0 0 10px;color:#58a6ff} h1 small{color:#8b949e;font-weight:normal}
 img{max-width:100%;border-radius:4px;background:#000}
 table{border-collapse:collapse;width:100%;font-size:13px;font-family:ui-monospace,monospace}
 td,th{padding:2px 8px;text-align:left;border-bottom:1px solid #21262d;white-space:nowrap}
 th{color:#8b949e;font-weight:600}
 .cq{color:#3fb950;font-weight:600} .me{color:#f85149;font-weight:700;background:#2d1214}
 .next{font-size:15px} .next .callchip-main{font-size:21px;padding:6px 14px}
 .dim{color:#8b949e;font-size:12px} .snr-good{color:#3fb950}.snr-bad{color:#8b949e}
 #stale{display:none;color:#f85149;font-weight:700}
 #events{font-family:ui-monospace,monospace;font-size:12px;white-space:pre-wrap;
  overflow-x:auto;max-height:100%;overflow-y:auto;margin:0;color:#d2a8ff}
 #events .tx{color:#f0883e;font-weight:600} #events .good{color:#3fb950;font-weight:600}
 #events .bad{color:#f85149;font-weight:600}
 #map{width:100%;display:block;background:#0d1117;border-radius:4px}
 .mlabel{font-size:calc(11px * var(--map-scale, 1));font-family:ui-monospace,monospace;font-weight:600}
 #map .dot-rx{r:calc(2.2px * var(--map-scale, 1))}
 #map .dot-home{r:calc(3.5px * var(--map-scale, 1))}
 #map .dot-tx{r:calc(3px * var(--map-scale, 1))}
 .txflow{animation:flow 1s linear infinite}
 @keyframes flow{to{stroke-dashoffset:-17}}
 @keyframes pulse{50%{opacity:.35}}
 .infobar{display:flex;gap:30px;flex-wrap:wrap;align-items:baseline}
 .infobar .it{display:flex;gap:8px;align-items:baseline}
 .infobar .k{color:#8b949e;font-size:11px;letter-spacing:.08em}
 .infobar .v{font-family:ui-monospace,monospace;font-size:15px;color:#c9d1d9;font-weight:600}

 /* ---- cockpit (always visible, glanceable from across the room) ---- */
 #cockpit{position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:26px;
  background:#0d1117;padding:6px 2px 14px;flex-wrap:wrap;border-bottom:1px solid #21262d;margin-bottom:12px}
 #cockpit .cpitem{display:flex;flex-direction:column;gap:2px}
 #cockpit .cpk{font-size:10px;letter-spacing:.12em;color:#8b949e}
 #cockpit .cpv{font-size:28px;font-weight:800;font-family:ui-monospace,monospace;line-height:1.1}
 #cpState.st-tx,#cpState.tx-live{color:#f85149;animation:pulse 1s ease-in-out infinite}
 #cpState.st-calling{color:#f0883e} #cpState.st-qso{color:#3fb950}
 #cpState.st-breather,#cpState.st-idle,#cpState.st-init,#cpState.st-{color:#8b949e}
 #cpNext{color:#3fb950}
 #cockpit .spacer{flex:1}
 #btnUnkey{background:#f85149;color:#fff;border:none;border-radius:6px;font-size:17px;
  font-weight:800;padding:14px 22px;cursor:pointer;letter-spacing:.03em}
 #btnUnkey:hover{background:#ff6a61} #btnUnkey:active{background:#da3833}
 #btnUnkey.live{animation:pulse 1s ease-in-out infinite}
 #btnBell.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
 #dryrunBanner{background:#3d2f00;color:#e3b341;border:1px solid #6b5300;border-radius:6px;
  padding:4px 10px;font-size:12px;font-weight:700;display:none;margin-bottom:8px}

 /* ---- widget system ---- */
 #dash{display:flex;flex-wrap:wrap;gap:14px;align-items:flex-start}
 .widget{background:#161b22;border:1px solid #30363d;border-radius:8px;display:flex;
  flex-direction:column;resize:both;overflow:auto;min-width:230px;min-height:96px;box-sizing:border-box}
 .widget.collapsed{resize:none;height:auto!important;min-height:0}
 .widget.collapsed .wbody{display:none}
 .wtitle{display:flex;align-items:center;gap:8px;padding:7px 10px;cursor:grab;user-select:none;
  border-bottom:1px solid #21262d;background:#11151c;flex:0 0 auto;border-radius:7px 7px 0 0}
 .wtitle:active{cursor:grabbing}
 .wtitle .wname{flex:1;font-size:12px;font-weight:700;color:#8b949e;letter-spacing:.04em;text-transform:uppercase}
 .wtitle .maptbtn{font-size:11px;padding:1px 8px}
 .wtitle .maptbtn.active{background:#1f6feb;border-color:#1f6feb;color:#fff}
 .wcollapse{background:none;border:1px solid #30363d;color:#8b949e;border-radius:4px;
  font-size:11px;width:20px;height:18px;cursor:pointer;line-height:1}
 .wcollapse::before{content:'\\2013'}
 .widget.collapsed .wcollapse::before{content:'+'}
 .wcollapse:hover{color:#c9d1d9;border-color:#484f58}
 .wbody{padding:10px;flex:1 1 auto;overflow:auto;min-height:0}
 .widget[data-key=waterfall]{width:600px;height:220px}
 .widget[data-key=map]{width:380px;height:250px}
 .widget[data-key=decodes]{width:540px;height:320px}
 .widget[data-key=ops]{width:300px;height:320px}
 .widget[data-key=log]{width:300px;height:220px}
 .widget[data-key=events]{width:880px;height:170px}
 .widget[data-key=actions]{width:300px;height:320px}
 .widget[data-key=status]{width:100%;height:66px}

 .actionbtn{background:#21262d;color:#c9d1d9;border:1px solid #30363d;border-radius:5px;
  padding:5px 10px;font-size:12px;cursor:pointer}
 .actionbtn:hover{border-color:#58a6ff} .actionbtn:disabled{opacity:.5;cursor:default}
 .actionbtn.warn{background:#3d1f16;border-color:#f0883e;color:#f0883e}
 .arow{display:flex;gap:8px;align-items:center;margin:6px 0;flex-wrap:wrap}
 .astatus{display:flex;gap:16px;margin-bottom:6px;flex-wrap:wrap}
 .callchip{background:#0d1117;border:1px solid #30363d;color:#56d4dd;border-radius:12px;
  padding:2px 9px;font-size:12px;font-family:ui-monospace,monospace;cursor:pointer;margin:2px 3px 2px 0}
 .callchip:hover{border-color:#56d4dd} .callchip:disabled{opacity:.5;cursor:default}
 .callchip-main{color:#3fb950;border-color:#3fb950}
 select,input[type=number]{background:#0d1117;color:#c9d1d9;border:1px solid #30363d;border-radius:4px;padding:3px}
</style></head><body>
<h1>\U0001F4FB FT8-Claude <small>— __MYCALL__ · __MYGRID__ · RX monitor</small> <span id=stale>⚠ STALE — rx-loop not updating</span></h1>
<div id=cockpit>
 <div class=cpitem><span class=cpk>STATE</span><span class="cpv st-" id=cpState>—</span></div>
 <div class=cpitem><span class=cpk>BAND</span><span class=cpv id=cpBand>—</span></div>
 <div class=cpitem><span class=cpk>NEXT CALL</span><span class="cpv" id=cpNext>—</span></div>
 <div class=spacer></div>
 <button id=btnBell class=actionbtn title="desktop alerts: new QSO, chase ended, watchdog/abort, decode silence &gt;3 min">Alerts: OFF</button>
 <button id=resetLayout class=actionbtn title="restore default widget layout">Reset layout</button>
 <button id=btnUnkey title="kill chaser + rigctl T 0 — no confirmation">STOP + UNKEY</button>
</div>
<div id=dash>

 <div class=widget data-key=status>
  <div class=wtitle><span class=wname>Status</span><button class=wcollapse></button></div>
  <div class=wbody><div class=infobar id=info><span class=dim>loading station config…</span></div></div>
 </div>

 <div class=widget data-key=decodes>
  <div class=wtitle><span class=wname>Decodes</span><span class=dim id=upd></span><button class=wcollapse></button></div>
  <div class=wbody><table id=dec><tr><th>slot</th><th>SNR</th><th>DT</th><th>Hz</th><th>message</th></tr></table></div>
 </div>

 <div class=widget data-key=ops>
  <div class=wtitle><span class=wname>Next call</span><button class=wcollapse></button></div>
  <div class=wbody id=opsBody>
   <div class="next dim">suggestion:</div>
   <div class=next id=next>—</div>
   <div class=dim id=cand></div>
   <div class=arow><button id=btnSkip class=actionbtn>Skip current target</button>
    <span class=dim id=targetStatus></span></div>
   <div style="margin-top:10px"><span class=wname style="text-transform:none;font-size:11px">Calling ME</span>
    <div id=me class=dim>nobody yet</div></div>
   <div class=dim style="margin-top:8px">Click a callsign to request it as next target. Display + request only — the control operator transmits.</div>
  </div>
 </div>

 <div class=widget data-key=actions>
  <div class=wtitle><span class=wname>Actions</span><button class=wcollapse></button></div>
  <div class=wbody>
   <div id=dryrunBanner>DRY-RUN MODE — actions are logged, not executed</div>
   <div class=astatus>
    <span class=it><span class=k>RX&nbsp;</span><span class=v id=stRx>—</span></span>
    <span class=it><span class=k>CHASER&nbsp;</span><span class=v id=stChaser>—</span></span>
    <span class=it><span class=k>PTT&nbsp;</span><span class=v id=stPtt>—</span></span>
   </div>
   <div class=arow><button id=btnRxStart class=actionbtn>Start RX</button>
    <button id=btnRxStop class=actionbtn>Stop RX</button></div>
   <div class=arow>
    <input id=chaseN type=number min=1 max=180 value=1>
    <select id=chaseMode><option value=qsos>QSOs</option><option value=minutes>minutes</option></select>
    <button id=btnChaseStart class="actionbtn warn">Chase</button>
    <button id=btnChaseStop class=actionbtn>Stop chase</button>
   </div>
   <div id=chaseConfirmMsg class=dim style="display:none">You are the control operator — stay at the station.
    <div class=arow><button id=btnChaseConfirm class="actionbtn warn">Confirm start chase</button>
     <button id=btnChaseCancel class=actionbtn>Cancel</button></div></div>
   <div class=dim id=actionsMsg></div>
   <div class=dim style="margin-top:6px">STOP + UNKEY is always available, top right — no confirmation, one click.</div>
  </div>
 </div>

 <div class=widget data-key=map>
  <div class=wtitle><span class=wname>World map</span>
   <span class=dim style="flex:0 0 auto">heard (cyan) · TX (red) · home (gold)</span>
   <button id=mapAuto class="actionbtn maptbtn active">Auto</button>
   <button id=mapWorld class="actionbtn maptbtn">World</button>
   <button class=wcollapse></button></div>
  <div class=wbody style="padding:4px">
   <svg id=map viewBox="0 0 1000 500" preserveAspectRatio="xMidYMid meet">
    <path d="__WORLD__" fill="#1c2430" stroke="#30363d" stroke-width="0.5" vector-effect="non-scaling-stroke"/>
    <g id=rx></g><g id=tx></g><g id=home></g>
   </svg>
  </div>
 </div>

 <div class=widget data-key=waterfall>
  <div class=wtitle><span class=wname>Waterfall</span><button class=wcollapse></button></div>
  <div class=wbody><img id=wf src=/waterfall.png></div>
 </div>

 <div class=widget data-key=log>
  <div class=wtitle><span class=wname>QSO log</span><span class=dim id=qn></span><button class=wcollapse></button></div>
  <div class=wbody><table id=log><tr><th>call</th><th>band</th><th>grid</th><th>date</th></tr></table></div>
 </div>

 <div class=widget data-key=events>
  <div class=wtitle><span class=wname>Chaser events</span><span class=dim>data/chase.log — engine diary, last __EVENT_LINES__ lines</span><button class=wcollapse></button></div>
  <div class=wbody><pre id=events>no events yet</pre></div>
 </div>

</div>
<script>
const DRYRUN = __DRYRUN__;
function evClass(l){
 if(/\\bTX #|keyed/.test(l)) return 'tx';
 if(/LOGGED QSO|ANSWERED|QSO complete|DONE:/.test(l)) return 'good';
 if(/ABORT|fail|no response|STALE/.test(l)) return 'bad';
 return '';
}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}

/* ---- world map ---- */
const MW=1000, MH=500;
let HOME=null, CFG=null;
let mapPoints={rx:[], tx:null};
function grid2ll(g){                       // Maidenhead 4/6-char -> [lat,lon] (cell center)
 g=(g||'').trim().toUpperCase();
 if(!/^[A-R]{2}[0-9]{2}([A-X]{2})?$/.test(g)) return null;
 let lon=(g.charCodeAt(0)-65)*20-180 + (g.charCodeAt(2)-48)*2;
 let lat=(g.charCodeAt(1)-65)*10-90  + (g.charCodeAt(3)-48);
 if(g.length>=6){ lon+=(g.charCodeAt(4)-65)/12 + 1/24; lat+=(g.charCodeAt(5)-65)/24 + 1/48; }
 else           { lon+=1; lat+=0.5; }
 return [lat,lon];
}
function ll2xy(ll){ return [(ll[1]+180)/360*MW, (90-ll[0])/180*MH]; }
function isGrid(t){ return /^[A-R]{2}[0-9]{2}$/.test(t) && t!=='RR73'; }
function decodeTime(date,slot){            // "260704","014045" -> ms UTC
 let t=Date.UTC(2000+ +date.slice(0,2), +date.slice(2,4)-1, +date.slice(4,6),
                +slot.slice(0,2), +slot.slice(2,4), +slot.slice(4,6));
 if(t>Date.now()+60000) t-=86400000;       // midnight wrap
 return t;
}

/* ---- viewBox auto-zoom (part A): fit bbox of home+RX dots+TX endpoint,
   ~15% pad, clamped to [2-grid-field .. whole world], eased over ~450ms.
   All layers share the viewBox so they stay geometrically correct; strokes
   use vector-effect=non-scaling-stroke and marker/label sizes are scaled by
   var(--map-scale) so they stay visually constant as the box zooms. ---- */
let vb={x:0,y:0,w:MW,h:MH}, vbTarget={x:0,y:0,w:MW,h:MH};
let vbAnimFrom=null, vbAnimStart=0, vbAnimId=null;
const VB_ANIM_MS=450;
const MIN_VB_W=110, MIN_VB_H=55;            // ~2 Maidenhead grid fields (40°lon x 20°lat)
let mapMode='auto';

function lerp(a,b,t){return a+(b-a)*t;}
function applyViewBox(v){
 const svg=document.getElementById('map');
 svg.setAttribute('viewBox', v.x.toFixed(2)+' '+v.y.toFixed(2)+' '+v.w.toFixed(2)+' '+v.h.toFixed(2));
 svg.style.setProperty('--map-scale', (v.w/MW).toFixed(4));
}
function vbEqual(a,b){return Math.abs(a.x-b.x)<0.5&&Math.abs(a.y-b.y)<0.5&&Math.abs(a.w-b.w)<0.5&&Math.abs(a.h-b.h)<0.5;}
function animateViewBoxTo(target){
 if(vbEqual(target,vbTarget)&&vbEqual(vb,target)) return;
 vbTarget=target;
 if(vbAnimId) cancelAnimationFrame(vbAnimId);
 vbAnimFrom={x:vb.x,y:vb.y,w:vb.w,h:vb.h}; vbAnimStart=performance.now();
 function step(now){
  const t=Math.min(1,(now-vbAnimStart)/VB_ANIM_MS);
  const e=1-Math.pow(1-t,3);                // easeOutCubic
  vb={x:lerp(vbAnimFrom.x,vbTarget.x,e), y:lerp(vbAnimFrom.y,vbTarget.y,e),
      w:lerp(vbAnimFrom.w,vbTarget.w,e), h:lerp(vbAnimFrom.h,vbTarget.h,e)};
  applyViewBox(vb);
  vbAnimId=(t<1)?requestAnimationFrame(step):null;
 }
 vbAnimId=requestAnimationFrame(step);
}
function computeBBox(){
 const pts=[];
 if(HOME) pts.push(HOME);
 for(const p of mapPoints.rx) pts.push(p);
 if(mapPoints.tx) pts.push(mapPoints.tx);
 if(!pts.length) return {x:0,y:0,w:MW,h:MH};
 let minX=Math.min.apply(null,pts.map(p=>p[0])), maxX=Math.max.apply(null,pts.map(p=>p[0]));
 let minY=Math.min.apply(null,pts.map(p=>p[1])), maxY=Math.max.apply(null,pts.map(p=>p[1]));
 let w=maxX-minX, h=maxY-minY;
 const padX=Math.max(w*0.15,10), padY=Math.max(h*0.15,6);
 minX-=padX; maxX+=padX; minY-=padY; maxY+=padY;
 w=maxX-minX; h=maxY-minY;
 if(w<MIN_VB_W){const cx=(minX+maxX)/2; minX=cx-MIN_VB_W/2; maxX=cx+MIN_VB_W/2; w=MIN_VB_W;}
 if(h<MIN_VB_H){const cy=(minY+maxY)/2; minY=cy-MIN_VB_H/2; maxY=cy+MIN_VB_H/2; h=MIN_VB_H;}
 const AR=MW/MH;
 if(w/h<AR){ const need=h*AR; const cx=(minX+maxX)/2; minX=cx-need/2; maxX=cx+need/2; w=need; }
 else if(w/h>AR){ const need=w/AR; const cy=(minY+maxY)/2; minY=cy-need/2; maxY=cy+need/2; h=need; }
 if(w>MW){w=MW;h=MH;}
 let x=minX, y=minY;
 if(x<0)x=0; if(y<0)y=0;
 if(x+w>MW)x=MW-w; if(y+h>MH)y=MH-h;
 return {x,y,w,h};
}
function updateMapZoom(){ if(mapMode==='auto') animateViewBoxTo(computeBBox()); }
function setMapMode(m){
 mapMode=m;
 document.getElementById('mapAuto').classList.toggle('active', m==='auto');
 document.getElementById('mapWorld').classList.toggle('active', m==='world');
 if(m==='world') animateViewBoxTo({x:0,y:0,w:MW,h:MH}); else updateMapZoom();
 scheduleSaveLayout();
}

async function loadCfg(){
 try{
  const r=await fetch('/config'); if(!r.ok) return; CFG=await r.json();
  const ll=grid2ll(CFG.mygrid); if(ll) HOME=ll2xy(ll);
  if(HOME) document.getElementById('home').innerHTML=
   `<circle class=dot-home cx="${HOME[0]}" cy="${HOME[1]}" fill="#e3b341" stroke="#0d1117" stroke-width="1" vector-effect="non-scaling-stroke"/>`+
   `<text x="${HOME[0]+7}" y="${HOME[1]+4}" class=mlabel fill="#e3b341">${esc(CFG.mycall)}</text>`;
  updateMapZoom();
  document.getElementById('cpBand').textContent=CFG.band||'—';
  const mhz=CFG.dial_hz?(CFG.dial_hz/1e6).toFixed(3)+' MHz':'—';
  const items=[['CALL',CFG.mycall],['GRID',CFG.mygrid],['BAND',CFG.band||'—'],
               ['DIAL',mhz],['POWER',(CFG.tx_pwr||'—')+' W'],['MODE',CFG.mode]];
  document.getElementById('info').innerHTML=items.map(i=>
   `<span class=it><span class=k>${i[0]}</span><span class=v>${esc(String(i[1]))}</span></span>`).join('');
 }catch(e){}
}
function renderRX(s){
 if(!HOME) return;
 const seen={};                            // dedupe by callsign, keep newest
 function add(call,grid,t){
  if(!call||call.length<3||call.includes('<')||call===(CFG&&CFG.mycall)) return;
  if(!isGrid(grid)) return;
  if(!(call in seen)||t>seen[call].t) seen[call]={g:grid,t:t};
 }
 for(const d of s.recent||[]){
  const tk=d.msg.trim().split(/\\s+/);
  if(tk.length>=2&&isGrid(tk[tk.length-1])) add(tk[tk.length-2],tk[tk.length-1],decodeTime(d.date,d.slot));
 }
 const today=new Date().toISOString().slice(2,10).replace(/-/g,'');
 for(const c of s.candidates||[]) if(c.grid&&c.slot) add(c.call,c.grid,decodeTime(today,c.slot));
 let h=''; const pts=[];
 for(const call in seen){
  const e=seen[call], age=(Date.now()-e.t)/1000;
  if(age>900) continue;                    // keep ~15 min
  const ll=grid2ll(e.g); if(!ll) continue;
  const [x,y]=ll2xy(ll), op=Math.max(.15,1-age/900);
  pts.push([x,y]);
  h+=`<line x1="${HOME[0]}" y1="${HOME[1]}" x2="${x}" y2="${y}" stroke="#56d4dd" stroke-width="0.6" opacity="${(op*.45).toFixed(2)}" vector-effect="non-scaling-stroke"/>`;
  h+=`<circle class=dot-rx cx="${x}" cy="${y}" fill="#56d4dd" opacity="${op.toFixed(2)}"/>`;
 }
 document.getElementById('rx').innerHTML=h;
 mapPoints.rx=pts;
 updateMapZoom();
}
function renderTX(e){
 const g=document.getElementById('tx');
 if(!e||!HOME||!(e.state==='calling'||e.state==='qso')||!e.grid){g.innerHTML='';mapPoints.tx=null;updateMapZoom();return;}
 const ll=grid2ll(e.grid); if(!ll){g.innerHTML='';mapPoints.tx=null;updateMapZoom();return;}
 const [x2,y2]=ll2xy(ll), [x1,y1]=HOME;
 mapPoints.tx=[x2,y2];
 const bow=Math.min(80,Math.hypot(x2-x1,y2-y1)*0.25)+8;   // quadratic, bowed poleward
 const d=`M${x1} ${y1} Q${(x1+x2)/2} ${(y1+y2)/2-bow} ${x2} ${y2}`;
 let h='';
 if(e.tx){
  h+=`<path d="${d}" fill="none" stroke="#f85149" stroke-width="6" opacity="0.18" vector-effect="non-scaling-stroke"/>`;
  h+=`<path d="${d}" fill="none" stroke="#f85149" stroke-width="1.8" stroke-dasharray="10 7" class=txflow vector-effect="non-scaling-stroke"/>`;
 }else{
  h+=`<path d="${d}" fill="none" stroke="#f85149" stroke-width="1.2" opacity="0.45" vector-effect="non-scaling-stroke"/>`;
 }
 h+=`<circle class=dot-tx cx="${x2}" cy="${y2}" fill="#f85149"/>`;
 h+=`<text x="${x2+6}" y="${y2-6}" class=mlabel fill="#f85149">${esc(e.target||'')}</text>`;
 g.innerHTML=h;
 updateMapZoom();
}
async function engTick(){
 let e=null;
 try{
  const r=await fetch('/engine.json?t='+Date.now());
  e=r.ok?await r.json():null;
 }catch(err){}
 renderTX(e);
 const st=(e&&e.state)||'';
 const cp=document.getElementById('cpState');
 cp.textContent=st?st.toUpperCase():'—';
 cp.className='cpv st-'+st.toLowerCase().replace(/[^a-z]/g,'');
 const tx=!!(e&&e.tx);
 cp.classList.toggle('tx-live',tx);
 document.getElementById('btnUnkey').classList.toggle('live',tx);
 /* ---- alerts (4.3): chase ended / watchdog-abort — edge-triggered off
    engine.json's state field so a steady state never re-fires ---- */
 const stl=st.toLowerCase();
 if(stl && stl!==lastEngineState){
  if(stl==='done' || stl==='ended'){
   fireAlert('Chase ended', `chaser state: ${st}`+(e&&e.target?` (last target ${e.target})`:''));
  }else if(stl.includes('abort') || stl.includes('watchdog')){
   fireAlert('Watchdog/abort', `engine state: ${st}`+(e&&e.msg?` — ${e.msg}`:''));
  }
 }
 lastEngineState=stl||lastEngineState;
}

async function tick(){
 try{
  const r=await fetch('/status.json?t='+Date.now()); const s=await r.json();
  document.getElementById('wf').src='/waterfall.png?t='+Date.now();
  document.getElementById('upd').textContent=' updated '+s.updated_utc+'Z, slot '+s.slot+' ('+s.slot_decodes+' decodes)';
  const age=(Date.now()/1000)-(Date.parse(s.updated_utc+'Z')/1000);
  document.getElementById('stale').style.display=age>60?'inline':'none';
  /* ---- alerts (4.3): decode silence >3 min while rx-loop is running ---- */
  if(age>180 && lastRxRunning){
   if(!lastSilenceFlag){ fireAlert('Decode silence', `no new decodes for ${Math.round(age/60)} min — check band/audio`); lastSilenceFlag=true; }
  }else{
   lastSilenceFlag=false;
  }
  let h='<tr><th>slot</th><th>SNR</th><th>DT</th><th>Hz</th><th>message</th></tr>';
  for(const d of [...s.recent].reverse()){
   const cls=d.msg.startsWith('CQ')?'cq':(d.msg.includes('__MYCALL__')?'me':'');
   h+=`<tr class="${cls}"><td>${d.slot}</td><td class="${d.snr>=-12?'snr-good':'snr-bad'}">${d.snr}</td><td>${d.dt}</td><td>${d.freq}</td><td>${d.msg}</td></tr>`;}
  document.getElementById('dec').innerHTML=h;
  if(s.next_call){
   document.getElementById('next').innerHTML=
    `<button class="callchip callchip-main" data-call="${esc(s.next_call.call)}">${esc(s.next_call.call)} ${esc(s.next_call.grid)} (${s.next_call.snr} dB)</button>`;
   document.getElementById('cpNext').textContent=s.next_call.call;
  }else{
   document.getElementById('next').textContent='—';
   document.getElementById('cpNext').textContent='—';
  }
  document.getElementById('cand').innerHTML=s.candidates&&s.candidates.length>1
   ?'also: '+s.candidates.slice(1).map(c=>`<button class=callchip data-call="${esc(c.call)}">${esc(c.call)} ${c.snr}dB</button>`).join(' ')
   :'';
  document.getElementById('me').innerHTML=s.calling_me&&s.calling_me.length?s.calling_me.map(d=>`<span class=me>${d.msg} (${d.snr} dB)</span>`).join('<br>'):'nobody yet';
  document.getElementById('qn').textContent=' '+s.qso_count+' total';
  /* ---- alerts (4.3): new QSO logged (qso_count increased) ---- */
  if(lastQsoCount!==null && s.qso_count>lastQsoCount && s.qsos && s.qsos.length){
   const q0=s.qsos[s.qsos.length-1];
   fireAlert('QSO logged', `${q0.call} ${q0.grid||''}`.trim());
  }
  lastQsoCount=s.qso_count;
  let q='<tr><th>call</th><th>band</th><th>grid</th><th>date</th></tr>';
  for(const x of [...(s.qsos||[])].reverse()) q+=`<tr><td>${x.call}</td><td>${x.band}</td><td>${x.grid}</td><td>${x.date}</td></tr>`;
  document.getElementById('log').innerHTML=q;
  renderRX(s);
 }catch(e){document.getElementById('stale').style.display='inline';}
 try{
  const r=await fetch('/events?t='+Date.now()); const ej=await r.json();
  const el=document.getElementById('events');
  const atBottom=el.scrollHeight-el.scrollTop-el.clientHeight<30;
  el.innerHTML=(ej.lines&&ej.lines.length)
   ? ej.lines.map(l=>`<span class="${evClass(l)}">${esc(l)}</span>`).join('\\n')
   : 'no events yet';
  if(atBottom) el.scrollTop=el.scrollHeight;
 }catch(e){}
}

/* ---- Actions widget: RX/chase control, target pick/skip, STOP+UNKEY.
   All calls are POSTs to this server's own /action/* endpoints (localhost-only,
   dry-run aware server-side). No radio control code runs in the browser. ---- */
async function postAction(path, body){
 try{
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});
  let j={}; try{j=await r.json();}catch(e){}
  return {ok:r.ok && j.ok!==false, status:r.status, body:j};
 }catch(e){ return {ok:false, error:String(e)}; }
}
function setActionsMsg(t){ document.getElementById('actionsMsg').textContent=t; }
async function refreshActionsState(){
 try{
  const r=await fetch('/actions/state?t='+Date.now()); const j=await r.json();
  document.getElementById('stRx').textContent=j.rxloop?'running':'stopped';
  document.getElementById('stChaser').textContent=j.chaser?'running':'idle';
  document.getElementById('stPtt').textContent=j.ptt?'TX':'RX';
  lastRxRunning=!!j.rxloop;
 }catch(e){}
}
function wireActions(){
 if(DRYRUN) document.getElementById('dryrunBanner').style.display='block';
 document.getElementById('btnRxStart').addEventListener('click',async()=>{
  setActionsMsg('starting RX…');
  const r=await postAction('/action/rx/start',{});
  setActionsMsg(r.ok?'RX start requested':'RX start failed: '+(r.body.error||r.error||r.status));
  refreshActionsState();
 });
 document.getElementById('btnRxStop').addEventListener('click',async()=>{
  setActionsMsg('stopping RX…');
  const r=await postAction('/action/rx/stop',{});
  setActionsMsg(r.ok?'RX stop requested':'RX stop failed: '+(r.body.error||r.error||r.status));
  refreshActionsState();
 });
 document.getElementById('btnChaseStart').addEventListener('click',()=>{
  document.getElementById('chaseConfirmMsg').style.display='block';
 });
 document.getElementById('btnChaseCancel').addEventListener('click',()=>{
  document.getElementById('chaseConfirmMsg').style.display='none';
 });
 document.getElementById('btnChaseConfirm').addEventListener('click',async()=>{
  const n=parseFloat(document.getElementById('chaseN').value);
  const mode=document.getElementById('chaseMode').value;
  document.getElementById('chaseConfirmMsg').style.display='none';
  setActionsMsg('starting chase…');
  const r=await postAction('/action/chase/start',{n,mode,confirm:true});
  setActionsMsg(r.ok?'chase start requested':'chase start failed: '+(r.body.error||r.error||r.status));
  refreshActionsState();
 });
 document.getElementById('btnChaseStop').addEventListener('click',async()=>{
  setActionsMsg('stopping chase…');
  const r=await postAction('/action/chase/stop',{});
  setActionsMsg(r.ok?'chase stop requested':'chase stop failed: '+(r.body.error||r.error||r.status));
  refreshActionsState();
 });
 document.getElementById('btnUnkey').addEventListener('click',async()=>{
  const btn=document.getElementById('btnUnkey'); btn.disabled=true;
  const r=await postAction('/action/unkey',{});
  btn.disabled=false;
  setActionsMsg(r.ok?('UNKEY sent — PTT readback: '+(r.body.ptt!=null?r.body.ptt:'?')):'UNKEY FAILED: '+(r.body.error||r.error||r.status));
  refreshActionsState();
 });
 // target pick/skip: event delegation since #next/#cand are re-rendered every tick
 document.getElementById('opsBody').addEventListener('click',async e=>{
  const chip=e.target.closest('.callchip');
  if(chip){
   const call=chip.dataset.call; chip.disabled=true;
   const r=await postAction('/action/target/pick',{call});
   document.getElementById('targetStatus').textContent=r.ok?`requested ${call} @ ${new Date().toLocaleTimeString()}`:'request failed';
   return;
  }
  if(e.target.id==='btnSkip'){
   const r=await postAction('/action/target/skip',{});
   document.getElementById('targetStatus').textContent=r.ok?'skip requested @ '+new Date().toLocaleTimeString():'skip failed';
  }
 });
}

/* ---- alerts (4.3): client-side only, derived from the existing /status.json,
   /engine.json and /actions/state polls above — no server push. Browser
   Notification API when granted; tab-title flash as fallback when denied/
   unavailable. Off by default; the bell toggle's state rides along in the
   same /layout blob the widget system already persists (server just stores
   whatever JSON it's given, so no dashboard.py endpoint changes needed). ---- */
let alertsEnabled=false, notifPermission=(window.Notification && Notification.permission) || 'default';
let lastQsoCount=null, lastEngineState='', lastRxRunning=false, lastSilenceFlag=false;
let titleFlashTimer=null; const BASE_TITLE=document.title;
function flashTitle(text){
 if(titleFlashTimer) return;               // already flashing
 let on=false;
 const marker='★ '+text;               // "★ QSO!" etc. — fallback when Notification is denied
 const stop=()=>{ clearInterval(titleFlashTimer); titleFlashTimer=null; document.title=BASE_TITLE;
  document.removeEventListener('visibilitychange', onVis); };
 const onVis=()=>{ if(!document.hidden) stop(); };
 document.addEventListener('visibilitychange', onVis);
 titleFlashTimer=setInterval(()=>{ document.title=on?BASE_TITLE:marker; on=!on; }, 1000);
 setTimeout(stop, 30000);                   // safety cap regardless of focus
}
function doAlert(kind, text){
 console.log('[coa-alert]', kind, text);    // always logged — verifiable without a radio
 if(window.Notification && Notification.permission==='granted'){
  try{ new Notification('COTA — '+kind, {body:text}); return; }catch(e){}
 }
 flashTitle(text);
}
function fireAlert(kind, text){ if(alertsEnabled) doAlert(kind, text); }
function updateBellUI(){
 const b=document.getElementById('btnBell');
 b.textContent='Alerts: '+(alertsEnabled?'ON':'OFF');
 b.classList.toggle('active', alertsEnabled);
}
function wireBell(){
 document.getElementById('btnBell').addEventListener('click', async ()=>{
  if(!alertsEnabled){
   if(window.Notification && Notification.permission==='default'){
    try{ notifPermission=await Notification.requestPermission(); }catch(e){}
   }
   alertsEnabled=true;
  }else{
   alertsEnabled=false;
  }
  updateBellUI();
  scheduleSaveLayout();
 });
}
/* dev-only test hook: verify each alert path without a radio —
   coaSimulateAlert('qso'|'chase_end'|'abort'|'silence') from the console, or
   load the page with ?simulateAlert=qso (etc.) to fire one automatically. */
window.coaSimulateAlert=function(kind){
 const sims={
  qso:()=>doAlert('QSO logged', 'TEST1AA FN20 (simulated)'),
  chase_end:()=>doAlert('Chase ended', 'chaser state: done (simulated)'),
  abort:()=>doAlert('Watchdog/abort', 'engine state: watchdog-test (simulated)'),
  silence:()=>doAlert('Decode silence', 'no new decodes for 3+ min (simulated)'),
 };
 if(sims[kind]){ sims[kind](); return true; }
 console.warn('coaSimulateAlert: unknown kind', kind, '— use one of', Object.keys(sims));
 return false;
};
(function(){
 const p=new URLSearchParams(location.search).get('simulateAlert');
 if(p) setTimeout(()=>window.coaSimulateAlert(p), 1500);
})();

/* ---- widget system (part B): resize (native CSS resize handles), collapse,
   drag-reorder (native HTML5 DnD), persisted server-side (data/ui-layout.json,
   atomic write) with localStorage as write-through cache. ---- */
let dragKey=null, layoutSaveTimer=null;
function scheduleSaveLayout(){ clearTimeout(layoutSaveTimer); layoutSaveTimer=setTimeout(saveLayout,500); }
function currentLayout(){
 const widgets={};
 document.querySelectorAll('#dash > .widget').forEach((w,i)=>{
  widgets[w.dataset.key]={order:i, collapsed:w.classList.contains('collapsed'),
   w:w.style.width||null, h:w.style.height||null};
 });
 if(widgets.map) widgets.map.mapMode=mapMode;
 return {widgets, notify:alertsEnabled};
}
function saveLayout(){
 const layout=currentLayout();
 try{localStorage.setItem('coa-layout', JSON.stringify(layout));}catch(e){}
 fetch('/layout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(layout)}).catch(()=>{});
}
function applyLayout(layout){
 if(!layout||!layout.widgets) return;
 const w=layout.widgets;
 const keys=Object.keys(w).filter(k=>document.querySelector(`.widget[data-key="${k}"]`));
 keys.sort((a,b)=>(w[a].order||0)-(w[b].order||0));
 const dash=document.getElementById('dash');
 for(const k of keys){
  const el=document.querySelector(`.widget[data-key="${k}"]`);
  dash.appendChild(el);
  if(w[k].w) el.style.width=w[k].w;
  if(w[k].h) el.style.height=w[k].h;
  if(w[k].collapsed) el.classList.add('collapsed');
 }
 if(w.map&&w.map.mapMode) setMapMode(w.map.mapMode);
 if(typeof layout.notify==='boolean'){ alertsEnabled=layout.notify; updateBellUI(); }
}
async function loadLayout(){
 let layout=null;
 try{ const r=await fetch('/layout'); if(r.ok){ const j=await r.json(); if(j&&j.widgets) layout=j; } }catch(e){}
 if(!layout){ try{ const c=localStorage.getItem('coa-layout'); if(c) layout=JSON.parse(c); }catch(e){} }
 if(layout) applyLayout(layout);
}
function resetLayout(){
 document.querySelectorAll('#dash > .widget').forEach(w=>{
  w.style.width=''; w.style.height=''; w.classList.remove('collapsed');
 });
 const order=['status','decodes','ops','actions','map','waterfall','log','events'];
 const dash=document.getElementById('dash');
 for(const k of order){ const el=document.querySelector(`.widget[data-key="${k}"]`); if(el) dash.appendChild(el); }
 setMapMode('auto');
 try{localStorage.removeItem('coa-layout');}catch(e){}
 saveLayout();
}
function initWidgetChrome(){
 document.querySelectorAll('.widget').forEach(w=>{
  w.querySelector('.wcollapse').addEventListener('click',()=>{
   w.classList.toggle('collapsed');
   scheduleSaveLayout();
  });
  const title=w.querySelector('.wtitle');
  title.draggable=true;
  title.addEventListener('dragstart',e=>{ dragKey=w.dataset.key; e.dataTransfer.effectAllowed='move'; });
  w.addEventListener('dragover',e=>{ if(dragKey) e.preventDefault(); });
  w.addEventListener('drop',e=>{
   e.preventDefault();
   if(!dragKey||dragKey===w.dataset.key){dragKey=null;return;}
   const src=document.querySelector(`.widget[data-key="${dragKey}"]`);
   if(src){
    const rect=w.getBoundingClientRect();
    const before=(e.clientX-rect.left)<rect.width/2;
    w.parentNode.insertBefore(src, before?w:w.nextSibling);
    scheduleSaveLayout();
   }
   dragKey=null;
  });
  new ResizeObserver(()=>scheduleSaveLayout()).observe(w);
 });
}
document.getElementById('mapAuto').addEventListener('click',()=>setMapMode('auto'));
document.getElementById('mapWorld').addEventListener('click',()=>setMapMode('world'));
document.getElementById('resetLayout').addEventListener('click',resetLayout);

initWidgetChrome();
updateBellUI();
wireBell();
loadLayout();
wireActions();
loadCfg().then(tick); setInterval(tick,5000);
engTick(); setInterval(engTick,2000);
refreshActionsState(); setInterval(refreshActionsState,3000);
</script></body></html>"""
PAGE = (PAGE.replace("__MYCALL__", MYCALL).replace("__MYGRID__", MYGRID)
            .replace("__EVENT_LINES__", str(EVENT_LINES))
            .replace("__WORLD__", world_map.WORLD_PATH)
            .replace("__DRYRUN__", "true" if DRYRUN else "false"))

def chase_tail(n=EVENT_LINES):
    """Last n lines of chase.log without reading a huge file into memory."""
    try:
        with open(CHASELOG, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 64 * 1024))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        return [l.rstrip() for l in lines if l.strip()][-n:]
    except OSError:
        return []

def atomic_write_json(path, obj):
    """tmp + os.replace so a reader never sees a half-written file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)

def log_action(line):
    """Append one audit-trail line to data/actions.log. Never raises."""
    try:
        os.makedirs(DATA, exist_ok=True)
        with open(ACTIONS_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())}Z {line}\n")
    except OSError:
        pass

def _proc_running(pattern):
    """True if some process's full command line contains `pattern` (an absolute
    path) — never a bare name, so this can't match an unrelated process."""
    try:
        r = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False

def _spawn_detached(cmd, log_path):
    """Spawn cmd fully detached (new session, own pgid) so this HTTP server can
    never accidentally signal it; stdout+stderr appended to log_path."""
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    lf = open(log_path, "a")
    subprocess.Popen(cmd, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                      cwd=_ROOT, start_new_session=True, close_fds=True)

def _pkill(pattern):
    """Kill by exact absolute-path pattern match only — never a broad pattern,
    and never this server's own pid/pgid (dashboard.py's own path never matches
    qso.py's or rx-loop.sh's absolute paths)."""
    try:
        r = subprocess.run(["pkill", "-f", pattern], timeout=5)
        return r.returncode == 0
    except Exception:
        return False

class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=DATA, **kw)

    def send_body(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, obj):
        self.send_body(json.dumps(dict(ok=True, **obj)).encode(), "application/json")

    def _err(self, code, msg):
        self.send_body(json.dumps({"ok": False, "error": msg}).encode(), "application/json", code)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/" or path.startswith("/index"):
            self.send_body(PAGE.encode(), "text/html; charset=utf-8")
        elif path == "/events":
            body = json.dumps({"lines": chase_tail()}).encode()
            self.send_body(body, "application/json")
        elif path == "/config":
            self.send_body(json.dumps(CONFIG).encode(), "application/json")
        elif path == "/layout":
            try:
                with open(LAYOUT_JSON, "rb") as f:
                    self.send_body(f.read(), "application/json")
            except OSError:
                self.send_body(b"{}", "application/json")
        elif path == "/actions/state":
            engine = {}
            try:
                with open(os.path.join(DATA, "engine.json")) as f:
                    engine = json.load(f)
            except Exception:
                pass
            state = {"chaser": _proc_running(QSO_PY), "rxloop": _proc_running(RXLOOP_SH),
                      "ptt": bool(engine.get("tx")), "engine_state": engine.get("state"),
                      "dryrun": DRYRUN}
            self.send_body(json.dumps(state).encode(), "application/json")
        else:
            self.path = path
            super().do_GET()

    def do_POST(self):
        # Local-only, belt and suspenders (server already binds 127.0.0.1 only).
        if self.client_address[0] not in ("127.0.0.1", "::1"):
            self.send_body(b'{"ok":false,"error":"local only"}', "application/json", 403)
            return
        path = self.path.split("?")[0]
        parse_ok = True
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
            length = min(length, MAX_POST_BODY)
            raw = self.rfile.read(length) if length else b""
            body = json.loads(raw) if raw else {}
            if not isinstance(body, dict):
                body = {}
                parse_ok = False
        except Exception:
            body = {}
            parse_ok = False
        try:
            if path == "/layout":
                if not parse_ok or "widgets" not in body or not isinstance(body["widgets"], dict):
                    return self._err(400, "malformed layout body")
                atomic_write_json(LAYOUT_JSON, body)
                self._ok({})
            elif path == "/action/rx/start":
                self._action_rx_start()
            elif path == "/action/rx/stop":
                self._action_rx_stop()
            elif path == "/action/chase/start":
                self._action_chase_start(body)
            elif path == "/action/chase/stop":
                self._action_chase_stop()
            elif path == "/action/unkey":
                self._action_unkey()
            elif path == "/action/target/pick":
                self._action_target_write(body, TARGET_REQ, "pick", need_call=True)
            elif path == "/action/target/skip":
                self._action_target_write(body, SKIP_REQ, "skip", need_call=False)
            else:
                self._err(404, "no such endpoint")
        except Exception as e:
            log_action(f"ERROR handling POST {path}: {e!r}")
            self._err(500, "internal error")

    # ---- action handlers ----
    def _action_rx_start(self):
        if DRYRUN:
            log_action(f"[DRYRUN] would start rx-loop: bash {RXLOOP_SH} >> {DATA}/rx-loop.log 2>&1 &")
            return self._ok({"started": True, "dryrun": True})
        if _proc_running(RXLOOP_SH):
            log_action("rx/start: already running, no-op")
            return self._ok({"started": False, "already": True})
        _spawn_detached(["bash", RXLOOP_SH], os.path.join(DATA, "rx-loop.log"))
        log_action(f"rx/start: spawned bash {RXLOOP_SH}")
        self._ok({"started": True})

    def _action_rx_stop(self):
        if DRYRUN:
            log_action(f"[DRYRUN] would stop rx-loop: pkill -f {RXLOOP_SH}")
            return self._ok({"stopped": True, "dryrun": True})
        ok = _pkill(RXLOOP_SH)
        log_action(f"rx/stop: pkill -f {RXLOOP_SH} -> {ok}")
        self._ok({"stopped": ok})

    def _action_chase_start(self, body):
        if not body.get("confirm"):
            return self._err(400, "confirm required")
        mode = body.get("mode")
        if mode not in ("qsos", "minutes"):
            return self._err(400, "mode must be 'qsos' or 'minutes'")
        try:
            n = float(body.get("n"))
        except (TypeError, ValueError):
            return self._err(400, "n must be numeric")
        if mode == "qsos":
            n = int(n)
            if not (1 <= n <= 20):
                return self._err(400, "n out of range (1-20 QSOs)")
            args = ["python3", QSO_PY, "--max-qsos", str(n)]
            desc = f"{n} QSO(s)"
        else:
            if not (1 <= n <= 180):
                return self._err(400, "n out of range (1-180 minutes)")
            args = ["python3", QSO_PY, "--minutes", str(n)]
            desc = f"{n:g} min budget"
        if DRYRUN:
            log_action(f"[DRYRUN] would start chaser: {' '.join(args)} (>> {CHASELOG})")
            return self._ok({"started": True, "dryrun": True})
        if _proc_running(QSO_PY):
            log_action("chase/start: refused, chaser already running")
            return self._err(409, "chaser already running")
        if not _proc_running(RXLOOP_SH):
            log_action("chase/start: refused, rx-loop not running")
            return self._err(409, "start RX first")
        _spawn_detached(args, CHASELOG)
        log_action(f"chase/start: spawned {' '.join(args)} ({desc})")
        self._ok({"started": True})

    def _action_chase_stop(self):
        if DRYRUN:
            log_action(f"[DRYRUN] would stop chaser: pkill -f {QSO_PY}")
            return self._ok({"stopped": True, "dryrun": True})
        ok = _pkill(QSO_PY)
        log_action(f"chase/stop: pkill -f {QSO_PY} -> {ok}")
        self._ok({"stopped": ok})

    def _action_unkey(self):
        """STOP + UNKEY: zero confirmation, one click. Kills the chaser then
        sends rigctl T 0 (release PTT) and reads PTT back. Never sends T 1."""
        if DRYRUN:
            log_action(f"[DRYRUN] would UNKEY: pkill -f {QSO_PY}; "
                       f"rigctl -m {RIG_MODEL} -r {CAT_PORT} -s {CAT_BAUD} T 0")
            return self._ok({"unkeyed": True, "dryrun": True, "ptt": None})
        killed = _pkill(QSO_PY)
        ptt = None
        try:
            subprocess.run(["rigctl", "-m", RIG_MODEL, "-r", CAT_PORT, "-s", CAT_BAUD, "T", "0"],
                           capture_output=True, text=True, timeout=10)
            r2 = subprocess.run(["rigctl", "-m", RIG_MODEL, "-r", CAT_PORT, "-s", CAT_BAUD, "t"],
                               capture_output=True, text=True, timeout=10)
            ptt = r2.stdout.strip()
        except Exception as e:
            log_action(f"UNKEY: rigctl error: {e!r}")
        log_action(f"UNKEY: pkill -f {QSO_PY} (killed={killed}); rigctl T 0; PTT readback={ptt}")
        self._ok({"unkeyed": True, "killed": killed, "ptt": ptt})

    def _action_target_write(self, body, path, kind, need_call):
        call = str(body.get("call", "")).strip().upper()
        if need_call and not call:
            return self._err(400, "call required")
        obj = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if call:
            obj["call"] = call
        atomic_write_json(path, obj)
        log_action(f"target/{kind}: {obj}")
        self._ok({"written": os.path.basename(path)})

    def log_message(self, *a):
        pass

if __name__ == "__main__":
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("127.0.0.1", PORT), H) as srv:
        print(f"FT8-Claude dashboard: http://localhost:{PORT}"
              + (" [COA_DRYRUN]" if DRYRUN else ""))
        srv.serve_forever()
