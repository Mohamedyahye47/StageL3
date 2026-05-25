from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal

LayerCode = Literal["normalizer", "selector", "evaluator"]

JSONCapability = Literal[
    "native_json_schema",
    "json_object",
    "prompt_json_then_validate",
    "unsupported",
]

LayerLabels: dict[LayerCode, str] = {
    "normalizer": "Normalisation",
    "selector": "Sélection",
    "evaluator": "Évaluation",
}


@dataclass(frozen=True)
class ProviderSpec:
    code: str
    label: str
    adapter: str | None
    key_envs: tuple[str, ...]
    model_env: str
    default_model: str
    supported_layers: tuple[LayerCode, ...]
    json_capability: JSONCapability
    endpoint_env: str | None = None
    deployment_env: str | None = None
    api_version_env: str | None = None

    def models(self) -> list[str]:
        raw = os.getenv(self.model_env, self.default_model)
        models: list[str] = []
        for item in (self.default_model, *raw.split(",")):
            model = item.strip()
            if model and model not in models and not _is_placeholder(model):
                models.append(model)
        if self.code == "gemini" and "gemini-2.5-flash" not in models:
            models.append("gemini-2.5-flash")
        return models or [self.default_model]

    def default_runtime_model(self) -> str:
        return self.models()[0]


PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "gemini": ProviderSpec(
        code="gemini",
        label="Gemini",
        adapter="gemini",
        key_envs=("GEMINI_API_KEY",),
        model_env="GEMINI_MODEL",
        default_model="gemini-2.5-flash-lite",
        supported_layers=("normalizer", "selector", "evaluator"),
        json_capability="native_json_schema",
    ),
    "openai": ProviderSpec(
        code="openai",
        label="OpenAI",
        adapter="openai_compatible",
        key_envs=("OPENAI_API_KEY",),
        model_env="OPENAI_MODEL",
        default_model="gpt-4.1-mini",
        supported_layers=("normalizer", "selector", "evaluator"),
        json_capability="json_object",
    ),
    "azure_openai": ProviderSpec(
        code="azure_openai",
        label="Azure OpenAI",
        adapter="azure_openai",
        key_envs=("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT"),
        model_env="AZURE_OPENAI_DEPLOYMENT",
        default_model="replace-with-your-azure-deployment-name",
        supported_layers=("normalizer", "selector", "evaluator"),
        json_capability="json_object",
        endpoint_env="AZURE_OPENAI_ENDPOINT",
        deployment_env="AZURE_OPENAI_DEPLOYMENT",
        api_version_env="AZURE_OPENAI_API_VERSION",
    ),
    "anthropic": ProviderSpec(
        code="anthropic",
        label="Anthropic",
        adapter="anthropic",
        key_envs=("ANTHROPIC_API_KEY",),
        model_env="ANTHROPIC_MODEL",
        default_model="claude-3-5-haiku-latest",
        supported_layers=("normalizer", "selector", "evaluator"),
        json_capability="prompt_json_then_validate",
    ),
    "mistral": ProviderSpec(
        code="mistral",
        label="Mistral",
        adapter="openai_compatible",
        key_envs=("MISTRAL_API_KEY",),
        model_env="MISTRAL_MODEL",
        default_model="mistral-small-latest",
        supported_layers=("normalizer", "selector", "evaluator"),
        json_capability="json_object",
    ),
    "deepseek": ProviderSpec(
        code="deepseek",
        label="DeepSeek",
        adapter="openai_compatible",
        key_envs=("DEEPSEEK_API_KEY",),
        model_env="DEEPSEEK_MODEL",
        default_model="deepseek-chat",
        supported_layers=("normalizer", "selector", "evaluator"),
        json_capability="json_object",
    ),
    "cohere": ProviderSpec(
        code="cohere",
        label="Cohere",
        adapter=None,
        key_envs=("COHERE_API_KEY",),
        model_env="COHERE_MODEL",
        default_model="command-r-plus",
        supported_layers=(),
        json_capability="unsupported",
    ),
    "groq": ProviderSpec(
        code="groq",
        label="Groq",
        adapter="openai_compatible",
        key_envs=("GROQ_API_KEY",),
        model_env="GROQ_MODEL",
        default_model="llama-3.1-70b-versatile",
        supported_layers=("normalizer", "selector", "evaluator"),
        json_capability="json_object",
    ),
    "together": ProviderSpec(
        code="together",
        label="Together AI",
        adapter="openai_compatible",
        key_envs=("TOGETHER_API_KEY",),
        model_env="TOGETHER_MODEL",
        default_model="meta-llama/Llama-3.1-70B-Instruct-Turbo",
        supported_layers=("normalizer", "selector", "evaluator"),
        json_capability="json_object",
    ),
    "xai": ProviderSpec(
        code="xai",
        label="xAI",
        adapter="openai_compatible",
        key_envs=("XAI_API_KEY",),
        model_env="XAI_MODEL",
        default_model="grok-2-latest",
        supported_layers=("normalizer", "selector", "evaluator"),
        json_capability="json_object",
    ),
    "perplexity": ProviderSpec(
        code="perplexity",
        label="Perplexity",
        adapter=None,
        key_envs=("PERPLEXITY_API_KEY",),
        model_env="PERPLEXITY_MODEL",
        default_model="sonar",
        supported_layers=(),
        json_capability="unsupported",
    ),
}


OPENAI_COMPATIBLE_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "mistral": "https://api.mistral.ai/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/v1/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "together": "https://api.together.xyz/v1/chat/completions",
    "xai": "https://api.x.ai/v1/chat/completions",
}


class AIProviderConfigError(RuntimeError):
    """Raised when a provider/model configuration cannot be executed safely."""


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


def provider_is_configured(spec: ProviderSpec) -> bool:
    return all(env_is_configured(name) for name in spec.key_envs)


def _declared_provider_codes() -> list[str]:
    raw = os.getenv(
        "SUPPORTED_AI_PROVIDERS",
        "gemini,openai,azure_openai,anthropic,mistral,deepseek,cohere,groq,together,xai,perplexity",
    )
    codes = []
    for item in raw.split(","):
        code = item.strip().lower()
        if code and code != "ollama" and code not in codes:
            codes.append(code)
    return codes


def _disabled_reason(spec: ProviderSpec | None, layer: LayerCode) -> str | None:
    if spec is None:
        return "Adaptateur non implémenté."
    if spec.adapter is None:
        return "Adaptateur non implémenté."
    if layer not in spec.supported_layers:
        return "Ce fournisseur n'est pas disponible pour cette couche."
    if spec.json_capability == "unsupported":
        return "JSON structuré non supporté pour cette couche."
    if not provider_is_configured(spec):
        return "La clé API du fournisseur sélectionné n'est pas configurée."
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
        "implemented": spec.adapter is not None,
        "configured": provider_is_configured(spec),
        "available": reason is None,
        "json_capability": spec.json_capability,
        "supported_layers": list(spec.supported_layers),
        "disabled_reason": reason,
        "key_envs": list(spec.key_envs),
    }


def providers_by_layer() -> dict[str, dict[str, list[dict[str, Any]]]]:
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    declared = _declared_provider_codes()
    for layer in LayerLabels:
        statuses = [provider_status(code, layer) for code in declared]
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
            for item in values["available"]
        }
    return result


def validate_layer_config(layer: LayerCode, provider: str, model: str) -> ProviderSpec:
    provider_code = (provider or "").strip().lower()
    model_name = (model or "").strip()
    spec = PROVIDER_SPECS.get(provider_code)
    reason = _disabled_reason(spec, layer)
    if reason:
        raise AIProviderConfigError(reason)
    assert spec is not None
    if model_name not in spec.models():
        raise AIProviderConfigError("Le modèle sélectionné n'est pas compatible avec ce fournisseur.")
    return spec
