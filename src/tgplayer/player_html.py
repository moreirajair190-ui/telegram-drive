"""HTML/JS do player premium embutido (QtWebEngine).

Player de vídeo HTML5 completo, com:
- Controles modernos (play/pause, ±10s, velocidade, volume, tela cheia, PiP)
- Barra de progresso com pré-visualização de buffer carregado
- Retomada automática da posição salva
- Comunicação com o app (progresso/posição) via título do documento (lido por
  JS pelo lado Python através de runJavaScript em intervalos) — abordagem
  robusta que não depende de QWebChannel estar disponível.
- Tratamento de erros com botão de "tentar VLC".
"""

from __future__ import annotations

import html
import json


def build_player_html(title: str, url: str, start_position_ms: int = 0) -> str:
    safe_title = html.escape(title)
    url_json = json.dumps(url)
    start_seconds = max(0, int(start_position_ms)) / 1000.0

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{
    --accent:#7c5cff; --accent2:#8e72ff; --bg:#05070f;
    --text:#f8fafc; --muted:#94a3b8;
  }}
  * {{ box-sizing:border-box; -webkit-user-select:none; user-select:none; }}
  html,body {{ margin:0; width:100%; height:100%; background:var(--bg); color:var(--text);
    font-family:Inter,"Segoe UI",Arial,sans-serif; overflow:hidden; }}
  .wrap {{ width:100vw; height:100vh; display:flex; flex-direction:column;
    background:radial-gradient(circle at 50% -10%, #131a2e 0%, #05070f 55%, #02030a 100%); }}
  .stage {{ position:relative; flex:1; min-height:0; display:flex; align-items:center;
    justify-content:center; background:#000; overflow:hidden; cursor:default; }}
  video {{ width:100%; height:100%; background:#000; object-fit:contain; }}

  .topbar {{ position:absolute; top:0; left:0; right:0; padding:20px 24px 56px;
    background:linear-gradient(to bottom, rgba(0,0,0,.78), transparent);
    transition:opacity .25s; display:flex; align-items:flex-start; gap:14px; }}
  .title {{ font-size:17px; font-weight:800; letter-spacing:-.02em;
    text-shadow:0 2px 18px rgba(0,0,0,.85); flex:1; line-height:1.3; }}
  .badge {{ font-size:11px; font-weight:800; padding:5px 12px; border-radius:999px;
    background:rgba(124,92,255,.22); border:1px solid rgba(124,92,255,.5); color:#cdbcff; }}

  .center {{ position:absolute; inset:0; display:grid; place-items:center; pointer-events:none; }}
  .bigbtn {{ width:92px; height:92px; border-radius:999px;
    border:1px solid rgba(255,255,255,.22); background:rgba(10,14,26,.55);
    color:#fff; font-size:34px; display:grid; place-items:center;
    backdrop-filter:blur(18px); transition:.18s; box-shadow:0 24px 70px rgba(0,0,0,.5);
    pointer-events:auto; cursor:pointer; }}
  .bigbtn:hover {{ transform:scale(1.06); background:rgba(124,92,255,.3); }}
  .bigbtn.hidden {{ opacity:0; transform:scale(.7); pointer-events:none; }}

  .spinner {{ position:absolute; width:64px; height:64px; border-radius:50%;
    border:4px solid rgba(255,255,255,.14); border-top-color:var(--accent);
    animation:spin 0.9s linear infinite; display:none; }}
  .spinner.show {{ display:block; }}
  @keyframes spin {{ to {{ transform:rotate(360deg); }} }}

  .controls {{ position:absolute; left:0; right:0; bottom:0; padding:60px 22px 18px;
    background:linear-gradient(to top, rgba(0,0,0,.9), rgba(0,0,0,.35) 60%, transparent);
    transition:opacity .25s; }}
  .stage.idle {{ cursor:none; }}
  .stage.idle .controls, .stage.idle .topbar {{ opacity:0; }}

  .seekwrap {{ position:relative; height:18px; display:flex; align-items:center; cursor:pointer; }}
  .track {{ position:absolute; left:0; right:0; height:6px; border-radius:6px;
    background:rgba(255,255,255,.16); overflow:hidden; }}
  .buffered {{ position:absolute; left:0; top:0; bottom:0; width:0%;
    background:rgba(255,255,255,.28); }}
  .played {{ position:absolute; left:0; top:0; bottom:0; width:0%;
    background:linear-gradient(90deg, var(--accent), var(--accent2)); }}
  .thumb {{ position:absolute; width:14px; height:14px; border-radius:50%; background:#fff;
    box-shadow:0 0 0 4px rgba(124,92,255,.4); transform:translateX(-50%);
    left:0%; transition:transform .1s; }}
  .seekwrap:hover .track {{ height:8px; }}
  .seekwrap:hover .thumb {{ width:16px; height:16px; }}

  .row {{ display:flex; align-items:center; gap:10px; margin-top:14px; }}
  .time {{ font-size:13px; font-weight:700; color:#cbd5e1; font-variant-numeric:tabular-nums;
    min-width:120px; }}
  .spacer {{ flex:1; }}

  button.ctl, select.ctl {{ border:1px solid rgba(255,255,255,.14);
    background:rgba(20,26,46,.7); color:#fff; border-radius:11px; padding:9px 12px;
    font-weight:750; cursor:pointer; backdrop-filter:blur(12px); font-size:13px;
    transition:.15s; }}
  button.ctl:hover, select.ctl:hover {{ background:rgba(124,92,255,.32);
    border-color:rgba(124,92,255,.55); }}
  .round {{ width:48px; height:48px; border-radius:999px; font-size:18px; padding:0;
    display:grid; place-items:center; }}
  .round.play {{ background:var(--accent); border-color:var(--accent); }}
  .round.play:hover {{ background:#8e72ff; }}

  .volwrap {{ display:flex; align-items:center; gap:8px; }}
  input[type=range].vol {{ width:96px; accent-color:var(--accent); cursor:pointer; }}

  .toast {{ position:absolute; top:84px; left:50%; transform:translateX(-50%);
    background:rgba(20,26,46,.92); color:#fff; border:1px solid rgba(124,92,255,.5);
    border-radius:999px; padding:10px 18px; font-weight:800; opacity:0; transition:.22s;
    pointer-events:none; font-size:14px; }}
  .toast.show {{ opacity:1; }}

  .err {{ position:absolute; left:50%; top:50%; transform:translate(-50%,-50%);
    max-width:520px; background:rgba(127,29,29,.92); border:1px solid rgba(254,202,202,.4);
    color:#fee2e2; padding:20px 22px; border-radius:16px; display:none; text-align:center; }}
  .err h3 {{ margin:0 0 8px; font-size:16px; }}
  .err p {{ margin:0; font-size:13px; color:#fecaca; line-height:1.5; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="stage" id="stage">
    <video id="video" src={url_json} playsinline preload="auto"></video>

    <div class="spinner" id="spinner"></div>
    <div class="center"><div class="bigbtn hidden" id="bigbtn">▶</div></div>

    <div class="topbar">
      <div class="title">{safe_title}</div>
      <div class="badge" id="badge">Streaming</div>
    </div>

    <div class="toast" id="toast"></div>

    <div class="err" id="err">
      <h3>Não foi possível reproduzir esta aula</h3>
      <p id="errmsg">Tente reabrir a aula ou use o VLC pela janela principal.</p>
      <div style="margin-top:14px;display:flex;gap:10px;justify-content:center;">
        <button class="ctl" id="retry">↻ Tentar de novo</button>
        <button class="ctl" id="openvlc" style="background:rgba(124,92,255,.4)">Abrir no VLC</button>
      </div>
    </div>

    <div class="controls" id="controls">
      <div class="seekwrap" id="seekwrap">
        <div class="track">
          <div class="buffered" id="buffered"></div>
          <div class="played" id="played"></div>
        </div>
        <div class="thumb" id="thumb"></div>
      </div>
      <div class="row">
        <button class="ctl round play" id="play">▶</button>
        <button class="ctl" id="back">↺ 10</button>
        <button class="ctl" id="fwd">10 ↻</button>
        <span class="time" id="time">00:00 / 00:00</span>
        <div class="spacer"></div>
        <div class="volwrap">
          <button class="ctl" id="mute" style="padding:9px 10px">🔊</button>
          <input class="vol" id="vol" type="range" min="0" max="1" step="0.01" value="1">
        </div>
        <select class="ctl" id="speed">
          <option value="0.5">0.5x</option>
          <option value="0.75">0.75x</option>
          <option value="1" selected>1x</option>
          <option value="1.25">1.25x</option>
          <option value="1.5">1.5x</option>
          <option value="1.75">1.75x</option>
          <option value="2">2x</option>
        </select>
        <button class="ctl" id="pip" title="Picture in Picture">⧉</button>
        <button class="ctl" id="full">⛶</button>
      </div>
    </div>
  </div>
</div>

<script>
const v=document.getElementById('video'), stage=document.getElementById('stage');
const play=document.getElementById('play'), big=document.getElementById('bigbtn');
const played=document.getElementById('played'), buffered=document.getElementById('buffered');
const thumb=document.getElementById('thumb'), seekwrap=document.getElementById('seekwrap');
const timeEl=document.getElementById('time'), speed=document.getElementById('speed');
const toast=document.getElementById('toast'), err=document.getElementById('err');
const errmsg=document.getElementById('errmsg'), vol=document.getElementById('vol');
const mute=document.getElementById('mute'), spinner=document.getElementById('spinner');
const badge=document.getElementById('badge');

const START={start_seconds};
let dragging=false, idleTimer=null, resumed=false;

// Estado lido pelo lado Python (progresso/posição/comandos) via runJavaScript.
window.__tg_state = {{position:0, duration:0, paused:true, ended:false, wantVlc:false}};
function pushState(){{
  const s = window.__tg_state || {{}};
  window.__tg_state = {{
    position: Math.floor((v.currentTime||0)*1000),
    duration: Math.floor((v.duration||0)*1000),
    paused: v.paused, ended: v.ended,
    volume: v.volume, muted: v.muted,
    rate: v.playbackRate,
    wantVlc: !!s.wantVlc
  }};
}}

function fmt(s){{
  if(!isFinite(s)||s<0) s=0; s=Math.floor(s);
  const h=Math.floor(s/3600), m=Math.floor((s%3600)/60), sec=s%60;
  const mm=String(m).padStart(2,'0'), ss=String(sec).padStart(2,'0');
  return h? `${{h}}:${{mm}}:${{ss}}` : `${{mm}}:${{ss}}`;
}}
function showToast(t){{ toast.textContent=t; toast.classList.add('show');
  clearTimeout(toast._t); toast._t=setTimeout(()=>toast.classList.remove('show'),900); }}

function render(){{
  const d=v.duration||0, c=v.currentTime||0;
  if(!dragging && d>0){{
    const pct=(c/d)*100;
    played.style.width=pct+'%'; thumb.style.left=pct+'%';
  }}
  // Buffer carregado
  if(v.buffered.length && d>0){{
    let bEnd=0;
    for(let i=0;i<v.buffered.length;i++){{ if(v.buffered.start(i)<=c) bEnd=v.buffered.end(i); }}
    buffered.style.width=Math.min(100,(bEnd/d)*100)+'%';
  }}
  timeEl.textContent=fmt(c)+' / '+fmt(d);
  const sym=v.paused?'▶':'❚❚';
  play.textContent=sym; big.textContent=sym;
  big.classList.toggle('hidden', !v.paused);
  pushState();
}}

function toggle(){{ if(v.paused){{ v.play().catch(showErr); }} else {{ v.pause(); }} render(); }}
function showErr(e){{
  spinner.classList.remove('show');
  err.style.display='block';
  errmsg.textContent='Detalhe: '+((e&&e.message)?e.message:(e||'erro desconhecido'))+
    '. Tente reabrir a aula ou use o VLC.';
}}

play.onclick=toggle; big.onclick=toggle;
stage.addEventListener('click',(e)=>{{ if(e.target===stage||e.target===v) toggle(); }});

// Botões da tela de erro
document.getElementById('retry').onclick=()=>{{
  err.style.display='none'; spinner.classList.add('show');
  try{{ v.load(); v.play().catch(showErr); }}catch(e){{ showErr(e); }} }};
document.getElementById('openvlc').onclick=()=>{{
  window.__tg_state = Object.assign(window.__tg_state||{{}}, {{wantVlc:true}}); }};

document.getElementById('back').onclick=()=>{{ v.currentTime=Math.max(0,v.currentTime-10); showToast('↺ 10s'); }};
document.getElementById('fwd').onclick=()=>{{ v.currentTime=Math.min((v.duration||1e9),v.currentTime+10); showToast('10s ↻'); }};
speed.onchange=()=>{{ v.playbackRate=parseFloat(speed.value); showToast(speed.value+'x'); }};
vol.oninput=()=>{{ v.volume=parseFloat(vol.value); v.muted=false; mute.textContent=v.volume==0?'🔇':'🔊'; }};
mute.onclick=()=>{{ v.muted=!v.muted; mute.textContent=v.muted?'🔇':'🔊'; }};
document.getElementById('full').onclick=()=>{{
  if(!document.fullscreenElement) stage.requestFullscreen?.(); else document.exitFullscreen?.(); }};
document.getElementById('pip').onclick=async()=>{{
  try{{ if(document.pictureInPictureElement) await document.exitPictureInPicture();
    else await v.requestPictureInPicture(); }}catch(e){{ showToast('PiP indisponível'); }} }};

// Seek na barra de progresso
function seekTo(clientX){{
  const r=seekwrap.getBoundingClientRect();
  let ratio=(clientX-r.left)/r.width; ratio=Math.max(0,Math.min(1,ratio));
  if(isFinite(v.duration)) v.currentTime=ratio*v.duration;
}}
seekwrap.addEventListener('mousedown',(e)=>{{ dragging=true; seekTo(e.clientX);
  const pct=((v.currentTime||0)/(v.duration||1))*100; played.style.width=pct+'%'; thumb.style.left=pct+'%'; }});
window.addEventListener('mousemove',(e)=>{{ if(dragging){{ seekTo(e.clientX);
  const pct=((v.currentTime||0)/(v.duration||1))*100; played.style.width=pct+'%'; thumb.style.left=pct+'%'; }} }});
window.addEventListener('mouseup',()=>{{ dragging=false; }});

['timeupdate','durationchange','play','pause','progress','loadedmetadata','seeked','ratechange']
  .forEach(ev=>v.addEventListener(ev,render));

v.addEventListener('waiting',()=>{{ spinner.classList.add('show'); badge.textContent='Carregando…'; }});
v.addEventListener('canplay',()=>{{ spinner.classList.remove('show'); badge.textContent='Pronto'; }});
v.addEventListener('playing',()=>{{ spinner.classList.remove('show'); badge.textContent='Reproduzindo'; }});
v.addEventListener('stalled',()=>badge.textContent='Buffer…');
v.addEventListener('error',()=>showErr(v.error&&v.error.message));

// Retoma posição salva quando os metadados carregarem.
v.addEventListener('loadedmetadata',()=>{{
  if(!resumed && START>0 && isFinite(v.duration) && START < v.duration-5){{
    try{{ v.currentTime=START; showToast('Retomando de '+fmt(START)); }}catch(e){{}}
  }}
  resumed=true;
}});

// Atalhos de teclado
document.addEventListener('keydown',(e)=>{{
  if(e.code==='Space'){{ e.preventDefault(); toggle(); }}
  else if(e.key==='ArrowLeft'){{ v.currentTime=Math.max(0,v.currentTime-10); showToast('↺ 10s'); }}
  else if(e.key==='ArrowRight'){{ v.currentTime=Math.min((v.duration||1e9),v.currentTime+10); showToast('10s ↻'); }}
  else if(e.key==='ArrowUp'){{ v.volume=Math.min(1,v.volume+0.1); vol.value=v.volume; showToast('Vol '+Math.round(v.volume*100)+'%'); }}
  else if(e.key==='ArrowDown'){{ v.volume=Math.max(0,v.volume-0.1); vol.value=v.volume; showToast('Vol '+Math.round(v.volume*100)+'%'); }}
  else if(e.key.toLowerCase()==='f'){{ document.getElementById('full').click(); }}
  else if(e.key.toLowerCase()==='m'){{ mute.click(); }}
  else if(e.key.toLowerCase()==='j'){{ v.currentTime=Math.max(0,v.currentTime-10); }}
  else if(e.key.toLowerCase()==='l'){{ v.currentTime=v.currentTime+10; }}
  else if(e.key==='>'){{ speed.selectedIndex=Math.min(speed.options.length-1,speed.selectedIndex+1); speed.onchange(); }}
  else if(e.key==='<'){{ speed.selectedIndex=Math.max(0,speed.selectedIndex-1); speed.onchange(); }}
}});

function resetIdle(){{ stage.classList.remove('idle'); clearTimeout(idleTimer);
  if(!v.paused) idleTimer=setTimeout(()=>stage.classList.add('idle'),2600); }}
stage.addEventListener('mousemove',resetIdle);
stage.addEventListener('mouseleave',()=>{{ if(!v.paused) stage.classList.add('idle'); }});

spinner.classList.add('show');
resetIdle(); render();
v.play().catch(()=>{{ badge.textContent='Clique para reproduzir'; spinner.classList.remove('show'); }});
setInterval(pushState, 1000);
</script>
</body></html>"""
