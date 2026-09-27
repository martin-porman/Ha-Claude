"""File routes blueprint.

Endpoints:
- GET /api/files/list
- GET /api/files/read
- POST /api/files/write
"""

import logging
import os
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

file_bp = Blueprint('files', __name__)


@file_bp.route('/api/files/list', methods=['GET'])
def api_files_list():
    """List files and directories in the HA config dir (or a subdirectory).

    Query param: path (optional) — relative subpath, e.g. 'packages'
    Returns: {path, entries: [{name, type, path, size?}], count}
    """
    import api as _api
    subpath = request.args.get('path', '').strip()
    if '..' in subpath or (subpath and subpath.startswith('/')):
        return jsonify({"error": "Invalid path."}), 400
    dirpath = os.path.join(_api.HA_CONFIG_DIR, subpath) if subpath else _api.HA_CONFIG_DIR
    if not os.path.isdir(dirpath):
        return jsonify({"error": f"Directory '{subpath}' not found."}), 404
    try:
        entries = []
        for entry in sorted(os.listdir(dirpath)):
            if entry.startswith('.'):
                continue
            full = os.path.join(dirpath, entry)
            rel = os.path.join(subpath, entry).replace('\\', '/') if subpath else entry
            if os.path.isdir(full):
                entries.append({"name": entry, "type": "directory", "path": rel})
            else:
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                entries.append({"name": entry, "type": "file", "path": rel, "size": size})
        entries = entries[:100]
        return jsonify({"path": subpath or "/", "entries": entries, "count": len(entries)})
    except Exception as e:
        logger.error(f"api_files_list error: {e}")
        return jsonify({"error": str(e)}), 500


@file_bp.route('/api/files/read', methods=['GET'])
def api_files_read():
    """Read a file from the HA config dir (chunked for large files).

    Query params:
      file   — relative path, e.g. 'packages/lights.yaml'
      offset — char offset to start reading from (default 0)
      chunk  — max chars to return per request (default 0 = whole file)
    Returns: {filename, content, size, offset, chunk_size, has_more}
    """
    import api as _api
    CHUNK_SIZE = 40000  # chars per page (0 = no chunking)
    filename = request.args.get('file', '').strip()
    if not filename:
        return jsonify({"error": "file parameter is required."}), 400
    if '..' in filename or filename.startswith('/'):
        return jsonify({"error": "Invalid filename. Use relative paths only."}), 400
    filepath = os.path.join(_api.HA_CONFIG_DIR, filename)
    if not os.path.isfile(filepath):
        return jsonify({"error": f"File '{filename}' not found."}), 404
    try:
        with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
            full = f.read()
        total = len(full)
        try:
            offset = max(0, int(request.args.get('offset', 0)))
        except (ValueError, TypeError):
            offset = 0
        try:
            chunk = max(0, int(request.args.get('chunk', 0)))
        except (ValueError, TypeError):
            chunk = 0
        if chunk > 0:
            content = full[offset:offset + chunk]
            has_more = (offset + chunk) < total
        else:
            content = full[offset:]
            has_more = False
        return jsonify({
            "filename": filename,
            "content": content,
            "size": total,
            "offset": offset,
            "chunk_size": len(content),
            "has_more": has_more,
        })
    except Exception as e:
        logger.error(f"api_files_read error: {e}")
        return jsonify({"error": str(e)}), 500


@file_bp.route('/api/files/write', methods=['POST'])
def api_files_write():
    """Write content to a file in the HA config dir.

    JSON body: {file, content, force (optional bool)}
    Returns: {ok, filename, size} or {error, truncation_warning, original_size, new_size}

    Safety: if new content is less than 60% of the original file size, the write
    is rejected unless force=true is passed explicitly.
    """
    import api as _api
    data = request.get_json(silent=True) or {}
    filename = (data.get('file') or '').strip()
    if not filename:
        return jsonify({"error": "file parameter is required."}), 400
    if '..' in filename or filename.startswith('/'):
        return jsonify({"error": "Invalid filename. Use relative paths only."}), 400
    content = data.get('content', '')
    if not isinstance(content, str):
        return jsonify({"error": "content must be a string."}), 400
    force = bool(data.get('force', False))
    filepath = os.path.join(_api.HA_CONFIG_DIR, filename)
    if not os.path.isfile(filepath):
        return jsonify({"error": f"File '{filename}' not found."}), 404
    try:
        original_size = os.path.getsize(filepath)
        new_size = len(content.encode('utf-8'))
        # Anti-truncation guard: reject if new content is less than 60% of original
        if not force and original_size > 200 and new_size < original_size * 0.6:
            logger.warning(
                f"api_files_write: truncation guard triggered for {filename} "
                f"(original={original_size}B, new={new_size}B)"
            )
            return jsonify({
                "error": "Write rejected: new content is significantly shorter than the original file. "
                         "This may indicate truncated content. Pass force=true to override.",
                "truncation_warning": True,
                "original_size": original_size,
                "new_size": new_size,
            }), 409
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        logger.info(f"api_files_write: saved {filename} ({new_size}B, was {original_size}B)")
        return jsonify({"ok": True, "filename": filename, "size": new_size})
    except Exception as e:
        logger.error(f"api_files_write error: {e}")
        return jsonify({"error": str(e)}), 500
