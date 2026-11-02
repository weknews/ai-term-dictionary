#!/usr/bin/env python3
"""주간 용어집 — 지난 갱신 뒤 발행된 뉴스를 근거로 terms.js 수정안을 만든다. 모델 호출은 weekly.sh가 한다.

  digest DATE W   지난 갱신(CHANGES의 upto) 뒤 발행된 호를 모아 선별 턴 입력을 만든다
  pick   DATE W   선별 결과를 거르고, 원문을 받을 목록과 작문 턴 입력을 만든다
  apply  DATE W   작문 결과를 원문과 대조해 통과한 것만 terms.js에 넣고 PR 본문을 쓴다

검증은 verify.py와 같은 기준이다. 숫자는 근거 원문에 글자 그대로 있어야 하고, 금지 표현은 막는다.
떨어진 항목은 빼고 PR 본문에 사유를 남긴다. 한 건 때문에 그 주를 통째로 쉬지 않는다.
exit 3 = 할 일 없음 (새 호가 없거나 고칠 것이 없다)
"""
import glob
import json
import os
import re
import subprocess
import sys

from verify import BAN, ROOT, parse_pages, stray_numbers

CMD, DATE, W = sys.argv[1:4]
KEYS = "k e c y d n s a f".split()   # terms.js 필드 순서. a = 현황(s)을 관찰한 날짜
SITE = "https://weknews.github.io/ai-term-dictionary/"
MAX_EACH = 8                         # 새 용어·현황 수정 각각 한 주 상한. 검토할 수 있는 양만 낸다


def path(name, ext):
    return os.path.join(W, "wk-%s-%s.%s" % (name, DATE, ext))


def load():
    return json.loads(subprocess.check_output(["node", "-e",
        "global.window={};require('%s/terms.js');"
        "process.stdout.write(JSON.stringify({t:window.TERMS,c:window.CATS,h:window.CHANGES||[]}))" % ROOT]))


def ser(t):
    """terms.js 한 줄. 기존 줄과 글자 그대로 같게 나온다 — 고친 줄만 diff에 잡힌다."""
    return "{" + ", ".join("%s:%s" % (k, json.dumps(t[k], ensure_ascii=False)) for k in KEYS if t.get(k)) + "},"


def model_json(p):
    m = re.search(r"\{.*\}", open(p).read(), re.S)
    return json.loads(m.group(), strict=False) if m else {}


def norm(s):
    return re.sub(r"[\s()·\-]", "", s.lower())


def digest():
    d = load()
    upto = max([c["upto"] for c in d["h"]] or ["0000"])
    issues = [json.load(open(f)) for f in sorted(glob.glob(ROOT + "/news/20*.json"))]
    issues = [x for x in issues if upto < x["date"] <= DATE]
    if not issues:
        print("지난 갱신(%s) 뒤 새 호가 없다" % upto)
        sys.exit(3)
    refs, out = {}, ["## 이번에 볼 뉴스 — %d호 (%s ~ %s)" % (len(issues), issues[0]["date"], issues[-1]["date"])]
    for x in issues:
        out.append("\n### %s — %s" % (x["date"], x["headline"]))
        for i in x.get("items") or []:
            ref = "%s/%s" % (x["date"], i["id"])
            refs[ref] = {"d": x["date"], "t": i["title"], "url": i.get("url", "")}
            used = " (용어: %s)" % ", ".join(i["terms"]) if i.get("terms") else ""
            out.append("- [%s] %s — %s%s" % (ref, i["title"], i.get("summary", ""), used))
        for t in x.get("new_terms") or []:
            out.append("- 용어집 후보: %s — %s" % (t["term"], t.get("note", "")))
    used = {t for x in issues for i in x.get("items") or [] for t in i.get("terms") or []}
    out.append("\n## 이번 뉴스에 나온 표제어의 지금 현황")
    out += ["- %s | %s" % (t["k"], "%s 기준: %s" % (t["a"], t["s"]) if t.get("s") else "현황 줄 없음")
            for t in d["t"] if t["k"] in used]
    out.append("\n## 용어집 표제어 전체 (한글 | 원어)")
    out += ["%s | %s" % (t["k"], t["e"]) for t in d["t"]]
    out.append("\n## 분류 (c)\n" + ", ".join("%s=%s" % (c["id"], c["ko"]) for c in d["c"]))
    open(path("digest", "md"), "w").write("\n".join(out))
    meta = {"n": len(issues), "first": issues[0]["date"], "upto": issues[-1]["date"], "refs": refs}
    json.dump(meta, open(path("refs", "json"), "w"), ensure_ascii=False)
    print("뉴스 %d호, 항목 %d개" % (len(issues), len(refs)))


def pick():
    refs = json.load(open(path("refs", "json")))["refs"]
    K = {t["k"]: t for t in load()["t"]}
    sel = model_json(path("select", "txt"))
    add = [a for a in sel.get("add") or [] if a.get("term") and a["term"] not in K][:MAX_EACH]
    edit = [e for e in sel.get("edit") or [] if e.get("k") in K][:MAX_EACH]
    for x in add + edit:
        x["refs"] = [r.lstrip("#") for r in x.get("refs") or [] if r.lstrip("#") in refs][:2]
    add, edit = [a for a in add if a["refs"]], [e for e in edit if e["refs"]]
    json.dump(sel.get("skip") or [], open(path("skip", "json"), "w"), ensure_ascii=False)
    if not add + edit:
        print("고칠 것 없음")
        sys.exit(3)
    want = sorted({r for x in add + edit for r in x["refs"]})
    # fetch_pages.py 입력. 제목 앞의 #ID가 원문 파일에 찍혀야 작문 턴이 근거를 ID로 단다
    json.dump([{"id": r, "title": "#%s %s" % (r, refs[r]["t"]), "project": "용어집", "url": refs[r]["url"]}
               for r in want], open(path("cands", "json"), "w"), ensure_ascii=False)
    out = ["## 새로 넣을 말"]
    out += ["- %s (분류 %s) — 근거 %s — %s" % (a["term"], a.get("c", "?"), ", ".join(a["refs"]), a.get("why", ""))
            for a in add] or ["- 없음"]
    out.append("\n## 현황(s)을 고칠 표제어")
    for e in edit:
        t = K[e["k"]]
        out.append("- %s — 근거 %s — %s\n  지금 정의(d): %s\n  지금 현황(s): %s"
                   % (e["k"], ", ".join(e["refs"]), e.get("why", ""), t["d"], t.get("s") or "없음"))
    open(path("picked", "md"), "w").write("\n".join(out))
    print("새 용어 %d · 현황 %d · 원문 %d건" % (len(add), len(edit), len(want)))


def apply():
    meta = json.load(open(path("refs", "json")))
    refs, bodies = meta["refs"], parse_pages(path("pages", "md"))
    d = load()
    K, cats = {t["k"]: t for t in d["t"]}, {c["id"] for c in d["c"]}
    known = {norm(x) for t in d["t"] for x in re.split(r"[,/]", t["k"] + "," + t["e"]) if x.strip()}
    draft = model_json(path("draft", "txt"))
    ok, dropped = [], []
    for op, xs in (("추가", draft.get("add") or []), ("현황", draft.get("edit") or [])):
        for x in xs:
            k = str(x.get("k", "")).strip()
            src = [refs[r] for r in (str(r).lstrip("#") for r in x.get("from") or [])
                   if r in refs and bodies.get(refs[r]["url"])]
            fields = ("k", "e", "c", "y", "d", "n", "s", "f") if op == "추가" else ("s",)
            t = {f: str(x[f]).strip() for f in fields if str(x.get(f) or "").strip()}
            errs = [] if src else ["근거 원문이 없다"]
            if op == "추가":
                errs += ["%s 누락" % f for f in ("k", "e", "c", "y", "d") if f not in t]
                if any(norm(v) in known for v in re.split(r"[,/]", k + "," + t.get("e", "")) if v.strip()):
                    errs.append("이미 있는 표제어와 겹친다")
                if t.get("c") not in cats:
                    errs.append("없는 분류 '%s'" % t.get("c"))
                if t.get("f") not in (None, "hot", "care", "old"):
                    errs.append("없는 플래그 '%s'" % t["f"])
            elif k not in K:
                errs.append("없는 표제어")
            elif t.get("s", "") in ("", K[k].get("s", "")):
                errs.append("새 현황(s)이 비었거나 그대로다")
            text = " ".join(t.get(f, "") for f in ("d", "n", "s"))
            if re.search(r"<[a-z/]", text, re.I):
                errs.append("HTML 태그")
            evidence = "\n".join(bodies[r["url"]] for r in src)
            errs += ["숫자 '%s'이 근거 원문에 없다" % n for n in stray_numbers(text, evidence)]
            errs += ["금지 표현(%s) '%s'" % (name, h) for name, pat in BAN.items() for h in re.findall(pat, text)]
            if errs:
                dropped.append("- (%s) %s — %s" % (op, k or "?", "; ".join(errs)))
                continue
            ok.append((op, k, t, src))
            known.add(norm(k))   # 같은 주에 같은 말을 두 번 넣지 않게

    lines = open(ROOT + "/terms.js").read().split("\n")
    items, adds = [], []
    for op, k, t, src in ok:
        frm = [{"d": r["d"], "t": r["t"]} for r in src]
        if op == "추가":
            if t.get("s"):
                t["a"] = DATE
            adds.append(ser(t))
            items.append({"k": k, "op": op, "after": t["d"], "from": frm})
            continue
        old = K[k]
        if ser(old) not in lines:
            dropped.append("- (현황) %s — terms.js에서 이 항목 줄을 찾지 못했다 (손으로 줄을 바꿨나?)" % k)
            continue
        lines[lines.index(ser(old))] = ser(dict(old, s=t["s"], a=DATE))
        items.append({"k": k, "op": op, "before": old.get("s", ""), "after": t["s"], "from": frm})

    body = pr_body(meta, ok, items, dropped)
    open(path("pr", "md"), "w").write(body)
    print(body)
    if not items:
        sys.exit(3)
    end = max(i for i, l in enumerate(lines) if l.startswith("{k:")) + 1
    lines[end:end] = adds
    ch = lines.index("window.CHANGES = [")
    close = lines.index("];", ch)
    lines.insert(close, json.dumps({"date": DATE, "upto": meta["upto"], "items": items}, ensure_ascii=False) + ",")
    open(ROOT + "/terms.js", "w").write("\n".join(lines))


def pr_body(meta, ok, items, dropped):
    na = sum(1 for i in items if i["op"] == "추가")
    out = ["**새 용어 %d · 현황 수정 %d — 뉴스 %d호(%s ~ %s) 근거**"
           % (na, len(items) - na, meta["n"], meta["first"], meta["upto"])]
    T = {k: t for op, k, t, src in ok}

    def cite(i):
        return " · ".join("[%s %s](%s?d=%s#now)" % (f["d"][5:], f["t"], SITE, f["d"]) for f in i["from"])
    if na:
        out.append("\n## 새 용어")
        for i in items:
            if i["op"] != "추가":
                continue
            t = T[i["k"]]
            out.append("- **%s** (%s · %s · %s)%s\n  - 정의: %s" % (t["k"], t["e"], t["c"], t["y"],
                       " `%s`" % t["f"] if t.get("f") else "", t["d"]))
            out += ["  - %s: %s" % (lbl, t[f]) for f, lbl in (("n", "뉘앙스"), ("s", "현황")) if t.get(f)]
            out.append("  - 근거: " + cite(i))
    if len(items) > na:
        out.append("\n## 현황 수정")
        for i in items:
            if i["op"] == "현황":
                out.append("- **%s**\n  - 전: %s\n  - 후: %s\n  - 근거: %s" % (i["k"], i["before"] or "(없음)", i["after"], cite(i)))
    if dropped:
        out.append("\n## 검증에서 빠진 것\n" + "\n".join(dropped))
    skip = json.load(open(path("skip", "json"))) if os.path.exists(path("skip", "json")) else []
    if skip:
        out.append("\n## 넣지 않은 후보\n" + "\n".join("- %s — %s" % (s.get("term", ""), s.get("why", "")) for s in skip))
    out.append("\n## 검토\n- 근거 원문 유무·숫자·금지 표현·중복은 코드로 대조했다. "
               "**정의가 원문과 맞는지, 넣을 만한 말인지는 사람이 본다.** 고칠 게 있으면 이 브랜치에서 `terms.js`를 직접 고친다.\n"
               "- 병합하면 %s 호(그날 호가 없으면 다음 호)에 \"이번 주 용어집에서 바뀐 것\"이 붙고, 갱신 탭에도 남는다." % DATE)
    return "\n".join(out) + "\n"


{"digest": digest, "pick": pick, "apply": apply}[CMD]()
