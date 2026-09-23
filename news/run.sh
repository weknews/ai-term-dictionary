#!/bin/zsh
# 오늘의 늬우스 — launchd가 평일 07:30에 부른다. 수동: news/run.sh [YYYY-MM-DD]
#
# 돈 드는 일은 모델 두 턴뿐이고, 둘 다 도구 없이 1턴이다 (hn-researcher gen_report.sh 재사용).
#   1 수집   collect.py — hn-researcher가 06:44에 받아 둔 목록 + 연구소 피드 → 코드로 1차 거르기   0 토큰
#   2 선별   1턴 — 약 150줄에서 8~12건                                                       ~2만 토큰
#   3 원문   hn-researcher fetch_pages.py                                                    0 토큰
#   4 작문   1턴 — 확보한 원문만 보고 JSON 한 장                                              ~5만 토큰
#   5 검증   verify.py — 링크·숫자·금지 표현. 실패하면 사유를 붙여 고쳐 쓰기 1회, 그래도 실패면 쉰다
#   6 발행   news/DATE 브랜치로 PR. 사람이 병합하면 Actions가 Pages에 올린다
#
# 커밋은 별도 worktree(origin/main 기준)에서 한다. 사람이 쓰는 체크아웃의 브랜치·변경분은 건드리지 않는다.
set -e
export PATH="$HOME/.local/share/mise/shims:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
REPO=$PWD
HNR="${HNR:-$HOME/myworks/hn-researcher}"
export MODEL="${NEWS_MODEL:-sonnet}"   # 반드시 지정한다 — 비우면 대화용 최상위 모델을 물려받아 사용량이 몇 배로 뛴다
DATE=${1:-$(date +%F)}
W=$REPO/news/work
mkdir -p "$W"
LOG="$W/run-$DATE.log"
exec >>"$LOG" 2>&1
echo "== $(date +'%F %T') 시작 ($DATE, model=$MODEL)"

# 자동 실행은 평일만. 날짜를 주면 요일과 상관없이 돈다
[ -z "$1" ] && [ "$(date +%u)" -gt 5 ] && { echo "주말 — 건너뜀"; exit 0; }

LOCK=$W/.lock
[ -d "$LOCK" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin -120)" ] && rmdir "$LOCK"
mkdir "$LOCK" 2>/dev/null || { echo "다른 실행 중 — 건너뜀"; exit 0; }
WT=$W/wt-$DATE
cleanup() { git -C "$REPO" worktree remove --force "$WT" 2>/dev/null || true; rmdir "$LOCK" 2>/dev/null; }
trap cleanup EXIT
fail() {
  echo "실패: $1"
  osascript -e "display notification \"$1\" with title \"오늘의 늬우스 $DATE 실패\"" 2>/dev/null || true
  exit 1
}

# hn-researcher가 아직 돌고 있으면 최대 30분 기다린다. 그래도 없으면 collect.py가 직접 받는다
for i in {1..30}; do [ -d "$HNR/logs/.lock" ] || break; sleep 60; done

git fetch -q origin main
git ls-remote --exit-code origin "refs/heads/news/$DATE" >/dev/null 2>&1 && { echo "이미 PR 브랜치 있음"; exit 0; }
git worktree add -q -B "news/$DATE" "$WT" origin/main
[ -f "$WT/news/$DATE.json" ] && { echo "이미 발행됨"; exit 0; }

# 스크립트·프롬프트는 이 체크아웃 것을 쓰고, 데이터(최근 발행분·terms.js)는 origin/main worktree에서 읽는다
export NEWS_ROOT=$WT

# 1 수집
python3 "$REPO/news/collect.py" "$DATE" "$W" "$HNR" || fail "수집 실패"

# 2 선별
MIN_BYTES=50 "$HNR/gen_report.sh" "$W/select-$DATE.txt" <<EOF || fail "선별 턴 실패"
$(cat "$REPO/news/prompts/select.md")
$(cat "$W/list-$DATE.md")
EOF
python3 - "$W/select-$DATE.txt" "$W/candidates-$DATE.json" "$W/list-$DATE.md" <<'PY' || fail "선별 결과 해석 실패"
import json, re, sys
raw, out, lst = open(sys.argv[1]).read(), sys.argv[2], open(sys.argv[3]).read()
cands = json.loads(re.search(r"\[.*\]", raw, re.S).group(), strict=False)
titles = dict(re.findall(r"#(\S+) (.*)", lst))
# fetch_pages.py가 쓰는 형식으로 맞춘다 (title·project·why)
# 제목 앞에 #ID를 붙인다 — 원문 파일의 제목 줄에 ID가 찍혀야 작문 턴이 항목마다 ID를 정확히 단다
json.dump([{"id": c["id"].lstrip("#"), "title": "#%s %s" % (c["id"].lstrip("#"), titles.get(c["id"].lstrip("#"), "")),
            "project": "늬우스", "why": c.get("why", "")} for c in cands], open(out, "w"), ensure_ascii=False)
print(f"선별 {len(cands)}건")
PY

# 3 원문
python3 "$HNR/fetch_pages.py" "$W/candidates-$DATE.json" "$W/urls-$DATE.json" > "$W/pages-$DATE.md" \
  || fail "원문 수집 실패"
echo "원문: $(head -1 "$W/pages-$DATE.md")"

TERMS=$(node -e "global.window={};require('$WT/terms.js');console.log(window.TERMS.map(t=>t.k).join(', '))")
write_turn() {  # $1 출력 경로, $2 덧붙일 지시(고쳐 쓰기일 때)
  MIN_BYTES=500 "$HNR/gen_report.sh" "$1" <<EOF
$(cat "$REPO/news/prompts/write.md")

## 발행일
$DATE

## 용어집 표제어 목록 (terms에는 이 중에서만)
$TERMS
$2

## 수집해 둔 원문
$(cat "$W/pages-$DATE.md")
EOF
}

# 4 작문 → 5 검증 (실패하면 사유를 붙여 한 번 더)
write_turn "$W/draft-$DATE.json" "" || fail "작문 턴 실패"
if ! python3 "$REPO/news/verify.py" "$DATE" "$W/draft-$DATE.json" "$W/urls-$DATE.json" \
       "$W/pages-$DATE.md" "$WT/news/$DATE.json" > "$W/verify-$DATE.txt"; then
  echo "검증 1차 실패:"; cat "$W/verify-$DATE.txt"
  write_turn "$W/draft2-$DATE.json" "
## 고쳐 쓰기
직전 초안이 검증에서 떨어졌다. 아래 사유를 전부 고친 새 JSON을 낸다. 원문에 없는 숫자는 빼거나 원문 표기 그대로 옮긴다.

### 직전 초안
$(cat "$W/draft-$DATE.json")

### 검증 사유
$(cat "$W/verify-$DATE.txt")" || fail "고쳐 쓰기 턴 실패"
  python3 "$REPO/news/verify.py" "$DATE" "$W/draft2-$DATE.json" "$W/urls-$DATE.json" \
    "$W/pages-$DATE.md" "$WT/news/$DATE.json" > "$W/verify2-$DATE.txt" \
    || { cat "$W/verify2-$DATE.txt"; fail "검증 2차 실패 — 오늘은 발행하지 않음"; }
  RETRIED="고쳐 쓰기 1회 후 통과"
fi
cat "$W/verify"*"-$DATE.txt" | tail -1

# 로컬 검토(news/review.sh)가 이 사본과 원문(pages)을 나란히 띄운다
cp "$WT/news/$DATE.json" "$W/final-$DATE.json"
# DRY=1이면 여기서 멈춘다 (시험용)
[ -n "$DRY" ] && { echo "DRY — PR 생략: $W/final-$DATE.json"; exit 0; }

# 6 PR
HEAD=$(python3 -c "import json;print(json.load(open('$WT/news/$DATE.json'))['headline'])")
python3 - "$WT/news/$DATE.json" "$W/dead-$DATE.txt" "${RETRIED:-1회 통과}" > "$W/pr-$DATE.md" <<'PY'
import json, sys
d, dead = json.load(open(sys.argv[1])), open(sys.argv[2]).read().split("\n")
dead = [x for x in dead if x]
print(f"**{d['headline']}**\n\n{d['lede']}\n\n## 항목")
for i in d["items"]:
    print(f"- [{i['kind']}] **{i['title']}** — [{i['source']}]({i['url']})")
if d.get("new_terms"):
    print("\n## 용어집 후보 (검토 필요)")
    for t in d["new_terms"]:
        print(f"- **{t['term']}** — {t.get('note', '')}")
print(f"\n## 검증\n- {sys.argv[3]}\n- 숫자·링크는 코드로 대조했다. **해석이 원문과 맞는지는 사람이 본다.**")
if dead:
    print(f"- 오늘 실패한 소스: {', '.join(dead)}")
PY
git -C "$WT" add "news/$DATE.json"
git -C "$WT" commit -qm "늬우스 $DATE — $HEAD"
git -C "$WT" push -q -u origin "news/$DATE"
gh pr create -R weknews/ai-term-dictionary --base main --head "news/$DATE" \
  --title "늬우스 $DATE — $HEAD" --body-file "$W/pr-$DATE.md"
osascript -e "display notification \"검토: news/review.sh — $HEAD\" with title \"오늘의 늬우스 $DATE 초안\" sound name \"Glass\"" 2>/dev/null || true

find "$W" -maxdepth 1 -type f -name '*-20*' -mtime +14 -delete 2>/dev/null || true
echo "== $(date +'%F %T') 완료"
