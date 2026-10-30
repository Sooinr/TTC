#!/usr/bin/env python3
"""state.json + trades.csv + settings.json → 웹 대시보드(index.html) 생성
   2단 레이아웃(데스크톱) / 1단 세로 스택(모바일) 반응형 · 제어 패널 내장"""
import csv, json, os, html
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
START_CASH = 10000000
DEFAULTS = {"enabled": True, "top_n": 4, "rebal_min": 20, "stop": -0.08, "take": 0.08, "trail": 0.05}

def load(p, d):
    try:
        with open(p, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return d

assets = json.loads(load(os.path.join(HERE, "universe_us.json"), "[]"))
NAME = {a["code"]: a.get("name", a["code"]) for a in assets}
SEC = {a["code"]: a.get("sector", "") for a in assets}
PX = {a["code"]: a["hist"][-1] for a in assets if a.get("hist")}

st = {}
try:
    st = json.loads(load(os.path.join(HERE, "state.json"), "{}"))
except ValueError:
    pass
if not st:
    st = {"cash": START_CASH, "pos": {}, "realized": 0, "wins": 0, "closes": 0,
          "trades": 0, "rebal_count": 0, "last_rebal": 0, "day_closes": {}, "equity_hist": []}

cfg = dict(DEFAULTS)
try:
    cfg.update(json.loads(load(os.path.join(HERE, "settings.json"), "{}")))
except ValueError:
    pass
TOP = int(cfg.get("top_n") or 4)

def mom(arr):
    if len(arr) < 60:
        return None
    return 0.5 * (arr[-1] / arr[-20] - 1) + 0.5 * (arr[-1] / arr[-60] - 1)

for c, v in st.get("day_closes", {}).items():
    if v:
        PX[c] = v[-1]

inv = sum(p["qty"] * PX[c] for c, p in st["pos"].items() if c in PX)
total = st["cash"] + inv
ret = total / START_CASH - 1
upl = sum(p["qty"] * (PX[c] - p["avg"]) for c, p in st["pos"].items() if c in PX)
inv_pct = (inv / total * 100) if total else 0

def esc(s): return html.escape(str(s))
def fmt(n): return f"{n:,.0f}"
def fnum(x, d=1, sign=False):
    if x is None: return "준비중"
    return ("+" if sign and x >= 0 else "") + f"{x*100:.{d}f}%"

def kpi(k, v): return f"<div class='kpi'><div class='k'>{k}</div><div class='kv'>{v}</div></div>"

def mdd_txt():
    vals = [x[1] for x in (st.get("equity_hist") or [])]
    if len(vals) < 2:
        return "0.0%"
    peak, mdd = 0, 0
    for v in vals:
        peak = max(peak, v)
        if peak:
            mdd = min(mdd, v / peak - 1)
    return f"{mdd*100:.1f}%"

try:
    from zoneinfo import ZoneInfo
    _n = datetime.now(ZoneInfo("America/New_York"))
    MKT_OPEN = _n.weekday() < 5 and ((_n.hour > 9 or (_n.hour == 9 and _n.minute >= 30)) and _n.hour < 16)
except Exception:
    MKT_OPEN = False

# ── 계좌 카드 ──
ACC = f"""<div class="card"><div class="bignum">${fmt(total)}</div>
<div class="k">수익률 {fnum(ret, 1, True)} · 시작자산 ${fmt(START_CASH)} · <b style="color:{'#2FBF71' if cfg.get('enabled', True) else '#E5484D'}">봇 {'실행중' if cfg.get('enabled', True) else '정지됨'}</b></div>
<div class="kgrid">
{kpi('현금', '$' + fmt(st['cash']))}{kpi('투자중', f'{inv_pct:.0f}%')}{kpi('평가손익', '$' + fmt(upl))}{kpi('실현손익', '$' + fmt(st['realized']))}
{kpi('최대낙폭', mdd_txt())}{kpi('리밸런스', str(st['rebal_count']) + '회')}{kpi('매매', str(st['trades']) + '건')}{kpi('승률', str(round(st['wins'] / st['closes'] * 100)) + '%' if st['closes'] else '—')}
</div></div>"""

# ── 보유 테이블 ──
rows_pos = ""
for c, p in st["pos"].items():
    px = PX.get(c, 0)
    r = (px / p["avg"] - 1) * 100 if px else 0
    col = "#2FBF71" if r >= 0 else "#E5484D"
    rows_pos += (f"<tr><td><b>{c}</b> {esc(NAME.get(c, c))}</td><td>{esc(SEC.get(c, ''))}</td>"
                 f"<td>{fmt(p['qty'])}</td><td>{p['avg']:.2f}</td><td>{px:.2f}</td>"
                 f"<td style='color:{col}'>{'▲ +' if r >= 0 else '▼ '}{r:.1f}%</td></tr>")
rows_pos = rows_pos or "<tr><td colspan='6'>보유 종목 없음 — 다음 리밸런스에서 편입 시도</td></tr>"
cash_pct = 100 - inv_pct
HOLD = f"""<div class="card"><table><tr><th>종목</th><th>섹터</th><th>수량</th><th>평단$</th><th>현재가</th><th>손익</th></tr>{rows_pos}</table>
<div class="bar"><i style="width:{inv_pct:.0f}%"></i><span>현금 {cash_pct:.0f}% / 투자 {inv_pct:.0f}%</span></div></div>"""

# ── 매매 기록 ──
rows_tr = ""
for r in load(os.path.join(HERE, "trades.csv"), "").strip().splitlines()[-15:][::-1]:
    f = list(csv.reader([r]))[0] if r else []
    if len(f) >= 6 and f[0] != "시각":
        pnl = f[6] if len(f) > 6 and f[6] else ""
        col = (" style='color:#2FBF71'" if pnl and not pnl.startswith("-")
               else (" style='color:#E5484D'" if pnl.startswith("-") else ""))
        rows_tr += (f"<tr><td>{esc(f[0][-8:])}</td><td>{esc(f[1])}</td><td>{esc(f[2])} {esc(NAME.get(f[2], f[2]))}</td>"
                    f"<td>{esc(f[4])}</td><td>{esc(f[5])}</td><td{col}>{esc(pnl) or '—'}</td></tr>")
rows_tr = rows_tr or "<tr><td colspan='6'>아직 기록 없음 — 장 시작 후 첫 리밸런스에서 매수</td></tr>"
TRADES = f"<div class='card'><table><tr><th>시각</th><th>구분</th><th>종목</th><th>가격</th><th>사유</th><th>손익$</th></tr>{rows_tr}</table></div>"

# ── 자산 곡선 (SVG) ──
eh = st.get("equity_hist") or []
if len(eh) >= 2:
    vals = [x[1] for x in eh]
    lo, hi = min(vals), max(vals)
    if hi - lo < 1:
        hi = lo + 1
    pts = " ".join(f"{i/(len(vals)-1)*300:.1f},{56-(v-lo)/(hi-lo)*50:.1f}" for i, v in enumerate(vals))
    curve = (f"<svg viewBox='0 0 300 60' preserveAspectRatio='none' class='chart'>"
             f"<polyline points='{pts}' fill='none' stroke='{'#2FBF71' if vals[-1] >= vals[0] else '#E5484D'}' stroke-width='1.6'/></svg>")
else:
    curve = "<div class='chart' style='text-align:center;padding-top:26px'>실행 데이터가 쌓이면 곡선이 그려집니다</div>"
CURVE = f"<div class='card'>{curve}<div class='k'>총자산 ${fmt(total)} ({fnum(ret, 1, True)})</div></div>"

# ── 랭킹 (24종목) ──
rk = []
for a in assets:
    c = a["code"]
    rk.append((c, mom(st.get("day_closes", {}).get(c) or [])))
rk.sort(key=lambda x: (x[1] is None, -(x[1] or 0)))
mx = max([abs(v) for _, v in rk if v is not None] or [0.05]) or 0.05
rk_html = ""
for i, (c, v) in enumerate(rk):
    held = c in st["pos"]
    w = min(abs(v) / mx * 100, 100) if v is not None else 0
    col = ("#4A5568" if v is None else ("#F5B942" if held else ("#2FBF71" if v >= 0 else "#E5484D")))
    held_mark = ' <b style="color:#F5B942">●</b>' if held else ''
    rk_html += (f"<div class='rk{' held' if held else ''}'><span class='rn'>{i+1}</span>"
                f"<span class='rc'>{esc(NAME.get(c, c))}{held_mark}</span>"
                f"<span class='rb'><i style='width:{w:.0f}%;background:{col}'></i></span>"
                f"<span class='rv' style='color:{col}'>{fnum(v)}</span></div>")
RANK = f"<div class='card'>{rk_html}<div class='k' style='margin-top:6px'>노란 막대 = 현재 보유 · 점수 = 개별 모멘텀 60% + 섹터 열기 40%</div></div>"

# ── 섹터 카드 ──
secv, seen = {}, set()
for c, v in rk:
    s = SEC.get(c)
    if not s or s in seen:
        continue
    seen.add(s)
    ms = [mom(st["day_closes"][x]) for x in st.get("day_closes", {}) if SEC.get(x) == s]
    ms = [m for m in ms if m is not None]
    secv[s] = sum(ms) / len(ms) if ms else None
order = sorted(secv, key=lambda s: -(secv[s] if secv[s] is not None else -9))
best = order[0] if order else None
sc = ""
for s in order:
    v = secv[s]
    held = any(SEC.get(c) == s for c in st["pos"])
    col = "#4A5568" if v is None else ("#2FBF71" if v >= 0 else "#E5484D")
    held_s = ' <b style="color:#F5B942">보유중</b>' if held else ''
    sc += (f"<div class='sc{' hsec' if held else ''}'><div class='sn'>{esc(s)}{held_s}</div>"
           f"<div class='sv' style='color:{col}'>{fnum(v)}</div></div>")
SECTORS = f"<div class='card'><div class='k'>최강 섹터: <b style='color:#F5B942'>{esc(best or '—')}</b></div><div class='cards' style='margin-top:6px'>{sc}</div></div>"

opts = lambda vals, cur: "".join(f"<option {'selected' if x == cur else ''} value='{x}'>{x}</option>" for x in vals)
v_top = TOP
v_reb = int(cfg.get("rebal_min") or 20)
v_stop = round(abs(cfg.get("stop") or -0.08) * 100)
v_take = round((cfg.get("take") or 0.08) * 100)
v_trail = round((cfg.get("trail") or 0.05) * 100)

CONTROL = """
<div class="card" id="ctrl">
 <h3 style="margin:0 0 6px;font-size:13px;color:#F5B942">전략 설정 · 제어</h3>
 <div id="badge" style="font-size:13px;font-weight:700;color:__BC__">봇 상태: __BT__</div>
 <div style="display:flex;flex-wrap:wrap;gap:6px;margin:8px 0">
  <button onclick="preset('aggr')" class="bt2">공격형</button>
  <button onclick="preset('bal')" class="bt2">균형형</button>
  <button onclick="preset('def')" class="bt2">보수형</button>
 </div>
 <label class="lb">동시 보유 종목 수 <b id="v_top" style="color:#F5B942">__VTOP__</b>개
  <input type="range" id="c_top" min="2" max="8" step="1" value="__VTOP__" class="rng"
   oninput="document.getElementById('v_top').textContent=this.value"></label>
 <label class="lb">리밸런스 주기(분)
  <select id="c_rebal" class="in">__OREB__</select></label>
 <div style="display:flex;flex-wrap:wrap;gap:8px">
  <label class="lb">손절 %<input id="c_stop" type="number" step="1" class="in" style="width:60px" value="__VST__"></label>
  <label class="lb">익절 %<input id="c_take" type="number" step="1" class="in" style="width:60px" value="__VTK__"></label>
  <label class="lb">트레일링 %<input id="c_trail" type="number" step="1" class="in" style="width:60px" value="__VTR__"></label>
 </div>
 <div style="display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 4px">
  <button onclick="setEn(true)" class="bt" style="background:#2FBF71">▶ 시작</button>
  <button onclick="setEn(false)" class="bt" style="background:#E5484D">■ 정지</button>
  <button onclick="saveCfg()" class="bt" style="background:#F5B942;color:#0E1216">설정 저장</button>
  <button onclick="runNow()" class="bt" style="background:#3E76D6">지금 리밸런스</button>
 </div>
 <div style="margin-top:6px;font-size:12px">
  <input id="tok" class="in" style="width:100%;max-width:260px" placeholder="GitHub 토큰">
  <button onclick="saveTok()" class="bt" style="background:#29323E">토큰 저장</button>
 </div>
 <div id="msg" style="margin-top:6px;font-size:12px;color:#F5B942"></div>
 <div class="k" style="margin-top:6px">변경은 <b>다음 실행(≤20분)부터 적용</b> · 토큰은 브라우저에만 저장</div>
</div>
<script>
var OWNER = location.hostname.split(".")[0], REPO = (location.pathname.split("/")[1] || "");
var P = { aggr:{top_n:6, rebal_min:10, stop:-0.12, take:0.15, trail:0.07},
         bal:{top_n:4, rebal_min:20, stop:-0.08, take:0.08, trail:0.05},
         def:{top_n:3, rebal_min:30, stop:-0.05, take:0.05, trail:0.03} };
function b64e(s){ return btoa(unescape(encodeURIComponent(s))); }
function b64d(s){ return decodeURIComponent(escape(atob(s.replace(/\\n/g, "")))); }
function hdr(){ var h = { "Accept": "application/vnd.github+json" };
  var t = localStorage.getItem("gh_pat"); if (t) h["Authorization"] = "Bearer " + t; return h; }
function msg(m){ document.getElementById("msg").textContent = m; }
function badge(on){ var b = document.getElementById("badge");
  b.innerHTML = "봇 상태: " + (on ? "실행중" : "정지됨") + ' <span class="k">(변경 시 다음 실행부터)</span>';
  b.style.color = on ? "#2FBF71" : "#E5484D"; }
async function getCfg(){
  var r = await fetch("https://api.github.com/repos/" + OWNER + "/" + REPO + "/contents/settings.json", { headers: hdr() });
  if (r.status === 404) return { sha: null, cfg: {} };
  if (!r.ok) throw new Error("HTTP " + r.status);
  var j = await r.json();
  return { sha: j.sha, cfg: JSON.parse(b64d(j.content)) };
}
async function putCfg(cfg, sha){
  var r = await fetch("https://api.github.com/repos/" + OWNER + "/" + REPO + "/contents/settings.json", {
    method: "PUT", headers: Object.assign({ "Content-Type": "application/json" }, hdr()),
    body: JSON.stringify({ message: "대시보드에서 설정 변경", content: b64e(JSON.stringify(cfg, null, 2)), sha: sha, branch: "main" }) });
  if (!r.ok) throw new Error(r.status === 403 ? "권한 없음 — 토큰 Contents 권한 확인" : "HTTP " + r.status);
}
async function updateCfg(mut){
  for (var i = 0; i < 3; i++){
    var g = await getCfg();
    try { var next = mut(g.cfg); await putCfg(next, g.sha); return next; }
    catch (e) { if (String(e.message).indexOf("409") < 0) throw e; }
  }
}
function fromInputs(){
  return { top_n: +document.getElementById("c_top").value,
           rebal_min: +document.getElementById("c_rebal").value,
           stop: -Math.abs(+document.getElementById("c_stop").value) / 100,
           take: Math.abs(+document.getElementById("c_take").value) / 100,
           trail: Math.abs(+document.getElementById("c_trail").value) / 100 };
}
function fill(c){
  if (c.top_n){ document.getElementById("c_top").value = c.top_n; document.getElementById("v_top").textContent = c.top_n; }
  if (c.rebal_min) document.getElementById("c_rebal").value = c.rebal_min;
  if (c.stop != null) document.getElementById("c_stop").value = Math.round(Math.abs(c.stop) * 100);
  if (c.take != null) document.getElementById("c_take").value = Math.round(c.take * 100);
  if (c.trail != null) document.getElementById("c_trail").value = Math.round(c.trail * 100);
}
function preset(k){ fill(P[k]); msg("프리셋 적용됨 — [설정 저장]을 눌러야 반영"); }
async function setEn(on){
  try { await updateCfg(function(c){ c.enabled = on; return c; });
        badge(on); msg(on ? "시작 완료 — 다음 실행부터 매매" : "정지 완료 — 다음 실행부터 매매 안 함"); }
  catch (e) { msg("실패: " + e.message); }
}
async function saveCfg(){
  try { var c = await updateCfg(function(c0){ var n = fromInputs(); n.enabled = c0.enabled !== false; return n; });
        fill(c); msg("설정 저장 완료 — 다음 실행부터 적용"); }
  catch (e) { msg("실패: " + e.message); }
}
async function runNow(){
  try { var r = await fetch("https://api.github.com/repos/" + OWNER + "/" + REPO + "/actions/workflows/bot.yml/dispatches",
        { method: "POST", headers: Object.assign({ "Content-Type": "application/json" }, hdr()), body: JSON.stringify({ ref: "main" }) });
    if (!r.ok) throw new Error(r.status === 403 ? "토큰에 Actions 권한 필요" : "HTTP " + r.status);
    msg("실행 요청 완료 — Actions 탭에서 확인");
  } catch (e) { msg("실패: " + e.message); }
}
function saveTok(){ localStorage.setItem("gh_pat", document.getElementById("tok").value.trim()); msg("토큰 저장 완료"); }
if (!REPO) document.getElementById("ctrl").style.display = "none";
else (async function(){ try { var g = await getCfg(); fill(g.cfg); badge(g.cfg.enabled !== false); }
       catch (e) { msg("설정 읽기 실패: " + e.message); } })();
setInterval(function(){ document.getElementById("clk").textContent =
  new Date().toLocaleTimeString("ko-KR"); }, 1000);
</script>
"""

now = datetime.now().strftime("%m-%d %H:%M UTC")
STATUS = ("<div class='k'>봇이 지금 보고 있는 것: " +
          ("장중 — 실시간 매매 진행" if MKT_OPEN else "장 마감 — 다음 개장(서울 밤 11:30~12:30) 후 자동 재개") +
          " · 마지막 실행 " + now + "</div>")

page = f"""<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>로테이션 봇 대시보드</title>
<style>
*{{box-sizing:border-box}}
body{{font-family:sans-serif;background:#0E1216;color:#E9EDF3;margin:0;padding:10px}}
.top{{display:flex;flex-wrap:wrap;align-items:center;gap:10px;border-bottom:1px solid #29323E;padding-bottom:8px}}
.tt{{font-size:17px;font-weight:800}} .bdg{{background:#F5B942;color:#0E1216;border-radius:5px;padding:2px 7px;font-size:11px;font-weight:700}}
.k{{color:#93A0B0;font-size:11px}}
.grid{{display:flex;flex-wrap:wrap;gap:12px;margin-top:12px}}
.colL{{flex:1.5;min-width:320px}} .colR{{flex:1;min-width:320px}}
@media(max-width:880px){{.grid{{flex-direction:column}}.colR{{order:-1}}}}
h2{{font-size:13.5px;color:#F5B942;margin:14px 0 2px}}
.card{{background:#161C24;border:1px solid #29323E;border-radius:10px;padding:10px;margin-top:8px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px}}
td,th{{padding:5px 6px;border-bottom:1px solid #29323E;text-align:left;white-space:nowrap}}
.kpi{{flex:1;min-width:calc(25% - 8px);background:#1D2530;border:1px solid #29323E;border-radius:8px;padding:7px 9px}}
.kv{{font-size:14px;font-weight:700;margin-top:2px}}
.kgrid{{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}}
.bignum{{font-size:30px;font-weight:800;color:{'#2FBF71' if ret >= 0 else '#E5484D'}}}
.rk{{display:flex;align-items:center;gap:8px;font-size:12.5px;padding:3px 4px;border-radius:4px}}
.rn{{width:18px;color:#93A0B0;text-align:right}} .rc{{width:100px;overflow:hidden;text-overflow:ellipsis}}
.rb{{flex:1;background:#1D2530;border-radius:3px;height:11px;overflow:hidden}}
.rb i{{display:block;height:11px}} .rv{{width:52px;text-align:right;font-weight:600}}
.rk.held{{background:#22271a}}
.sc{{flex:1;min-width:110px;background:#1D2530;border:1px solid #29323E;border-radius:8px;padding:7px 9px}}
.hsec{{border-color:#F5B942}} .sn{{font-size:11px;color:#93A0B0}} .sv{{font-size:15px;font-weight:700;margin-top:2px}}
.cards{{display:flex;flex-wrap:wrap;gap:7px}}
.chart{{width:100%;height:110px;display:block}}
.bar{{position:relative;background:#1D2530;border-radius:4px;height:16px;margin-top:8px;overflow:hidden}}
.bar i{{position:absolute;left:0;top:0;height:16px;background:#2FBF71;opacity:.6}}
.bar span{{position:absolute;width:100%;text-align:center;font-size:10.5px;color:#E9EDF3;line-height:16px}}
.status{{border:1px solid #F5B942;border-radius:10px;padding:9px 11px;margin-top:8px;font-size:12px;color:#B8C2CF}}
.in{{background:#1D2530;color:#E9EDF3;border:1px solid #29323E;border-radius:6px;padding:5px 7px;font-size:12.5px}}
.bt{{color:#fff;border:0;border-radius:7px;padding:9px 13px;font-weight:700;cursor:pointer;font-size:13px}}
.bt2{{background:#1D2530;color:#E9EDF3;border:1px solid #29323E;border-radius:7px;padding:6px 12px;cursor:pointer;font-size:12px}}
.lb{{display:flex;flex-direction:column;gap:3px;font-size:12px;color:#93A0B0;flex:1;min-width:110px}}
.rng{{width:100%}}
</style>
<div class="top"><span class="tt">포트폴리오 로테이션 터미널</span><span class="bdg">US PAPER</span>
<span class="k">유니버스 24종목 · 11섹터 · Alpaca 실시간 시세</span>
<span class="k" style="margin-left:auto" id="clk">{now}</span></div>
<div class="grid">
 <div class="colL">
  <h2>회전 · 매매 기록</h2>{TRADES}
  <h2>자산 곡선</h2>{CURVE}
  <h2>유니버스 모멘텀 랭킹</h2>{RANK}
  <h2>섹터 현황</h2>{SECTORS}
 </div>
 <div class="colR">
  <h2>계좌 현황</h2>{ACC}
  <h2>보유 포트폴리오 ({len(st['pos'])}/{TOP})</h2>{HOLD}
  <div class="status">{STATUS}</div>
  <h2>제어</h2>{CONTROL.replace('__BC__', '#2FBF71' if cfg.get('enabled', True) else '#E5484D').replace('__BT__', '실행중' if cfg.get('enabled', True) else '정지됨').replace('__VTOP__', str(v_top)).replace('__OREB__', opts([10, 15, 20, 30, 60], v_reb)).replace('__VST__', str(v_stop)).replace('__VTK__', str(v_take)).replace('__VTR__', str(v_trail))}
 </div>
</div>
<div class="k" style="margin:14px 0 30px">모의 계좌 · 열람/제어 전용 — 실제 주문은 발생하지 않습니다</div>"""

with open(os.path.join(HERE, "index.html"), "w", encoding="utf-8") as f:
    f.write(page)
print(f"[대시보드] 생성 완료 — ${total:,.0f} · 보유 {len(st['pos'])}/{TOP} · 상태 {'실행중' if cfg.get('enabled', True) else '정지됨'}")
