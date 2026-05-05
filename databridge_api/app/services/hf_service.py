from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from huggingface_hub import CommitOperationAdd, HfApi

from app.config import HF_DATASET_VISIBILITY, HF_NAMESPACE, HF_TOKEN


def get_hf_api(token: str | None = None) -> HfApi:
    resolved_token = token or HF_TOKEN
    if not resolved_token:
        raise RuntimeError("HF_TOKEN est manquant cote backend.")
    return HfApi(token=resolved_token)


def build_repo_id(slug: str, namespace: str | None = None) -> str:
    resolved_namespace = (namespace or HF_NAMESPACE).strip()
    if not resolved_namespace:
        raise RuntimeError("HF_NAMESPACE est manquant cote backend.")
    return f"{resolved_namespace}/{slug}"


def get_remote_dataset_url(repo_id: str) -> str:
    return f"https://huggingface.co/datasets/{repo_id}"


def get_manifest_url(repo_id: str, remote_version: str) -> str:
    return f"{get_remote_dataset_url(repo_id)}/resolve/main/versions/{remote_version}/manifest.json"


def get_repo_file_url(repo_id: str, repo_path: str) -> str:
    clean_path = repo_path.replace("\\", "/").lstrip("/")
    return f"{get_remote_dataset_url(repo_id)}/resolve/main/{clean_path}"


def ensure_dataset_repo(
    api: HfApi,
    repo_id: str,
    visibility: str | None = None,
) -> None:
    resolved_visibility = (visibility or HF_DATASET_VISIBILITY or "public").lower()
    api.create_repo(
        repo_id=repo_id,
        repo_type="dataset",
        private=resolved_visibility != "public",
        exist_ok=True,
    )


def upload_dataset_artifacts(
    api: HfApi,
    repo_id: str,
    artifacts: dict[str, str],
    *,
    commit_message: str,
) -> None:
    if not artifacts:
        raise RuntimeError("Aucun artefact a publier vers Hugging Face.")

    operations: list[CommitOperationAdd] = []
    with tempfile.TemporaryDirectory(prefix="richat_publish_") as temp_dir:
        temp_root = Path(temp_dir)
        for repo_path, content in artifacts.items():
            local_path = temp_root / repo_path
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(content, encoding="utf-8")
            operations.append(
                CommitOperationAdd(
                    path_in_repo=repo_path.replace("\\", "/"),
                    path_or_fileobj=str(local_path),
                )
            )

        api.create_commit(
            repo_id=repo_id,
            repo_type="dataset",
            operations=operations,
            commit_message=commit_message,
        )


def list_remote_datasets(api: HfApi | None = None) -> list[str]:
    client = api or get_hf_api()
    return [dataset.id for dataset in client.list_datasets(author=HF_NAMESPACE)]


def check_hf_health(api: HfApi | None = None) -> dict[str, Any]:
    try:
        client = api or get_hf_api()
        whoami = client.whoami()
        user_name = whoami.get("name") if isinstance(whoami, dict) else None
        return {
            "ok": True,
            "namespace": HF_NAMESPACE or user_name,
            "visibility": HF_DATASET_VISIBILITY or "public",
            "message": f"Connexion Hugging Face OK pour {user_name or HF_NAMESPACE}.",
        }
    except Exception as exc:
        return {
            "ok": False,
            "namespace": HF_NAMESPACE or None,
            "visibility": HF_DATASET_VISIBILITY or "public",
            "message": _format_hf_error(exc),
        }


def _format_hf_error(exc: Exception) -> str:
    message = str(exc)
    lowered = message.lower()
    if "getaddrinfo failed" in lowered:
        return "Connexion Hugging Face indisponible : impossible de resoudre le nom du serveur."
    if "timed out" in lowered or "timeout" in lowered:
        return "Connexion Hugging Face indisponible : delai d'attente depasse."
    return f"Connexion Hugging Face indisponible : {message}"



def delete_remote_dataset(repo_id: str, api: HfApi | None = None) -> None:
    """
    Deletes a dataset repository from Hugging Face.

    Warning:
    This is irreversible on Hugging Face.
    """
    client = api or get_hf_api()
    client.delete_repo(
        repo_id=repo_id,
        repo_type="dataset",
        missing_ok=True,
    )