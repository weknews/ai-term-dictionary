#!/usr/bin/env python3
"""공유용 단일 HTML 빌드 — terms.js를 index.html 안에 인라인한다.

    python3 build.py

결과: dist/위뉴-AI-용어집.html  (파일 하나. 그대로 보내거나 아무 데나 올리면 된다)
글꼴만 Google Fonts에서 받아오므로, 오프라인에서는 시스템 글꼴로 대체돼 표시된다.
"""
import io, os, re

ROOT = os.path.dirname(os.path.abspath(__file__))
html = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
terms = io.open(os.path.join(ROOT, "terms.js"), encoding="utf-8").read()

tag = '<script src="terms.js" charset="utf-8"></script>'
if tag not in html:
    raise SystemExit("index.html에서 terms.js 참조를 찾지 못했다. 태그가 바뀌었는지 확인할 것.")

html = html.replace(tag, "<script>\n" + terms.replace("</script>", "<\\/script>") + "\n</script>")

version = re.search(r"<b>(v[\d.]+)</b>", html)
os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
out = os.path.join(ROOT, "dist", "위뉴-AI-용어집.html")
io.open(out, "w", encoding="utf-8").write(html)
print("%s  (%s, %.0f KB)" % (out, version.group(1) if version else "?", len(html.encode()) / 1024))
