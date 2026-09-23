#!/usr/bin/env python3
"""늬우스 검증 + 확정. LLM을 쓰지 않는다.

작문 턴이 낸 JSON 초안을 수집해 둔 원문과 대조한다. 통과하면 news/DATE.json으로 확정한다.
출처 검증을 사람 기억이 아니라 구조로 한다 — 모델은 확보된 원문만 받았고,
여기서 다음을 기계로 확인한다.

  1. 모든 항목이 실제로 수집에 성공한 후보를 가리키는가
  2. 본문에 나온 숫자가 그 항목의 원문에 글자 그대로 있는가 (지어낸 수치 차단)
     헤드라인·도입부 숫자는 그날 원문 전체와 대조한다
  3. 과장·결산어·깎아내림·위치 참조 같은 금지 표현 (용어집 check.py와 같은 기준)
  4. terms가 실제 용어집 표제어인가 (아니면 조용히 뺀다 — 발행을 막을 일은 아니다)

실패하면 사유를 stdout에 찍고 exit 1. run.sh가 그 사유를 붙여 고쳐 쓰기를 한 번 시킨다.

한계: 숫자를 맞게 옮기고 해석을 틀리는 경우는 못 잡는다. 그래서 초기에는 PR로 사람이 본다.

Usage: verify.py DATE DRAFT.json urls.json pages.md OUT.json
"""
import json
import os
import re
import subprocess
import sys
from urllib.parse import urlparse

DATE, DRAFT, URLS, PAGES, OUT = sys.argv[1:6]
# 데이터(발행분·terms.js)를 읽을 곳. run.sh가 origin/main worktree를 넘긴다
ROOT = os.environ.get("NEWS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ponytail: check.py의 금지 목록과 겹친다. check.py가 모듈이 아니라 스크립트라서 복사했다 —
# 셋째 소비처가 생기면 공용 파일로 뺀다.
BAN = {
    "과장": r"혁신적|획기적|압도적|전례 없는|파격적|게임 ?체인저|판도를 바꾼|주목할 만|시사하는 바",
    "결산어": r"결론적으로|요약하면|정리하자면|이를 통해|관건은|중요한 것은|하는 이유다|라고 할 수 있다",
    "깎아내림": r"촌스럽|아마추어|까인다|한심|무식|따위|녀석",
    "위치 참조": r"아래 표|위 표|앞서 본|위에서 본|아래 항목",
    "회사 관점": r"위뉴가|위뉴의|우리 회사|우리 제품",
    # 원문에 없는 전망을 덧붙이는 버릇. 첫 시험(2026-09-23)에서 두 번 연속 나왔다
    "원문 밖 전망": r"위험을 (?:오히려 )?키울|위험이 커진|본격화|경쟁이 (?:붙었|시작됐)|불붙|재점화|판도가|새 시대|만에 해낸",
    "반복 버릇": r"대목|지점|참고할 만하다",
}
NUM = re.compile(r"\d+(?:[.,]\d+)*")


def load_terms():
    out = subprocess.check_output(["node", "-e",
        "global.window={};require('%s/terms.js');process.stdout.write(JSON.stringify(window.TERMS.map(t=>t.k)))" % ROOT])
    return set(json.loads(out))


def parse_pages(path):
    """fetch_pages.py 출력 → {URL: 본문}. 항목은 '---' 줄로 끊긴다."""
    bodies = {}
    for block in open(path).read().split("\n---\n"):
        m = re.search(r"URL: (\S+)", block)
        if m:
            bodies[m.group(1)] = block
    return bodies


def norm(s):
    return s.replace(",", "")


# "3억 5천만", "3억 7천5백만", "1.3억" 같은 한국어 단위 금액
KOR_UNIT = re.compile(r"(?:(\d+(?:\.\d+)?)\s*억)?\s*(?:(\d+)\s*천)?\s*(?:(\d+)\s*백)?\s*(?:(\d+)\s*)?만|(\d+(?:\.\d+)?)\s*억")
EN_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(billion|million|bn|[BM])\b", re.I)


def kor_value(m):
    if m.group(5):
        return round(float(m.group(5)) * 1e8)
    eok, cheon, baek, rest = (float(g) if g else 0 for g in m.groups()[:4])
    return round(eok * 1e8 + (cheon * 1000 + baek * 100 + rest) * 1e4)


def source_values(source):
    vals = {round(float(n) * (1e9 if u.lower() in ("billion", "bn", "b") else 1e6)) for n, u in EN_UNIT.findall(source)}
    vals |= {int(norm(n)) for n in re.findall(r"\d{1,3}(?:,\d{3})+|\d{5,}", source)}
    if re.search(r"\bmillion\b|\b1M\b", source, re.I):   # "per million tokens" → "100만 토큰당"
        vals.add(10**6)
    if re.search(r"\bbillion\b", source, re.I):
        vals.add(10**9)
    return vals


def stray_numbers(text, source):
    """원문에 없는 숫자.
    한국어 단위 금액("3억 5천만")은 값으로 환산해 원문의 "350 million"과 맞춰 본다. 쪼개서 보면
    한 자리 수 예외에 걸려 대조를 빠져나가기 때문이다(2026-09-23 실제로 그렇게 빠져나갔다)."""
    src = norm(source)
    kor, vals = [], None
    for m in KOR_UNIT.finditer(text):
        if not m.group(0).strip() or m.group(0).strip() == "만":
            continue
        vals = vals if vals is not None else source_values(source)
        if kor_value(m) not in vals:
            kor.append(m.group(0).strip())
    text = KOR_UNIT.sub(" ", text)   # 환산을 통과한 금액은 개별 숫자 대조에서 뺀다
    bad = []
    for n in NUM.findall(text):
        k = norm(n)
        if len(k) == 1:                      # 한 자리 수는 서수·개수로 흔해 오탐만 늘린다
            continue
        if re.fullmatch(r"20[2-3]\d", k):    # 연도
            continue
        # 숫자 경계를 본다 — "95"가 "1995" 안에 들어 있다고 통과시키지 않는다
        if not re.search(r"(?<![\d.])" + re.escape(k) + r"(?![\d])", src):
            bad.append(n)
    return bad + [f"{k}(환산하면 원문 어느 금액과도 맞지 않는다)" for k in kor]


def main():
    urls = json.load(open(URLS))
    bodies = parse_pages(PAGES)
    raw = open(DRAFT).read()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        print("JSON 객체를 찾지 못함")
        return 1
    try:
        d = json.loads(m.group(), strict=False)  # 모델이 문자열 안에 줄바꿈을 그대로 넣는 경우가 있다
    except json.JSONDecodeError as e:
        print(f"JSON 파싱 실패: {e}")
        return 1

    errs, terms = [], load_terms()
    items = d.get("items") or []
    if not 4 <= len(items) <= 7:
        errs.append(f"항목 수 {len(items)}개 — 4~7개여야 한다")
    big = sum(1 for it in items if it.get("size") == "big")
    if not 1 <= big <= 2:
        errs.append(f"큰 소식(size=big)이 {big}개 — 1~2개여야 한다")
    s3 = d.get("summary3") or []
    if len(s3) != 3:
        errs.append(f"summary3가 {len(s3)}줄 — 세 줄이어야 한다")

    # 헤드라인·세 줄 요약·오늘의 용어 설명은 특정 항목에 묶이지 않으니 그날 원문 전체와 대조한다
    all_src = "\n".join(bodies.values())
    tod = d.get("term_of_day") or {}
    for label, text in [("headline", d.get("headline", "")), ("summary3", " ".join(s3)),
                        ("오늘의 용어", tod.get("context", ""))]:
        for n in stray_numbers(text, all_src):
            errs.append(f"{label}: 숫자 '{n}'이 어느 원문에도 없다")
    if tod and tod.get("term") not in terms:
        d["term_of_day"] = None   # 표제어가 아니면 칸만 비운다 — 발행을 막을 일은 아니다

    for it in items:
        cid = str(it.get("id", "")).lstrip("#")
        url = urls.get(cid, "")
        body = bodies.get(url)
        label = it.get("title", cid)[:30]
        if not body:
            errs.append(f"[{label}] 후보 {cid}의 원문이 수집되지 않았다 — 근거 없는 항목")
            continue
        text = " ".join(str(it.get(k, "")) for k in ("title", "summary", "why"))
        # 원문을 읽은 항목에 "댓글 기준"을 붙이면 출처를 거꾸로 적은 것이다 (반대도 마찬가지)
        comments = bool(re.search(r"^- HN 댓글 \|", body, re.M))
        if "댓글 기준" in text and not comments:
            errs.append(f"[{label}] 원문을 읽은 항목인데 '댓글 기준'이라고 적었다")
        if comments and "댓글 기준" not in text:
            errs.append(f"[{label}] 댓글만 읽은 항목인데 '댓글 기준'이라고 밝히지 않았다")
        for n in stray_numbers(text, body):
            errs.append(f"[{label}] 숫자 '{n}'이 원문에 없다")
        limit = 450 if it.get("size") == "big" else 220
        if len(it.get("summary", "")) > limit:
            errs.append(f"[{label}] 요약이 너무 길다 ({len(it['summary'])}자, {it.get('size')}는 {limit}자까지)")
        it["id"], it["url"] = cid, url
        it["source"] = urlparse(url).netloc.removeprefix("www.")
        it["terms"] = [t for t in it.get("terms") or [] if t in terms]

    # 스타트업 렌즈 — 항목과 같은 기준에 더해, 결론 대신 질문으로 끝나는지 본다
    lens = d.get("lens") or []
    if len(lens) > 2:
        errs.append(f"스타트업 렌즈가 {len(lens)}개 — 2개까지")
    for l in lens:
        cid = str(l.get("id", "")).lstrip("#")
        url = urls.get(cid, "")
        body = bodies.get(url)
        label = "렌즈 " + l.get("title", cid)[:24]
        if not body:
            errs.append(f"[{label}] 후보 {cid}의 원문이 수집되지 않았다 — 근거 없는 항목")
            continue
        for n in stray_numbers(" ".join(str(l.get(k, "")) for k in ("title", "what", "who", "question")), body):
            errs.append(f"[{label}] 숫자 '{n}'이 원문에 없다")
        if not str(l.get("question", "")).strip().endswith("?"):
            errs.append(f"[{label}] question이 물음표로 끝나지 않는다 — 결론이 아니라 질문이어야 한다")
        if re.search(r"위뉴", json.dumps(l, ensure_ascii=False)):
            errs.append(f"[{label}] 특정 회사 이름을 썼다")
        l["id"], l["url"] = cid, url
        l["source"] = urlparse(url).netloc.removeprefix("www.")

    blob = json.dumps(d, ensure_ascii=False)
    for name, pat in BAN.items():
        for hit in re.findall(pat, blob):
            errs.append(f"금지 표현({name}): '{hit}'")

    if errs:
        print("\n".join(f"- {e}" for e in errs))
        return 1

    d = {"date": DATE, **{k: d.get(k) for k in ("headline", "summary3", "items", "lens", "term_of_day", "new_terms", "dropped")}}
    json.dump(d, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"통과: {len(items)}개 항목 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
