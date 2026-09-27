"""Model name and provider utilities."""

import re
import api  # Import api module to access global variables


def normalize_model_name(model_name: str) -> str:
    """Convert user-friendly model name to technical name.
    Handles legacy names with emoji badges (🆓, 🧪) for backward compatibility."""
    raw = str(model_name or "").strip()
    if not raw:
        return raw

    def _strip_badges(name: str) -> str:
        return re.sub(r'[\s]*[🆓🧪]+[\s]*$', '', name).strip()

    candidate = _strip_badges(raw)

    # Direct lookup
    if candidate in api.MODEL_NAME_MAPPING:
        return api.MODEL_NAME_MAPPING[candidate]

    # Reverse lookup from provider display maps (supports dynamic labels such as:
    # "NVIDIA: Llama 3.3 70B Instruct" -> "meta/llama-3.3-70b-instruct").
    # This prevents sending display labels directly to provider APIs.
    try:
        for _prov_map in (api.PROVIDER_DISPLAY or {}).values():
            if not isinstance(_prov_map, dict):
                continue
            for tech_name, display_name in _prov_map.items():
                if candidate == str(display_name).strip():
                    return str(tech_name)
    except Exception:
        pass

    # Loose fallback: "Provider: Label" exact text matching (case-insensitive).
    # Useful when display maps are stale but MODEL_DISPLAY_MAPPING contains it.
    try:
        c_low = candidate.lower()
        for tech_name, display_name in (api.MODEL_DISPLAY_MAPPING or {}).items():
            if c_low == str(display_name).strip().lower():
                return str(tech_name)
    except Exception:
        pass

    # Not found, return as-is (assume it's already a technical name)
    return candidate


def get_model_provider(model_name: str) -> str:
    """Get the provider prefix from a model name."""
    if model_name.startswith("Claude:"):
        return "anthropic"
    elif model_name.startswith("OpenAI:"):
        return "openai"
    elif model_name.startswith("Google:"):
        return "google"
    elif model_name.startswith("NVIDIA:"):
        return "nvidia"
    elif model_name.startswith("GitHub:"):
        return "github"
    # Try to infer from technical name
    tech_name = normalize_model_name(model_name)
    # GitHub Models uses fully-qualified IDs with a 'vendor/' prefix (openai/, meta/, mistral-ai/, etc.)
    # All of these belong to GitHub — not to the individual vendor's direct API.
    _GITHUB_VENDOR_PREFIXES = (
        "openai/", "meta/", "mistral-ai/", "mistralai/", "microsoft/",
        "deepseek/", "cohere/", "ai21-labs/", "xai/",
    )
    if any(tech_name.startswith(p) for p in _GITHUB_VENDOR_PREFIXES):
        return "github"
    if tech_name.startswith("claude-"):
        return "anthropic"
    elif tech_name in ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1", "o3-mini"]:
        return "openai"
    elif tech_name.startswith("gemini-"):
        return "google"
    elif tech_name.startswith(("moonshotai/", "nvidia/")):
        return "nvidia"
    return "unknown"


def validate_model_provider_compatibility() -> tuple[bool, str]:
    """Validate that the selected model is compatible with the selected provider."""
    if not api.AI_MODEL:
        return True, ""  # No model selected, use default

    # Solo provider "stretti" hanno modelli esclusivi — skip check per tutti gli altri
    _STRICT_PROVIDERS = {"anthropic", "openai", "google"}
    if api.AI_PROVIDER not in _STRICT_PROVIDERS:
        return True, ""

    model_provider = get_model_provider(api.AI_MODEL)
    if model_provider == "unknown":
        return True, ""  # Can't determine, allow it

    if model_provider != api.AI_PROVIDER:
        # Multilingual warning messages
        from core.translations import LANGUAGE
        warnings = {
            "en": f"⚠️ WARNING: You selected model '{api.AI_MODEL}' which is not compatible with provider '{api.AI_PROVIDER}'. Change provider or model.",
            "it": f"⚠️ ATTENZIONE: Hai selezionato un modello '{api.AI_MODEL}' che non è compatibile con il provider '{api.AI_PROVIDER}'. Cambia provider o modello.",
            "es": f"⚠️ ADVERTENCIA: Has seleccionado el modelo '{api.AI_MODEL}' que no es compatible con el proveedor '{api.AI_PROVIDER}'. Cambia proveedor o modelo.",
            "fr": f"⚠️ ATTENTION: Vous avez sélectionné le modèle '{api.AI_MODEL}' qui n'est pas compatible avec le fournisseur '{api.AI_PROVIDER}'. Changez de fournisseur ou de modèle."
        }
        error_msg = warnings.get(LANGUAGE, warnings["en"])
        return False, error_msg

    return True, ""


def get_active_model() -> str:
    """Get the active model name (technical format).
    Prefers the user's selected model/provider if set, else falls back to global AI_MODEL."""
    # Solo Anthropic, OpenAI e Google hanno modelli esclusivi — per tutti gli altri provider
    # (gateway multi-vendor: NVIDIA, GitHub, OpenRouter, Groq, ecc.) accettiamo qualsiasi modello.
    _STRICT_PROVIDERS = {"anthropic", "openai", "google"}

    def _model_ok(model: str) -> bool:
        """Return True if model is compatible with current provider (or check is N/A)."""
        if api.AI_PROVIDER not in _STRICT_PROVIDERS:
            return True  # gateway provider: never reject
        mp = get_model_provider(model)
        return mp in (api.AI_PROVIDER, "unknown")

    # Use SELECTED_MODEL if the user has made a selection AND provider matches
    if api.SELECTED_MODEL and api.SELECTED_PROVIDER == api.AI_PROVIDER:
        model = normalize_model_name(api.SELECTED_MODEL)
        if _model_ok(model):
            if api.AI_PROVIDER == "openai" and model.startswith("openai/"):
                return model.split("/", 1)[1]
            return model

    # Fall back to AI_MODEL (from config/env)
    if api.AI_MODEL:
        model = normalize_model_name(api.AI_MODEL)
        if _model_ok(model):
            if api.AI_PROVIDER == "openai" and model.startswith("openai/"):
                return model.split("/", 1)[1]
            return model

    # Custom provider: fall back to configured model name
    if api.AI_PROVIDER == "custom" and api.CUSTOM_MODEL_NAME:
        return api.CUSTOM_MODEL_NAME

    # Last resort: use provider default
    return api.PROVIDER_DEFAULTS.get(api.AI_PROVIDER, {}).get("model", "unknown")
