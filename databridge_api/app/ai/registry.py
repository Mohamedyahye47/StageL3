from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

LayerCode = Literal["recommendation"]

LayerLabels = {
    "recommendation": "Recommandation IA",
}


@dataclass(frozen=True)
class ProviderSpec:
    code: str
    label: str
    key_envs: tuple[str, ...]
    model_env: str
    default_model: str
    supported_layers: tuple[LayerCode, ...]
    json_capability: str

    def models(self) -> list[str]:
        if self.code == "local":
            return [self.default_model]
        model = os.getenv(self.model_env, "").strip()
        return [model] if model and not _is_placeholder(model) else []

    def default_runtime_model(self) -> str:
        models = self.models()
        return models[0] if models else self.default_model


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "local": ProviderSpec(
        code="local",
        label="Mode local",
        key_envs=(),
        model_env="AI_MODEL",
        default_model="regles_metier_locales",
        supported_layers=("recommendation",),
        json_capability="local_rules",
    ),
    "openai_compatible": ProviderSpec(
        code="openai_compatible",
        label="OpenAI-compatible",
        key_envs=("AI_BASE_URL", "AI_API_KEY"),
        model_env="AI_MODEL",
        default_model="",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
    ),
}


class AIProviderConfigError(RuntimeError):
    """Raised when an AI provider configuration cannot be executed safely."""


def _is_placeholder(value: str | None) -> bool:
    if value is None:
        return True
    text = value.strip()
    if not text:
        return True
    lowered = text.lower()
    return (
        lowered.startswith("replace-with")
        or lowered.startswith("your-")
        or lowered in {"changeme", "change-me", "todo", "none", "null"}
    )


def env_is_configured(env_name: str) -> bool:
    return not _is_placeholder(os.getenv(env_name))


def _provider_not_supported(provider_code: str) -> bool:
    return provider_code not in PROVIDER_SPECS


def _disabled_reason(spec: ProviderSpec | None, layer: LayerCode) -> str | None:
    if spec is None:
        return "Provider IA non supporté. Utilisez local ou openai_compatible."
    if layer not in spec.supported_layers:
        return "Provider IA non supporté. Utilisez local ou openai_compatible."
    if spec.code == "local":
        return None
    if not env_is_configured("AI_BASE_URL"):
        return "AI_BASE_URL est obligatoire pour openai_compatible."
    if not env_is_configured("AI_API_KEY"):
        return "AI_API_KEY est obligatoire pour openai_compatible."
    if not env_is_configured("AI_MODEL"):
        return "AI_MODEL est obligatoire pour openai_compatible."
    return None


def provider_status(provider_code: str, layer: LayerCode) -> dict[str, Any]:
    spec = PROVIDER_SPECS.get(provider_code)
    reason = _disabled_reason(spec, layer)
    if spec is None:
        return {
            "code": provider_code,
            "label": provider_code,
            "models": [],
            "default_model": "",
            "implemented": False,
            "configured": False,
            "available": False,
            "json_capability": "unsupported",
            "supported_layers": [],
            "disabled_reason": reason,
            "key_envs": [],
        }
    return {
        "code": spec.code,
        "label": spec.label,
        "models": spec.models(),
        "default_model": spec.default_runtime_model(),
        "implemented": True,
        "configured": reason is None,
        "available": reason is None,
        "json_capability": spec.json_capability,
        "supported_layers": list(spec.supported_layers),
        "disabled_reason": reason,
        "key_envs": list(spec.key_envs),
    }


def providers_by_layer() -> dict[str, dict[str, list[dict[str, Any]]]]:
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for layer in LayerLabels:
        statuses = [provider_status(code, layer) for code in ("local", "openai_compatible")]
        result[layer] = {
            "available": [item for item in statuses if item["available"]],
            "disabled": [item for item in statuses if not item["available"]],
        }
    return result


def models_by_layer() -> dict[str, dict[str, list[str]]]:
    result: dict[str, dict[str, list[str]]] = {}
    for layer, values in providers_by_layer().items():
        result[layer] = {
            item["code"]: item["models"]
            for item in [*values["available"], *values["disabled"]]
            if item["models"]
        }
    return result


def validate_layer_config(layer: LayerCode, provider: str, model: str) -> ProviderSpec:
    provider_code = (provider or "").strip().lower()
    if _provider_not_supported(provider_code):
        raise AIProviderConfigError("Provider IA non supporté. Utilisez local ou openai_compatible.")

    spec = PROVIDER_SPECS[provider_code]
    reason = _disabled_reason(spec, layer)
    if reason:
        raise AIProviderConfigError(reason)

    if spec.code == "local":
        return spec

    model_name = (model or "").strip()
    if not model_name:
        raise AIProviderConfigError("AI_MODEL est obligatoire pour openai_compatible.")
    if model_name not in spec.models():
        raise AIProviderConfigError("Le modèle sélectionné n'est pas compatible avec ce fournisseur.")
    return spec
