from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


DEFAULT_GONKA_BASE_URL = "https://api.gonkarouter.io/v1"
SUPPORTED_SEARCH_PROVIDERS = {"duckduckgo", "tavily"}


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class AppConfig:
    gonka_base_url: str
    gonka_api_key: str
    gonka_claim_model: str
    gonka_verify_model_1: str
    gonka_verify_model_2: str
    gonka_judge_model: str
    gonka_vision_model: str
    search_provider: str
    tavily_api_key: str
    env_file_found: bool
    gonka_timeout_seconds: float = 60.0
    gonka_fallback_model: str = ""

    @property
    def claim_model(self) -> str:
        return self.gonka_claim_model or self.gonka_verify_model_1

    @property
    def judge_model(self) -> str:
        return self.gonka_judge_model or self.gonka_verify_model_1

    def missing_required_values(self) -> list[str]:
        missing: list[str] = []
        if not self.gonka_api_key:
            missing.append("GONKA_API_KEY")
        if not self.gonka_verify_model_1:
            missing.append("GONKA_VERIFY_MODEL_1")
        if not self.gonka_verify_model_2:
            missing.append("GONKA_VERIFY_MODEL_2")
        if self.search_provider == "tavily" and not self.tavily_api_key:
            missing.append("TAVILY_API_KEY")
        return missing

    def multi_model_issue(self) -> str | None:
        if not self.gonka_verify_model_1 or not self.gonka_verify_model_2:
            return (
                "The application does not yet satisfy the hackathon multi-model "
                "requirement because two verifier models are not configured."
            )
        if self.gonka_verify_model_1 == self.gonka_verify_model_2:
            return "GONKA_VERIFY_MODEL_1 and GONKA_VERIFY_MODEL_2 must be different model IDs."
        return None


def load_config(env_path: str | Path = ".env") -> AppConfig:
    path = Path(env_path)
    env_file_found = path.exists()
    if env_file_found:
        load_dotenv(path, override=False)

    base_url = os.getenv("GONKA_BASE_URL", DEFAULT_GONKA_BASE_URL).strip()
    validate_base_url(base_url)

    search_provider = os.getenv("SEARCH_PROVIDER", "duckduckgo").strip().lower()
    if search_provider not in SUPPORTED_SEARCH_PROVIDERS:
        raise ConfigError(
            f"Unsupported SEARCH_PROVIDER={search_provider!r}. Use duckduckgo or tavily."
        )

    return AppConfig(
        gonka_base_url=base_url,
        gonka_api_key=os.getenv("GONKA_API_KEY", "").strip(),
        gonka_claim_model=os.getenv("GONKA_CLAIM_MODEL", "").strip(),
        gonka_verify_model_1=os.getenv("GONKA_VERIFY_MODEL_1", "").strip(),
        gonka_verify_model_2=os.getenv("GONKA_VERIFY_MODEL_2", "").strip(),
        gonka_judge_model=os.getenv("GONKA_JUDGE_MODEL", "").strip(),
        gonka_vision_model=os.getenv("GONKA_VISION_MODEL", "").strip(),
        search_provider=search_provider,
        tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
        env_file_found=env_file_found,
        gonka_timeout_seconds=read_timeout_seconds(),
        gonka_fallback_model=os.getenv("GONKA_FALLBACK_MODEL", "").strip(),
    )


def validate_base_url(base_url: str) -> None:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(
            f"Invalid GONKA_BASE_URL={base_url!r}. Use a full URL such as {DEFAULT_GONKA_BASE_URL}."
        )


def read_timeout_seconds() -> float:
    raw_value = os.getenv("GONKA_TIMEOUT_SECONDS", "60").strip()
    try:
        timeout = float(raw_value)
    except ValueError as exc:
        raise ConfigError("GONKA_TIMEOUT_SECONDS must be a number between 30 and 600.") from exc
    if not 30 <= timeout <= 600:
        raise ConfigError("GONKA_TIMEOUT_SECONDS must be between 30 and 600.")
    return timeout
