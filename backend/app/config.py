from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # LLM backend switch: "ollama" | "internal"
    llm_backend: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_api_key: str = ""
    ollama_model: str = "gemma3n:e4b"
    # Internal OpenAI-compatible API (used when llm_backend == "internal")
    internal_llm_base_url: str = ""
    internal_llm_api_key: str = ""
    internal_llm_model: str = ""
    # 사내 TLS: CA 번들(.pem) 경로를 주면 검증 유지(권장). verify_ssl=false 면 검증 끔(비보안)
    internal_llm_ca_bundle: str = ""
    # 기본 false: 사내 CA 검증 불가 환경이 많아 우선 연결이 되도록 함(MITM 위험 감수).
    # CA 번들을 구할 수 있으면 internal_llm_ca_bundle 지정 + true 로 바꾸는 걸 권장.
    internal_llm_verify_ssl: bool = False
    # 사내 프록시 우회: true 면 startup 에서 HTTP(S)_PROXY 환경변수를 비워 직접 연결한다
    bypass_proxy: bool = False
    app_db_path: str = "./app.db"
    cors_origins: str = "http://localhost:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def active_model(self) -> str:
        """현재 선택된 백엔드의 모델명 (트레이스/로깅용)."""
        if self.llm_backend == "internal":
            return self.internal_llm_model or "internal"
        return self.ollama_model


@lru_cache
def get_settings() -> Settings:
    return Settings()
