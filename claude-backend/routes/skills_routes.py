"""Skills routes blueprint.

Endpoints:
- GET  /api/skills              — list installed skills
- GET  /api/skills/store        — list available skills from GitHub registry
- POST /api/skills/install      — install a skill from store
- DELETE /api/skills/<name>     — uninstall a skill
"""

import logging
import sys
import os
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

skills_bp = Blueprint("skills", __name__)

# Ensure the app root (/app) is in sys.path so `import skills` works
# from within the routes/ subdirectory
_APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP_ROOT not in sys.path:
    sys.path.insert(0, _APP_ROOT)

try:
    import skills as _skills_mod
    _SKILLS_AVAILABLE = True
except ImportError:
    _skills_mod = None
    _SKILLS_AVAILABLE = False
    logger.warning("Skills module not available — skills endpoints will return errors.")


def _no_module():
    return jsonify({"error": "skills_unavailable", "skills": []}), 200


@skills_bp.route("/api/skills", methods=["GET"])
def api_skills_list():
    """List all installed skills."""
    if not _SKILLS_AVAILABLE:
        return jsonify({"success": True, "skills": []}), 200
    try:
        return jsonify({"success": True, "skills": _skills_mod.list_skills()}), 200
    except Exception as e:
        logger.error(f"Skills list error: {e}")
        return jsonify({"error": str(e)}), 500


@skills_bp.route("/api/skills/store", methods=["GET"])
def api_skills_store():
    """Fetch available skills from GitHub registry."""
    if not _SKILLS_AVAILABLE:
        return _no_module()
    try:
        installed = {s["name"] for s in _skills_mod.list_skills()}
        store = _skills_mod.fetch_store_index(installed_names=installed)
        return jsonify({"success": True, "skills": store}), 200
    except Exception as e:
        logger.warning(f"Skills store fetch error: {e}")
        return jsonify({"error": str(e), "skills": []}), 200


@skills_bp.route("/api/skills/install", methods=["POST"])
def api_skills_install():
    """Install a skill from the store."""
    if not _SKILLS_AVAILABLE:
        return _no_module()
    try:
        body = request.get_json(silent=True) or {}
        name = (body.get("name") or "").strip().lower()
        raw_url = (body.get("raw_url") or "").strip()

        if not name:
            return jsonify({"error": "name is required"}), 400
        if not raw_url:
            return jsonify({"error": "raw_url is required"}), 400

        content = _skills_mod.fetch_skill_md(raw_url)
        result = _skills_mod.install_skill(name, content)
        if result.get("success"):
            return jsonify(result), 200
        return jsonify(result), 400
    except Exception as e:
        logger.error(f"Skills install error: {e}")
        return jsonify({"error": str(e)}), 500


@skills_bp.route("/api/skills/<name>", methods=["DELETE"])
def api_skills_delete(name):
    """Uninstall a skill."""
    if not _SKILLS_AVAILABLE:
        return _no_module()
    try:
        deleted = _skills_mod.delete_skill(name)
        if deleted:
            return jsonify({"success": True, "name": name}), 200
        return jsonify({"error": f"Skill '{name}' not found"}), 404
    except Exception as e:
        logger.error(f"Skills delete error: {e}")
        return jsonify({"error": str(e)}), 500
