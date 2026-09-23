#!/usr/bin/env python3
"""늬우스 1단계: 후보 목록 만들기. LLM을 쓰지 않는다.

HN·Lobsters·GeekNews 하루치는 hn-researcher가 매일 06:44에 이미 받아 둔다.
그걸 그대로 읽고(없으면 hn-researcher의 수집기를 직접 부른다), 연구소 블로그 피드만 더한다.
그다음 코드로 1차 거르기를 한다 — 1,100여 건을 선별 턴에 통째로 넘기면 그것만으로
10만 토큰이 넘는다. AI 관련 키워드와 반응 점수로 150건 안팎까지 줄인다.

출력 (work 디렉토리):
  list-DATE.md   선별 턴 입력. URL은 넣지 않는다 (URL은 토큰 밀도가 높다 — hn-researcher 교훈)
  urls-DATE.json {ID: URL} — 원문 수집 단계가 ID로 되찾는다

Usage: collect.py DATE WORKDIR HNR_DIR
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

DATE, WORK, HNR = sys.argv[1], sys.argv[2], sys.argv[3]
# 데이터(발행분·terms.js)를 읽을 곳. run.sh가 origin/main worktree를 넘긴다
ROOT = os.environ.get("NEWS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = "weknews-ai-news/1.0 (+https://weknews.github.io/ai-term-dictionary/)"
# 블로그는 하루에 한 편도 안 나오는 날이 많아 HN보다 창을 넓게 잡는다.
# 겹침은 아래 '최근 발행분 제외'가 막는다.
LAB_WINDOW_H = 48
MAX_LINES = 160
MIN_POINTS = 20          # HN·Lobsters. GeekNews는 점수 체계가 달라 제외 대상 아님

LAB_FEEDS = [
    ("OpenAI", "https://openai.com/news/rss.xml"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml"),
    ("Simon Willison", "https://simonwillison.net/atom/everything/"),
]
# 스타트업 렌즈용 — 투자·가격·제품·플랫폼 전략. AI 관련만 남긴다(키워드 필터).
# Lenny's·Stratechery는 유료 글이 섞여 있다. 본문을 못 받으면 원문 수집 단계에서 자연히 빠진다.
BIZ_FEEDS = [
    ("TechCrunch", "https://techcrunch.com/category/artificial-intelligence/feed/"),
    ("Lenny's Newsletter", "https://www.lennysnewsletter.com/feed"),
    ("Stratechery", "https://stratechery.com/feed/"),
    ("Latent Space", "https://www.latent.space/feed"),
    ("YC", "https://www.ycombinator.com/blog/rss/"),
]
# Anthropic 뉴스는 RSS가 없고, sitemap의 lastmod도 믿을 수 없다(모델 출시 페이지는 /news/ 밖에
# 있고 수정 시각이 발행 시각과 따로 논다). 목록 페이지가 최신순이라 거기 링크를 읽고,
# 처음 본 날을 WORK에 적어 둔다 — 처음 본 지 이틀 안 된 것만 후보로 낸다.
# ponytail: 페이지 구조가 바뀌면 조용히 0건이 된다. run.sh가 0건을 PR 본문에 경고로 남긴다.
ANTHROPIC_NEWS = "https://www.anthropic.com/news"

AI = re.compile(
    r"\b(ai|llms?|gpt[-\w.]*|claude|gemini|llama|qwen|deepseek|mistral|openai|anthropic|deepmind|"
    r"hugging ?face|agents?|agentic|mcp|rag|embeddings?|transformers?|diffusion|inference|fine-?tun\w*|"
    r"prompts?|tokens?|model weights|open[- ]weights?|neural|machine learning|ml|copilot|cursor|codex|"
    r"chatbot|reasoning model|benchmark|eval\w*|alignment|jailbreak|prompt injection|vibe ?coding|gpu|nvidia)\b"
    r"|인공지능|에이전트|모델|언어모델|생성형|프롬프트|추론|학습|딥러닝|머신러닝|챗봇|벡터|임베딩|바이브",
    re.I)
LINE = re.compile(r"^- \[(\d+)p/(\d+)c\] #(\S+) (.*)$")


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read(5_000_000)


def ts(text):
    text = (text or "").strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return parsedate_to_datetime(text)


def lab_id(url):
    return "lab" + hashlib.sha1(url.encode()).hexdigest()[:8]


def feed_items(label, raw, since):
    """RSS 2.0과 Atom을 둘 다 읽는다."""
    root, out = ET.fromstring(raw), []
    A = "{http://www.w3.org/2005/Atom}"
    for it in root.iter("item"):
        out.append((it.findtext("title"), it.findtext("link"), it.findtext("pubDate")))
    for e in root.iter(f"{A}entry"):
        link = e.find(f"{A}link")
        out.append((e.findtext(f"{A}title"), link.get("href") if link is not None else "",
                    e.findtext(f"{A}published") or e.findtext(f"{A}updated")))
    items = []
    for title, url, when in out:
        try:
            if not (title and url) or ts(when) < since:
                continue
        except (TypeError, ValueError):
            continue
        items.append({"id": lab_id(url.strip()), "url": url.strip(), "title": f"[{label}] {title.strip()}"})
    return items


def anthropic_items(raw, since):
    state_p = os.path.join(WORK, "anthropic-seen.json")
    state = json.load(open(state_p)) if os.path.exists(state_p) else {}
    paths = re.findall(r'href="(/(?:news/[a-z0-9-]+|claude-[a-z0-9-]+))"', raw.decode("utf-8", "replace"))
    items, cutoff = [], since.strftime("%F")
    for path in dict.fromkeys(paths[:20]):          # 순서 유지 중복 제거
        url = "https://www.anthropic.com" + path
        state.setdefault(url, DATE)
        if state[url] >= cutoff:
            slug = path.rsplit("/", 1)[-1].replace("-", " ")
            items.append({"id": lab_id(url), "url": url, "title": f"[Anthropic] {slug}"})
    json.dump(state, open(state_p, "w"), ensure_ascii=False, indent=0)
    return items


def recent_urls(days=7):
    """최근 발행분에 실린 URL — 같은 소식이 며칠 연속 실리지 않게 한다."""
    seen, d = set(), os.path.join(ROOT, "news")
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", f) and f[:10] >= (
                datetime.strptime(DATE, "%Y-%m-%d") - timedelta(days=days)).strftime("%F"):
            seen.update(i.get("url", "") for i in json.load(open(os.path.join(d, f))).get("items", []))
    return seen


def hn_digest():
    """hn-researcher가 오늘 받아 둔 것을 쓴다. 없으면 그 수집기를 직접 부른다."""
    w = os.path.join(HNR, "out", "work")
    digest, urls = os.path.join(w, f"digest-{DATE}.md"), {}
    if not os.path.exists(digest):
        print("hn-researcher 오늘 치 없음 — 수집기 직접 실행", file=sys.stderr)
        digest = os.path.join(WORK, f"hn-digest-{DATE}.md")
        u1, u2 = os.path.join(WORK, f"hn-urls-{DATE}.json"), os.path.join(WORK, f"hn-urls-extra-{DATE}.json")
        with open(digest, "w") as f:
            subprocess.run([sys.executable, os.path.join(HNR, "sources.py"), "--digest", "--urls", u2], stdout=f)
            subprocess.run([sys.executable, os.path.join(HNR, "fetch_hn.py"), "--urls", u1], stdout=f, check=True)
        paths = [u1, u2]
    else:
        paths = [os.path.join(w, f"urls-{DATE}.json"), os.path.join(w, f"urls-extra-{DATE}.json")]
    for p in paths:
        if os.path.exists(p):
            urls.update(json.load(open(p)))
    return open(digest).read(), urls


if __name__ == "__main__":
    since = datetime.now(timezone.utc) - timedelta(hours=LAB_WINDOW_H)
    seen = recent_urls()
    text, urls = hn_digest()

    lines = []
    for ln in text.splitlines():
        m = LINE.match(ln)
        if not m:
            continue
        pts, cid, title = int(m.group(1)), m.group(3), m.group(4)
        if urls.get(cid) in seen or not AI.search(title):
            continue
        if not cid.startswith("gn") and pts < MIN_POINTS:
            continue
        lines.append((pts, ln))
    lines.sort(key=lambda x: -x[0])
    lines = [ln for _, ln in lines[:MAX_LINES]]

    os.makedirs(WORK, exist_ok=True)
    labs, dead = [], []
    for label, url in LAB_FEEDS + [("Anthropic", ANTHROPIC_NEWS)]:
        try:
            raw = get(url)
            got = anthropic_items(raw, since) if label == "Anthropic" else feed_items(label, raw, since)
            labs += [i for i in got if i["url"] not in seen]
        except Exception as e:  # 한 소스가 죽어도 나머지로 발행한다
            dead.append(f"{label}: {type(e).__name__}")
    biz = []
    for label, url in BIZ_FEEDS:
        try:
            got = feed_items(label, get(url), since)
            biz += [i for i in got if i["url"] not in seen and AI.search(i["title"])]
        except Exception as e:
            dead.append(f"{label}: {type(e).__name__}")
    for i in labs + biz:
        urls[i["id"]] = i["url"]

    with open(os.path.join(WORK, f"list-{DATE}.md"), "w") as f:
        # 연구소 발표를 먼저 둔다. 긴 목록 끝에 붙이면 선별이 집어가지 않는다 (hn-researcher 실측)
        f.write(f"# 연구소·기업 발표 원문 — {len(labs)}건\n\n")
        f.writelines(f"- #{i['id']} {i['title']}\n" for i in labs)
        f.write(f"\n# 비즈니스·제품 (스타트업 렌즈 후보) — {len(biz)}건\n\n")
        f.writelines(f"- #{i['id']} {i['title']}\n" for i in biz)
        f.write(f"\n# 커뮤니티 반응 (HN·Lobsters·GeekNews, AI 관련만) — {len(lines)}건\n\n")
        f.writelines(ln + "\n" for ln in lines)
    json.dump(urls, open(os.path.join(WORK, f"urls-{DATE}.json"), "w"), ensure_ascii=False)
    # 실패한 소스는 경고로만 남긴다 — PR 본문에 실려 사람이 본다
    with open(os.path.join(WORK, f"dead-{DATE}.txt"), "w") as f:
        f.writelines(d + "\n" for d in dead)
    print(f"후보 목록: 발표 {len(labs)}건 + 비즈니스 {len(biz)}건 + 커뮤니티 {len(lines)}건"
          + (f" (실패: {', '.join(dead)})" if dead else ""), file=sys.stderr)
