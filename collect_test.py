# =============================================================
#  collect_test.py  ―  자료 수집 테스트 (배치 임베딩 입력 만들기)
# -------------------------------------------------------------
#  하는 일 (작은 규모로 전 과정 확인):
#    1. GitHub 검색 API 로 star 높은 Python repo 목록을 가져옵니다.
#       (목록만 받으므로 요청이 적습니다 = 검색 한도에 거의 안 걸림)
#    2. 각 repo 의 README 본문을 raw.githubusercontent.com 에서
#       정적 파일로 직접 내려받습니다. (REST API 한도와 무관)
#    3. 받은 README 들을 Batch 임베딩 입력용 JSONL 로 저장합니다.
#
#  실행:  python3 collect_test.py
#  결과:  embedding_requests.jsonl  (다음 단계 배치 입력 파일)
#         collected_meta.json       (어떤 repo 였는지 메타)
# =============================================================

import json
import time
import requests

# -------------------------------------------------------------
#  설정 (테스트는 작게)
# -------------------------------------------------------------
HOW_MANY = 10            # 테스트로 가져올 repo 개수
MIN_STARS = 5000         # 이 별 수 이상만 (품질 필터)
GITHUB_TOKEN = ""        # 있으면 넣기(검색 한도↑). 비어 있어도 소량은 OK.

# raw README 를 받을 때, 브랜치와 파일명이 repo 마다 달라서 후보를 순서대로 시도합니다.
BRANCHES = ["main", "master"]
FILENAMES = ["README.md", "README.rst", "readme.md", "README.MD", "README"]


def search_repos(n, min_stars):
    """GitHub 검색 API 로 star 높은 Python repo 의 (owner/repo) 목록을 가져옵니다."""
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    url = "https://api.github.com/search/repositories"
    params = {
        "q": f"language:python stars:>{min_stars}",
        "sort": "stars",
        "order": "desc",
        "per_page": n,     # 테스트라 한 페이지(최대 100)면 충분
    }
    r = requests.get(url, headers=headers, params=params, timeout=20)
    r.raise_for_status()
    items = r.json()["items"]
    # 필요한 정보만 추립니다: 전체이름, 기본 브랜치, 설명
    return [
        {
            "full_name": it["full_name"],
            "default_branch": it.get("default_branch", "main"),
            "description": it.get("description") or "",
            "stars": it["stargazers_count"],
        }
        for it in items
    ]


def fetch_readme(full_name, default_branch):
    """raw.githubusercontent.com 에서 README 본문을 직접 내려받습니다.
       기본 브랜치를 먼저 시도하고, 안 되면 main/master + 여러 파일명을 시도합니다.
       성공하면 (텍스트, 사용한 URL), 모두 실패하면 (None, None)."""
    # 시도할 브랜치 순서: 기본 브랜치를 맨 앞에 두고 중복 제거
    branches = [default_branch] + [b for b in BRANCHES if b != default_branch]
    for br in branches:
        for fn in FILENAMES:
            url = f"https://raw.githubusercontent.com/{full_name}/{br}/{fn}"
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200 and r.text.strip():
                    return r.text, url
            except Exception:
                pass  # 네트워크 일시 오류는 조용히 다음 후보로
    return None, None


def main():
    print(f"[1] repo 목록 검색 중... (star>{MIN_STARS}, {HOW_MANY}개)")
    repos = search_repos(HOW_MANY, MIN_STARS)
    print(f"    → {len(repos)}개 찾음\n")

    requests_out = []   # 배치 입력(JSONL) 줄들
    meta_out = []       # 어떤 repo 였는지 기록

    print("[2] README 본문 다운로드 (raw, 한도 무관)")
    for i, repo in enumerate(repos):
        text, used_url = fetch_readme(repo["full_name"], repo["default_branch"])
        if text is None:
            print(f"    [skip] {repo['full_name']} — README 못 찾음")
            continue

        # 너무 긴 README 는 앞부분만 사용(임베딩 토큰 절약 + 핵심은 보통 앞에 있음).
        # 약 8000자(대략 2~3천 토큰) 선에서 자릅니다.
        text = text[:8000]

        key = f"doc_{i}"
        # Batch 임베딩 입력 형식: 한 줄에 {key, request:{content:{parts:[{text}]}}}
        requests_out.append({
            "key": key,
            "request": {"content": {"parts": [{"text": text}]}},
        })
        meta_out.append({
            "key": key,
            "full_name": repo["full_name"],
            "stars": repo["stars"],
            "description": repo["description"],
            "readme_chars": len(text),
            "source_url": used_url,
        })
        print(f"    [ok] {repo['full_name']:45} ⭐{repo['stars']:>7}  {len(text)}자")
        time.sleep(0.2)   # 예의상 약간의 간격(과도한 연속요청 방지)

    # [3] 파일로 저장
    with open("embedding_requests.jsonl", "w", encoding="utf-8") as f:
        for req in requests_out:
            f.write(json.dumps(req, ensure_ascii=False) + "\n")
    with open("collected_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta_out, f, ensure_ascii=False, indent=2)

    print(f"\n[3] 저장 완료")
    print(f"    embedding_requests.jsonl : {len(requests_out)}건 (배치 입력)")
    print(f"    collected_meta.json      : 메타데이터")
    print(f"\n다음 단계: python3 batch_test.py 로 이 파일을 임베딩합니다.")


if __name__ == "__main__":
    main()
