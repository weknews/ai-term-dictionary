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
    "원문 밖 전망": r"위험을 (?:오히려 )?키울|위험이 커진|본격화|경쟁이 (?:붙었|시작됐)|판도가|새 시대|만에 해낸",
    "반복 버릇": r"대목|참고할 만하다",
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


def stray_numbers(text, source):
    src = norm(source)
    # 토큰 단가의 "100만 토큰당"은 원문 "per million"의 번역이다. 숫자 변환 금지의 유일한 예외
    if re.search(r"per million|per 1M|/1M|1M tokens|million tokens", source, re.I):
        text = re.sub(r"100만(?=\s*(?:(?:입력|출력)?\s*토큰)?\s*개?\s*당)", "", text)
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
    return bad


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
    if not 3 <= len(items) <= 8:
        errs.append(f"항목 수 {len(items)}개 — 3~8개여야 한다")

    all_src = "\n".join(bodies.values())
    for field in ("headline", "lede"):
        for n in stray_numbers(d.get(field, ""), all_src):
            errs.append(f"{field}: 숫자 '{n}'이 어느 원문에도 없다")

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
        if len(it.get("summary", "")) > 450:
            errs.append(f"[{label}] 요약이 너무 길다 ({len(it['summary'])}자)")
        it["id"], it["url"] = cid, url
        it["source"] = urlparse(url).netloc.removeprefix("www.")
        it["terms"] = [t for t in it.get("terms") or [] if t in terms]

    blob = json.dumps(d, ensure_ascii=False)
    for name, pat in BAN.items():
        for hit in re.findall(pat, blob):
            errs.append(f"금지 표현({name}): '{hit}'")

    if errs:
        print("\n".join(f"- {e}" for e in errs))
        return 1

    d = {"date": DATE, **{k: d.get(k) for k in ("headline", "lede", "items", "new_terms", "dropped")}}
    json.dump(d, open(OUT, "w"), ensure_ascii=False, indent=1)
    print(f"통과: {len(items)}개 항목 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
