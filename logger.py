# =============================================================
#  logger.py  ―  화면 로그 출력 (타이쿤 감성)
# -------------------------------------------------------------
#  하는 일:
#   작업이 어느 단계까지 갔는지를 색깔 있는 한 줄로 보여줍니다.
#   공장/연구소 라인처럼 [지시실] [코딩실] [보안팀] ... 형태로요.
#
#  왜 따로 빼놨나?
#   orchestrator(일 시키는 파일)는 "흐름"에만 집중하게 하고,
#   "예쁘게 출력하는 일"은 이 파일이 전담합니다.
#   로그 모양을 바꾸고 싶으면 이 파일만 고치면 됩니다.
#
#  필요 라이브러리: rich  (서버에서 pip install rich 필요)
# =============================================================

from datetime import datetime

from rich.console import Console
from rich.panel import Panel

# 화면 출력 담당 객체
console = Console()


# -------------------------------------------------------------
#  단계(스테이션)별 아이콘과 색상
#   - orchestrator 의 각 단계가 이 이름으로 로그를 찍습니다.
# -------------------------------------------------------------
STATION_STYLES = {
    "지시실":  ("📋", "bold blue"),      # 1단계 지시 (26B)
    "코딩실":  ("💻", "bold green"),     # 2단계 코딩 (31B)
    "보안팀":  ("🔒", "bold red"),       # 3단계 AST 검사
    "연구소":  ("🔬", "bold yellow"),    # 4단계 디버깅 (26B)
    "검토실":  ("🎯", "bold magenta"),   # 5단계 검토 (31B)
    "평가실":  ("⭐", "bold gold1"),      # 6단계 평가 (31B)
    "주제실":  ("🎲", "bold cyan"),      # 주제 생성
}


def log_station(station, message):
    """
    한 단계의 진행 상황을 색깔 한 줄로 출력합니다.
    예) 14:03:21 [코딩실] 코드 생성 중...
    """
    icon, style = STATION_STYLES.get(station, ("▶", "white"))
    timestamp = datetime.now().strftime("%H:%M:%S")
    console.print(
        f"[dim]{timestamp}[/dim] "
        f"[{style}]{icon} [{station}][/{style}] "
        f"{message}"
    )


def log_ast_result(is_safe, message):
    """
    AST(보안) 검사 결과를 눈에 띄는 박스로 보여줍니다.
    통과면 초록, 실패면 빨강.
    """
    if is_safe:
        console.print(Panel(
            "[bold green]✅ 보안 통과[/bold green]",
            title="[bold red]🔒 보안팀[/bold red]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            f"[bold red]❌ 위험 감지![/bold red]\n{message}",
            title="[bold red]🔒 보안팀[/bold red]",
            border_style="red",
        ))


def log_evaluation(score, summary):
    """
    AI 평가 결과(점수 + 한줄평)를 별점과 함께 보여줍니다.
    4.5점 이상은 금색, 4점대는 노랑, 그 아래는 빨강.
    """
    color = "gold1" if score >= 4.5 else "yellow" if score >= 4.0 else "red"
    stars = "⭐" * int(score)
    console.print(Panel(
        f"[{color}]{score:.1f}/5.0  {stars}[/{color}]\n{summary}",
        title="[bold gold1]⭐ 평가실[/bold gold1]",
        border_style=color,
    ))


def log_task_start(idea, task_num):
    """새 작업이 시작될 때 큰 제목 박스를 보여줍니다."""
    console.print(Panel(
        f"[bold white]{idea}[/bold white]",
        title=f"[bold cyan]🏭 AI AUTO CODER  |  작업 #{task_num:03d}[/bold cyan]",
        border_style="cyan",
    ))


def log_task_result(saved, result_id=None):
    """작업이 끝났을 때 저장됐는지(초록) 폐기됐는지(빨강) 보여줍니다."""
    if saved:
        console.print(Panel(
            f"[bold green]DB 저장 완료  (ID: #{result_id})[/bold green]",
            border_style="green",
        ))
    else:
        console.print(Panel(
            "[bold red]평점 4점 미만 — 폐기[/bold red]",
            border_style="red",
        ))
