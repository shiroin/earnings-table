/**
 * Financials（各社の業績シート）が更新されたら、すぐに Web アプリ（earnings-table）の企業マスター同期を起動する。
 *
 * 5分ごとに Google ドライブで「前回から更新されたスプレッドシート」を探し、ウォッチリストの Financials 列にある
 * ファイルだけを拾って、その会社だけの同期（sync.yml の only）を GitHub Actions で起動する。
 * 手作業での修正も、自動更新（earnings-autoupdate）による書き込みも、どちらも拾える。
 *
 * 準備:
 *  1. script.google.com で新しいプロジェクトを作り（既存の github_trigger.gs のプロジェクトに追加しても可）、このファイルを貼る
 *  2. プロジェクトの設定 → スクリプト プロパティに EARNINGS_TABLE_TOKEN を追加
 *     （GitHub の fine-grained token。リポジトリ shiroin/earnings-table だけ、権限は Actions: Read and write）
 *  3. installFinancialsWatch() を一度実行（Google ドライブとスプレッドシートを読む許可を求められるので許可）
 */
const FW_REPO = 'shiroin/earnings-table';
const FW_WORKFLOW = 'sync.yml';
const FW_MASTER_ID = '1NMjMQ6JeUuRRyy4X7m79YCTnklUgkdE76407QYY5ahQ';
const FW_SHEET = 'global_company_watch_300';

function installFinancialsWatch() {
  uninstallFinancialsWatch();
  ScriptApp.newTrigger('runFinancialsWatch').timeBased().everyMinutes(5).create();
  PropertiesService.getScriptProperties().setProperty('FW_LAST', new Date(Date.now() - 10 * 60000).toISOString());
  runFinancialsWatch();
}

function uninstallFinancialsWatch() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === 'runFinancialsWatch')
    .forEach(t => ScriptApp.deleteTrigger(t));
}

/** ウォッチリストから「Financials のファイルID → ティッカー」の対応を作る（30分キャッシュ） */
function fwFileMap_() {
  const cache = CacheService.getScriptCache(), hit = cache.get('FW_MAP');
  if (hit) return JSON.parse(hit);
  const rows = SpreadsheetApp.openById(FW_MASTER_ID).getSheetByName(FW_SHEET).getDataRange().getDisplayValues();
  const hi = rows.findIndex(r => r.some(c => String(c).trim() === 'Ticker'));
  const head = rows[hi].map(c => String(c).trim());
  const tk = head.indexOf('Ticker');
  let fin = head.indexOf('Financials');
  if (fin < 0) {   // 見出しが変わっていたら、スプレッドシートのURLが一番多い列
    const cnt = {};
    rows.slice(hi + 1).forEach(r => r.forEach((v, i) => { if (/docs\.google\.com\/spreadsheets\/d\//.test(v)) cnt[i] = (cnt[i] || 0) + 1; }));
    fin = +Object.keys(cnt).sort((a, b) => cnt[b] - cnt[a])[0];
  }
  const map = {};
  rows.slice(hi + 1).forEach(r => {
    const m = String(r[fin] || '').match(/\/spreadsheets\/d\/([A-Za-z0-9_-]+)/), t = String(r[tk] || '').trim();
    if (m && t) (map[m[1]] = map[m[1]] || []).push(t);
  });
  cache.put('FW_MAP', JSON.stringify(map), 1800);
  return map;
}

function fwApi_(method, path, payload) {
  const token = PropertiesService.getScriptProperties().getProperty('EARNINGS_TABLE_TOKEN');
  if (!token) throw new Error('スクリプト プロパティ EARNINGS_TABLE_TOKEN がありません');
  const res = UrlFetchApp.fetch('https://api.github.com/repos/' + FW_REPO + path, {
    method: method,
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
    contentType: 'application/json',
    payload: payload ? JSON.stringify(payload) : undefined,
    muteHttpExceptions: true,
  });
  if (res.getResponseCode() >= 300) throw new Error('GitHub ' + method + ' ' + path + ': ' + res.getResponseCode() + ' ' + res.getContentText());
  return res.getContentText() ? JSON.parse(res.getContentText()) : null;
}

function runFinancialsWatch() {
  const props = PropertiesService.getScriptProperties();
  const now = new Date(), last = new Date(props.getProperty('FW_LAST') || (Date.now() - 10 * 60000));
  const pending = new Set(JSON.parse(props.getProperty('FW_PENDING') || '[]'));

  // 前回から更新されたスプレッドシート（1分の重なりを持たせて取りこぼしを防ぐ）
  const since = new Date(last.getTime() - 60000).toISOString().replace(/\.\d+Z$/, '');
  const map = fwFileMap_();
  const it = DriveApp.searchFiles('mimeType = "application/vnd.google-apps.spreadsheet" and modifiedDate > "' + since + '"');
  while (it.hasNext()) {
    const f = it.next(), ts = map[f.getId()];
    if (ts) ts.forEach(t => pending.add(t));
  }
  props.setProperty('FW_LAST', now.toISOString());
  if (!pending.size) return;

  // 同期が動いている・待っている間は起動しない（待ちの run は新しい起動で置き換えられて会社が抜けるため）。次の5分で起動する
  const busy = ['in_progress', 'queued'].some(s =>
    (fwApi_('get', '/actions/workflows/' + FW_WORKFLOW + '/runs?status=' + s + '&per_page=1').workflow_runs || []).length);
  if (busy) { props.setProperty('FW_PENDING', JSON.stringify([...pending])); return; }

  const only = [...pending].join(',');
  fwApi_('post', '/actions/workflows/' + FW_WORKFLOW + '/dispatches', { ref: 'main', inputs: { only: only } });
  props.setProperty('FW_PENDING', '[]');
  console.log('同期を起動: ' + only);
}
