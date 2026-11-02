/**
 * 企業ロゴを Google ドライブのフォルダにアップロードし、
 * ウォッチリスト（global_company_watch_300）の E列「指定ロゴ」にファイルURLを書き込みます。
 *
 * 使い方（1回だけ）:
 *  1. https://script.google.com/ を開き「新しいプロジェクト」
 *  2. このファイルの中身を全部貼り付けて保存
 *  3. 関数「uploadLogos」を選んで「実行」→ 権限を許可
 *  4. 6分で止まった場合は、もう一度「実行」（続きから再開します）
 *
 * ロゴの元データ: https://shiroin.github.io/earnings-table/logos/drive_manifest.json
 */
const FOLDER_ID = '1_5DvpyBsynJte9GWFHUe-NeBS8Ho1PjV';
const SHEET_ID  = '1NMjMQ6JeUuRRyy4X7m79YCTnklUgkdE76407QYY5ahQ';
const SHEET_GID = 1060296635;
const BASE      = 'https://shiroin.github.io/earnings-table/logos/';
const OVERWRITE = false;   // true にすると、E列にURLがある会社も取り直す

function uploadLogos() {
  const t0 = Date.now();
  const list = JSON.parse(UrlFetchApp.fetch(BASE + 'drive_manifest.json?t=' + t0).getContentText());
  const folder = DriveApp.getFolderById(FOLDER_ID);
  const sh = SpreadsheetApp.openById(SHEET_ID).getSheets().find(s => s.getSheetId() === SHEET_GID);
  const values = sh.getDataRange().getValues();

  // 見出し行（「No.」「指定ロゴ」がある行）を探す
  let hr = values.findIndex(r => r.some(v => String(v).trim() === 'No.') && r.some(v => String(v).trim() === '指定ロゴ'));
  if (hr < 0) throw new Error('見出し行（No. / 指定ロゴ）が見つかりません');
  const cNo = values[hr].findIndex(v => String(v).trim() === 'No.');
  const cLogo = values[hr].findIndex(v => String(v).trim() === '指定ロゴ');
  const rowOf = {};
  for (let i = hr + 1; i < values.length; i++) {
    const no = String(values[i][cNo]).trim();
    if (/^\d+$/.test(no)) rowOf[no] = i;
  }

  // フォルダ内の既存ファイル（同名は使い回さず置き換え）
  const existing = {};
  const it = folder.getFiles();
  while (it.hasNext()) { const f = it.next(); (existing[f.getName()] = existing[f.getName()] || []).push(f); }

  let done = 0, skipped = 0, failed = [];
  for (const e of list) {
    const i = rowOf[String(e.no)];
    if (i === undefined) { failed.push(e.no + ' ' + e.ja + '（行なし）'); continue; }
    const cur = String(values[i][cLogo] || '');
    if (!OVERWRITE && /drive\.google\.com/.test(cur)) { skipped++; continue; }
    if (Date.now() - t0 > 5 * 60 * 1000) {
      Logger.log('時間切れ。もう一度「実行」すると続きから再開します（完了 %s 件）', done);
      return;
    }
    try {
      const blob = UrlFetchApp.fetch(BASE + encodeURIComponent(e.file)).getBlob().setName(e.title);
      if (e.mime) blob.setContentType(e.mime);
      (existing[e.title] || []).forEach(f => f.setTrashed(true));
      const f = folder.createFile(blob);
      f.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
      const url = 'https://drive.google.com/file/d/' + f.getId() + '/view';
      sh.getRange(i + 1, cLogo + 1).setValue(url);
      values[i][cLogo] = url;
      done++;
    } catch (err) {
      failed.push(e.no + ' ' + e.ja + ': ' + err);
    }
  }
  Logger.log('完了: %s 件アップロード / %s 件はURL済みのため省略 / 失敗 %s 件', done, skipped, failed.length);
  if (failed.length) Logger.log(failed.join('\n'));
}
