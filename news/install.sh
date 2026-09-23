#!/bin/zsh
# launchd에 평일 07:30 작업을 등록한다. 해제: launchctl bootout gui/$(id -u)/com.weknews.ai-news
set -e
cd "$(dirname "$0")/.."
mkdir -p news/work
DST=~/Library/LaunchAgents/com.weknews.ai-news.plist
sed "s|__REPO__|$PWD|g" news/com.weknews.ai-news.plist > "$DST"
launchctl bootout gui/$(id -u)/com.weknews.ai-news 2>/dev/null || true
launchctl bootstrap gui/$(id -u) "$DST"
echo "등록: $DST (평일 07:30)"
