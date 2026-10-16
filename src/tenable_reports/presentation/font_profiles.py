"""Perfis tipográficos verificáveis por plataforma."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from tenable_reports.infrastructure.runtime_platform import (
    RuntimePlatform,
    RuntimePlatformInfo,
)


_FONT_ASSETS_ROOT = Path(__file__).resolve().parents[1] / "assets" / "fonts"
_PACKAGE_MANIFEST = _FONT_ASSETS_ROOT / "font-manifest.json"
_STYLES = ("regular", "bold", "italic")


class FontProfileError(RuntimeError):
    """Falha fechada de resolução tipográfica com diagnóstico sanitizado."""

    def __init__(self, code: str, *, path: str | None = None) -> None:
        self.code = code
        self.path = Path(path).name if path else None
        detail = f" ({self.path})" if self.path else ""
        super().__init__(f"{code}{detail}")


@dataclass(frozen=True, slots=True)
class FontFace:
    """Arquivos imutáveis de uma família tipográfica."""

    family: str
    regular: Path
    bold: Path
    italic: Path


@dataclass(frozen=True, slots=True)
class FontProfile:
    """Perfil tipográfico integral usado por DOCX e gráficos."""

    profile_id: str
    document: FontFace
    alternate: FontFace

    @property
    def document_family(self) -> str:
        return self.document.family

    @property
    def alternate_family(self) -> str:
        return self.alternate.family

    @property
    def regular_path(self) -> Path:
        return self.document.regular

    @property
    def bold_path(self) -> Path:
        return self.document.bold

    @property
    def italic_path(self) -> Path:
        return self.document.italic


def _read_manifest(path: Path) -> Mapping[str, Any]:
    if not path.is_file():
        raise FontProfileError("FONT_MANIFEST_MISSING", path=path.name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FontProfileError("FONT_MANIFEST_INVALID", path=path.name) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise FontProfileError("FONT_MANIFEST_INVALID", path=path.name)
    return payload


def _profile_definition(
    manifest: Mapping[str, Any],
    profile_id: str,
    *,
    manifest_name: str,
) -> Mapping[str, Any]:
    if manifest.get("profile_id") == profile_id:
        definition: object = manifest
    else:
        profiles = manifest.get("profiles")
        definition = profiles.get(profile_id) if isinstance(profiles, dict) else None
    if not isinstance(definition, dict):
        raise FontProfileError("FONT_PROFILE_MISSING", path=manifest_name)
    return definition


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_path(root: Path, definition: Mapping[str, Any]) -> Path:
    filename = definition.get("file")
    expected_hash = definition.get("sha256")
    relative = Path(filename) if isinstance(filename, str) else Path("font")
    if (
        not isinstance(filename, str)
        or relative.is_absolute()
        or ".." in relative.parts
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
    ):
        raise FontProfileError("FONT_MANIFEST_INVALID", path=str(filename or "font"))
    path = root / relative
    if not path.is_file():
        raise FontProfileError("FONT_FILE_MISSING", path=filename)
    if _sha256(path) != expected_hash.casefold():
        raise FontProfileError("FONT_HASH_MISMATCH", path=filename)
    return path


def _font_face(root: Path, definition: object, *, manifest_name: str) -> FontFace:
    if not isinstance(definition, dict):
        raise FontProfileError("FONT_MANIFEST_INVALID", path=manifest_name)
    family = definition.get("family")
    faces = definition.get("faces")
    if not isinstance(family, str) or not family.strip() or not isinstance(faces, dict):
        raise FontProfileError("FONT_MANIFEST_INVALID", path=manifest_name)
    resolved: dict[str, Path] = {}
    for style in _STYLES:
        item = faces.get(style)
        if not isinstance(item, dict):
            raise FontProfileError("FONT_MANIFEST_INVALID", path=manifest_name)
        resolved[style] = _verified_path(root, item)
    return FontFace(
        family=family.strip(),
        regular=resolved["regular"],
        bold=resolved["bold"],
        italic=resolved["italic"],
    )


def _build_profile(
    *,
    profile_id: str,
    root: Path,
    manifest_path: Path,
) -> FontProfile:
    manifest = _read_manifest(manifest_path)
    definition = _profile_definition(
        manifest,
        profile_id,
        manifest_name=manifest_path.name,
    )
    families = definition.get("families")
    if not isinstance(families, dict):
        raise FontProfileError("FONT_MANIFEST_INVALID", path=manifest_path.name)
    return FontProfile(
        profile_id=profile_id,
        document=_font_face(
            root,
            families.get("document"),
            manifest_name=manifest_path.name,
        ),
        alternate=_font_face(
            root,
            families.get("alternate"),
            manifest_name=manifest_path.name,
        ),
    )


def _default_windows_fonts() -> Path:
    windows_root = Path(os.environ.get("WINDIR", r"C:\Windows"))
    return windows_root / "Fonts"


def resolve_font_profile(
    platform: RuntimePlatformInfo,
    *,
    windows_fonts: Path | None = None,
) -> FontProfile:
    """Resolve e valida integralmente o perfil da plataforma informada."""

    if platform.platform is RuntimePlatform.WINDOWS:
        root = Path(windows_fonts) if windows_fonts is not None else _default_windows_fonts()
        local_manifest = root / "font-manifest.json"
        manifest = local_manifest if local_manifest.is_file() else _PACKAGE_MANIFEST
        return _build_profile(
            profile_id="windows-calibri-v1",
            root=root,
            manifest_path=manifest,
        )
    if platform.platform is RuntimePlatform.LINUX:
        return _build_profile(
            profile_id="linux-carlito-v1",
            root=_FONT_ASSETS_ROOT,
            manifest_path=_PACKAGE_MANIFEST,
        )
    raise FontProfileError("UNSUPPORTED_PLATFORM")
