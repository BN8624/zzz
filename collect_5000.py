# =============================================================
#  collect_5000.py  ―  대량 수집 (README 5,000건 목표)
# -------------------------------------------------------------
#  collect_test.py 의 확장판. 두 가지 GitHub 제약을 넘습니다:
#    1) 한 검색 쿼리는 최대 1,000개(100개 × 10페이지)만 돌려줌
#       → star 구간을 여러 개로 쪼개서 각각 검색(구간당 최대 1,000개).
#    2) 검색 한도가 빡빡함
#       → .env 의 GITHUB_TOKEN 으로 인증(분당 30회).
#
#  결과(이어쓰기 가능):
#    embedding_requests.jsonl  ― 임베딩 입력(텍스트)  ※ 기존에 있으면 이어붙임
#    collected_meta.json       ― repo 메타
#  같은 repo 가 여러 구간에 안 겹치게 full_name 으로 중복 제거합니다.
#
#  실행:  python3 collect_5000.py
#  그 다음:  python3 embed_to_db.py   (이어하기로 임베딩)
# =============================================================

import os
import json
import time
import requests
from dotenv import load_dotenv

load_dotenv()

# -------------------------------------------------------------
#  설정
# -------------------------------------------------------------
TARGET = 5000            # 목표 개수(대략. 구간 합이 이보다 크면 거기서 멈춤)
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
README_MAX_CHARS = 8000  # README 본문 앞부분만 사용(임베딩 토큰 절약)

REQ_JSONL = "embedding_requests.jsonl"
META_JSON = "collected_meta.json"

# star 구간(높은 것부터). 한 구간이 1,000개를 넘으면 검색이 잘려서
# 일부만 받습니다 — 그래도 양질(별 높은 순)부터 채우므로 문제 없습니다.
# 더 촘촘히 받고 싶으면 구간을 잘게 쪼개세요(예: 1000..1200, 1200..1500 ...).
STAR_BANDS = [
    "stars:>20000",
    "stars:10000..20000",
    "stars:7000..10000",
    "stars:5000..7000",
    "stars:4000..5000",
    "stars:3000..4000",
    "stars:2500..3000",
    "stars:2000..2500",
    "stars:1700..2000",
    "stars:1400..1700",
    "stars:1200..1400",
    "stars:1000..1200",
]

BRANCHES = ["main", "master"]
FILENAMES = ["README.md", "README.rst", "readme.md", "README.MD", "README"]


def gh_headers():
    h = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


def search_band(band, want):
    """한 star 구간에서 repo 목록을 페이지 넘겨가며 최대 want(≤1000)개 모읍니다."""
    out = []
    page = 1
    while len(out) < want and page <= 10:   # 10페이지 = 1000개가 검색 상한
        params = {
            "q": f"language:python {band}",
            "sort": "stars", "order": "desc",
            "per_page": 100, "page": page,
        }
        r = requests.get("https://api.github.com/search/repositories",
                         headers=gh_headers(), params=params, timeout=20)
        if r.status_code == 403:
            # 검색 한도(분당) 초과 — 잠깐 쉬고 같은 페이지 재시도
            print("      [rate] 검색 한도, 30초 대기")
            time.sleep(30)
            continue
        r.raise_for_status()
        items = r.json().get("items", [])
        if not items:
            break
        for it in items:
            out.append({
                "full_name": it["full_name"],
                "default_branch": it.get("default_branch", "main"),
                "description": it.get("description") or "",
                "stars": it["stargazers_count"],
            })
        page += 1
        time.sleep(2)   # 검색 호출 간 간격(분당 한도 보호)
    return out


def fetch_readme(full_name, default_branch):
    """raw.githubusercontent.com 에서 README 본문을 직접 받습니다(API 한도 무관)."""
    branches = [default_branch] + [b for b in BRANCHES if b != default_branch]
    for br in branches:
        for fn in FILENAMES:
            url = f"https://raw.githubusercontent.com/{full_name}/{br}/{fn}"
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200 and r.text.strip():
                    return r.text, url
            except Exception:
                pass
    return None, None


def load_existing():
    """이미 모은 게 있으면 불러와 이어쓰기(중복 방지)."""
    reqs, meta, seen = [], [], set()
    if os.path.exists(REQ_JSONL):
        with open(REQ_JSONL, encoding="utf-8") as f:
            reqs = [json.loads(l) for l in f if l.strip()]
    if os.path.exists(META_JSON):
        with open(META_JSON, encoding="utf-8") as f:
            meta = json.load(f)
            seen = {m["full_name"] for m in meta}
    return reqs, meta, seen


def main():
    if not GITHUB_TOKEN:
        print("⚠️  GITHUB_TOKEN 이 없습니다. 느릴 수 있지만 진행은 됩니다.\n")

    reqs, meta, seen = load_existing()
    start_count = len(meta)
    print(f"시작 — 기존 {start_count}건 / 목표 {TARGET}건\n")

    # 1) 구간별로 repo 목록 모으기 (중복 제거)
    print("[1] repo 목록 수집 (star 구간별)")
    repos = []
    for band in STAR_BANDS:
        if start_count + len(repos) >= TARGET:
            break
        got = search_band(band, want=1000)
        new = [g for g in got if g["full_name"] not in seen]
        for g in new:
            seen.add(g["full_name"])
        repos.extend(new)
        print(f"    {band:20} +{len(new):>4}개 (누적 후보 {len(repos)})")

    # 목표 개수에 맞춰 자르기
    need = max(0, TARGET - start_count)
    repos = repos[:need]
    print(f"    → 이번에 받을 repo: {len(repos)}개\n")

    # 2) README 다운로드 → 입력/메타에 추가
    print("[2] README 다운로드 (raw)")
    idx = start_count
    ok = 0
    for repo in repos:
        text, used_url = fetch_readme(repo["full_name"], repo["default_branch"])
        if not text:
            continue
        text = text[:README_MAX_CHARS]
        key = f"doc_{idx}"
        reqs.append({"key": key,
                     "request": {"content": {"parts": [{"text": text}]}}})
        meta.append({"key": key, "full_name": repo["full_name"],
                     "stars": repo["stars"], "description": repo["description"],
                     "readme_chars": len(text), "source_url": used_url})
        idx += 1
        ok += 1
        if ok % 100 == 0:
            print(f"    ...{ok}건 받음")
        time.sleep(0.1)

    # 3) 저장(전체 다시 씀 = 이어쓰기 결과 반영)
    with open(REQ_JSONL, "w", encoding="utf-8") as f:
        for req in reqs:
            f.write(json.dumps(req, ensure_ascii=False) + "\n")
    with open(META_JSON, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    print(f"\n[3] 저장 완료")
    print(f"    이번에 추가: {ok}건 / 전체: {len(meta)}건")
    print(f"    다음: python3 embed_to_db.py  (임베딩, 이어하기 가능)")


if __name__ == "__main__":
    main()
