#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""발행 전 전체 점검.  python3 check.py

CONTRIBUTING.md에 적어둔 규칙을 전부 기계로 확인한다.
사람이 기억해서 돌리는 절차는 결국 안 돌아가므로 한 명령으로 묶었다.
"""
import io, json, os, re, subprocess, sys
from collections import Counter

ROOT = os.path.dirname(os.path.abspath(__file__))
fail, warn = [], []

def load_terms():
    return json.loads(subprocess.check_output(["node", "-e",
        "global.window={};require('%s/terms.js');"
        "process.stdout.write(JSON.stringify({t:window.TERMS,c:window.CATS}))" % ROOT]).decode())

d = load_terms()
TERMS, CATS = d["t"], d["c"]
html = io.open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
body = html[html.index('<div class="wrap">'):]
body = re.sub(r"<style.*?</style>", "", body, flags=re.S)
PROSE = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
# 갱신 이력은 과거 시점의 수치·인용을 담으므로 검사에서 제외한다
_log = body[body.index('data-view="log"'):] if 'data-view="log"' in body else ""
LIVE = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body[:body.index('data-view="log"')] if _log else body))
DATA = " ".join(" ".join(filter(None, [t.get("d"), t.get("n"), t.get("s")])) for t in TERMS)

def head(x): print("\n" + x); print("-" * len(x) * 2)

# ── 1. 데이터 무결성 ──────────────────────────────────
head("1. 데이터 무결성")
ids = {c["id"] for c in CATS}
orphan = [t["k"] for t in TERMS if t["c"] not in ids]
dup = [k for k, n in Counter(t["k"] for t in TERMS).items() if n > 1]
tagged = [t["k"] for t in TERMS for f in ("d", "n", "s") if t.get(f) and re.search(r"<[a-z/]", t[f], re.I)]
noD = [t["k"] for t in TERMS if not t.get("d")]
for label, lst in [("분류 미매칭", orphan), ("중복 용어", dup), ("HTML 태그 혼입", tagged), ("정의 누락", noD)]:
    if lst: fail.append("%s: %s" % (label, ", ".join(lst[:8])))
print("용어 %d · 분류 %d · 현황줄 %d · 뉘앙스 %d"
      % (len(TERMS), len(CATS), sum(1 for t in TERMS if t.get("s")), sum(1 for t in TERMS if t.get("n"))))
print("이상: %s" % ("없음" if not (orphan or dup or tagged or noD) else "위 목록 참고"))

# ── 2. 화면 표기와 실제 수치 ──────────────────────────
head("2. 화면에 적힌 수치")
for pat, real, name in [
    (r"(\d+)개 용어", len(TERMS), "머리말 용어 수"),
    (r"(\d+)개를 다 읽을", len(TERMS), "상황별 탭 용어 수"),
    (r"사전 탭에 (\d+)개", len(TERMS), "상황별 안내 용어 수"),
    (r"(\d+)개 중 (\d+)개다", None, "시점 의존 안내"),
]:
    m = re.search(pat, LIVE)
    if not m: continue
    if name == "시점 의존 안내":
        a, b = int(m.group(1)), int(m.group(2))
        ok = (a == len(TERMS) and b == sum(1 for t in TERMS if t.get("s")))
        print("%-18s %s → %s" % (name, m.group(0), "일치" if ok else "불일치"))
        if not ok: fail.append("%s 불일치: 화면 %s / 실제 %d 중 %d" % (name, m.group(0), len(TERMS), sum(1 for t in TERMS if t.get("s"))))
    else:
        ok = int(m.group(1)) == real
        print("%-18s %s → %s" % (name, m.group(0), "일치" if ok else "불일치"))
        if not ok: fail.append("%s 불일치: 화면 %s / 실제 %d" % (name, m.group(1), real))

# ── 3. 깎아내리는 표현 · 위치 참조 ────────────────────
head("3. 표현 원칙")
BAN = {
 "사람을 깎아내림": r"촌스럽|아마추어 소리|까임|까인다|욕이다|시간이 멈춘|신뢰를 잃|업데이트가 안 된|삽질|무식|한심|놈|녀석|따위",
 "위치 참조": r"아래 표|위 표|앞서 본|위에서 본|아래 항목|이 용어집",
 "회사 관점": r"위뉴가|위뉴의|위뉴에|우리 제품|우리 본업|내부 공유용",
}
for name, pat in BAN.items():
    hits = [(lbl, m.group(0).strip()) for lbl, txt in (("본문", LIVE), ("사전", DATA))
            for m in re.finditer(r".{0,30}(" + pat + r").{0,30}", txt)]
    print("%-14s %d건" % (name, len(hits)))
    for lbl, x in hits[:6]:
        print("   [%s] …%s…" % (lbl, x)); warn.append("%s [%s] %s" % (name, lbl, x))

# ── 4. 윤문 지표 (밀도) ───────────────────────────────
head("4. 윤문 지표 — 1만자당 밀도")
COMMA = r"(?:[가-힣]*(?:하|되|있|없|[가-힣])(?:고|며|지만|면서|어서|아서)|[가-힣]*(?:했|됐|왔|갔|봤|났|았|었)(?:고|지만|는데)),"
CHECKS = {
 "연결어미+쉼표": COMMA,
 "'가지고 있다'": r"가지고 있다|갖고 있다",
 "이중 피동": r"되어지|지게 된다",
 "'~에 있어'": r"에 있어",
 "'~를 통해'": r"를 통해|을 통해|통하여",
 "'~에 의해' 피동": r"에 의해 [가-힣]+되|에 의해 생성",
 "AI 결산어": r"결론적으로|요약하면|정리하자면|이를 통해",
 "의의 과장": r"시사하는 바|주목할 만|매우 중요",
 "hype 어휘": r"혁신적|획기적|압도적|전례 없는|파격적",
 "분열문": r"관건은|중요한 것은|필요한 것은",
 "'~하는 이유다'": r"하는 이유다|인 이유다",
 "사전 은유": r"잠식|청사진|적신호|신호탄|뿌리내리|짓누르|발판",
 "'~고 있다'": r"고 있다",
 "이중 완곡": r"가능성이 있을 수|수 있을 것으로 보",
}
print("%-16s %9s %9s" % ("지표", "사전", "본문"))
for n, pt in CHECKS.items():
    a = len(re.findall(pt, DATA)) / len(DATA) * 10000
    b = len(re.findall(pt, PROSE)) / len(PROSE) * 10000
    mark = ""
    if n != "연결어미+쉼표" and max(a, b) > 3: mark = "  ← 확인"; warn.append("윤문 지표 %s 높음" % n)
    print("%-16s %8.1f  %8.1f%s" % (n, a, b, mark))

# ── 5. 반복 버릇 ──────────────────────────────────────
head("5. 같은 말 반복 (1만자당 5회 초과 시 확인)")
for w in ["자리", "지점", "대목", "장치", "관문", "정석", "카드", "중론", "셈이다"]:
    a = len(re.findall(w, DATA)) / len(DATA) * 10000
    b = len(re.findall(w, PROSE)) / len(PROSE) * 10000
    if max(a, b) > 5: warn.append("반복 '%s'" % w)
    print("%-8s 사전 %4.1f  본문 %4.1f%s" % (w, a, b, "  ← 확인" if max(a, b) > 5 else ""))

# ── 6. 리듬 ───────────────────────────────────────────
head("6. 리듬")
for label, txt in (("사전", DATA), ("본문", PROSE)):
    c = Counter(m[-2:] for m in re.findall(r"([가-힣]{2,3})\.", txt)); tot = sum(c.values()) or 1
    top = c.most_common(3)
    over = [k for k, v in top if v / tot > .15]
    if over: warn.append("%s 종결 '%s' 쏠림" % (label, over[0]))
    print("%-4s 종결 상위: %s%s" % (label, ", ".join("%s %.0f%%" % (k, v/tot*100) for k, v in top),
                                  "  ← 15% 초과" if over else ""))

# ── 7. 2025년 이후 용어 출처 확인 대상 ────────────────
head("7. 출처 확인 대상 (2025년 이후 등장)")
recent = [t["k"] for t in TERMS if re.search(r"202[5-9]", t.get("y", ""))]
print("%d개 — 정의를 고칠 때는 원문을 다시 확인할 것" % len(recent))

# ── 결과 ──────────────────────────────────────────────
print("\n" + "=" * 46)
if fail:
    print("실패 %d건" % len(fail))
    for f in fail: print("  ✗ " + f)
if warn:
    print("확인 필요 %d건" % len(warn))
    for w in warn[:12]: print("  ! " + w)
if not fail and not warn: print("전부 통과")
sys.exit(1 if fail else 0)
