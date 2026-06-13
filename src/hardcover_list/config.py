import json
import os

from calibre.utils.config import JSONConfig
from calibre_plugins.hardcover_list.hcl_graphql.client import (
    DEFAULT_REQUESTS_PER_MINUTE,
    get_configured_requests_per_minute,
)

PLUGIN_PREFS = JSONConfig("plugins/Hardcover Lists.json")
METADATA_PREFS = JSONConfig("metadata_sources/Hardcover.json")

PLUGIN_PREFS.defaults["api_key"] = ""
PLUGIN_PREFS.defaults["requests_per_minute"] = DEFAULT_REQUESTS_PER_MINUTE


def get_api_key() -> str:
    key = str(PLUGIN_PREFS.get("api_key", "") or "").strip()
    if key:
        return key
    return str(METADATA_PREFS.get("api_key", "") or "").strip()


def get_requests_per_minute() -> int:
    value = PLUGIN_PREFS.get("requests_per_minute", DEFAULT_REQUESTS_PER_MINUTE)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_REQUESTS_PER_MINUTE


def _rate_config_path() -> str:
    base = os.environ.get("CALIBRE_CONFIG_DIRECTORY")
    if not base:
        for candidate in (
            os.path.join(os.path.expanduser("~"), "Library", "Preferences", "calibre"),
            os.path.join(os.path.expanduser("~"), ".config", "calibre"),
        ):
            if os.path.isdir(candidate):
                base = candidate
                break
        else:
            base = os.path.join(
                os.path.expanduser("~"), "Library", "Preferences", "calibre"
            )
    return os.path.join(base, "hardcover_api.json")


def sync_rate_limit_config() -> None:
    """Publish the Lists plugin rate limit for all Hardcover API clients."""
    rpm = get_requests_per_minute()
    path = _rate_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"requests_per_minute": rpm}, handle)


def ensure_plugin_prefs() -> None:
    """Ensure Lists prefs exist, reusing the metadata plugin API key when unset."""
    api_key = str(PLUGIN_PREFS.get("api_key", "") or "").strip()
    if not api_key:
        metadata_key = str(METADATA_PREFS.get("api_key", "") or "").strip()
        if metadata_key:
            PLUGIN_PREFS["api_key"] = metadata_key

    if get_configured_requests_per_minute() != get_requests_per_minute():
        sync_rate_limit_config()
