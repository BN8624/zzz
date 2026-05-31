# =============================================================
#  main.py  ―  진입점 (모든 것을 합쳐서 실행)
# -------------------------------------------------------------
#  이 파일 하나만 실행하면:
#    (1) 백그라운드에서 자동 코딩 루프가 돌고
#    (2) 동시에 대시보드(폰 접속용)도 켜집니다.
#
#  실행:  python3 main.py
#  폰 접속:  http://<서버IP>:5000
#
#  자동 루프 정책 (단순화: "한도까지 최대 속도"):
#    - 페이싱(작업마다 쉬기)과 작업수 제한을 모두 없앴습니다.
#      쉬지 않고 계속 돌려 무료 한도를 최대한 채웁니다(= 실패 데이터를 많이 쌓음).
#    - 유일한 제동: 26B 또는 31B 중 하나가 안전선(1450)에 닿으면 멈추고,
#      태평양 자정에 카운터가 리셋되면 자동으로 다시 돕니다.
#    - 카운터는 usage.json 에 영속 저장되어, 재시작해도 "오늘 누적 호출수"가
#      유지됩니다. 그래서 이 안전선이 재시작에도 뚫리지 않습니다(핵심).
#    - 한도(1500)에 실제로 걸려 429 가 나도, 작업 하나만 건너뛰고 계속 갑니다.
#      어차피 자정 리셋 후 다시 도므로 손해가 없습니다(에러 격리).
# =============================================================

import sys
import time
import threading
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo   # 시간대(태평양시간) 계산용 (파이썬 표준)

from orchestrator import CodeAgent
from topic_generator import AutoTopicGenerator
from database import CodeDatabase
from agents import get_usage
from logger import log_task_result, console
# (제거) DAILY_LIMIT, PACING 더 이상 사용 안 함.
#   페이싱(작업마다 쉬기)과 작업수 제한을 모두 없앴습니다. 이제 유일한 제동은
#   "26B 또는 31B 중 하나가 안전선(1450)에 닿으면 멈추고, 태평양 자정 리셋까지 대기"
#   하나뿐입니다. 카운터가 파일(usage.json)에 영속 저장되므로, 재시작해도
#   누적이 유지되어 이 안전선이 정확히 작동합니다.


# -------------------------------------------------------------
#  안전선 설정
# -------------------------------------------------------------
#  구글 무료 한도는 모델당 하루 1,500회입니다.
#  딱 1,500 까지 쓰면 위험하므로, 그 전에 멈출 "안전선"을 둡니다.
#  (콘솔 표시와 실제 카운트가 어긋날 수 있어 보수적으로 잡음)
SAFE_LIMIT = 1450

#  태평양 시간대 (구글 한도 리셋 기준)
PACIFIC = ZoneInfo("America/Los_Angeles")

#  마지막으로 자동정리(cleanup)를 돈 날짜(태평양 기준). 하루 1번만 돌게.
_last_cleanup_day = None


def pacific_today():
    """지금 '태평양 시간 기준' 날짜를 돌려줍니다. (한도 리셋 기준일)"""
    return datetime.now(PACIFIC).date().isoformat()


def over_limit():
    """
    오늘 26B 또는 31B 중 하나라도 안전선을 넘었는지 확인합니다.
    둘 중 더 많이 쓴 쪽이 기준입니다(먼저 한도에 닿는 쪽).
    넘었으면 True (= 멈춰야 함).
    """
    usage = get_usage()
    used = max(usage["26b"]["calls"], usage["31b"]["calls"])
    return used >= SAFE_LIMIT


def maybe_cleanup(db):
    """태평양 날짜가 바뀌었으면 하루 한 번 자동정리를 돌립니다."""
    global _last_cleanup_day
    today = pacific_today()
    if _last_cleanup_day != today:
        _last_cleanup_day = today
        db.daily_cleanup()


# =============================================================
#  (제거) maybe_reprocess ― 배치 재처리 폐지.
#   사유: 3점대 코드를 보완해도 모델 한계가 천장이라 실효가 없었고,
#   3.5 Flash(유료)에 의존해 무료 운영 원칙과 맞지 않았습니다.
#   대신 orchestrator 의 자가발전 루프(실패에서 교훈 추출)로 대체했습니다.
# =============================================================


# =============================================================
#  자동 코딩 루프 (백그라운드 스레드에서 돎)
# =============================================================
def auto_loop():
    topic_gen = AutoTopicGenerator()
    agent = CodeAgent()
    db = CodeDatabase()

    console.print("[bold green]🏭 자동 코딩 루프 시작[/bold green]")

    while True:
        try:
            # (0) 하루 한 번 자동정리 (날짜 바뀌었으면)
            maybe_cleanup(db)

            # (1) ★ 유일한 제동: 한도 안전선.
            #     26B 또는 31B 중 하나라도 안전선(1450)에 닿으면, 태평양 자정에
            #     카운터가 리셋될 때까지 10분씩 쉬며 기다립니다. 페이싱이 없으므로
            #     그 전까지는 쉬지 않고 최대 속도로 돌아 한도를 꽉 채웁니다.
            #     (카운터가 usage.json 에 영속 저장되어 재시작해도 누적이 유지됨)
            if over_limit():
                console.print(
                    "[bold yellow]⏸  오늘 한도 도달 — 리셋(태평양 자정)까지 대기[/bold yellow]"
                )
                time.sleep(600)   # 10분 쉬고 다시 확인
                continue

            # (2) 주제 생성 → 6단계 실행 → 저장
            topic_info = topic_gen.generate()
            result = agent.run(topic_info["topic"])
            result_id = db.save(topic_info["topic"], result)

            # 저장됐는지(4점 이상) 결과 표시
            log_task_result(saved=result_id is not None, result_id=result_id)

            # (3) 페이싱 없음: 쉬지 않고 곧바로 다음 작업으로.
            #     속도 제어는 호출 사이 최소간격(RATE_LIMIT, 분당 한도 방지)과
            #     위 (1) 의 일일 안전선만 담당합니다.

        except Exception as e:
            # 작업 하나가 통째로 실패해도 전체는 죽지 않게.
            console.print(f"[bold red]⚠️ 작업 실패(건너뜀): {e}[/bold red]")
            traceback.print_exc()
            time.sleep(30)   # 잠깐 쉬고 다음 작업으로


# =============================================================
#  실행 진입점
# =============================================================
def main():
    # (1) 자동 루프를 백그라운드 스레드로 시작.
    #     daemon=True → 메인이 끝나면 같이 종료.
    t = threading.Thread(target=auto_loop, daemon=True)
    t.start()

    # (2) 대시보드(Flask)를 켭니다. 이게 메인 프로세스를 붙잡아 둡니다.
    #     폰에서 http://<서버IP>:5000 으로 접속.
    from dashboard import app
    console.print("[bold cyan]🖥  대시보드 시작 → http://<서버IP>:5000[/bold cyan]")
    app.run(host="0.0.0.0", port=5000)


if __name__ == "__main__":
    main()
