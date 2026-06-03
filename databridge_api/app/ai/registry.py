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
    base_url: str
    key_envs: tuple[str, ...]
    models_list: tuple[str, ...]
    default_model: str
    supported_layers: tuple[LayerCode, ...]
    json_capability: str
    base_url_env: str | None = None
    model_env: str | None = None
    models_env: str | None = None

    def models(self) -> list[str]:
        if self.code == "local":
            return [self.default_model]
        if self.code == "openai_compatible":
            model = os.getenv(self.model_env or "AI_MODEL", "").strip()
            return [model] if model and not _is_placeholder(model) else []

        configured_models = _split_csv(os.getenv(self.models_env or ""))
        return _unique_non_placeholder([*self.models_list, *configured_models])

    def default_runtime_model(self) -> str:
        configured = os.getenv(self.model_env, "").strip() if self.model_env else ""
        if configured and configured in self.models():
            return configured
        models = self.models()
        return self.default_model if self.default_model in models else (models[0] if models else "")

    def chat_completions_base_url(self) -> str:
        if self.base_url_env:
            return os.getenv(self.base_url_env, "").strip()
        return self.base_url


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "local": ProviderSpec(
        code="local",
        label="Mode local",
        base_url="",
        key_envs=(),
        models_list=("regles_metier_locales",),
        default_model="regles_metier_locales",
        supported_layers=("recommendation",),
        json_capability="local_rules",
    ),
    "gemini": ProviderSpec(
        code="gemini",
        label="Gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        key_envs=("GEMINI_API_KEY",),
        models_list=(
            "gemini-2.5-flash-lite",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
        ),
        default_model="gemini-2.5-flash-lite",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="GEMINI_MODELS",
    ),
    "openai": ProviderSpec(
        code="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        key_envs=("OPENAI_API_KEY",),
        models_list=(
            "gpt-4.1-mini",
            "gpt-4.1",
            "gpt-4.1-nano",
            "gpt-4o-mini",
            "gpt-4o",
            "o4-mini",
            "o3-mini",
            "o3",
        ),
        default_model="gpt-4.1-mini",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="OPENAI_MODELS",
    ),
    "deepseek": ProviderSpec(
        code="deepseek",
        label="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        key_envs=("DEEPSEEK_API_KEY",),
        models_list=(
            "deepseek-chat",
            "deepseek-reasoner",
            "deepseek-v4-flash",
            "deepseek-v4-pro",
        ),
        default_model="deepseek-chat",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="DEEPSEEK_MODELS",
    ),
    "mistral": ProviderSpec(
        code="mistral",
        label="Mistral",
        base_url="https://api.mistral.ai/v1",
        key_envs=("MISTRAL_API_KEY",),
        models_list=(
            "mistral-small-latest",
            "mistral-medium-latest",
            "mistral-large-latest",
            "ministral-3b-latest",
            "ministral-8b-latest",
            "open-mistral-nemo",
            "codestral-latest",
        ),
        default_model="mistral-small-latest",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="MISTRAL_MODELS",
    ),
    "groq": ProviderSpec(
        code="groq",
        label="Groq",
        base_url="https://api.groq.com/openai/v1",
        key_envs=("GROQ_API_KEY",),
        models_list=(
            "llama-3.3-70b-versatile",
            "llama-3.1-70b-versatile",
            "llama-3.1-8b-instant",
            "deepseek-r1-distill-llama-70b",
            "qwen/qwen3-32b",
        ),
        default_model="llama-3.3-70b-versatile",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="GROQ_MODELS",
    ),
    "together": ProviderSpec(
        code="together",
        label="Together AI",
        base_url="https://api.together.xyz/v1",
        key_envs=("TOGETHER_API_KEY",),
        models_list=(
            "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "meta-llama/Llama-3.1-70B-Instruct-Turbo",
            "meta-llama/Llama-3.1-8B-Instruct-Turbo",
            "mistralai/Mixtral-8x7B-Instruct-v0.1",
            "Qwen/Qwen2.5-72B-Instruct-Turbo",
        ),
        default_model="meta-llama/Llama-3.3-70B-Instruct-Turbo",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="TOGETHER_MODELS",
    ),
    "xai": ProviderSpec(
        code="xai",
        label="xAI",
        base_url="https://api.x.ai/v1",
        key_envs=("XAI_API_KEY",),
        models_list=(
            "grok-3-mini",
            "grok-3-latest",
            "grok-2-latest",
            "grok-2",
        ),
        default_model="grok-3-mini",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="XAI_MODELS",
    ),
    "perplexity": ProviderSpec(
        code="perplexity",
        label="Perplexity",
        base_url="https://api.perplexity.ai",
        key_envs=("PERPLEXITY_API_KEY",),
        models_list=(
            "sonar",
            "sonar-pro",
            "sonar-reasoning",
            "sonar-reasoning-pro",
        ),
        default_model="sonar",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="PERPLEXITY_MODELS",
    ),
    "openrouter": ProviderSpec(
        code="openrouter",
        label="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        key_envs=("OPENROUTER_API_KEY",),
        models_list=(
            "openai/gpt-4.1-mini",
            "google/gemini-2.5-flash",
            "deepseek/deepseek-chat",
            "mistralai/mistral-small",
            "meta-llama/llama-3.3-70b-instruct",
        ),
        default_model="openai/gpt-4.1-mini",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="OPENROUTER_MODELS",
    ),
    "fireworks": ProviderSpec(
        code="fireworks",
        label="Fireworks AI",
        base_url="https://api.fireworks.ai/inference/v1",
        key_envs=("FIREWORKS_API_KEY",),
        models_list=(
            "accounts/fireworks/models/llama-v3p1-70b-instruct",
            "accounts/fireworks/models/llama-v3p1-8b-instruct",
            "accounts/fireworks/models/deepseek-v3",
            "accounts/fireworks/models/qwen2p5-72b-instruct",
        ),
        default_model="accounts/fireworks/models/llama-v3p1-70b-instruct",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="FIREWORKS_MODELS",
    ),
    "cerebras": ProviderSpec(
        code="cerebras",
        label="Cerebras",
        base_url="https://api.cerebras.ai/v1",
        key_envs=("CEREBRAS_API_KEY",),
        models_list=(
            "llama3.1-8b",
            "llama3.1-70b",
            "llama-3.3-70b",
        ),
        default_model="llama-3.3-70b",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="CEREBRAS_MODELS",
    ),
    "nvidia": ProviderSpec(
        code="nvidia",
        label="NVIDIA NIM",
        base_url="https://integrate.api.nvidia.com/v1",
        key_envs=("NVIDIA_API_KEY",),
        models_list=(
            "meta/llama-3.1-70b-instruct",
            "meta/llama-3.1-8b-instruct",
            "nvidia/llama-3.1-nemotron-70b-instruct",
        ),
        default_model="meta/llama-3.1-70b-instruct",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="NVIDIA_MODELS",
    ),
    "sambanova": ProviderSpec(
        code="sambanova",
        label="SambaNova",
        base_url="https://api.sambanova.ai/v1",
        key_envs=("SAMBANOVA_API_KEY",),
        models_list=(
            "Meta-Llama-3.1-70B-Instruct",
            "Meta-Llama-3.1-8B-Instruct",
            "DeepSeek-R1",
        ),
        default_model="Meta-Llama-3.1-70B-Instruct",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        models_env="SAMBANOVA_MODELS",
    ),
    "openai_compatible": ProviderSpec(
        code="openai_compatible",
        label="OpenAI-compatible personnalisé",
        base_url="",
        key_envs=("AI_API_KEY",),
        models_list=(),
        default_model="",
        supported_layers=("recommendation",),
        json_capability="prompt_json_then_validate",
        base_url_env="AI_BASE_URL",
        model_env="AI_MODEL",
    ),
}


class AIProviderConfigError(RuntimeError):
    """Raised when an AI provider configuration cannot be executed safely."""


def _split_csv(value: str | None) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _unique_non_placeholder(values: list[str] | tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = (value or "").strip()
        if not clean or _is_placeholder(clean) or clean in result:
            continue
        result.append(clean)
    return result


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


def _provider_supported_message() -> str:
    return "Provider IA non supporté. Utilisez un fournisseur déclaré dans le registre IA."


def _disabled_reason(spec: ProviderSpec | None, layer: LayerCode) -> str | None:
    if spec is None:
        return _provider_supported_message()
    if layer not in spec.supported_layers:
        return "Ce fournisseur n'est pas disponible pour cette couche."
    if spec.code == "local":
        return None
    if spec.base_url_env and not env_is_configured(spec.base_url_env):
        return f"{spec.base_url_env} est obligatoire pour {spec.label}."
    missing_keys = [env_name for env_name in spec.key_envs if not env_is_configured(env_name)]
    if missing_keys:
        return f"{missing_keys[0]} est obligatoire pour {spec.label}."
    if not spec.models():
        return f"AI_MODEL est obligatoire pour {spec.label}."
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
        statuses = [provider_status(code, layer) for code in PROVIDER_SPECS]
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
        raise AIProviderConfigError(_provider_supported_message())

    spec = PROVIDER_SPECS[provider_code]
    reason = _disabled_reason(spec, layer)
    if reason:
        raise AIProviderConfigError(reason)

    if spec.code == "local":
        return spec

    model_name = (model or "").strip()
    if not model_name:
        raise AIProviderConfigError(f"AI_MODEL est obligatoire pour {spec.label}.")
    if model_name not in spec.models():
        raise AIProviderConfigError("Le modèle sélectionné n'est pas compatible avec ce fournisseur.")
    return spec
