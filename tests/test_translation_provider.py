from __future__ import annotations

import json
from urllib.error import HTTPError

import pytest

from tenable_reports.infrastructure.translation import GoogleTextTranslator


class _Response:
    def __init__(self, payload: object) -> None:
        self._payload = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._payload


def test_google_translator_uses_safe_google_route_maps_pt_br_and_caches() -> None:
    requested: list[tuple[str, float]] = []

    def open_url(request: object, *, timeout: float) -> _Response:
        requested.append((str(getattr(request, "full_url", "")), timeout))
        return _Response(["Descricao tecnica traduzida."])

    translator = GoogleTextTranslator(open_url=open_url, timeout_seconds=17)
    first = translator("Technical description.", "en", "pt-BR")
    second = translator("Technical description.", "en", "pt-BR")

    assert first == "Descricao tecnica traduzida."
    assert second == first
    assert len(requested) == 1
    assert requested[0][1] == 17
    assert requested[0][0].startswith("https://clients5.google.com/translate_a/t?")
    assert "sl=en" in requested[0][0]
    assert "tl=pt" in requested[0][0]
    assert "q=Technical+description." in requested[0][0]


def test_google_translator_extracts_text_from_real_nested_response_shape() -> None:
    translator = GoogleTextTranslator(
        open_url=lambda *_args, **_kwargs: _Response(
            [["Descrição técnica traduzida.", "en"]]
        )
    )

    assert (
        translator("Technical description.", "auto", "pt-BR")
        == "Descrição técnica traduzida."
    )


def test_google_translator_retries_429_without_exposing_text_in_error() -> None:
    attempts = 0
    sleeps: list[float] = []

    def open_url(request: object, *, timeout: float) -> _Response:
        nonlocal attempts
        attempts += 1
        raise HTTPError(
            url=str(getattr(request, "full_url", "")),
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=None,
        )

    translator = GoogleTextTranslator(
        open_url=open_url,
        sleep=sleeps.append,
        max_attempts=3,
    )

    with pytest.raises(RuntimeError) as error:
        translator("Sensitive technical description.", "en", "pt-BR")

    assert attempts == 3
    assert sleeps == [1.0, 2.0]
    assert "Sensitive technical description" not in str(error.value)
    assert "clients5.google.com" not in str(error.value)


@pytest.mark.parametrize(
    "payload",
    ([], [""], {"unexpected": "shape"}),
)
def test_google_translator_rejects_empty_or_invalid_payload(payload: object) -> None:
    translator = GoogleTextTranslator(open_url=lambda *_args, **_kwargs: _Response(payload))

    with pytest.raises(ValueError, match="resposta"):
        translator("Technical description.", "en", "pt-BR")
