#!/bin/zsh
# 주간 용어집 — launchd가 금요일 뉴스(run.sh) 다음에 부른다. 수동: news/weekly.sh [YYYY-MM-DD]
#
# 지난 갱신 뒤 발행된 뉴스를 근거로 terms.js 수정안(새 용어 · 현황 줄)을 PR로 낸다. 자동 병합하지 않는다.
# 매일 고치지 않는 이유: 하루치 근거로 현황이 흔들리고, 칸을 채우려는 억지 변경이 는다 (2026-10-20 결정).
#   1 모으기  weekly.py digest — 지난 갱신(CHANGES의 upto) 뒤 발행된 호의 항목·용어집 후보   0 토큰
#   2 선별    1턴 — 넣을 말·고칠 현황과 근거 항목                                        ~3만 토큰 (첫 회는 더)
#   3 원문    hn-researcher fetch_pages.py — 근거 항목의 원문                             0 토큰
#   4 작문    1턴 — 원문만 보고 항목을 쓴다                                               ~5만 토큰
#   5 검증    weekly.py apply — 근거·숫자·금지 표현·중복. 떨어진 항목은 빼고 사유를 PR에 남긴다
#   6 PR      check.py 통과 후 glossary/DATE 브랜치로. 병합하면 그날 호에 "이번 주 용어집에서 바뀐 것"이 붙는다
set -e
export PATH="$HOME/.local/share/mise/shims:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
cd "$(dirname "$0")/.."
REPO=$PWD
R=weknews/ai-term-dictionary
HNR="${HNR:-$HOME/myworks/hn-researcher}"
export MODEL="${NEWS_MODEL:-sonnet}"   # 반드시 지정한다 — run.sh와 같은 이유
DATE=${1:-$(date +%F)}
W=$REPO/news/work
mkdir -p "$W"
exec >>"$W/weekly-$DATE.log" 2>&1
echo "== $(date +'%F %T') 시작 ($DATE, model=$MODEL)"

note() { osascript -e "display notification \"$1\" with title \"용어집 주간 갱신 $DATE\"${2:+ sound name \"$2\"}" 2>/dev/null || true; }
fail() { echo "실패: $1"; note "실패: $1"; exit 1; }
# weekly.py는 할 일이 없으면 3으로 끝난다 — 실패가 아니다
wk() { local rc=0; python3 "$REPO/news/weekly.py" "$1" "$DATE" "$W" || rc=$?
       [ $rc = 3 ] && { echo "이번 주는 바꿀 것 없음 ($1)"; note "이번 주는 바꿀 것 없음"; exit 0; }
       [ $rc = 0 ] || fail "weekly.py $1"; }

# 지난 PR이 열려 있으면 같은 호를 또 읽게 된다. 병합하거나 닫은 뒤에 돈다
OPEN=$(gh pr list -R $R --state open --json headRefName -q '.[].headRefName | select(startswith("glossary/"))' | head -1)
[ -z "$OPEN" ] || fail "지난 갱신 PR($OPEN)이 아직 열려 있다"

git fetch -q origin main
WT=$W/wt-glossary-$DATE
git worktree add -q -B "glossary/$DATE" "$WT" origin/main
trap 'git -C "$REPO" worktree remove --force "$WT" 2>/dev/null || true' EXIT
export NEWS_ROOT=$WT   # 데이터(terms.js·발행분)는 origin/main에서 읽고 고친다

# 1 모으기 → 2 선별
wk digest
MIN_BYTES=20 "$HNR/gen_report.sh" "$W/wk-select-$DATE.txt" <<EOF || fail "선별 턴 실패"
$(cat "$REPO/news/prompts/weekly-select.md")
$(cat "$W/wk-digest-$DATE.md")
EOF
wk pick

# 3 원문 → 4 작문
python3 "$HNR/fetch_pages.py" "$W/wk-cands-$DATE.json" > "$W/wk-pages-$DATE.md" || fail "원문 수집 실패"
echo "원문: $(head -1 "$W/wk-pages-$DATE.md")"
MIN_BYTES=100 "$HNR/gen_report.sh" "$W/wk-draft-$DATE.txt" <<EOF || fail "작문 턴 실패"
$(cat "$REPO/news/prompts/weekly-write.md")

$(cat "$W/wk-picked-$DATE.md")

## 근거 원문
$(cat "$W/wk-pages-$DATE.md")
EOF

# 5 검증·반영 → 6 PR
wk apply
python3 "$WT/check.py" > "$W/wk-check-$DATE.txt" || { cat "$W/wk-check-$DATE.txt"; fail "check.py 실패 — PR을 내지 않았다"; }
{ echo "\n## check.py\n\`\`\`"; sed -n '/^=====/,$p' "$W/wk-check-$DATE.txt"; echo "\`\`\`"; } >> "$W/wk-pr-$DATE.md"

SUM=$(head -1 "$W/wk-pr-$DATE.md" | tr -d '*')
git -C "$WT" add terms.js
git -C "$WT" commit -qm "용어집 $DATE — ${SUM%% —*}"
git -C "$WT" push -q -u origin "glossary/$DATE"
gh pr create -R $R --base main --head "glossary/$DATE" --title "용어집 $DATE — $SUM" --body-file "$W/wk-pr-$DATE.md"
note "검토할 PR: ${SUM%% —*}" Glass
echo "== $(date +'%F %T') 완료"
