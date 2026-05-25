from __future__ import annotations

import json
import os
import re
from abc import ABC, abstractmethod
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

try:
    from google import genai
except ImportError:  # pragma: no cover - API remains importable without Gemini routes.
    genai = None

from app.ai.registry import (
    OPENAI_COMPATIBLE_ENDPOINTS,
    AIProviderConfigError,
    LayerCode,
    ProviderSpec,
    validate_layer_config,
)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

AI_HTTP_TIMEOUT = float(os.getenv("AI_HTTP_TIMEOUT", "60"))


class AIProviderExecutionError(RuntimeError):
    """Raised when a configured provider fails to return valid structured JSON."""


class AIProviderClient(ABC):
    """Common interface for all executable AI providers."""

    def __init__(self, spec: ProviderSpec) -> None:
        self.spec = spec

    @abstractmethod
    def generate_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
        temperature: float,
    ) -> SchemaT:
        """Return a Pydantic-validated JSON payload."""


class GeminiClient(AIProviderClient):
    def generate_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
        temperature: float,
    ) -> SchemaT:
        if genai is None:
            raise AIProviderConfigError("google-genai n'est pas installé.")
        api_key = _required_env(self.spec.key_envs[0])
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=model,
            contents=f"{system_prompt}\n\n{user_prompt}",
            config={
                "response_mime_type": "application/json",
                "response_schema": schema,
                "temperature": temperature,
            },
        )
        if hasattr(response, "parsed") and response.parsed:
            return schema.model_validate(response.parsed)
        return _validate_json_text(response.text or "", schema)


class OpenAICompatibleClient(AIProviderClient):
    def generate_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
        temperature: float,
    ) -> SchemaT:
        endpoint = OPENAI_COMPATIBLE_ENDPOINTS.get(self.spec.code)
        if not endpoint:
            raise AIProviderConfigError("Adaptateur non implémenté.")
        api_key = _required_env(self.spec.key_envs[0])
        payload = _chat_payload(model, system_prompt, user_prompt, schema, temperature)
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        return _post_chat_completion(endpoint, headers, payload, schema, provider_label=self.spec.label)


class AzureOpenAIClient(AIProviderClient):
    def generate_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
        temperature: float,
    ) -> SchemaT:
        api_key = _required_env("AZURE_OPENAI_API_KEY")
        endpoint = _required_env("AZURE_OPENAI_ENDPOINT").rstrip("/")
        deployment = model
        api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"
        payload = _chat_payload(model, system_prompt, user_prompt, schema, temperature)
        payload.pop("model", None)
        headers = {"api-key": api_key, "Content-Type": "application/json"}
        return _post_chat_completion(url, headers, payload, schema, provider_label=self.spec.label)


class AnthropicClient(AIProviderClient):
    def generate_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: type[SchemaT],
        temperature: float,
    ) -> SchemaT:
        api_key = _required_env(self.spec.key_envs[0])
        schema_instruction = _json_schema_instruction(schema)
        payload = {
            "model": model,
            "max_tokens": 4096,
            "temperature": temperature,
            "system": f"{system_prompt}\n\n{schema_instruction}",
            "messages": [{"role": "user", "content": user_prompt}],
        }
        headers = {
            "x-api-key": api_key,
            "anthropic-version": os.getenv("ANTHROPIC_VERSION", "2023-06-01"),
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=AI_HTTP_TIMEOUT) as client:
                response = client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise AIProviderExecutionError(f"Erreur fournisseur {self.spec.label}: HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise AIProviderExecutionError(f"Erreur de connexion au fournisseur {self.spec.label}.") from exc

        text_parts = [
            block.get("text", "")
            for block in data.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return _validate_json_text("\n".join(text_parts), schema)


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
    client = _client_for(spec)
    return client.generate_json(
        model=model,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=schema,
        temperature=temperature,
    )


def generate_text(
    *,
    provider: str,
    model: str,
    prompt: str,
    temperature: float,
) -> str:
    spec = validate_layer_config("normalizer", provider, model)
    if spec.adapter != "gemini":
        raise AIProviderConfigError("Le test rapide en texte libre est disponible uniquement pour Gemini.")
    if genai is None:
        raise AIProviderConfigError("google-genai n'est pas installé.")
    api_key = _required_env("GEMINI_API_KEY")
    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config={"temperature": temperature},
    )
    return response.text or ""


def _client_for(spec: ProviderSpec) -> AIProviderClient:
    if spec.adapter == "gemini":
        return GeminiClient(spec)
    if spec.adapter == "azure_openai":
        return AzureOpenAIClient(spec)
    if spec.adapter == "openai_compatible":
        return OpenAICompatibleClient(spec)
    if spec.adapter == "anthropic":
        return AnthropicClient(spec)
    raise AIProviderConfigError("Adaptateur non implémenté.")


def _post_chat_completion(
    endpoint: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    schema: type[SchemaT],
    *,
    provider_label: str,
) -> SchemaT:
    try:
        with httpx.Client(timeout=AI_HTTP_TIMEOUT) as client:
            response = client.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
    except httpx.HTTPStatusError as exc:
        raise AIProviderExecutionError(f"Erreur fournisseur {provider_label}: HTTP {exc.response.status_code}") from exc
    except httpx.HTTPError as exc:
        raise AIProviderExecutionError(f"Erreur de connexion au fournisseur {provider_label}.") from exc

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
        "temperature": temperature,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": f"{system_prompt}\n\n{_json_schema_instruction(schema)}"},
            {"role": "user", "content": user_prompt},
        ],
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


def _required_env(name: str) -> str:
    value = os.getenv(name)
    lowered = value.strip().lower() if value else ""
    if (
        not lowered
        or lowered.startswith("replace-with")
        or lowered.startswith("your-")
        or lowered in {"changeme", "change-me", "todo", "none", "null"}
    ):
        raise AIProviderConfigError("La clé API du fournisseur sélectionné n'est pas configurée.")
    return value.strip()
