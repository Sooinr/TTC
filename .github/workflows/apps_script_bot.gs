/**
 * 주식 포트폴리오 로테이션 봇 — Google Apps Script 버전
 * =====================================================
 * 브라우저 앱·server_bot.py와 같은 전략: 모멘텀+섹터 상위 4종목 총액 분할 보유,
 * 유연 회전, 목표 익절·트레일링·손절, 섹터당 최대 2종목, 시장 약세 시 방어 모드.
 *
 * 특징:
 *  - 시세를 야후 파이낸스에서 직접 조회 (Apps Script는 서버 실행이라 프록시·API 키 불필요)
 *  - 계좌 상태는 이 스프레드시트의 'State' 시트, 매매 기록은 'Trades' 시트에 저장
 *  - 5분마다 트리거 실행 → 컴퓨터를 꺼도 구글 클라우드에서 계속 동작
 *  - 미국 정규장(ET 기준) 밖에는 자동 대기
 *
 * 설치(3단계):
 *  1) sheets.new 로 시트 생성 → 확장 프로그램 → Apps Script → 아래 코드 전체를 붙여넣기
 *  2) 함수 선택에서 install 을 골라 실행 → 권한 승인(경고 화면: 고급 → 이동 → 허용)
 *  3) 끝. 5분마다 자동 실행됩니다. 상태는 'State', 기록은 'Trades' 시트에서 확인
 * 중단: uninstall 실행(트리거 삭제)
 */

// ──────────────── 설정 ────────────────
var TOP_N = 4;            // 동시 보유 종목 수 (최적화 1위 세팅)
var REBAL_MIN = 20;       // 리밸런스 주기(분)
var STOP = -0.08;         // 개별 손절
var TAKE = 0.08;          // 목표 익절
var TRAIL = 0.05;         // 트레일링 (고점 대비 하락)
var FEE = 0.0005;         // 수수료
var START_CASH = 10000000;
var MAIL_TO = '';
var ALPACA_ID = 'PK3JJRZMJZJ23S4UTIWAV2XF7W';  // Alpaca 실시간 시세 키 (설정 시트에서도 교체 가능)
var ALPACA_SEC = 'Bn3LnKguLZg9zhxVtPNW8XmCwthYzXniBkFaTrmdgQWJ';
var ACTION_KEY = 'RC-GYNE8S8K'; // 원격 조작(시작)키 — 대시보드 URL 뒤 ?key=이값&action=rebalance 로 즉시 리밸런스. '설정' 시트에서도 바꿀 수 있음
var HIST_BARS = 150;      // 보관할 일봉 수

var SECTOR = {
  AAPL:'빅테크', MSFT:'빅테크', AMZN:'빅테크', GOOGL:'빅테크', META:'빅테크',
  NVDA:'반도체·AI', AVGO:'반도체·AI', AMD:'반도체·AI', MU:'반도체·AI', QCOM:'반도체·AI',
  PLTR:'AI·소프트웨어', COIN:'가상자산·핀테크', HOOD:'가상자산·핀테크',
  TSLA:'전기차·모빌리티', UBER:'전기차·모빌리티', XOM:'에너지', JPM:'금융',
  LLY:'의료·제약', UNH:'의료·제약', COST:'소비·유통', MCD:'소비·유통',
  NFLX:'엔터테인먼트', BA:'산업·방산', CAT:'산업·방산'
};
var CODES = Object.keys(SECTOR);
var NAME = {
  AAPL:'애플', MSFT:'마이크로소프트', AMZN:'아마존', GOOGL:'알파벳', META:'메타',
  NVDA:'엔비디아', AVGO:'브로드컴', AMD:'AMD', MU:'마이크론', QCOM:'퀄컴',
  PLTR:'팔란티어', COIN:'코인베이스', HOOD:'로빈후드',
  TSLA:'테슬라', UBER:'우버', XOM:'엑손모빌', JPM:'JP모건',
  LLY:'일라일리', UNH:'유나이티드헬스', COST:'코스트코', MCD:'맥도날드',
  NFLX:'넷플릭스', BA:'보잉', CAT:'캐터필러'
};

// ──────────────── 설치/제거 ────────────────
function install() {
  ensureSheets();
  ScriptApp.getProjectTriggers().forEach(function(t){ ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('tick').timeBased().everyMinutes(5).create();
  Logger.log('봇 설치 완료 — 5분마다 자동 실행됩니다.');
  tick();   // 즉시 1회 실행해 상태 시트 생성
}
function uninstall() {
  ScriptApp.getProjectTriggers().forEach(function(t){ ScriptApp.deleteTrigger(t); });
  Logger.log('봇을 중지했습니다.');
}

// ──────────────── 시트 (상태 저장소) ────────────────
function ss(){
  var b = SpreadsheetApp.getActive();
  if (b) return b;
  // 시트 없이 script.google.com에서 만든 독립 실행형: 전용 시트를 자동 생성해 연결
  var props = PropertiesService.getScriptProperties();
  var id = props.getProperty('SS_ID');
  if (id) {
    try { return SpreadsheetApp.openById(id); } catch (e) { /* 시트가 삭제됨 → 재생성 */ }
  }
  b = SpreadsheetApp.create('로테이션 봇 계좌');
  props.setProperty('SS_ID', b.getId());
  Logger.log('전용 시트를 새로 만들었습니다: 로테이션 봇 계좌');
  return b;
}
function ensureSheets(){
  var book = ss();
  if (!book.getSheetByName('State')) book.insertSheet('State');
  if (!book.getSheetByName('Trades')){
    var t = book.insertSheet('Trades');
    t.appendRow(['시각','구분','종목','수량','가격','사유','손익($)']);
  }
  if (!book.getSheetByName('설정')){
    var c = book.insertSheet('설정');
    c.getRange(1, 1, 1, 3).setValues([['설정', '값', '설명']]);
    c.getRange(2, 1, 7, 3).setValues([
      ['보유 종목 수',   4,  '동시에 몇 종목을 총액 균등 분할해 보유할지 (1~10, 4~6 권장)'],
      ['손절 %',        8,  '개별 종목이 이 손실에 도달하면 매도'],
      ['익절 %',        8,  '이 수익에 도달하면 익절'],
      ['트레일링 %',     5,  '고점 대비 이만큼 빠지면 익절 (수익 중일 때만)'],
      ['리밸런스 주기(분)', 20, '정기 재배분 주기 (최소 5분 권장)'],
      ['알림 이메일',     '', '넣으면 리밸런스 때 이 주소로 메일 발송 (빈칸이면 끔)'],
      ['원격 조작 키',    '', '넣으면 대시보드 URL에 ?key=값&action=rebalance 로 즉시 리밸런스 (비우면 코드에 저장된 시작키 사용)'],
      ['알파카 키 ID',  '', '미국 실시간 시세용 Alpaca 키 (비우면 코드에 저장된 키 사용)'],
      ['알파카 시크릿',  '', '위 키와 한 쌍 — 시트 대시보드에는 항상 가려져 표시됨']
    ]);
    c.setColumnWidth(3, 420);
  }
  if (!book.getSheetByName('Hist')){
    var h = book.insertSheet('Hist');
    h.appendRow(['코드','종목명','최근 일봉(쉼표 구분)']);
    CODES.forEach(function(c){ h.appendRow([c, SECTOR[c], '']); });
  }
}
function applySettings(){
  var sh = ss().getSheetByName('설정');
  if (!sh || sh.getLastRow() < 2) return;
  var vals = sh.getRange(2, 1, sh.getLastRow()-1, 3).getValues();
  vals.forEach(function(r){
    var k = String(r[0]).trim(), v = r[1];
    if (k === '' || v === '' || v == null) return;
    var n = Number(v);
    if (k === '보유 종목 수'){ if (n >= 1 && n <= 10) TOP_N = Math.round(n); }
    else if (k === '손절 %'){ var s1 = Math.abs(n); if (s1 > 0) STOP = -(s1 < 1 ? s1*100 : s1)/100; }
    else if (k === '익절 %'){ var s2 = Math.abs(n); if (s2 > 0) TAKE = (s2 < 1 ? s2*100 : s2)/100; }
    else if (k === '트레일링 %'){ var s3 = Math.abs(n); if (s3 > 0) TRAIL = (s3 < 1 ? s3*100 : s3)/100; }
    else if (k === '리밸런스 주기(분)'){ if (n >= 5) REBAL_MIN = Math.round(n); }
    else if (k === '알림 이메일'){ MAIL_TO = String(v).trim(); }
    else if (k === '원격 조작 키'){ ACTION_KEY = String(v).trim(); }
    else if (k === '알파카 키 ID'){ ALPACA_ID = String(v).trim(); }
    else if (k === '알파카 시크릿'){ ALPACA_SEC = String(v).trim(); }
  });
}
function getState(){
  var v = ss().getSheetByName('State').getRange('A1').getValue();
  if (v) return JSON.parse(v);
  return {
    cash: START_CASH, pos: {}, realized: 0, wins: 0, closes: 0,
    trades: 0, rebalCount: 0, lastRebal: 0, peaks: {}, lastBarDate: ''
  };
}
function saveState(st){
  ss().getSheetByName('State').getRange('A1').setValue(JSON.stringify(st));
}
function getHist(){
  var sh = ss().getSheetByName('Hist'), out = {};
  var last = sh.getLastRow();
  if (last < 2) { CODES.forEach(function(c){ out[c] = []; }); return out; }
  var vals = sh.getRange(2, 1, last-1, 2).getValues();
  vals.forEach(function(r){ out[r[0]] = String(r[1]).length ? String(r[1]).split(',').map(Number) : []; });
  CODES.forEach(function(c){ if (!out[c]) out[c] = []; });
  return out;
}
function saveHist(hist){
  var sh = ss().getSheetByName('Hist');
  var rows = CODES.map(function(c){ return [c, hist[c].join(',')]; });
  sh.getRange(2, 1, Math.max(sh.getLastRow()-1, rows.length), 2).clearContent();
  sh.getRange(2, 1, rows.length, 2).setValues(rows);
}
function logTrade(side, code, qty, px, reason, pnl){
  ss().getSheetByName('Trades').appendRow(
    [new Date(), side, code, Math.round(qty), Math.round(px*100)/100, reason,
     pnl == null ? '' : Math.round(pnl)]);
  Logger.log(side + ' ' + code + ' ' + Math.round(qty) + '주 @ ' + px.toFixed(2) + ' — ' + reason);
}

// ──────────────── 시세 (Alpaca 실시간 우선 → 실패 시 야후 대체) ────────────────
function fetchQuotes(){
  if (ALPACA_ID && ALPACA_SEC){
    try { return fetchQuotesAlpaca(); }
    catch(e){ Logger.log('Alpaca 시세 실패 — 야후로 대체: ' + e); }
  }
  return fetchQuotesYahoo();
}
function fetchQuotesAlpaca(){
  var url = 'https://data.alpaca.markets/v2/stocks/snapshots?symbols=' +
            encodeURIComponent(CODES.join(',')) + '&feed=iex';
  var res = UrlFetchApp.fetch(url, {muteHttpExceptions:true, headers:{
    'APCA-API-KEY-ID': ALPACA_ID, 'APCA-API-SECRET-KEY': ALPACA_SEC }});
  if (res.getResponseCode() !== 200) throw new Error('HTTP ' + res.getResponseCode());
  var j = JSON.parse(res.getContentText()), out = {};
  for (var sym in j){
    var sn = j[sym];
    if (!sn) continue;
    var p = (sn.latestTrade && sn.latestTrade.p) ||
            (sn.prevDailyBar && sn.prevDailyBar.c) || (sn.dailyBar && sn.dailyBar.c);
    if (p) out[sym] = p;
  }
  return out;
}
function fetchQuotesYahoo(){
  var url = 'https://query1.finance.yahoo.com/v8/finance/spark?symbols=' + CODES.join(',') +
            '&range=1d&interval=5m&_=' + Date.now();
  var res = UrlFetchApp.fetch(url, {muteHttpExceptions:true});
  var j = JSON.parse(res.getContentText());
  var out = {};
  var root = j && j.spark && j.spark.result ? j.spark.result : [];
  for (var i = 0; i < root.length; i++){
    var r0 = root[i].response && r0.response ? r0.response[0] : null;
    var meta = r0 && r0.meta;
    if (meta && typeof meta.regularMarketPrice === 'number')
      out[String(meta.symbol).toUpperCase()] = meta.regularMarketPrice;
  }
  return out;
}

// 첫 실행 시 최근 1년 일봉 시딩 (모멘텀 계산용 — 요청 1회로 전 종목)
function seedHistory(hist){
  var need = CODES.filter(function(c){ return !hist[c] || hist[c].length < 60; });
  if (!need.length) return;
  var url = 'https://query1.finance.yahoo.com/v8/finance/spark?symbols=' + need.join(',') +
            '&range=1y&interval=1d&_=' + Date.now();
  var res = UrlFetchApp.fetch(url, {muteHttpExceptions:true});
  var j = JSON.parse(res.getContentText());
  var root = j && j.spark && j.spark.result ? j.spark.result : [];
  for (var i = 0; i < root.length; i++){
    var r0 = root[i].response && root[i].response[0];
    var q = r0 && r0.indicators && r0.indicators.quote && r0.indicators.quote[0];
    var sym = r0 && r0.meta ? String(r0.meta.symbol).toUpperCase() : '';
    if (!q || !q.close || !sym) continue;
    var arr = [];
    for (var k = 0; k < q.close.length; k++) if (typeof q.close[k] === 'number') arr.push(q.close[k]);
    if (arr.length >= 61) hist[sym] = arr.slice(0, -1).slice(-HIST_BARS);  // 오늘(부분봉) 제외 후 저장
  }
  Logger.log('히스토리 시딩 완료 — ' + need.length + '종목');
}

// ──────────────── 전략 지표 ────────────────
function sma(arr, n){ if (arr.length < n) return NaN; var s=0; for (var i=arr.length-n;i<arr.length;i++) s+=arr[i]; return s/n; }
function mom(c){ if (c.length < 60) return NaN; return 0.5*(c[c.length-1]/c[c.length-20]-1) + 0.5*(c[c.length-1]/c[c.length-60]-1); }
function r20(c){ return c.length >= 21 ? c[c.length-1]/c[c.length-21]-1 : NaN; }
function sectorHeat(hist, sec){
  var s=0, n=0;
  for (var i=0;i<CODES.length;i++){
    var c = CODES[i];
    if (SECTOR[c] !== sec) continue;
    var m = mom(hist[c]);
    if (!isNaN(m)){ s+=m; n++; }
  }
  return n ? s/n : 0;
}
function marketOpen(){
  var et = Utilities.formatDate(new Date(), 'America/New_York', 'u HH:mm');
  var parts = et.split(' ');
  var wd = Number(parts[0]), h = Number(parts[1].split(':')[0]), m = Number(parts[1].split(':')[1]);
  if (wd === 6 || wd === 7) return false;           // 토/일
  return (h > 9 || (h === 9 && m >= 30)) && h < 16;
}

// ──────────────── 매매헬퍼 ────────────────
function equity(st, prices){
  var inv = 0;
  for (var c in st.pos) if (prices[c]) inv += st.pos[c].qty * prices[c];
  return st.cash + inv;
}
function closePos(st, prices, code, reason){
  var p = st.pos[code], px = prices[code];
  if (!p || !px) return false;
  var gross = p.qty * px * (1 - FEE);
  var pnl = gross - p.qty * p.avg;
  st.cash += gross; st.realized += pnl;
  st.wins += pnl > 0 ? 1 : 0; st.closes++; st.trades++;
  delete st.pos[code];
  logTrade(pnl <= 0 && reason.indexOf('손절') >= 0 ? '손절' : '매도', code, p.qty, px, reason, pnl);
  return true;
}
function openPos(st, prices, code, qty, reason){
  var px = prices[code];
  var cost = qty * px * (1 + FEE);
  if (qty < 1 || cost > st.cash) return false;
  st.cash -= cost;
  st.pos[code] = {qty: qty, avg: cost/qty, peak: px};
  st.trades++;
  logTrade('매수', code, qty, px, reason, null);
  return true;
}
function ranked(hist, prices, topN){
  var out = [], cnt = {};
  CODES.forEach(function(c){
    var m = mom(hist[c]);
    if (!prices[c] || isNaN(m) || m <= 0) return;
    if (hist[c][hist[c].length-1] <= sma(hist[c], 100)) return;
    out.push({code:c, score: 0.6*m + 0.4*sectorHeat(hist, SECTOR[c])});
  });
  out.sort(function(a,b){ return b.score - a.score; });
  var res = [];
  for (var i=0;i<out.length;i++){
    var s = SECTOR[out[i].code];
    if ((cnt[s]||0) >= 2) continue;
    cnt[s] = (cnt[s]||0)+1;
    res.push(out[i]);
    if (res.length >= topN) break;
  }
  return res;
}

// ──────────────── 메인 틱 (트리거가 5분마다 호출) ────────────────
function tick(force){
  if (!force && !marketOpen()) return;
  ensureSheets();
  applySettings();
  var st = getState();
  var hist = getHist();
  try { seedHistory(hist); } catch(e){ Logger.log('히스토리 시딩 실패: ' + e); }
  var prices;
  try { prices = fetchQuotes(); } catch(e){ Logger.log('시세 조회 실패: ' + e); saveState(st); return; }
  if (Object.keys(prices).length < 5){ saveState(st); return; }

  // 오늘 봉 갱신 / 새 거래일 처리
  var today = Utilities.formatDate(new Date(), 'America/New_York', 'yyyy-MM-dd');
  CODES.forEach(function(c){
    if (!prices[c] || !hist[c].length) return;
    if (st.lastBarDate !== today){
      hist[c].push(prices[c]);
      if (hist[c].length > HIST_BARS) hist[c].shift();
    } else {
      hist[c][hist[c].length-1] = prices[c];
    }
  });
  st.lastBarDate = today;

  // 1) 손절·익절·트레일링
  for (var c in st.pos){
    if (!prices[c]) continue;
    var p = st.pos[c];
    p.peak = Math.max(p.peak || p.avg, prices[c]);
    var ret = prices[c]/p.avg - 1;
    var m20 = r20(hist[c]), m60 = mom(hist[c]);
    if (ret >= TAKE) closePos(st, prices, c, '목표 익절 +' + (ret*100).toFixed(1) + '% — 수익 확정');
    else if (ret > 0 && prices[c] <= p.peak*(1-TRAIL)) closePos(st, prices, c, '트레일링 익절 — 고점 대비 ' + ((prices[c]/p.peak-1)*100).toFixed(1) + '%');
    else if (!isNaN(m20) && m20 < 0 && m60 < 0) closePos(st, prices, c, '모멘텀 소멸 이탈 (' + (m20*100).toFixed(1) + '%)');
    else if (ret <= STOP) closePos(st, prices, c, '손절 ' + (ret*100).toFixed(1) + '%');
  }

  // 2) 유연 회전 + 정기 리밸런스
  var nowMs = Date.now();
  if (force || nowMs - (st.lastRebal || 0) >= REBAL_MIN*60000){
    st.lastRebal = nowMs;
    st.rebalCount++;
    st.flexSwaps = 0;
    var picks = ranked(hist, prices, TOP_N);
    var codes = picks.map(function(x){ return x.code; });
    for (var c2 in st.pos) if (codes.indexOf(c2) < 0) closePos(st, prices, c2, '리밸런스 #' + st.rebalCount + ' 랭킹 이탈');
    var total = equity(st, prices);
    picks.forEach(function(x){
      openPos(st, prices, x.code, (total/picks.length)/prices[x.code], '리밸런스 균등 배분 #' + st.rebalCount);
    });
    Logger.log('리밸런스 #' + st.rebalCount + ': ' + codes.join(', '));
    if (MAIL_TO){
      MailApp.sendEmail(MAIL_TO, '[로테이션 봇] 리밸런스 #' + st.rebalCount,
        '새 보유: ' + codes.join(', ') + '\n총자산: $' + Math.round(total).toLocaleString());
    }
  } else if ((st.flexSwaps || 0) < 2){
    // 유연 회전: 최약 보유 ↔ 최강 후보
    var held = [], c3;
    for (c3 in st.pos){
      var sc = 0.6*mom(hist[c3]) + 0.4*sectorHeat(hist, SECTOR[c3]);
      held.push({code:c3, score:sc});
    }
    var cand = ranked(hist, prices, TOP_N + 2).filter(function(x){ return !st.pos[x.code]; })[0];
    if (cand && held.length >= 2){
      held.sort(function(a,b){ return a.score - b.score; });
      var worst = held[0];
      var gap = cand.score - worst.score;
      if (gap > 0.002){
        closePos(st, prices, worst.code, '교체 — 예상 수익 격차 +' + (gap*100).toFixed(1) + '%p');
        openPos(st, prices, cand.code, (equity(st, prices)/TOP_N)/prices[cand.code], '약세 보유 → 강세 후보');
        st.flexSwaps = (st.flexSwaps || 0) + 1;
      }
    }
  }

  // 3) 상태 요약 저장
  st.summary = {
    time: new Date().toISOString(),
    equity: Math.round(equity(st, prices)),
    positions: Object.keys(st.pos).map(function(k){ return k + '@' + Math.round(prices[k]); }).join(', ')
  };
  st.equityHist = (st.equityHist || []).concat([[Date.now(), Math.round(equity(st, prices))]]).slice(-240);
  saveState(st);
  saveHist(hist);
}


// ──────────────── 웹 대시보드 (URL로 어디서나 열람) ────────────────
/* 어디서나 보기: 편집기 우측 상단 [배포] → [새 배포] → 톱니바퀴 → [웹 앱]
   실행: 나 / 액세스 권한: 모든 사용자 로 배포 → 발급된 URL을 즐겨찾기·폰 홈화면에 추가.
   · '모든 사용자'면 구글 로그인 없이 링크만으로 어디서나 열람 (URL 자체가 비밀번호처럼 작동 — 유출 주의)
   · 원격 조작: ACTION_KEY 설정 후 URL 뒤에 ?key=ACTION_KEY값&action=rebalance
   · 코드 수정 후엔 [배포 관리] → 연필 → 새 버전 배포를 해야 반영됩니다. */
function doGet(e){
  e = e || {}; var p = e.parameter || {}; var msg = '';
  if (p.action){
    if (!ACTION_KEY) msg = '원격 조작 꺼짐 — 설정 시트의 원격 조작 키 필요';
    else if (p.key !== ACTION_KEY) msg = '키가 올바르지 않습니다';
    else if (p.action === 'rebalance'){ tick(true); msg = '강제 리밸런스 실행 완료'; }
    else msg = '알 수 없는 동작: ' + p.action;
  }
  ensureSheets();
  applySettings();
  var st = getState();
  var hist = getHist();
  var live = {};
  try { live = fetchQuotes(); } catch(err){ Logger.log('대시보드 시세 실패: ' + err); }
  function px(c){ return live[c] || (hist[c] && hist[c].length ? hist[c][hist[c].length-1] : 0); }
  var total = equity(st, live);
  var ret = total/START_CASH - 1;
  var inv = 0; for (var c in st.pos) if (px(c)) inv += st.pos[c].qty * px(c);
  var invPct = total ? Math.round(inv/total*100) : 0;
  var upl = 0; for (var c2 in st.pos) if (px(c2)) upl += st.pos[c2].qty * (px(c2) - st.pos[c2].avg);

  // MDD 계산
  var mdd = 0, peak = 0, eh = st.equityHist || [];
  for (var i = 0; i < eh.length; i++){
    peak = Math.max(peak, eh[i][1]);
    if (peak) mdd = Math.min(mdd, eh[i][1]/peak - 1);
  }

  // 보유 테이블
  var posRows = '', heldSec = {};
  Object.keys(st.pos).forEach(function(c){
    var cur = px(c); heldSec[SECTOR[c]] = 1;
    var r = cur && st.pos[c].avg ? (cur/st.pos[c].avg - 1)*100 : 0;
    posRows += '<tr><td><b>' + c + '</b> ' + (NAME[c]||'') + '</td><td>' + SECTOR[c] + '</td><td>' +
      Math.round(st.pos[c].qty).toLocaleString() + '</td><td>' + st.pos[c].avg.toFixed(2) + '</td><td>' +
      (cur ? cur.toFixed(2) : '-') + '</td><td style="color:' + (r >= 0 ? '#2FBF71' : '#E5484D') + '">' +
      (r >= 0 ? '▲ +' : '▼ ') + r.toFixed(1) + '%</td></tr>';
  }) || '';
  if (!posRows) posRows = '<tr><td colspan="6">보유 종목 없음 — 다음 리밸런스에서 편입 시도</td></tr>';

  // 유니버스 모멘텀 랭킹 (24개 전체)
  var rank = [];
  CODES.forEach(function(c){
    var m = mom(hist[c]);
    if (!isNaN(m)) rank.push({c:c, v:m});
    else rank.push({c:c, v:null});
  });
  rank.sort(function(a,b){ return (b.v == null ? -9 : b.v) - (a.v == null ? -9 : a.v); });
  var maxv = 0.05;
  rank.forEach(function(r){ if (r.v != null) maxv = Math.max(maxv, Math.abs(r.v)); });
  var rankRows = '';
  rank.forEach(function(r, i){
    var held = !!st.pos[r.c];
    var w = r.v == null ? 0 : Math.min(Math.abs(r.v)/maxv*100, 100);
    var col = r.v == null ? '#4A5568' : (r.v >= 0 ? (held ? '#F5B942' : '#2FBF71') : '#E5484D');
    rankRows += '<div class="rk' + (i < 4 ? ' top' : '') + (held ? ' held' : '') + '">' +
      '<span class="rn">' + (i+1) + '</span><span class="rc">' + (NAME[r.c]||r.c) +
      (held ? ' <b style="color:#F5B942">●</b>' : '') + '</span>' +
      '<span class="rb"><i style="width:' + w + '%;background:' + col + '"></i></span>' +
      '<span class="rv" style="color:' + col + '">' + (r.v == null ? '준비중' : (r.v >= 0 ? '+' : '') + (r.v*100).toFixed(1) + '%') + '</span></div>';
  });

  // 섹터 카드
  var secs = {}, seen = {};
  CODES.forEach(function(c){
    if (seen[SECTOR[c]]) return; seen[SECTOR[c]] = 1;
    secs[SECTOR[c]] = sectorHeat(hist, SECTOR[c]);
  });
  var best = null, worst = null;
  for (var s in secs){
    if (!best || secs[s] > secs[best]) best = s;
    if (!worst || secs[s] < secs[worst]) worst = s;
  }
  var secCards = '';
  Object.keys(secs).sort(function(a,b){ return secs[b] - secs[a]; }).forEach(function(s){
    var v = secs[s], held = !!heldSec[s];
    var col = v >= 0 ? '#2FBF71' : '#E5484D';
    secCards += '<div class="sc' + (held ? ' hsec' : '') + '"><div class="sn">' + s +
      (held ? ' <b style="color:#F5B942">보유중</b>' : '') + '</div>' +
      '<div class="sv" style="color:' + col + '">' + (v >= 0 ? '+' : '') + (v*100).toFixed(1) + '%</div></div>';
  });

  // 자산 곡선 SVG
  var svg = '';
  if (eh.length >= 2){
    var lo = Math.min.apply(null, eh.map(function(x){ return x[1]; }));
    var hi = Math.max.apply(null, eh.map(function(x){ return x[1]; }));
    if (hi - lo < 1) hi = lo + 1;
    var pts = eh.map(function(x, i){
      return (i/(eh.length-1)*300).toFixed(1) + ',' + (56 - (x[1]-lo)/(hi-lo)*50).toFixed(1);
    }).join(' ');
    svg = '<svg viewBox="0 0 300 60" preserveAspectRatio="none" class="chart">' +
      '<polyline points="' + pts + '" fill="none" stroke="' +
      (eh[eh.length-1][1] >= eh[0][1] ? '#2FBF71' : '#E5484D') + '" stroke-width="1.6"/></svg>';
  } else svg = '<div class="chart" style="color:#93A0B0;text-align:center;padding-top:18px">장중 실행 데이터가 쌓이면 곡선이 그려집니다</div>';

  // 설정 표시 + 원격 버튼
  var setRows = '';
  var cfg = ss().getSheetByName('설정');
  if (cfg && cfg.getLastRow() > 1){
    var vals = cfg.getRange(2, 1, cfg.getLastRow()-1, 2).getValues();
    vals.forEach(function(r){
      var lbl = String(r[0]);
      var disp;
      if (r[1] === '') disp = (lbl.indexOf('알파카') >= 0 ? '내장 키 사용' : '기본값');
      else disp = (lbl.indexOf('시크릿') >= 0 || lbl.indexOf('키') >= 0 ? '•••• 설정됨' : r[1]);
      setRows += '<tr><td>' + r[0] + '</td><td><b>' + disp + '</b></td></tr>';
    });
  }
  var base = '';
  try { base = ScriptApp.getService().getUrl(); } catch(err){}
  var btn = '';
  if (ACTION_KEY && base) btn = '<a class="btn" href="' + base + '?key=' + encodeURIComponent(ACTION_KEY) +
    '&action=rebalance">⚡ 지금 리밸런스 실행</a>';

  // 봇 상태 박스
  var nextMin = st.lastRebal ? Math.max(0, Math.round(((st.lastRebal || 0) + REBAL_MIN*60000 - Date.now())/60000)) : 0;
  var statusBox =
    '<div class="status"><div class="st1">' + (s_time(st)) +
    (st.lastRebal ? ' · 다음 리밸런스 약 ' + nextMin + '분 후' : ' · 다음 리밸런스 대기중') + '</div>' +
    '<div class="st2">보유 ' + Object.keys(st.pos).length + '/' + TOP_N + '종목' +
    (best ? ' · 최강 섹터 ' + best + ' ' + (secs[best]*100 >= 0 ? '+' : '') + (secs[best]*100).toFixed(1) + '%' : '') +
    (worst ? ' / 최약 ' + worst + ' ' + (secs[worst]*100).toFixed(1) + '%' : '') + '</div>' +
    '<div class="st2">개별 손절 ' + Math.round(Math.abs(STOP)*100) + '% · 익절 ' + Math.round(TAKE*100) +
    '% · 트레일링 ' + Math.round(TRAIL*100) + '% · 리밸런스 ' + REBAL_MIN + '분 주기 — 시트 [설정] 탭에서 변경 가능</div></div>';

  var html =
    '<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<meta http-equiv="refresh" content="60">' +
    '<style>body{font-family:sans-serif;background:#0E1216;color:#E9EDF3;padding:14px;margin:0}' +
    '.wrap{max-width:1080px;margin:0 auto}' +
    '.row{display:flex;flex-wrap:wrap;gap:12px}.col{flex:1;min-width:300px}' +
    'table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}' +
    'td,th{padding:5px 6px;border-bottom:1px solid #29323E;text-align:left}' +
    '.card{background:#161C24;border:1px solid #29323E;border-radius:10px;padding:12px;margin-top:12px}' +
    '.cards{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}' +
    '.kpi{flex:1;min-width:110px;background:#161C24;border:1px solid #29323E;border-radius:8px;padding:8px 10px}' +
    '.kv{font-size:15px;font-weight:700;margin-top:2px}' +
    '.k{color:#93A0B0;font-size:11px} h2{font-size:14px;color:#F5B942;margin:18px 0 2px}' +
    '.big{font-size:32px;font-weight:700;color:' + (ret >= 0 ? '#2FBF71' : '#E5484D') + ';margin:2px 0}' +
    '.rk{display:flex;align-items:center;gap:8px;font-size:12.5px;padding:2.5px 0}' +
    '.rn{width:18px;color:#93A0B0;text-align:right}.rc{width:96px}' +
    '.rb{flex:1;background:#1D2530;border-radius:3px;height:12px;overflow:hidden}' +
    '.rb i{display:block;height:12px;border-radius:3px}' +
    '.rv{width:56px;text-align:right;font-weight:600}' +
    '.rk.top .rc{color:#F5B942}.rk.held{background:#22271a;border-radius:4px}' +
    '.scards{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}' +
    '.sc{flex:1;min-width:118px;background:#161C24;border:1px solid #29323E;border-radius:8px;padding:8px}' +
    '.hsec{border-color:#F5B942}.sn{font-size:11.5px;color:#93A0B0}.sv{font-size:16px;font-weight:700;margin-top:3px}' +
    '.chart{width:100%;height:120px;display:block;margin-top:6px}' +
    '.status{border:1px solid #F5B942;border-radius:10px;padding:10px 12px;margin-top:12px;font-size:12.5px}' +
    '.st1{color:#F5B942;font-weight:600}.st2{color:#B8C2CF;margin-top:3px}' +
    '.btn{display:inline-block;background:#F5B942;color:#0E1216;font-weight:700;padding:9px 16px;border-radius:8px;text-decoration:none;margin-top:10px}' +
    '.msg{color:#F5B942;font-size:13px}</style>' +
    '<div class="wrap"><h2 style="margin-top:0">로테이션 봇 대시보드</h2>' +
    (msg ? '<div class="msg">' + msg + '</div>' : '') +
    '<div class="k">마지막 실행: ' + (s_time(st)) + ' · 자동 새로고침 60초</div>' +
    '<div class="big">$' + total.toLocaleString() + '</div>' +
    '<div class="k">수익률 ' + (ret >= 0 ? '+' : '') + (ret*100).toFixed(1) + '% · 시작자본 $' + START_CASH.toLocaleString() + '</div>' +
    '<div class="cards">' +
      kpi('현금', '$' + Math.round(st.cash).toLocaleString()) +
      kpi('투자중', invPct + '%') +
      kpi('평가손익', '$' + Math.round(upl).toLocaleString()) +
      kpi('실현손익', '$' + Math.round(st.realized).toLocaleString()) +
      kpi('최대낙폭', (mdd*100).toFixed(1) + '%') +
      kpi('리밸런스', st.rebalCount + '회') +
      kpi('매매', st.trades + '건') +
      kpi('승률', (st.closes ? Math.round(st.wins/st.closes*100) : 0) + '%') +
    '</div>' +
    statusBox + btn +
    '<h2>보유 포트폴리오 (' + Object.keys(st.pos).length + '/' + TOP_N + ')</h2>' +
    '<div class="card"><table><tr><th>종목</th><th>섹터</th><th>수량</th><th>평단$</th><th>현재가</th><th>손익</th></tr>' +
      posRows + '</table></div>' +
    '<div class="row"><div class="col">' +
      '<h2>자산 곡선</h2><div class="card">' + svg + '</div>' +
      '<h2>전략 설정 (시트 [설정] 탭에서 수정)</h2><div class="card">' +
      '<table>' + setRows + '</table></div>' +
    '</div><div class="col">' +
      '<h2>유니버스 모멘텀 랭킹 (24종목)</h2><div class="card">' + rankRows +
      '<div class="k" style="margin-top:6px">노란 = 현재 보유 · 점수 = 개별 모멘텀 60% + 섹터 열기 40%</div></div>' +
    '</div></div>' +
    '<h2>섹터 현황' + (best ? ' — 최강 <span style="color:#F5B942">' + best + '</span>' : '') + '</h2>' +
    '<div class="scards">' + secCards + '</div>' +
    '<h2>최근 매매</h2>' +
    '<div class="card">' + tradeTable() + '</div>' +
    '<div class="k" style="margin:14px 0 30px">미국 정규장(뉴욕 09:30~16:00)에만 매매 · 이 대시보드는 열람 전용 — 주문은 모의 계좌에서만 실행됩니다</div></div>';
  return HtmlService.createHtmlOutput(html).setTitle('로테이션 봇');
}
function s_time(st){
  var t = st.summary && st.summary.time;
  return t ? t.replace('T', ' ').replace('Z', ' UTC').split('.')[0] : '아직 실행 전 — install 실행 또는 장중 5분 대기';
}
function kpi(k, v){
  return '<div class="kpi"><div class="k">' + k + '</div><div class="kv">' + v + '</div></div>';
}
function tradeTable(){
  var t = ss().getSheetByName('Trades');
  var last = Math.min(t.getLastRow()-1, 15);
  var rows = last > 0 ? t.getRange(t.getLastRow()-last+1, 1, last, 7).getValues().reverse() : [];
  var tr = rows.map(function(r){
    return '<tr><td>' + Utilities.formatDate(new Date(r[0]), 'Asia/Seoul', 'MM-dd HH:mm') +
      '</td><td>' + r[1] + '</td><td>' + (NAME[r[2]] ? r[2] + ' ' + NAME[r[2]] : r[2]) +
      '</td><td>' + Number(r[4]).toFixed(2) + '</td><td>' + r[5] + '</td><td>' +
      (r[6] === '' ? '-' : r[6]) + '</td></tr>';
  }).join('');
  return '<table><tr><th>시각</th><th>구분</th><th>종목</th><th>가격</th><th>사유</th><th>손익$</th></tr>' +
    (tr || '<tr><td colspan="6">아직 기록 없음</td></tr>') + '</table>';
}
