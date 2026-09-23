#!/bin/zsh
# 발행 전 로컬 검토. 사용법: news/review.sh [YYYY-MM-DD]  (기본: 오늘)
#
# 1. 초안을 실제 발행 화면 그대로 띄운다 (index.html?review=날짜). 원문은 각 항목의 링크로 연다
# 2. 터미널에서 고른다
#      a  승인 — PR을 병합해 발행 (Actions가 check.py 후 Pages 반영)
#      e  수정 — 편집기로 JSON을 고치고, verify.py를 통과해야 PR에 반영. 반영 뒤 다시 보여 준다
#      q  나중에 — 아무것도 하지 않는다
# 사람이 쓰는 체크아웃은 건드리지 않는다. 초안 사본은 news/work/, 커밋은 임시 worktree에서.
set -e
cd "$(dirname "$0")/.."
REPO=$PWD
DATE=${1:-$(date +%F)}
W=$REPO/news/work
R=weknews/ai-term-dictionary
PORT=8752

PR=$(gh pr list -R $R --head "news/$DATE" --state open --json number -q '.[0].number')
[ -n "$PR" ] || { echo "news/$DATE 로 열린 PR이 없다 (이미 병합됐거나 오늘은 실패). 로그: $W/run-$DATE.log"; exit 1; }

# 원격 PR 브랜치가 정본이다 — 웹에서 고쳤어도 그걸 본다
git fetch -q origin "news/$DATE"
git show "origin/news/$DATE:news/$DATE.json" > "$W/final-$DATE.json"
[ -f "$W/pages-$DATE.md" ] || echo "경고: 원문 파일이 없다 — 수정(e) 시 검증을 못 한다"

python3 -m http.server $PORT -d "$REPO" >/dev/null 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
sleep 1
open "http://localhost:$PORT/?review=$DATE"

echo "PR #$PR — https://github.com/$R/pull/$PR"
echo "먼저 볼 것: 원문보다 센 해석어 · 누가 한 말인지 · 비교 기준 · why에 원문 밖 전망이 붙었는지"
while true; do
  read "k?[a] 승인·발행  [e] 수정  [q] 나중에 > "
  case $k in
    a)
      gh pr merge $PR -R $R --squash --delete-branch
      echo "병합했다. 1분쯤 뒤 https://weknews.github.io/ai-term-dictionary/ 에 뜬다."
      exit 0 ;;
    e)
      cp "$W/final-$DATE.json" "$W/edit-$DATE.json"
      ${EDITOR:-vi} "$W/edit-$DATE.json"
      # 사람이 고친 것도 같은 기준을 통과해야 한다 — 숫자를 손으로 바꾸다 틀리는 경우가 있다
      if ! python3 news/verify.py "$DATE" "$W/edit-$DATE.json" "$W/urls-$DATE.json" \
             "$W/pages-$DATE.md" "$W/final-$DATE.json"; then
        echo "검증에 걸렸다. 다시 e로 고치거나 q로 나간다 (고친 내용은 $W/edit-$DATE.json 에 남아 있다)"
        continue
      fi
      WT=$W/wt-review-$DATE
      git worktree add -q -f "$WT" "origin/news/$DATE" 2>/dev/null
      cp "$W/final-$DATE.json" "$WT/news/$DATE.json"
      git -C "$WT" commit -qam "뉴스 $DATE — 로컬 검토 반영"
      git -C "$WT" push -q origin "HEAD:news/$DATE"
      git worktree remove --force "$WT"
      echo "PR에 반영했다. 브라우저를 새로고침해 다시 본다." ;;
    q) exit 0 ;;
  esac
done
