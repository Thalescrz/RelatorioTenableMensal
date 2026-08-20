from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


class EnvironmentError(ValueError):
    """Configuracao local invalida."""


def load_dotenv_file(path: str | Path, *, override: bool = False) -> dict[str, str]:
    """Carrega um .env simples sem imprimir nem retornar segredos em erros."""
    env_path = Path(path)
    if not env_path.exists():
        return {}

    loaded: dict[str, str] = {}
    for number, raw_line in enumerate(env_path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise EnvironmentError(f"Linha {number} invalida no arquivo de ambiente.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            raise EnvironmentError(f"Nome de variavel invalido na linha {number}.")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value
    return loaded


def _parse_bool(name: str, raw: str, *, default: bool) -> bool:
    if not raw:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "sim", "on"}:
        return True
    if value in {"0", "false", "no", "nao", "off"}:
        return False
    raise EnvironmentError(f"{name} deve ser true ou false.")


def _validated_https_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc:
        raise EnvironmentError("TENABLE_BASE_URL deve ser uma URL HTTPS completa.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise EnvironmentError("TENABLE_BASE_URL nao pode conter credenciais, query ou fragmento.")
    return value


@dataclass(frozen=True, slots=True)
class CredentialConfig:
    access_key: str
    secret_key: str
    base_url: str = "https://cloud.tenable.com"
    ca_bundle: str | None = None
    timeout_seconds: float = 30.0
    validate_tls: bool = True

    @property
    def is_complete(self) -> bool:
        return bool(self.access_key.strip() and self.secret_key.strip())

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> "CredentialConfig":
        values = os.environ if environ is None else environ
        try:
            timeout = float(values.get("TENABLE_HTTP_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise EnvironmentError("TENABLE_HTTP_TIMEOUT_SECONDS deve ser numerico.") from exc
        if timeout <= 0:
            raise EnvironmentError("TENABLE_HTTP_TIMEOUT_SECONDS deve ser maior que zero.")

        ca_bundle = values.get("TENABLE_CA_BUNDLE", "").strip() or None
        if ca_bundle and not Path(ca_bundle).expanduser().is_file():
            raise EnvironmentError("TENABLE_CA_BUNDLE nao aponta para um arquivo existente.")

        return cls(
            access_key=values.get("TENABLE_ACCESS", "").strip(),
            secret_key=values.get("TENABLE_SECRET", "").strip(),
            base_url=_validated_https_url(
                values.get("TENABLE_BASE_URL", "https://cloud.tenable.com")
            ),
            ca_bundle=str(Path(ca_bundle).expanduser().resolve()) if ca_bundle else None,
            timeout_seconds=timeout,
            validate_tls=_parse_bool(
                "TENABLE_VALIDATE_TLS",
                values.get("TENABLE_VALIDATE_TLS", "true"),
                default=True,
            ),
        )
