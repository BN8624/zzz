# =============================================================
#  orchestrator.py  ―  멀티에이전트 루프 (이 시스템의 심장)
# -------------------------------------------------------------
#  하는 일:
#   하나의 주제를 받아서, 아래 6단계를 거쳐 코드를 완성합니다.
#
#   1. 지시   (26B) : 작업 계획 세우기
#   2. 코딩   (31B) : 코드 작성 (주석 꼼꼼히!)
#   3. AST검사       : 위험하면 → 2번으로 (코딩 직후 즉시 차단)
#   4. 디버깅 (26B) : 로직 점검, 문제 있으면 → 2번으로
#   5. 검토   (31B) : 최종 점검
#   6. 평가   (31B) : 1~5점 채점
#
#   역할 분담(3:3 균형):
#     26B = 지시 · 디버깅      (+ 주제생성은 topic_generator 에서)
#     31B = 코딩 · 검토 · 평가
#   → 두 모델 사용량이 비슷해져서 한도 병목을 피합니다.
# =============================================================

import os
import re
import json
from datetime import datetime
from zoneinfo import ZoneInfo

# 파일 로그(output.log)에 찍는 시각도 화면 로그와 똑같이 한국시간(KST)으로.
#  서버 시계는 UTC 지만, 사람이 읽는 로그 시각만 한국시간으로 통일합니다.
KST = ZoneInfo("Asia/Seoul")

from notebook import MistakeNotebook
from agents import call_gemma_26b, call_gemma_31b
from safety import CodeSafetyChecker
from logger import (
    log_station, log_ast_result, log_evaluation, log_task_start,
)
from config import MAX_DEBUG_ITERATIONS, LOG_PATH, SELF_IMPROVE


class CodeAgent:
    """주제 하나를 받아 6단계로 코드를 완성하는 일꾼."""

    def __init__(self):
        self.notebook = MistakeNotebook()       # 오답노트
        self.safety = CodeSafetyChecker()        # AST 검사기
        self.task_count = 0                      # 지금까지 처리한 작업 수

    # -------------------------------------------------------------
    #  로그를 파일에도 남깁니다(화면 로그와 별개로 기록 보관용).
    # -------------------------------------------------------------
    def log_file(self, message):
        timestamp = datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")
        os.makedirs("logs", exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")

    # -------------------------------------------------------------
    #  AI 응답에서 코드 블록(```python ... ```)만 뽑아냅니다.
    #  설명 글이 섞여 와도 순수 코드만 추출합니다.
    # -------------------------------------------------------------
    def _extract_code(self, text):
        match = re.search(r"```(?:python)?\n(.*?)```", text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text.strip()

    # =============================================================
    #  1단계: 지시 (26B) ― 작업 계획 세우기
    # =============================================================
    def step_1_instruct(self, idea, mistakes_context):
        log_station("지시실", "계획 수립 중...")
        prompt = f"""당신은 10년 경력의 Python 아키텍트입니다. 화려함보다 견고함과
실용성을 중시하며, 과한 추상화를 경계합니다.

# 사용자 요청
{idea}

# 과거 실수 (참고)
{mistakes_context or "이전 실수 없음"}

# 먼저 생각하세요 (출력하기 전 머릿속으로)
- 이 요청의 핵심 난관은 무엇인가? 흔히 빠지는 함정은?
- 어떤 엣지 케이스를 반드시 다뤄야 하는가?
- 과거 실수 중 이 작업에 해당하는 것이 있는가?

# 작업
위 고민을 반영해, 구현을 위한 단계별 계획을 작성하세요.

# 출력 형식
1. 구현 목표:
2. 기술 스택:
3. 단계별 계획:
4. 주의사항 (엣지 케이스 포함):
"""
        plan = call_gemma_26b(prompt)
        log_station("지시실", "계획 수립 완료")
        return plan

    # =============================================================
    #  2단계: 코딩 (31B) ― 코드 작성 (주석 꼼꼼히!)
    # =============================================================
    def step_2_code(self, plan, current_code, mistakes_context, iteration):
        log_station("코딩실", f"코드 생성 중... (반복 {iteration})")

        # 처음이면 새로 작성, 아니면 기존 코드 수정.
        task_line = (
            "완전한 Python 코드를 작성하세요."
            if iteration == 1
            else "기존 코드의 문제를 수정하세요."
        )

        prompt = f"""당신은 20년 경력의 시니어 Python 개발자입니다. 단순하고 견고하며
실행 가능한 코드를 작성하고, 불필요한 추상화나 죽은 코드를 남기지 않습니다.

# 구현 계획
{plan}

# 현재 코드
{current_code or "(처음 시작)"}

# 과거 실수 (참고)
{mistakes_context or "이전 실수 없음"}

# 작업
{task_line}

# 코드를 쓰기 전에 (머릿속으로 점검)
- 계획의 엣지 케이스가 코드에 실제로 반영되는가?
- 예외 처리, 자원 정리(파일/연결 닫기)가 빠지지 않았는가?
- 실제로 import 해서 바로 실행되는, 완결된 코드인가?

# 매우 중요한 규칙
- 모든 함수와 주요 로직에 한국어 주석을 꼼꼼하게 달아주세요.
- 프로그래밍 초보자도 읽고 이해할 수 있도록 설명하세요.
- 각 부분이 "무엇을, 왜" 하는지 주석으로 남기세요.

# 출력
완성된 Python 코드만 출력하세요 (코드 외 설명·머리말은 절대 쓰지 마세요).
"""
        code = call_gemma_31b(prompt)
        code = self._extract_code(code)
        log_station("코딩실", f"코드 생성 완료 ({len(code)}자)")
        return code

    # =============================================================
    #  3단계: AST 안전 검사 ― 코딩 직후 즉시!
    #   위험하면 디버깅까지 안 가고 바로 코딩 단계로 되돌립니다.
    #   (낭비를 줄이는 "빠른 실패" 구조)
    # =============================================================
    def step_3_ast_check(self, code):
        is_safe, msg = self.safety.check(code)
        log_ast_result(is_safe, msg)
        return is_safe, msg

    # =============================================================
    #  4단계: 디버깅 (26B) ― 로직 점검
    #   안전은 이미 통과한 코드이므로, 여기선 로직만 봅니다.
    # =============================================================
    def step_4_debug(self, code, mistakes_context):
        log_station("연구소", "로직 분석 중...")
        prompt = f"""당신은 깐깐한 코드 리뷰어입니다. 문제를 놓치면 안 됩니다.

# 코드
{code}

# 과거 실수 패턴 (참고)
{mistakes_context or "이전 실수 없음"}

# 작업 (안전은 이미 통과한 코드입니다. 로직만 보세요)
아래 4가지를 "하나씩" 실제로 점검하세요. 건너뛰지 마세요.
1. 구문 오류 — 실행하면 바로 깨지는 곳이 있는가
2. 논리 오류 — 의도와 다르게 동작하는 곳이 있는가
3. 과거 실수 패턴 재현 여부 — 위 실수를 반복하고 있지 않은가
4. 성능/자원 문제 — 비효율, 자원 누수(닫지 않은 파일/연결)가 있는가

# 출력 형식
4가지를 모두 점검해 문제가 하나도 없을 때만, 첫 줄에 정확히: OK
하나라도 문제가 있으면 (OK 라고 쓰지 말고):
- 문제 1: (설명)
- 문제 2: (설명)
"""
        result = call_gemma_26b(prompt)
        log_station("연구소", "분석 완료")
        return result

    # =============================================================
    #  5단계: 최종 검토 (31B)
    # =============================================================
    def step_5_review(self, code, plan, mistakes_context):
        log_station("검토실", "최종 검토 중...")
        prompt = f"""당신은 시니어 개발자입니다.

# 원래 계획
{plan}

# 최종 코드
{code}

# 작업
최종 검토를 수행하세요.

# 출력
## 평가: (좋음/보통/나쁨)
## 강점:
- ...
## 약점:
- ...
"""
        review = call_gemma_31b(prompt)
        log_station("검토실", "최종 검토 완료")
        return review

    # =============================================================
    #  6단계: AI 자동 평가 (31B) ― 1~5점 채점
    #   결과는 JSON 으로 받습니다. (점수 + 한줄평)
    # =============================================================
    def step_6_evaluate(self, code, idea):
        log_station("평가실", "평가 중...")
        prompt = f"""당신은 까다로운 시니어 코드 리뷰어입니다. 후하게 주지 말고 엄격하게,
그리고 매번 일관된 기준으로 평가하세요.

# 주제
{idea}

# 코드
{code}

# 점수 기준 (각 항목 1~5점, 엄격하게)
- 5점: 매우 우수. 예외처리·엣지케이스까지 갖춰 그대로 프로덕션 사용 가능
- 4점: 좋음. 동작하고 견고하나 사소한 개선 여지
- 3점: 보통. 동작은 하나 예외처리 부족, 교과서적, 실용성 애매
- 2점: 미흡. 빈 곳이 많거나 일부 동작 안 함
- 1점: 매우 미흡. 실행 불가 수준

# 평가 기준 예시 (보정용)
- 외부 요청에 타임아웃·예외처리가 없다 → execution/completeness 3점 이하
- 핵심 로직은 맞지만 Java 스타일의 과한 추상화, 죽은 코드 존재 → quality 3점
- 예외처리·엣지케이스까지 챙기고 바로 실행 가능 → 4~5점

# 평가 항목
1. execution(실행 가능성)  2. quality(코드 품질)  3. novelty(독창성)
4. practicality(실용성)    5. completeness(완성도)

# 채점 방법
먼저 reasoning 에 항목별 근거를 짧게 쓰고(이 추론을 마친 뒤에 점수 결정),
그 근거에 맞춰 각 점수를 매기세요. total 은 다섯 점수의 평균입니다.

# 출력 (아래 JSON 하나만, 다른 텍스트 없이)
{{
    "reasoning": "<항목별 근거를 한국어로 2~3문장>",
    "execution": <1-5>,
    "quality": <1-5>,
    "novelty": <1-5>,
    "practicality": <1-5>,
    "completeness": <1-5>,
    "total": <다섯 점수의 평균>,
    "summary": "<한 줄 평>"
}}
"""
        # 평가(채점)는 26B 담당.
        #  채점은 "코드 생성"이 아니라 "일관된 기준으로 판단"하는 일이라,
        #  추론에 강한 26B 가 맡습니다. 또한 코딩·검토를 한 31B 가
        #  자기 결과를 자기가 채점하지 않게 하여(자기채점 방지) 객관성을 둡니다.
        response = call_gemma_26b(prompt)
        evaluation = self._parse_json(response)

        score = evaluation.get("total", 0)
        summary = evaluation.get("summary", "평가 실패")
        log_evaluation(score, summary)
        return evaluation

    # -------------------------------------------------------------
    #  AI가 준 JSON 문자열을 안전하게 파이썬 자료로 바꿉니다.
    #  중간에 설명이 섞여 와도 { ... } 부분만 뽑아 파싱하고,
    #  실패하면 기본값(0점)을 돌려줍니다.
    # -------------------------------------------------------------
    def _parse_json(self, text):
        try:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            self.log_file(f"평가 JSON 파싱 실패: {e}")
        return {"total": 0, "summary": "평가 실패"}

    # =============================================================
    #  전체 실행 ― 위 6단계를 순서대로 돌립니다.
    # =============================================================
    def run(self, idea):
        self.task_count += 1
        log_task_start(idea, self.task_count)
        self.log_file(f"작업 시작: {idea}")

        # 시작 전: 이 주제와 비슷한 과거 실수를 찾아 둡니다.
        mistakes_context = self.notebook.format_for_prompt(idea)

        # 1. 지시 (계획 세우기)
        plan = self.step_1_instruct(idea, mistakes_context)

        # 2~4. 코딩 → AST → 디버깅 을 최대 MAX_DEBUG_ITERATIONS 번 반복
        code = ""
        completed = False
        i = 1
        for i in range(1, MAX_DEBUG_ITERATIONS + 1):
            # 2. 코딩
            code = self.step_2_code(plan, code, mistakes_context, i)

            # 3. AST 검사 (코딩 직후)
            is_safe, safety_msg = self.step_3_ast_check(code)
            if not is_safe:
                # 위험하면 디버깅 건너뛰고 바로 다시 코딩.
                mistakes_context += f"\n\n방금 문제: {safety_msg}\n안전한 코드로 작성하세요!"
                continue

            # 4. 디버깅 (로직 점검)
            debug_result = self.step_4_debug(code, mistakes_context)

            # "OK" 로 시작하면 통과 → 루프 종료
            if debug_result.strip().startswith("OK"):
                completed = True
                break
            # 아니면 문제점을 다음 코딩에 참고로 넘기고 다시.
            mistakes_context += f"\n\n방금 지적: {debug_result}"

        # 5. 최종 검토
        review = self.step_5_review(code, plan, mistakes_context)

        # 6. AI 평가
        evaluation = self.step_6_evaluate(code, idea)

        self.log_file(
            f"작업 완료: {idea} / 평점: {evaluation.get('total', 0):.1f} / "
            f"반복: {i}회 / 완성: {completed}"
        )

        # 자가발전: 4점 미만(폐기될) 작업이면 실패에서 교훈을 뽑아 오답노트에 기록.
        #  (성공작은 그냥 저장되고, 실패작은 폐기되더라도 '교훈'은 남깁니다)
        self.record_lesson(idea, code, review, evaluation)

        # 결과를 한 묶음으로 돌려줍니다(이후 DB 저장에 사용).
        return {
            "idea": idea,
            "plan": plan,
            "code": code,
            "review": review,
            "evaluation": evaluation,
            "completed": completed,
            "iterations": i,
        }

    # =============================================================
    #  (제거) reprocess_one ― 배치 재처리 폐지.
    #   사유: 3점대 코드를 보완해도 모델 능력이 천장이라 실효가 없었고,
    #   3.5 Flash(유료)에 의존해 무료 운영 원칙과 맞지 않았습니다.
    #   대신 아래 자가발전 루프로 "실패에서 배우는" 쪽에 집중합니다.
    # =============================================================

    # =============================================================
    #  자가발전 ― 실패(4점 미만) 작업에서 "한 줄 교훈"을 뽑아 오답노트에 기록.
    #   왜? 지금까지 오답노트는 읽기만 하고 채워지지 않아 늘 비어 있었습니다.
    #       (add_mistake 를 부르는 곳이 없었음) → 같은 실수를 매일 반복.
    #   이 메서드가 그 고리를 닫습니다: 망한 작업 → 교훈 압축 → 다음 작업이 참고.
    #
    #   교훈 정제는 flash_lite(3.1 Flash-Lite)가 담당합니다.
    #     - 가벼운 "요약/압축" 작업이라 Flash-Lite 의 강점과 딱 맞고,
    #     - 4점 미만일 때만 호출되어 빈도가 낮아 RPD 500 안에 넉넉히 들어옵니다.
    #   실패해도(호출 오류 등) 조용히 넘어갑니다(자가발전은 본 작업의 부가기능).
    # =============================================================
    def record_lesson(self, idea, code, review, evaluation):
        score = evaluation.get("total", 0)

        # 기준 점수 이상이면(= 쓸 만한 결과면) 교훈을 남기지 않습니다.
        if score >= SELF_IMPROVE["record_below_score"]:
            return

        log_station("연구소", f"실패 작업에서 교훈 추출 중... ({score:.1f}점)")

        # 이전 평가/검토의 "약점" + 실제 코드를 함께 재료로 줍니다.
        #  (기존엔 코드 없이 요약만 봐서 교훈이 추상적이었음 → 코드를 직접 보게 함)
        summary = evaluation.get("summary", "")
        # 코드가 너무 길면 앞부분만(교훈 추출엔 핵심 로직만 봐도 충분 + 토큰 절약).
        code_excerpt = (code or "")[:3000]
        prompt = f"""당신은 코드 리뷰 전문가입니다. 아래 코드가 낮은 평가를 받았습니다.
무엇이 구체적으로 잘못됐는지 코드에서 짚어, 재발 방지 교훈 한 줄을 뽑으세요.

# 작업 주제
{idea}

# 실제 코드 (앞부분)
{code_excerpt}

# 평가 요약 (낮은 점수 이유)
{summary}

# 검토 의견
{review}

# 할 일
이 코드에서 실제로 무엇이 문제였는지, "구체적인 기술적 함정"을 짚어 한 줄 교훈을 쓰세요.
- 추상적 격언(예: "예외 처리를 잘하자", "성능을 고려하자")은 금지. 그런 답은 쓸모없습니다.
- 대신 "무엇을, 왜, 어떻게" 가 드러나게 구체적으로 쓰세요.
- 비슷한 코드에서 재현될 수 있는 실수의 '패턴'을 짚되, 막연하게 일반화하지는 마세요.
- 반드시 한국어로, 한 문장으로만. (설명·머리말 없이 문장 하나)

좋은 예) "asyncio.gather 는 예외 발생 시 나머지 작업을 조용히 취소하므로 return_exceptions=True 로 받아 개별 처리할 것"
나쁜 예) "비동기 처리 시 예외를 잘 다뤄야 한다" (추상적이라 금지)
"""
        try:
            # 교훈 정제는 31B 담당(코드 분석 강점).
            #  기존 flash_lite 는 가벼운 요약에 강하나 코드의 구체적 결함 짚기엔 약해
            #  추상적 교훈이 나왔음 → 코드 분석에 강한 31B 로 교체.
            #  4점 미만일 때만 호출되는 저빈도 작업이라 31B 한도 부담은 작음.
            lesson = call_gemma_31b(prompt)
            lesson = (lesson or "").strip().split("\n")[0].strip()
            if not lesson:
                return  # 빈 응답이면 기록 안 함

            # 오답노트에 저장. (task=주제, problem=평가요약, lesson=정제된 교훈)
            self.notebook.add_mistake(
                task=idea,
                problem=summary or "평가 점수 미달",
                lesson=lesson,
            )
            log_station("연구소", f"교훈 기록 완료: {lesson[:40]}")
        except Exception as e:
            # 자가발전은 부가기능이라, 실패해도 본 작업 흐름을 막지 않습니다.
            self.log_file(f"교훈 기록 실패(무시): {e}")
