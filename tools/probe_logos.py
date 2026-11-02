#!/usr/bin/env python3
"""ロゴ候補の収集（確認用）: tools/probe_list.json の各社について、
Wikipedia(ja/en)の記事画像・Wikidata・公式サイトからロゴ候補を probe/ に保存する。"""
import json, sys, re, time
from pathlib import Path
from urllib.parse import urljoin
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import collect_logos as cl

OUT = Path("probe"); OUT.mkdir(exist_ok=True)

def wp_images(lang, q):
    urls = []
    try:
        r = cl.get(f"https://{lang}.wikipedia.org/w/api.php", params={"action": "query", "list": "search", "srsearch": q, "srlimit": 2, "format": "json"})
        for h in r.json()["query"]["search"]:
            t = h["title"]
            r2 = cl.get(f"https://{lang}.wikipedia.org/w/api.php", params={"action": "query", "titles": t, "prop": "pageimages|images", "piprop": "original|name", "imlimit": 50, "format": "json"})
            for p in r2.json()["query"]["pages"].values():
                if p.get("original"): urls.append((f"wp{lang}", p["original"]["source"]))
                for im in p.get("images", []):
                    n = im["title"].split(":", 1)[1]
                    if re.search(r"logo|ロゴ", n, re.I):
                        urls += [(f"wp{lang}L", u) for u in cl.wiki_file_urls(n)[:1]] + [(f"wp{lang}L", u) for u in cl.wiki_file_urls(n)[1:] if lang in u]
    except Exception as e:
        print("  wp err", lang, e)
    return urls

items = json.loads(Path("tools/probe_list.json").read_text(encoding="utf-8"))
log = []
for it in items:
    no = it["no"]; print(no, it["ja"], flush=True)
    cands = []
    cands += wp_images("ja", it["ja"])
    cands += wp_images("en", it["en"] or it["name"])
    try:
        lf, site = cl.wikidata_lookup(it["name"])
        if lf: cands.append(("wd", cl.commons_url(lf)))
    except Exception: site = None
    dom = it.get("domain") or site
    if dom:
        su = cl.normalize_site(dom)
        for c in cl.site_logo_candidates(su)[:5]:
            if isinstance(c, tuple):
                p = OUT / f"{no}_site_inline.svg"; svg = c[1]
                if "xmlns" not in svg: svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
                p.write_text(svg, encoding="utf-8"); log.append([no, "site_inline", su, p.name])
            else: cands.append(("site", c))
    seen = set(); k = 0
    for src, u in cands:
        if u in seen: continue
        seen.add(u)
        try:
            p = cl.download(u, OUT / f"{no}_{k}_{src}")
            log.append([no, src, u, p.name]); k += 1
        except Exception as e:
            pass
        if k >= 6: break
Path("probe/log.json").write_text(json.dumps(log, ensure_ascii=False, indent=0), encoding="utf-8")
print("done", len(log))
