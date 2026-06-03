from __future__ import annotations

import json
import os
import re
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.ai.registry import AIProviderConfigError, LayerCode, validate_layer_config

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class AIProviderExecutionError(RuntimeError):
    """Raised when the external AI provider fails without exposing secrets."""

    def __init__(self, message: str, *, status_code: int = 503) -> None:
        self.status_code = status_code
        super().__init__(message)


def generate_json(
    *,
    layer: LayerCode,
    provider: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: type[SchemaT],
    temperature: float,
) -> SchemaT:
    spec = validate_layer_config(layer, provider, model)
    if spec.code == "local":
        raise AIProviderConfigError("Le mode local ne déclenche aucun appel IA externe.")

    endpoint = _chat_completions_endpoint(_required_env("AI_BASE_URL", "AI_BASE_URL est obligatoire pour openai_compatible."))
    api_key = _required_env("AI_API_KEY", "AI_API_KEY est obligatoire pour openai_compatible.")
    model_name = (model or "").strip()
    if not model_name:
        raise AIProviderConfigError("AI_MODEL est obligatoire pour openai_compatible.")

    payload = _chat_payload(model_name, system_prompt, user_prompt, schema, temperature)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    return _post_chat_completion(endpoint, headers, payload, schema)


def _chat_completions_endpoint(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/chat/completions"


def _post_chat_completion(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    schema: type[SchemaT],
) -> SchemaT:
    timeout = _timeout_seconds()
    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException as exc:
        raise AIProviderExecutionError("Timeout du fournisseur IA externe.", status_code=504) from exc
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 401:
            raise AIProviderExecutionError("Clé API IA invalide ou non autorisée.", status_code=401) from exc
        if status == 429:
            raise AIProviderExecutionError("Quota ou limite de taux IA dépassé.", status_code=429) from exc
        raise AIProviderExecutionError(f"Erreur du fournisseur IA externe: HTTP {status}.") from exc
    except httpx.HTTPError as exc:
        raise AIProviderExecutionError("Erreur de connexion au fournisseur IA externe.") from exc
    except ValueError as exc:
        raise AIProviderExecutionError("Réponse fournisseur IA invalide.") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIProviderExecutionError("Réponse fournisseur IA invalide.") from exc
    return _validate_json_text(content or "", schema)


def _chat_payload(
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: type[SchemaT],
    temperature: float,
) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": f"{system_prompt}\n\n{_json_schema_instruction(schema)}"},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
    }


def _json_schema_instruction(schema: type[BaseModel]) -> str:
    return (
        "Réponds uniquement avec un objet JSON valide, sans Markdown. "
        "Le JSON doit respecter ce schéma Pydantic :\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )


def _validate_json_text(text: str, schema: type[SchemaT]) -> SchemaT:
    cleaned = _extract_json(text)
    try:
        return schema.model_validate_json(cleaned)
    except (ValidationError, ValueError) as exc:
        raise AIProviderExecutionError("La réponse IA ne respecte pas le schéma JSON attendu.") from exc


def _extract_json(text: str) -> str:
    stripped = (text or "").strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped, flags=re.IGNORECASE)
        stripped = re.sub(r"\s*```$", "", stripped)
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped
    match = re.search(r"\{.*\}", stripped, flags=re.DOTALL)
    if not match:
        raise AIProviderExecutionError("La réponse IA ne contient pas de JSON exploitable.")
    return match.group(0)


def _required_env(name: str, message: str) -> str:
    value = os.getenv(name)
    lowered = value.strip().lower() if value else ""
    if (
        not lowered
        or lowered.startswith("replace-with")
        or lowered.startswith("your-")
        or lowered in {"changeme", "change-me", "todo", "none", "null"}
    ):
        raise AIProviderConfigError(message)
    return value.strip()


def _timeout_seconds() -> float:
    try:
        from app import config as app_config

        return max(1.0, float(getattr(app_config, "AI_TIMEOUT_SECONDS", os.getenv("AI_TIMEOUT_SECONDS", "60"))))
    except ValueError:
        return 60.0
