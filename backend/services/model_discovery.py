from __future__ import annotations

from services.gonka_client import GonkaClient


def discover_models(client: GonkaClient) -> list[str]:
    model_ids = client.list_models()
    if not model_ids:
        raise ValueError("Gonka returned an empty model list for this account.")
    return model_ids


def validate_configured_models(
    available_model_ids: list[str],
    configured_model_ids: list[str],
) -> list[str]:
    available = set(available_model_ids)
    return [model_id for model_id in configured_model_ids if model_id and model_id not in available]
