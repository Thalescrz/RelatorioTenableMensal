from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


OpenUrl = Callable[..., Any]
Sleep = Callable[[float], None]


class GoogleTextTranslator:
    """Traduz texto técnico pelo Google, com cache e erros sem conteúdo sensível."""

    _ENDPOINT = "https://clients5.google.com/translate_a/t"

    def __init__(
        self,
        *,
        open_url: OpenUrl = urlopen,
        sleep: Sleep = time.sleep,
        timeout_seconds: float = 20,
        max_attempts: int = 3,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("O timeout de tradução deve ser positivo.")
        if max_attempts < 1:
            raise ValueError("O número de tentativas de tradução deve ser positivo.")
        self._open_url = open_url
        self._sleep = sleep
        self._timeout_seconds = float(timeout_seconds)
        self._max_attempts = int(max_attempts)
        self._cache: dict[tuple[str, str, str], str] = {}

    @staticmethod
    def _provider_language(language: str) -> str:
        normalized = str(language or "").strip()
        if normalized.casefold() == "pt-br":
            return "pt"
        return normalized

    def __call__(
        self,
        text: str,
        source_language: str,
        target_language: str,
    ) -> str:
        source = self._provider_language(source_language)
        target = self._provider_language(target_language)
        value = str(text or "").strip()
        key = (source, target, value)
        if key in self._cache:
            return self._cache[key]
        translated = self._request_translation(value, source=source, target=target)
        if not translated:
            raise ValueError("O serviço de tradução retornou um bloco vazio.")
        self._cache[key] = translated
        return translated

    def _request_translation(self, text: str, *, source: str, target: str) -> str:
        query = urlencode(
            {
                "client": "dict-chrome-ex",
                "sl": source,
                "tl": target,
                "q": text,
            }
        )
        request = Request(
            f"{self._ENDPOINT}?{query}",
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
            method="GET",
        )
        for attempt in range(1, self._max_attempts + 1):
            try:
                with self._open_url(request, timeout=self._timeout_seconds) as response:
                    status = int(getattr(response, "status", 200))
                    if status == 429 or status >= 500:
                        raise HTTPError(
                            url=self._ENDPOINT,
                            code=status,
                            msg="temporary translation failure",
                            hdrs=None,
                            fp=None,
                        )
                    if status >= 400:
                        raise ValueError("O serviço de tradução recusou a solicitação.")
                    payload = json.loads(response.read().decode("utf-8"))
                return self._translated_value(payload)
            except HTTPError as error:
                retryable = error.code == 429 or error.code >= 500
                if not retryable:
                    raise ValueError("O serviço de tradução recusou a solicitação.") from None
                if attempt == self._max_attempts:
                    break
            except (URLError, TimeoutError, OSError):
                if attempt == self._max_attempts:
                    break
            except (json.JSONDecodeError, UnicodeDecodeError):
                raise ValueError("O serviço de tradução não retornou uma resposta válida.") from None
            self._sleep(float(attempt))
        raise RuntimeError(
            "Falha temporária no serviço de tradução após as tentativas configuradas."
        ) from None

    @staticmethod
    def _translated_value(payload: object) -> str:
        def translated_text(value: object) -> str:
            if isinstance(value, str):
                return value.strip()
            if not isinstance(value, list) or not value:
                return ""
            if isinstance(value[0], str):
                return value[0].strip()
            parts = [
                translated_text(item)
                for item in value
                if isinstance(item, list)
            ]
            return "".join(part for part in parts if part).strip()

        translated = translated_text(payload)
        if translated:
            return translated
        raise ValueError("O serviço de tradução não retornou uma resposta válida.")


def build_default_text_translator() -> GoogleTextTranslator:
    return GoogleTextTranslator()


__all__ = ["GoogleTextTranslator", "build_default_text_translator"]
