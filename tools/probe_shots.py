#!/usr/bin/env python3
"""公式サイトのヘッダーのロゴを、表示されたまま画像として切り出す（確認用）。
入力: [[No, URL], ...]  出力: probe/<No>_<k>_shot.png と、元画像（svg/png）があれば probe/<No>_<k>_src.*"""
import json, sys, re
from pathlib import Path
from urllib.parse import urljoin
import requests
from playwright.sync_api import sync_playwright
OUT = Path("probe"); OUT.mkdir(exist_ok=True)
SEL = ["header a[href='/'] img", "header a[href='./'] img", "header [class*='logo' i] img", "header [class*='logo' i] svg",
       "[class*='logo' i] img", "[id*='logo' i] img", "img[alt*='logo' i]", "img[src*='logo' i]", "[class*='logo' i] svg", "header svg", "h1 img", "header img"]
items = json.load(open(sys.argv[1]))
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
with sync_playwright() as p:
    b = p.chromium.launch()
    ctx = b.new_context(user_agent=UA, device_scale_factor=3, viewport={"width": 1400, "height": 900}, locale="ja-JP")
    for no, url in items:
        print(no, url, flush=True)
        pg = ctx.new_page()
        try:
            pg.goto(url, wait_until="domcontentloaded", timeout=40000); pg.wait_for_timeout(4000)
        except Exception as e:
            print(" goto", e); pg.close(); continue
        k = 0; seen = set()
        for s in SEL:
            try: els = pg.query_selector_all(s)
            except Exception: continue
            for el in els[:3]:
                try:
                    bb = el.bounding_box()
                    if not bb or bb["width"] < 30 or bb["height"] < 12 or bb["y"] > 400 or bb["width"] > 700: continue
                    key = (round(bb["x"]), round(bb["y"]))
                    if key in seen: continue
                    seen.add(key)
                    el.screenshot(path=str(OUT / f"{no}_{k}_shot.png"), omit_background=True)
                    src = el.get_attribute("src") if el.evaluate("e=>e.tagName") == "IMG" else None
                    if src and not src.startswith("data:"):
                        try:
                            r = requests.get(urljoin(pg.url, src), headers={"User-Agent": UA}, timeout=20)
                            ext = ".svg" if (src.split("?")[0].lower().endswith(".svg") or b"<svg" in r.content[:500]) else (".png" if r.content[:4] == b"\x89PNG" else ".jpg" if r.content[:2] == b"\xff\xd8" else ".webp" if r.content[8:12] == b"WEBP" else "")
                            if ext and r.ok: (OUT / f"{no}_{k}_src{ext}").write_bytes(r.content)
                        except Exception: pass
                    elif el.evaluate("e=>e.tagName.toLowerCase()") == "svg":
                        svg = el.evaluate("e=>e.outerHTML")
                        if "xmlns" not in svg: svg = svg.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
                        (OUT / f"{no}_{k}_src.svg").write_text(svg, encoding="utf-8")
                    k += 1
                except Exception: pass
                if k >= 4: break
            if k >= 4: break
        pg.close()
    b.close()
