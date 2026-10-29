#!/usr/bin/env python3
"""
企業ロゴ一括収集スクリプト

使い方:
    pip install requests beautifulsoup4
    python collect_logos.py companies.csv            # -> ./logos/ に保存
    python collect_logos.py companies.csv -o out_dir

入力ファイル:
    CSV（1行目ヘッダー）: name,domain   ※domain は空欄でもOK
        トヨタ自動車,toyota.co.jp
        ソニーグループ,
    もしくは1行1社のテキストファイル（企業名のみ）

取得の優先順:
    1. Wikidata の「ロゴ画像」(P154) → Wikimedia Commons から原寸（SVGが多い）
    2. 公式サイトのHTMLから logo を含む <img>/<svg>、apple-touch-icon、og:image
    3. Google のファビコンサービス（256px、最終手段）

出力:
    out_dir/<企業名>.<拡張子>  と  out_dir/results.csv（取得元・URL・状態）
"""
import argparse
import csv
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote

import requests
from bs4 import BeautifulSoup

UA = "earnings-digest-logo-collector/1.1 (https://github.com/shiroin/earnings-digest; python-requests)"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"})
TIMEOUT = 15
WAIT = 0.5  # アクセス間隔（秒）

EXT_BY_TYPE = {
    "image/svg+xml": ".svg", "image/png": ".png", "image/jpeg": ".jpg",
    "image/gif": ".gif", "image/webp": ".webp", "image/x-icon": ".ico",
    "image/vnd.microsoft.icon": ".ico",
}


def safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", name).strip("_")[:100] or "company"


def get(url, **kw):
    time.sleep(WAIT)
    return SESSION.get(url, timeout=TIMEOUT, **kw)


# ---------- 1. Wikidata ----------
def wikidata_lookup(name: str):
    """企業名から (logo_file, website) を返す。見つからなければ (None, None)。"""
    for lang in ("ja", "en"):
        try:
            r = get("https://www.wikidata.org/w/api.php", params={
                "action": "wbsearchentities", "search": name, "language": lang,
                "uselang": lang, "type": "item", "limit": 5, "format": "json"})
            ids = [e["id"] for e in r.json().get("search", [])]
        except Exception:
            continue
        if not ids:
            continue
        try:
            r = get("https://www.wikidata.org/w/api.php", params={
                "action": "wbgetentities", "ids": "|".join(ids),
                "props": "claims", "format": "json"})
            ents = r.json().get("entities", {})
        except Exception:
            continue
        for qid in ids:  # 検索順位を保ったまま、ロゴかサイトを持つ最初の候補を採用
            claims = ents.get(qid, {}).get("claims", {})
            logo = _claim_value(claims, "P154")
            site = _claim_value(claims, "P856")
            if logo or site:
                return logo, site
    return None, None


def _claim_value(claims, prop):
    """現在の値を選ぶ: 推奨ランク > 終了日(P582)なし > 開始日(P580)が新しい順"""
    best, best_key = None, None
    for i, c in enumerate(claims.get(prop, [])):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if not isinstance(v, str) or c.get("rank") == "deprecated":
            continue
        q = c.get("qualifiers", {})
        start = ""
        for st in q.get("P580", []):
            start = st.get("datavalue", {}).get("value", {}).get("time", "") or start
        key = (c.get("rank") == "preferred", "P582" not in q, start, i)
        if best_key is None or key > best_key:
            best, best_key = v, key
    return best


def wiki_file_urls(filename: str):
    """Wikipedia/Commons のファイル名から候補URL（Commons → 英語版 → 日本語版）"""
    import hashlib
    name = re.sub(r"^(File|ファイル):", "", filename).replace(" ", "_")
    h = hashlib.md5(name.encode("utf-8")).hexdigest()
    return [f"https://upload.wikimedia.org/wikipedia/{p}/{h[0]}/{h[:2]}/{quote(name)}" for p in ("commons", "en", "ja")]


def commons_url(filename: str) -> str:
    """upload.wikimedia.org の原寸ファイルURL（Special:FilePath は弾かれることがある）"""
    import hashlib
    name = filename.replace(" ", "_")
    h = hashlib.md5(name.encode("utf-8")).hexdigest()
    return f"https://upload.wikimedia.org/wikipedia/commons/{h[0]}/{h[:2]}/{quote(name)}"


# ---------- 2. 公式サイト ----------
def site_logo_candidates(site_url: str):
    try:
        r = get(site_url)
        r.raise_for_status()
    except Exception:
        return []
    base = r.url
    soup = BeautifulSoup(r.text, "html.parser")
    cands = []

    # <img> で logo を含むもの（header 内を優先）
    def score_img(img):
        attrs = " ".join([img.get("src", ""), img.get("alt", ""),
                          " ".join(img.get("class", [])), img.get("id", "")]).lower()
        if "logo" not in attrs and "ロゴ" not in attrs:
            return None
        s = 10
        if img.find_parent(["header"]) or img.find_parent(attrs={"class": re.compile("header", re.I)}):
            s += 10
        if img.find_parent("a", href=re.compile(r"^(/|%s/?$)" % re.escape(base))):
            s += 5
        if (img.get("src") or "").lower().endswith(".svg"):
            s += 3
        return s

    scored = []
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if not src or src.startswith("data:"):
            continue
        s = score_img(img)
        if s:
            scored.append((s, urljoin(base, src)))
    cands += [u for _, u in sorted(scored, key=lambda x: -x[0])]

    # インライン <svg> の logo
    for svg in soup.find_all("svg"):
        attrs = (" ".join(svg.get("class", [])) + svg.get("id", "") + str(svg.get("aria-label", ""))).lower()
        if "logo" in attrs:
            cands.append(("inline-svg", str(svg)))
            break

    # apple-touch-icon / 大きめのアイコン / og:image
    for link in soup.find_all("link", rel=True):
        rel = " ".join(link["rel"]).lower()
        if "apple-touch-icon" in rel or ("icon" in rel and link.get("href", "").endswith(".svg")):
            cands.append(urljoin(base, link["href"]))
    og = soup.find("meta", property="og:logo")
    if og and og.get("content"):
        cands.append(urljoin(base, og["content"]))
    return cands


# ---------- 3. ファビコン ----------
def favicon_url(domain: str) -> str:
    return f"https://www.google.com/s2/favicons?sz=256&domain={domain}"


# ---------- 保存 ----------
def download(url: str, dest_stem: Path):
    r = get(url, allow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not ctype.startswith("image/") and not r.content.lstrip()[:5].lower().startswith((b"<svg", b"<?xml")):
        raise ValueError(f"画像ではありません ({ctype})")
    if len(r.content) < 200:
        raise ValueError("サイズが小さすぎます")
    ext = EXT_BY_TYPE.get(ctype) or Path(urlparse(r.url).path).suffix or ".img"
    path = dest_stem.with_suffix(ext)
    path.write_bytes(r.content)
    return path


def normalize_site(domain_or_url: str) -> str:
    d = domain_or_url.strip()
    return d if d.startswith("http") else "https://" + d


def collect_one(name: str, domain: str, out: Path, spec: str = ""):
    stem = out / safe_filename(name)
    errors = []

    # 手動指定（ロゴ指定列）: URL か「wiki:ファイル名.svg」
    if spec:
        urls = wiki_file_urls(spec[5:]) if spec.startswith("wiki:") else [spec]
        for u in urls:
            try:
                return "指定", u, download(u, stem), domain
            except Exception as e:
                errors.append(f"指定: {e}")

    logo_file, site = (None, None)
    try:
        logo_file, site = wikidata_lookup(name)
    except Exception as e:
        errors.append(f"wikidata: {e}")
    if logo_file:
        url = commons_url(logo_file)
        try:
            return "wikidata", url, download(url, stem), site
        except Exception as e:
            errors.append(f"commons: {e}")
            print(f"(commons失敗: {e}) ", end="")

    site_url = normalize_site(domain) if domain else site
    if site_url:
        for c in site_logo_candidates(site_url)[:6]:
            try:
                if isinstance(c, tuple):  # インラインSVG
                    p = stem.with_suffix(".svg")
                    svg = c[1]
                    if "xmlns" not in svg:
                        svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
                    p.write_text(svg, encoding="utf-8")
                    return "site(inline svg)", site_url, p, site_url
                return "site", c, download(c, stem), site_url
            except Exception as e:
                errors.append(f"site: {e}")
        host = urlparse(site_url).netloc or site_url
        try:
            u = favicon_url(host)
            return "favicon", u, download(u, stem), site_url
        except Exception as e:
            errors.append(f"favicon: {e}")

    raise RuntimeError("; ".join(errors) or "Wikidataで見つからず、ドメイン指定もありません")


def read_masters(path):
    """data/masters.json の企業 → (英語名, ドメイン, ロゴ指定)。指定は logos/overrides.csv（企業,ロゴ指定）"""
    import json
    from urllib.parse import urlparse as _u
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    ov = {}
    ovp = Path(path).resolve().parent.parent / "logos" / "overrides.csv"
    if ovp.exists():
        for r in csv.DictReader(ovp.read_text(encoding="utf-8-sig").splitlines()):
            ov[r["企業"].strip()] = r["ロゴ指定"].strip()
    out = []
    for c in data["companies"]:
        host = _u(c.get("ir") or "").netloc
        host = re.sub(r"^(ir|investor|investors|corporate|global|www)\.", "", host) if host else ""
        out.append((c["name"], host, ov.get(c["name"]) or ov.get(c.get("ja", "")) or "", c.get("ja", ""), c.get("ticker", "")))
    return out


def read_input(path):
    if str(path).endswith("masters.json"):
        return read_masters(path)
    if str(path).startswith("sheet:"):  # sheet:<スプレッドシートID>  → ウォッチリストシートをCSVで取得
        sid = str(path)[6:]
        r = SESSION.get(f"https://docs.google.com/spreadsheets/d/{sid}/gviz/tq",
                        params={"tqx": "out:csv", "sheet": "ウォッチリスト"}, timeout=30)
        r.raise_for_status()
        if r.text.lstrip().startswith("<"):
            raise SystemExit("スプレッドシートが共有されていません（リンクを知っている全員に閲覧権限を付けてください）")
        text = r.text
    else:
        text = Path(path).read_text(encoding="utf-8-sig")
    rows = list(csv.reader(text.splitlines()))
    if not rows:
        return []
    header = [h.strip().lower() for h in rows[0]]
    name_key = next((k for k in ("name", "企業名", "企業") if k in header), None)
    if name_key:
        ni = header.index(name_key)
        di = next((header.index(k) for k in ("domain", "url", "ドメイン") if k in header), None)
        si = header.index("ロゴ指定") if "ロゴ指定" in header else None
        rows = rows[1:]
    else:
        ni, di, si = 0, (1 if max(len(r) for r in rows) > 1 else None), None
    cell = lambda r, i: r[i].strip() if i is not None and len(r) > i else ""
    return [(r[ni].strip(), cell(r, di), cell(r, si), "", "") for r in rows if len(r) > ni and r[ni].strip()]


def main():
    ap = argparse.ArgumentParser(description="企業ロゴを一括収集します")
    ap.add_argument("input", help="企業リスト（CSV / 1行1社のテキスト / sheet:スプレッドシートID）")
    ap.add_argument("-o", "--out", type=Path, default=Path("logos"))
    ap.add_argument("--skip-existing", action="store_true", help="既にロゴがある企業は取り直さない")
    args = ap.parse_args()

    companies = read_input(args.input)
    args.out.mkdir(parents=True, exist_ok=True)
    report = []
    for i, (name, domain, spec, *_) in enumerate(companies, 1):
        print(f"[{i}/{len(companies)}] {name} ... ", end="", flush=True)
        if args.skip_existing and any((args.out / safe_filename(name)).with_suffix(e).exists()
                                      for e in (".svg", ".png", ".jpg", ".webp", ".gif", ".ico", ".img")):
            print("skip (取得済み)")
            report.append([name, domain, "ok", "existing", "", ""])
            continue
        try:
            src, url, path, site = collect_one(name, domain, args.out, spec)
            print(f"OK ({src}) -> {path.name}")
            report.append([name, domain or site or "", "ok", src, url, path.name])
        except Exception as e:
            print(f"NG: {e}")
            report.append([name, domain, "failed", "", "", str(e)[:300]])

    with open(args.out / "results.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["name", "domain", "status", "source", "url", "file_or_error"])
        w.writerows(report)
    # 企業名 → ファイル名 の対応表（Webアプリが使う）
    import json
    index = {}
    oldp = args.out / "index.json"
    if oldp.exists():
        try: index.update(json.loads(oldp.read_text(encoding="utf-8")))
        except Exception: pass
    for name, _d, _s, *alias in companies:
        for e in (".svg", ".png", ".jpg", ".webp", ".gif", ".ico", ".img"):
            f = (args.out / safe_filename(name)).with_suffix(e)
            if f.exists():
                for k in [name, *[a for a in alias if a]]:
                    index[k] = f.name
                break
    (args.out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")
    ok = sum(1 for r in report if r[2] == "ok")
    print(f"\n完了: {ok}/{len(report)} 社取得 → {args.out.resolve()}")
    print("※ 'favicon' 由来のものは小さいアイコンなので、必要なら手動で差し替えてください。")


if __name__ == "__main__":
    sys.exit(main())
