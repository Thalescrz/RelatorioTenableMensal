from __future__ import annotations

import sys
from types import SimpleNamespace
from unittest.mock import patch

from tenable_reports.infrastructure.translation import GoogleTextTranslator


def test_google_translator_is_lazy_maps_pt_br_and_caches_repeated_text() -> None:
    created: list[tuple[str, str]] = []
    translated: list[str] = []

    class FakeGoogleTranslator:
        def __init__(self, *, source: str, target: str) -> None:
            created.append((source, target))

        def translate(self, text: str) -> str:
            translated.append(text)
            return f"PT:{text}"

    module = SimpleNamespace(GoogleTranslator=FakeGoogleTranslator)
    with patch.dict(sys.modules, {"deep_translator": module}):
        translator = GoogleTextTranslator()
        first = translator("Technical description.", "en", "pt-BR")
        second = translator("Technical description.", "en", "pt-BR")

    assert first == "PT:Technical description."
    assert second == first
    assert created == [("en", "pt")]
    assert translated == ["Technical description."]
