"""CLI 출력 인코딩. ★부수효과가 목적인 유일한 모듈.

사내 PC 의 콘솔 기본 코드페이지가 cp949 라, 출력에 '—' 나 'σ' 가 하나만 있어도
UnicodeEncodeError 로 죽는다. 그런데 이 패키지의 CLI 는 전부 한국어 문장으로
진단을 내는 것이 존재 이유다 - 진단 대신 트레이스백을 내면 도구가 무의미해진다.

파일 출력에는 이 문제가 없다(전부 encoding='utf-8' 로 쓴다). 콘솔만의 문제라
콘솔만 손본다.

reconfigure 가 없거나 실패하는 환경(파이프로 묶인 경우, 옛 파이썬)에서는 조용히
넘어간다. 여기서 예외를 던지면 인코딩을 고치려다 프로그램을 죽이는 셈이 된다.
"""
import sys


def use_utf8() -> None:
    """stdout·stderr 를 UTF-8 로 맞춘다. CLI main() 의 첫 줄에서 부른다."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError, ValueError):
            pass
