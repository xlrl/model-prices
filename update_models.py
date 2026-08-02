#!/usr/bin/env python3
"""
Fetch the latest models from the OpenRouter API and, if anything changed
compared to the newest existing data/models_<timestamp>.json snapshot,
write a new timestamped snapshot there.

Models are sorted by ID for easier comparison and tracking.
"""

import copy
import glob
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def fetch_openrouter_models() -> list[dict[str, Any]]:
    """Fetch available models from OpenRouter's public models endpoint."""
    url = "https://openrouter.ai/api/v1/models"
    headers = {"User-Agent": "model-prices-update-bot/1.0"}

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req) as response:
            data = json.load(response)
            return data.get("data", [])
    except urllib.error.URLError as e:
        print(f"Error fetching OpenRouter models: {e}")
        return []
    except json.JSONDecodeError as e:
        print(f"Error decoding OpenRouter response: {e}")
        return []


def is_reasoning_model(model_data: dict[str, Any]) -> bool:
    """Determine if a model supports reasoning."""
    supported_params = model_data.get("supported_parameters", [])
    if "reasoning" in supported_params or "include_reasoning" in supported_params:
        return True

    # Check model name/slug for reasoning indicators
    model_id = model_data.get("id", "").lower()
    canonical_slug = model_data.get("canonical_slug")
    if canonical_slug:
        model_id = model_id + " " + canonical_slug.lower()

    reasoning_indicators = [
        "-r1", "r1", "reasoning", "thinking", "o3", "o4",
        "glm-4.5", "glm-4.6", "glm-4.7", "glm-5",
        "magistral", "hermes", "intellect", "green-r", "greenr",
        "nova-pro", "nova-lite", "nova-micro",
        "claude-opus", "claude-sonnet",
        "deepseek-r1", "deepseek-r1-", "deepseek-r1-distill",
        "qwen3-next", "qwen3-235b-a22b-thinking", "qwen3-30b-a3b-thinking"
    ]

    for indicator in reasoning_indicators:
        if indicator in model_id:
            return True

    # Check tags
    tags = model_data.get("tags", [])
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and "reasoning" in tag.lower():
                return True

    return False


def get_input_types(model_data: dict[str, Any]) -> list[str]:
    """Determine input types from model architecture."""
    input_types = ["text"]

    arch = model_data.get("architecture", {})
    input_modalities = arch.get("input_modalities", [])

    if "image" in input_modalities:
        input_types.append("image")

    return input_types


def get_context_window(model_data: dict[str, Any]) -> int:
    """Get context window size."""
    top_provider = model_data.get("top_provider", {})
    context = top_provider.get("context_length")
    if context:
        return int(context)

    context = model_data.get("context_length")
    if context:
        return int(context)

    return 128000


def get_max_tokens(model_data: dict[str, Any]) -> int:
    """Get max completion tokens."""
    top_provider = model_data.get("top_provider", {})
    max_tokens = top_provider.get("max_completion_tokens")
    if max_tokens:
        return int(max_tokens)

    # Default based on context window
    context = get_context_window(model_data)
    if context <= 32000:
        return 8192
    elif context <= 128000:
        return 16384
    else:
        return 32768


def convert_cost(pricing: dict[str, Any]) -> dict[str, float]:
    """Convert pricing to per million tokens."""
    # Pricing fields might be per token or per million; detect by magnitude
    prompt = pricing.get("prompt")
    completion = pricing.get("completion")

    def to_per_million(value) -> float:
        if value is None:
            return 0.0
        try:
            val = float(value)
            # If value < 0.001, assume per token, convert to per million
            if val < 0.001:
                return val * 1_000_000
            else:
                # Already per million (OpenRouter style)
                return val
        except (ValueError, TypeError):
            return 0.0

    return {
        "input": to_per_million(prompt),
        "output": to_per_million(completion),
        "cacheRead": 0.0,
        "cacheWrite": 0.0
    }


def convert_openrouter_model(model_data: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenRouter API model to pi model configuration."""
    model_id = model_data["id"]
    name = model_data.get("name", model_id)

    reasoning = is_reasoning_model(model_data)
    input_types = get_input_types(model_data)
    context_window = get_context_window(model_data)
    max_tokens = get_max_tokens(model_data)
    cost = convert_cost(model_data.get("pricing", {}))

    model_config = {
        "id": model_id,
        "name": f"OR: {name}",
        "reasoning": reasoning,
        "input": input_types,
        "contextWindow": context_window,
        "maxTokens": max_tokens,
        "cost": cost
    }

    if reasoning:
        model_config["compat"] = {
            "supportsReasoningEffort": False
        }

    return model_config


def dict_diff(old: Any, new: Any, path: str = "") -> dict[str, dict[str, Any]]:
    """
    Recursively compare two dicts (or other values) and return a dict of differences.
    Returns dict with keys being dot‑separated paths, values being {'old': old_value, 'new': new_value}
    """
    diff = {}
    if isinstance(old, dict) and isinstance(new, dict):
        all_keys = set(old.keys()) | set(new.keys())
        for key in all_keys:
            old_val = old.get(key)
            new_val = new.get(key)
            subpath = f"{path}.{key}" if path else key
            if key not in old:
                diff[subpath] = {"old": None, "new": new_val}
            elif key not in new:
                diff[subpath] = {"old": old_val, "new": None}
            else:
                # both exist, compare recursively
                subdiff = dict_diff(old_val, new_val, subpath)
                diff.update(subdiff)
    elif isinstance(old, list) and isinstance(new, list):
        # treat lists as equal if they have same elements in same order
        if old != new:
            diff[path] = {"old": old, "new": new}
    else:
        # primitive or different types
        if old != new:
            diff[path] = {"old": old, "new": new}
    return diff


def summarize_changes(
    old_data: dict[str, Any],
    new_data: dict[str, Any],
    providers: list[str]
) -> None:
    """Print a summary of model changes between old and new data."""
    print("\n--- Change Summary ---")
    any_change = False
    for provider in providers:
        old_models = {
            m["id"]: m
            for m in old_data.get("providers", {}).get(provider, {}).get("models", [])
        }
        new_models = {
            m["id"]: m
            for m in new_data.get("providers", {}).get(provider, {}).get("models", [])
        }
        added = sorted(set(new_models) - set(old_models))
        removed = sorted(set(old_models) - set(new_models))
        changed = sorted(
            mid for mid in set(old_models) & set(new_models)
            if old_models[mid] != new_models[mid]
        )
        total_old = len(old_models)
        total_new = len(new_models)
        label = provider.capitalize()
        print(f"\n{label}: {total_old} → {total_new} models")
        if added:
            print(f"  + {len(added)} added")
            for mid in added:
                print(f"      {mid}")
        if removed:
            print(f"  - {len(removed)} removed")
            for mid in removed:
                print(f"      {mid}")
        if changed:
            print(f"  ~ {len(changed)} changed")
            for mid in changed:
                diff = dict_diff(old_models[mid], new_models[mid])
                if diff:
                    print(f"      {mid}:")
                    for field, vals in sorted(diff.items()):
                        old_val = vals["old"]
                        new_val = vals["new"]

                        # Format values nicely
                        def fmt(v):
                            if v is None:
                                return "<none>"
                            if isinstance(v, (dict, list)):
                                # keep it short
                                s = json.dumps(v, separators=(',', ':'))
                                if len(s) > 40:
                                    s = s[:37] + "..."
                                return s
                            return str(v)
                        print(f"          {field}: {fmt(old_val)} → {fmt(new_val)}")
                else:
                    # shouldn't happen, but just in case
                    print(f"      {mid} (unknown diff)")
        if not added and not removed and not changed:
            print("  (no changes)")
        else:
            any_change = True
    if not any_change:
        print("No model changes detected.")
    print("---------------------")


def sort_models_by_id(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort models list by ID for consistent ordering."""
    return sorted(models, key=lambda x: x.get("id", "").lower())


def update_provider(
    models_data: dict[str, Any],
    provider_key: str,
    fetch_func,
    convert_func,
    default_config: dict[str, Any]
) -> bool:
    """Update models for a specific provider."""
    print(f"\nFetching {provider_key} models...")
    api_models = fetch_func()
    if not api_models:
        print(f"Failed to fetch {provider_key} models")
        return False

    print(f"Fetched {len(api_models)} {provider_key} models")

    # Convert models
    pi_models = []
    success = 0
    for model in api_models:
        try:
            pi_model = convert_func(model)
            pi_models.append(pi_model)
            success += 1
        except (KeyError, TypeError, ValueError) as e:
            print(f"Error converting {provider_key} model {model.get('id')}: {e}")

    print(f"Successfully converted {success} {provider_key} models")

    # Sort models by ID
    pi_models = sort_models_by_id(pi_models)
    print(f"Sorted {len(pi_models)} models by ID")

    # Update provider configuration
    if "providers" not in models_data:
        models_data["providers"] = {}

    # Preserve existing provider config or create default
    if provider_key not in models_data["providers"]:
        models_data["providers"][provider_key] = default_config
    else:
        # Merge default config with existing (preserving custom fields)
        for key, value in default_config.items():
            if key not in models_data["providers"][provider_key]:
                models_data["providers"][provider_key][key] = value

    # Update models list
    models_data["providers"][provider_key]["models"] = pi_models

    return True


def find_latest_snapshot(data_dir: Path) -> Path | None:
    """Find the most recent data/models_<timestamp>.json snapshot, by filename."""
    candidates = sorted(glob.glob(str(data_dir / "models_*.json")))
    if not candidates:
        return None
    return Path(candidates[-1])


def main() -> None:
    """Fetch latest models and, if changed, write a new timestamped snapshot to data/."""
    data_dir = Path(__file__).resolve().parent / "data"

    # Load newest existing snapshot, if any, as the comparison baseline
    latest_path = find_latest_snapshot(data_dir)
    if latest_path:
        print(f"Comparing against newest snapshot: {latest_path.name}")
        with open(latest_path, 'r') as f:
            old_models_data = json.load(f)
    else:
        print("No existing snapshot found in data/; will write an initial one.")
        old_models_data = {}

    # Start the new snapshot from the old one so untouched providers are preserved
    models_data = copy.deepcopy(old_models_data)
    models_data.pop("_meta", None)

    # OpenRouter configuration
    openrouter_default = {
        "baseUrl": "https://openrouter.ai/api/v1",
        "apiKey": "OPENROUTER_API_KEY",
        "api": "openai-completions",
        "compat": {
            "supportsDeveloperRole": False,
            "supportsReasoningEffort": False,
            "maxTokensField": "max_tokens"
        }
    }

    # Update OpenRouter
    updated = update_provider(
        models_data,
        "openrouter",
        fetch_openrouter_models,
        convert_openrouter_model,
        openrouter_default
    )

    if not updated:
        print("\nNo updates were made (fetch failed).")
        return

    changes = dict_diff(old_models_data.get("providers", {}), models_data.get("providers", {}))
    summarize_changes(old_models_data, models_data, ["openrouter"])

    if not changes:
        print("\nNo semantic change vs. the newest snapshot; not writing a new file.")
        return

    # Add metadata
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    models_data["_meta"] = {
        "updated": timestamp,
        "generator": "update_models.py",
        "note": "Models automatically fetched from APIs and sorted by ID"
    }

    data_dir.mkdir(exist_ok=True)
    suffix = timestamp.replace(":", "").replace("-", "")
    out_path = data_dir / f"models_{suffix}.json"
    with open(out_path, 'w') as f:
        json.dump(models_data, f, indent=2)

    print(f"\nWrote new snapshot to {out_path}")

    # Stats
    openrouter_count = len(models_data.get("providers", {}).get("openrouter", {}).get("models", []))
    print(f"OpenRouter models: {openrouter_count}")


if __name__ == "__main__":
    main()
