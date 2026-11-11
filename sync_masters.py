#!/usr/bin/env python3
"""
企業マスター同期: ウォッチリスト（global_company_watch_300）に載っている各社の
企業マスター（*_company_master.xlsm / .xlsx）を読み、Webアプリ用の data/masters.json を作る。

  python3 sync_masters.py                       # config.json の設定で実行（GitHub Actions 用）
  python3 sync_masters.py --watch-csv W.csv --master-dir DIR   # 手元のファイルで試す

前提:
  - ウォッチリストとマスターは「リンクを知っている全員（閲覧者）」で共有されていること
  - マスターの数値は「百万（入力通貨）」単位（設定シートの input_currency）
"""
import argparse, csv, io, json, re, sys, time, datetime
from pathlib import Path

import requests
import openpyxl

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "earnings-table-sync/1.0 (https://github.com/shiroin/earnings-table)"}

CUR = {"USD": "ドル", "JPY": "円", "EUR": "ユーロ", "CNY": "元", "KRW": "ウォン", "TWD": "台湾ドル",
       "HKD": "香港ドル", "DKK": "デンマーククローネ", "NOK": "ノルウェークローネ", "SEK": "スウェーデンクローナ",
       "CHF": "スイスフラン", "GBP": "ポンド", "INR": "ルピー", "CAD": "カナダドル", "AUD": "豪ドル", "SGD": "シンガポールドル"}
CUR_FROM_JA = {"ドル": "USD", "円": "JPY", "ユーロ": "EUR", "元": "CNY", "人民元": "CNY", "ウォン": "KRW", "台湾ドル": "TWD",
               "香港ドル": "HKD", "ポンド": "GBP"}


def file_id(url):
    m = re.search(r"/d/([a-zA-Z0-9_-]{20,})", url or "") or re.search(r"[?&]id=([a-zA-Z0-9_-]{20,})", url or "")
    return m.group(1) if m else None


# ---------------------------------------------------------------- ウォッチリスト
def fy_end(v):
    """決算期は「3月」のような1〜12月だけを受け付ける（列ずれで入った日付シリアル等は空にする）。"""
    m = re.fullmatch(r"\s*(\d{1,2})\s*月?(\D.*)?", v or "")
    return f"{int(m.group(1))}月" if m and 1 <= int(m.group(1)) <= 12 else ""

TAG_ORDER = []


def read_watchlist(text):
    rows = list(csv.reader(io.StringIO(text)))
    hi = next(i for i, r in enumerate(rows) if any(c.strip() == "Ticker" for c in r))
    head = [c.strip() for c in rows[hi]]
    col = lambda name: next((i for i, h in enumerate(head) if h == name), None)
    idx = {k: col(k) for k in ["No.", "Ticker", "企業名", "日本名", "暫定Tier(個別記事化60)", "業種カテゴリー1", "業種カテゴリー2",
                               "本拠地", "上場(307)", "決算資料", "指定ロゴ", "フォルダ", "Financials", "決算期", "決算日", "通貨", "単位"]}
    # 見出しの数字が変わる列（上場(486) など）は先頭一致で
    for k, pre in (("上場(307)", "上場("), ("暫定Tier(個別記事化60)", "暫定Tier(")):
        if idx[k] is None:
            idx[k] = next((i for i, h in enumerate(head) if h.startswith(pre)), None)
    # Financials の見出しが書き換わっていても、スプレッドシートのURLが一番多く入っている列を Financials とみなす
    if idx["Financials"] is None:
        body = rows[hi + 1:]
        counts = {}
        for r in body:
            for i, v in enumerate(r):
                if "docs.google.com/spreadsheets/d/" in v:
                    counts[i] = counts.get(i, 0) + 1
        if counts:
            idx["Financials"] = max(counts, key=counts.get)
            print(f"注意: 「Financials」の見出しが見つからないため、{idx['Financials'] + 1}列目（スプレッドシートのURLが{counts[idx['Financials']]}件）を使います")
    idx["fin_updated"] = next((i for i, h in enumerate(head) if "更新日" in h and h.lower().startswith("financial")), None)
    get = lambda r, k: (r[idx[k]].strip() if idx[k] is not None and idx[k] < len(r) else "")
    # カテゴリーの列（AI・メモリ・海運…）: 「通貨」の次の列から右端まで。1 が付いている会社がそのカテゴリー
    t0 = (idx["通貨"] + 1) if idx["通貨"] is not None else len(head)
    tag_cols = [(i, head[i]) for i in range(t0, len(head)) if head[i]]
    FLAG = {"1", "１", "true", "○", "◯", "y", "yes"}
    global TAG_ORDER
    TAG_ORDER = list(dict.fromkeys(h for _, h in tag_cols))
    out = []
    for r in rows[hi + 1:]:
        if not get(r, "企業名") and not get(r, "Ticker"):
            continue
        # ウォッチリスト本体 = 番号があり、本拠地・マスター・指定ロゴのどれかが入っている行（下の候補リストは除く）
        if not get(r, "No.").isdigit() or not (get(r, "本拠地") or file_id(get(r, "Financials")) or get(r, "指定ロゴ")):
            continue
        out.append({
            "no": get(r, "No."), "ticker": get(r, "Ticker"), "name": get(r, "企業名"), "ja": get(r, "日本名") or get(r, "企業名"),
            "tier": get(r, "暫定Tier(個別記事化60)"), "cat1": get(r, "業種カテゴリー1"), "cat2": get(r, "業種カテゴリー2"),
            "country": get(r, "本拠地"), "listed": get(r, "上場(307)"), "ir": get(r, "決算資料"),
            "folder": get(r, "フォルダ"), "master_id": file_id(get(r, "Financials")),
            "fy_end": fy_end(get(r, "決算期")), "date": get(r, "決算日").replace("/", "-"),
            "fin_updated": get(r, "fin_updated"),
            "tags": list(dict.fromkeys(h for i, h in tag_cols if i < len(r) and r[i].strip().lower() in FLAG)),
            "currency_ja": get(r, "通貨"), "unit_in": get(r, "単位"), "logo_spec": get(r, "指定ロゴ"),
        })
    return out


# ---------------------------------------------------------------- マスター
def table(ws):
    """'period' の行を見出しとして、その下の行を辞書のリストで返す"""
    rows = list(ws.iter_rows(values_only=True))
    hi = next((i for i, r in enumerate(rows) if r and str(r[0]).strip().lower() == "period"), None)
    if hi is None:
        return [], []
    head = [str(h).strip() if h is not None else "" for h in rows[hi]]
    cols = [h for h in head[1:] if h]
    data = []
    for r in rows[hi + 1:]:
        if not r or r[0] is None or str(r[0]).strip() == "":
            continue
        p = str(r[0]).strip()
        vals = {}
        for j, h in enumerate(head[1:], start=1):
            if not h:
                continue
            v = r[j] if j < len(r) else None
            if isinstance(v, str):
                v = v.replace(",", "").strip()
                try: v = float(v)
                except ValueError: v = None
            vals[h] = float(v) if isinstance(v, (int, float)) else None
        data.append({"period": p, **vals})
    # 値がすべて空の列は落とす
    cols = [c for c in cols if any(d.get(c) is not None for d in data)]
    return cols, data


def raw_table(ws):
    """文字列も残して 'period' 見出しの表を読む（ガイダンス・進捗・比較タブ用）"""
    rows = list(ws.iter_rows(values_only=True))
    hi = next((i for i, r in enumerate(rows) if r and str(r[0]).strip().lower() == "period"), None)
    if hi is None:
        return []
    head = [str(h).strip() if h is not None else "" for h in rows[hi]]
    out = []
    for r in rows[hi + 1:]:
        if not r or r[0] is None or str(r[0]).strip() == "":
            continue
        d = {}
        for j, h in enumerate(head):
            if h:
                v = r[j] if j < len(r) else None
                d[h] = v if isinstance(v, (int, float)) else ("" if v is None else str(v).strip())
        out.append(d)
    return out


def settings(wb):
    if "設定" not in wb.sheetnames:
        return {}, {}
    ws = wb["設定"]
    s, items = {}, {}
    for r in ws.iter_rows(values_only=True):
        if r and r[0] and str(r[0]).strip() not in ("setting", "会社マスター設定") and len(r) > 1:
            s[str(r[0]).strip()] = r[1]
        # 可変テーブル（種類 / 項目名 / 表示名 / カラー）: E〜H列
        if r and len(r) >= 8 and r[4] and r[5] and str(r[4]).strip() not in ("種類", "使い方"):
            items.setdefault(str(r[4]).strip(), {})[str(r[5]).strip()] = {"label": (str(r[6]).strip() if r[6] else str(r[5]).strip()),
                                                                          "color": (str(r[7]).strip() if r[7] else None)}
    return s, items


def scale_for(display_unit, currency):
    """表示単位 → (割る数, 単位ラベル)。入力は百万（通貨）"""
    cur = CUR.get(currency, currency or "")
    du = str(display_unit or "")
    if "兆" in du:   return 1_000_000, "兆" + cur
    if "十億" in du or "10億" in du: return 1_000, "10億" + cur
    if "億" in du:   return 100, "億" + cur
    if "百万" in du: return 1, "百万" + cur
    return 100, "億" + cur


PER1 = re.compile(r"(?:FYE?|CY)?\s*'?(\d{4}|\d{2})\s*[-_/. ]?\s*(?:年度?)?\s*(?:Q|第)\s*([1-4])", re.I)   # FY2027Q2 / 2026Q3 / FYE27Q1 / 2026年Q2
PER2 = re.compile(r"(?:Q|第)\s*([1-4])(?:四半期)?\s*[-_/. ]?\s*(?:FYE?|CY)?\s*'?(\d{4}|\d{2})", re.I)   # Q2 FY2026 / 2Q26


def parse_period(p):
    t = str(p or "").strip()
    mf = re.search(r"FYE?\s*'?(\d{4}|\d{2})\s*/\s*\d{1,2}\s*[-_ ]?\s*(?:Q\s*([1-4])|([1-4])\s*Q)", t, re.I)   # FY27/2 1Q（2月期）
    if mf:
        fy = int(mf.group(1)); return (fy + 2000 if fy < 100 else fy), int(mf.group(2) or mf.group(3))
    m0 = re.fullmatch(r"([1-4])\s*Q\s*'?(\d{4}|\d{2})", t, re.I)          # 2Q26
    if m0:
        fy = int(m0.group(2)); return (fy + 2000 if fy < 100 else fy), int(m0.group(1))
    m = None if re.match(r"(?:Q|第)", t, re.I) else PER1.search(t)
    if m:
        fy, q = int(m.group(1)), int(m.group(2))
    else:
        m = PER2.search(t)
        if not m:
            m3 = re.search(r"(\d{4}|\d{2})\s*[-_/. ]?\s*([1-4])\s*Q", t, re.I)   # 2026 2Q / 26 3Q
            if not m3:
                return None, None
            fy, q = int(m3.group(1)), int(m3.group(2))
        else:
            q, fy = int(m.group(1)), int(m.group(2))
    fy = fy + 2000 if fy < 100 else fy
    return fy, q


REVENUE_COLS = ["revenue", "net_sales", "sales", "total_revenue", "operating_revenue"]
OP_COLS = ["operating_profit", "operating_income", "operating_profit_ifrs", "core_operating_profit", "business_profit"]
PBT_COLS = ["profit_before_tax", "income_before_income_taxes", "income_before_taxes", "pretax_income", "pre_tax_income"]


def read_master(path_or_bytes):
    wb = openpyxl.load_workbook(io.BytesIO(path_or_bytes) if isinstance(path_or_bytes, bytes) else path_or_bytes,
                                data_only=True, read_only=False)
    s, items = settings(wb)
    out = {"company_name": s.get("company_name"), "input_currency": (s.get("input_currency") or "").strip() or None,
           "display_unit": s.get("display_unit"), "note": s.get("note")}

    def sheet(name):
        return table(wb[name]) if name in wb.sheetnames else ([], [])

    cols, q = sheet("会社全体_四半期")
    # 利益の欄に使う列: 営業利益（operating_profit / operating_income）を優先。net_income・eps などの列があっても使わない。
    # 総合商社など営業利益を開示しない会社は、税引前利益（profit_before_tax）
    low = {c.lower(): c for c in cols}
    rk = next((low[k] for k in REVENUE_COLS if k in low), "revenue")
    pk = next((low[k] for k in OP_COLS if k in low), None)
    kind = None
    if pk is None:
        pk = next((low[k] for k in PBT_COLS if k in low), None)
        kind = "pbt" if pk else None
    out["profit_kind"] = kind
    ek = low.get("eps")
    # 経常利益・税引前利益・純利益も持っておく（1社まとめの前年同期比に使う）。利益の欄に税引前利益を使った会社は pbt を重ねない
    ok_ = low.get("ordinary_profit")
    bk = None if kind == "pbt" else next((low[k] for k in PBT_COLS if k in low), None)
    nk = next((low[k] for k in ("net_income", "net_profit", "profit_attributable_to_owners") if k in low), None)
    out["quarters"] = [{"period": d["period"], "revenue": d.get(rk), "operating_profit": d.get(pk) if pk else None,
                        "eps": d.get(ek) if ek else None, "ordinary_profit": d.get(ok_) if ok_ else None,
                        "profit_before_tax": d.get(bk) if bk else None, "net_income": d.get(nk) if nk else None} for d in q]
    seg_meta = items.get("セグメント", {})
    for key, name in (("seg_revenue", "セグメント売上高_四半期"), ("seg_profit", "セグメント利益_四半期")):
        c, d = sheet(name)
        out[key] = {"columns": c, "rows": d} if c else None
    out["segments"] = {k: v for k, v in seg_meta.items()}
    out["guide_view"] = raw_table(wb["進捗・比較"]) if "進捗・比較" in wb.sheetnames else []
    return out


# ---------------------------------------------------------------- 加工
def enrich(w, m):
    """ウォッチリスト1社 + マスター → Webアプリ用の1件"""
    cur = (m.get("input_currency") or CUR_FROM_JA.get(w.get("currency_ja"), "") or "").upper()
    div, unit = scale_for(m.get("display_unit"), cur)
    qs = m["quarters"]
    byp = {}
    for d in qs:
        fy, qn = parse_period(d["period"])
        if fy:
            byp[(fy, qn)] = d

    def yoy(a, b):
        if a is None or b is None or b <= 0:
            return None
        return round((a / b - 1) * 100, 1)

    periods = []
    for d in qs:
        fy, qn = parse_period(d["period"])
        prev = byp.get((fy - 1, qn)) if fy else None
        r, o = d.get("revenue"), d.get("operating_profit")
        periods.append({
            "period": d["period"], "label": f"Q{qn}" if qn else d["period"],
            "revenue": None if r is None else round(r / div, 2), "operating_profit": None if o is None else round(o / div, 2),
            "revenue_yoy": yoy(r, prev and prev.get("revenue")), "op_yoy": yoy(o, prev and prev.get("operating_profit")),
            **({"eps": d["eps"]} if d.get("eps") is not None else {}),
            **{k: round(d[k] / div, 2) for k in ("ordinary_profit", "profit_before_tax", "net_income") if d.get(k) is not None},
            "margin": None if (r in (None, 0) or o is None) else round(o / r * 100, 1),
        })
    periods = [p for p in periods if p["revenue"] is not None or p["operating_profit"] is not None]

    def seg(block):
        if not block:
            return None
        cols = block["columns"]
        rows = []
        for d in block["rows"]:
            vals = {c: (None if d.get(c) is None else round(d[c] / div, 2)) for c in cols}
            if any(v is not None for v in vals.values()):
                rows.append({"period": d["period"], **vals})
        return {"columns": cols, "rows": rows[-24:]}

    return {
        "ticker": w["ticker"], "name": w["name"], "ja": w["ja"], "tier": w["tier"], "cat1": w["cat1"], "cat2": w["cat2"], "tags": w["tags"],
        "country": w["country"], "date": w["date"], "fy_end": w["fy_end"], "master_id": w["master_id"], "ir": w["ir"], "logo_spec": w["logo_spec"],
        "currency": cur, "unit": unit, "note": m.get("note"), "profit_kind": m.get("profit_kind"),
        "periods": periods[-24:],
        "seg_revenue": seg(m.get("seg_revenue")), "seg_profit": seg(m.get("seg_profit")),
        "segments": m.get("segments") or {},
        "guide": guide_rows(m.get("guide_view") or [], div, cur),
    }


PER_SHARE = re.compile(r"^(円|USD|ドル|\$|EUR|ユーロ|KRW|ウォン|TWD|CNY|元|HKD|%|％)$", re.I)


GUIDE_CUR = (("USD", r"USD|US\$|米ドル"), ("TWD", r"TWD|NTD|NT\$|台湾ドル"), ("JPY", r"JPY|円"), ("KRW", r"KRW|ウォン"),
             ("EUR", r"EUR|ユーロ"), ("CNY", r"CNY|RMB|人民元"), ("HKD", r"HKD|HK\$|香港ドル"), ("GBP", r"GBP|ポンド"))


def guide_rows(rows, div, cur=""):
    """進捗・比較タブ → Webアプリ用（金額は表示単位に、EPS・配当・利益率はそのまま）。
    会社全体と違う通貨の予想（TSMCの米ドル建てなど）は、その通貨の億単位にして disp_unit を付ける"""
    out = []
    for r in rows:
        unit = str(r.get("unit") or "")
        metric = str(r.get("metric") or "")
        amount = not (PER_SHARE.match(unit) or metric in ("eps", "dividend") or metric.endswith("margin"))
        rc = next((c for c, pat in GUIDE_CUR if re.search(pat, unit, re.I)), "") if amount else ""
        other = bool(cur and rc and rc != cur.upper())
        d = 100 if other else div
        def sc(v):
            if not isinstance(v, (int, float)):
                return None
            return round(v / d, 2) if amount else v
        def pc(v):
            return v if isinstance(v, (int, float)) else None
        out.append({"period": str(r.get("period") or ""), "type": str(r.get("type") or ""), "metric": metric,
                    "label": str(r.get("label") or ""), "target": str(r.get("target") or ""),
                    "actual": sc(r.get("actual")), "low": sc(r.get("low")), "high": sc(r.get("high")), "mid": sc(r.get("mid")),
                    "ratio": pc(r.get("ratio")), "judge": str(r.get("judge") or ""), "avg5": pc(r.get("avg5")),
                    "prev_year": pc(r.get("prev_year")), "consensus": sc(r.get("consensus")), "vs_consensus": pc(r.get("vs_consensus")),
                    "prev_mid": sc(r.get("prev_mid")), "revision": str(r.get("revision") or ""), "kind": str(r.get("kind") or ""),
                    "amount": amount, "unit": "" if amount else unit, "basis": str(r.get("basis") or ""),
                    "announced": str(r.get("announced") or ""), "note": str(r.get("note") or "")[:120],
                    **({"disp_unit": "億" + CUR.get(rc, rc)} if other else {})})
    return out


def download(fid, session):
    # Googleスプレッドシート形式のマスターは xlsx に書き出して取得
    r = session.get(f"https://docs.google.com/spreadsheets/d/{fid}/export", params={"format": "xlsx"}, timeout=60, headers=UA)
    if r.ok and r.content[:2] == b"PK":
        return r.content
    r = session.get("https://drive.google.com/uc", params={"export": "download", "id": fid}, timeout=60, headers=UA)
    r.raise_for_status()
    if r.content[:2] != b"PK":  # ウイルススキャン確認ページなど
        m = re.search(r'name="confirm" value="([^"]+)"', r.text)
        if m:
            r = session.get("https://drive.usercontent.google.com/download",
                            params={"id": fid, "export": "download", "confirm": m.group(1)}, timeout=60, headers=UA)
        if r.content[:2] != b"PK":
            raise ValueError("Excelファイルを取得できませんでした（共有設定を確認）")
    return r.content


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watch-csv", help="ウォッチリストのCSV（省略時は config.json のスプレッドシートから取得）")
    ap.add_argument("--master-dir", help="マスターのファイルを置いたフォルダ（試験用。ファイル名に Drive ID か Ticker を含める）")
    ap.add_argument("-o", "--out", default=str(ROOT / "data" / "masters.json"))
    ap.add_argument("--only", help="この会社だけ取り直す（ティッカー、カンマ区切り）。他は前回の内容を使う")
    ap.add_argument("--auto", action="store_true",
                    help="決算日が直近3日以内か、Financials更新日が今日・昨日の会社だけ取り直す（15分ごとの自動同期用）")
    a = ap.parse_args()
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    s = requests.Session()

    if a.watch_csv:
        text = Path(a.watch_csv).read_text(encoding="utf-8")
    else:
        w = cfg["watchlist"]
        r = s.get(f"https://docs.google.com/spreadsheets/d/{w['sheetId']}/export", params={"format": "csv", "gid": w["gid"]}, timeout=60, headers=UA)
        r.raise_for_status()
        text = r.content.decode("utf-8")
    watch = read_watchlist(text)
    if a.auto:
        jst = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).date()
        def d(v):
            m = re.match(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", str(v or ""))
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None
        pick = [w["ticker"] for w in watch if w["ticker"] and w["master_id"] and (
            (d(w["date"]) and 0 <= (jst - d(w["date"])).days <= 3) or (d(w.get("fin_updated")) and (jst - d(w["fin_updated"])).days <= 1))]
        if not pick:
            print("直近に決算・更新のあった会社なし"); return
        a.only = ",".join(pick)
        print(f"自動同期: {len(pick)}社 {a.only}")
    n_master = sum(1 for x in watch if x['master_id'])
    print(f"ウォッチリスト: {len(watch)}社（マスターあり {n_master}社）")
    try:   # ウォッチリストの読み違いで全社が消えないように: マスターありの会社が前回の半分未満なら書き込まずに止める
        prev_n = sum(1 for c in json.loads(Path(a.out).read_text(encoding="utf-8"))["companies"] if c.get("master_id"))
    except Exception:
        prev_n = 0
    if prev_n and n_master < prev_n * 0.5:
        sys.exit(f"マスターありの会社が前回 {prev_n}社 → 今回 {n_master}社 と大きく減ったので中止しました（ウォッチリストの列の見出しを確認してください）")

    old = {}
    outp = Path(a.out)
    if outp.exists():
        try: old = {c["ticker"] or c["name"]: c for c in json.loads(outp.read_text(encoding="utf-8"))["companies"]}
        except Exception: pass

    only = {t.strip().upper() for t in (a.only or "").split(",") if t.strip()}
    companies, errors = [], []
    for i, w in enumerate(watch, 1):
        key = w["ticker"] or w["name"]
        base = {"ticker": w["ticker"], "name": w["name"], "ja": w["ja"], "tier": w["tier"], "cat1": w["cat1"], "cat2": w["cat2"], "tags": w["tags"],
                "country": w["country"], "date": w["date"], "fy_end": w["fy_end"], "master_id": w["master_id"], "ir": w["ir"], "logo_spec": w["logo_spec"], "periods": []}
        if not w["master_id"]:
            companies.append(base); continue
        if only and (w["ticker"] or "").upper() not in only and key in old:
            prev = dict(old[key]); prev.update({k: base[k] for k in ("name", "ja", "tier", "cat1", "cat2", "tags", "country", "date", "fy_end", "master_id", "ir", "logo_spec")})
            companies.append(prev); continue
        try:
            if a.master_dir:
                f = next((p for p in Path(a.master_dir).iterdir() if w["master_id"] in p.name or (w["ticker"] and w["ticker"] in p.name) or w["name"].split()[0] in p.name), None)
                if not f: companies.append(base); continue
                m = read_master(str(f))
            else:
                m = read_master(download(w["master_id"], s))
                time.sleep(0.3)
            companies.append(enrich(w, m))
        except Exception as e:
            errors.append(f"{w['name']}: {e}")
            prev = old.get(key)
            companies.append(prev if prev else base)  # 失敗したら前回の内容を残す
        if i % 25 == 0:
            print(f"  {i}/{len(watch)}")

    outp.parent.mkdir(parents=True, exist_ok=True)
    data = {"updated": datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M"),
            "companies": companies, "errors": errors, "tag_order": TAG_ORDER}
    outp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    ok = sum(1 for c in companies if c.get("periods"))
    print(f"完了: 業績あり {ok}社 / エラー {len(errors)}件 → {outp}")
    for e in errors[:30]:
        print("  ", e)


if __name__ == "__main__":
    main()
