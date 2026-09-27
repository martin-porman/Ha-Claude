"""Dashboard routes blueprint.

Endpoints:
- GET /dashboard_api/states
- GET /dashboard_api/history
- POST /dashboard_api/services/<domain>/<service>
- GET /custom_dashboards/<name>
- GET /api/dashboard_html/<name>
- GET /custom_dashboards
"""

import logging
import os
import re
from datetime import datetime, timedelta

import requests
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__)


@dashboard_bp.route('/dashboard_api/states')
def dashboard_api_states():
    """Proxy GET /api/states using server-side SUPERVISOR_TOKEN."""
    import api
    try:
        resp = requests.get(
            f"{api.HA_URL}/api/states",
            headers=api.get_ha_headers(),
            timeout=30
        )
        return resp.json(), resp.status_code, {"Content-Type": "application/json"}
    except Exception as e:
        logger.error(f"Dashboard API proxy /states error: {e}")
        return jsonify({"error": str(e)}), 502


@dashboard_bp.route('/dashboard_api/history')
def dashboard_api_history():
    """Proxy GET /api/history/period using server-side SUPERVISOR_TOKEN."""
    import api
    try:
        entity_ids = request.args.get('entity_ids', '')
        hours = min(int(request.args.get('hours', 24)), 168)

        if not entity_ids:
            return jsonify({"error": "entity_ids parameter required"}), 400

        for eid in entity_ids.split(','):
            if not re.match(r'^[a-z_]+\.[a-z0-9_]+$', eid.strip()):
                return jsonify({"error": f"Invalid entity_id: {eid}"}), 400

        end_time = datetime.utcnow()
        start_time = end_time - timedelta(hours=hours)

        url = (f"{api.HA_URL}/api/history/period/{start_time.isoformat()}Z"
               f"?filter_entity_id={entity_ids}"
               f"&end_time={end_time.isoformat()}Z"
               f"&minimal_response&no_attributes")

        resp = requests.get(url, headers=api.get_ha_headers(), timeout=30)
        return resp.json(), resp.status_code, {"Content-Type": "application/json"}
    except Exception as e:
        logger.error(f"Dashboard API proxy /history error: {e}")
        return jsonify({"error": str(e)}), 502


@dashboard_bp.route('/dashboard_api/services/<domain>/<service>', methods=['POST'])
def dashboard_api_services(domain, service):
    """Proxy POST /api/services/<domain>/<service> using server-side SUPERVISOR_TOKEN."""
    import api
    try:
        if not re.match(r'^[a-z_]+$', domain) or not re.match(r'^[a-z_]+$', service):
            return jsonify({"error": "Invalid domain or service name"}), 400

        data = request.get_json(silent=True) or {}
        resp = requests.post(
            f"{api.HA_URL}/api/services/{domain}/{service}",
            headers=api.get_ha_headers(),
            json=data,
            timeout=30
        )
        return resp.json(), resp.status_code, {"Content-Type": "application/json"}
    except Exception as e:
        logger.error(f"Dashboard API proxy /services/{domain}/{service} error: {e}")
        return jsonify({"error": str(e)}), 502


@dashboard_bp.route('/custom_dashboards/<name>')
def custom_dashboards(name):
    """Serve custom HTML dashboards (legacy route, kept for backward compat)."""
    import api
    try:
        safe_name = name.lower().replace(" ", "-").replace("_", "-").replace(".", "-")
        if not safe_name.endswith(".html"):
            safe_name += ".html"

        if not all(c.isalnum() or c in '-.' for c in safe_name):
            return jsonify({"error": "Invalid dashboard name"}), 400

        dashboard_path = os.path.join(api.HA_CONFIG_DIR, "www", "dashboards", safe_name)

        if not os.path.isfile(dashboard_path):
            return jsonify({"error": f"Dashboard '{name}' not found"}), 404

        with open(dashboard_path, "r", encoding="utf-8") as f:
            html_content = f.read()

        logger.info(f"Serving custom dashboard: {safe_name}")
        return html_content, 200, {"Content-Type": "text/html; charset=utf-8"}

    except Exception as e:
        logger.error(f"Error serving dashboard: {e}")
        return jsonify({"error": f"Failed to serve dashboard: {str(e)}"}), 500


@dashboard_bp.route('/api/dashboard_html/<name>')
def api_dashboard_html(name):
    """Return HTML dashboard content as JSON (for bubble context / editing)."""
    import api
    try:
        safe_name = name.lower().replace(" ", "-").replace("_", "-").replace(".", "-")
        if not safe_name.endswith(".html"):
            safe_name += ".html"
        for subdir in [os.path.join("www", "dashboards"), ".html_dashboards"]:
            path = os.path.join(api.HA_CONFIG_DIR, subdir, safe_name)
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    html = f.read()
                return jsonify({"name": name, "html": html, "size": len(html)}), 200
        return jsonify({"error": f"Dashboard '{name}' not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dashboard_bp.route('/custom_dashboards')
def custom_dashboards_list():
    """List all available custom HTML dashboards."""
    import api
    try:
        dashboards = []
        dashboards_dir = os.path.join(api.HA_CONFIG_DIR, "www", "dashboards")
        if os.path.isdir(dashboards_dir):
            for filename in os.listdir(dashboards_dir):
                if filename.endswith(".html"):
                    file_path = os.path.join(dashboards_dir, filename)
                    dash_name = filename.replace(".html", "")
                    dashboards.append({
                        "name": dash_name,
                        "filename": filename,
                        "url": f"/local/dashboards/{filename}",
                        "size": os.path.getsize(file_path),
                        "modified": datetime.fromtimestamp(os.path.getmtime(file_path)).isoformat()
                    })

        logger.info(f"Listed {len(dashboards)} custom dashboards")
        return jsonify({"dashboards": dashboards, "count": len(dashboards)}), 200

    except Exception as e:
        logger.error(f"Error listing dashboards: {e}")
        return jsonify({"error": str(e)}), 500
