"""get_chat_model 이 만드는 클라이언트의 설정 검증.

이 파일이 존재하는 이유: Ollama 는 요청이 컨텍스트 창을 넘겨도 **에러를 내지
않는다.** 조용히 앞부분을 버리고 응답한다. 잘려나간 곳에 도구 정의가 있으면
모델은 어떤 도구도 부를 수 없고, 결과는 "빈 응답 + 도구 호출 없음"이 된다.
create_react_agent 는 도구 호출이 없으면 정상 종료로 처리하므로 recursion_limit
에도 안 걸린다 — 즉 실패가 아무 흔적도 남기지 않는다.

실제로 수집 파이프라인이 이 방식으로 조용히 실패했다. 통제 실험 결과:
  num_ctx 미지정(Ollama 기본 4096) → submit_layout 호출 0/2
  num_ctx=16384                    → submit_layout 호출 2/2
그때 요청 크기는 시스템 프롬프트 1,333자 + 도구 스키마 약 4,500자 +
격자 1,535자 ≈ 7,300자로, 한글 토큰 밀도를 감안하면 4096 토큰을 넘겼다.

그래서 num_ctx 는 "있으면 좋은 튜닝값"이 아니라 도구 호출이 동작하기 위한
전제 조건이다. 회귀하면 조용히 깨지므로 테스트로 고정한다.
"""
from app.config import Settings
from app.llm import get_chat_model


def test_ollama_model_sets_num_ctx():
    """num_ctx 가 클라이언트에 실제로 실려야 한다.

    빠지면 Ollama 기본값(4096)으로 서빙되고, 도구 정의가 잘려 모델이 도구를
    전혀 부르지 못한다 — 에러 없이.
    """
    model = get_chat_model(Settings(llm_backend="ollama", ollama_num_ctx=16384))
    assert model.num_ctx == 16384


def test_ollama_num_ctx_is_configurable():
    """`.env` 로 바꿀 수 있어야 한다 — 모델·문서 크기에 따라 조정이 필요하다."""
    model = get_chat_model(Settings(llm_backend="ollama", ollama_num_ctx=32768))
    assert model.num_ctx == 32768


def test_ollama_num_ctx_default_exceeds_ollama_runtime_default():
    """기본값이 Ollama 런타임 기본(4096)보다 커야 의미가 있다.

    이 단언이 깨졌다면 누군가 기본값을 낮춘 것이고, 그 순간 도구 호출이
    조용히 실패하기 시작한다.
    """
    assert Settings().ollama_num_ctx > 4096
