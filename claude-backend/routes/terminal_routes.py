"""Claude terminal page (ingress) + image paste/drop upload.

- GET  /term                  → page embedding the ttyd terminal (served by
                                 nginx at ./ttyd/) with paste/drag-drop images
- POST /api/terminal/upload   → save image to /data/terminal-images and type
                                 its path into the tmux "claude" session

Auth: covered by the global _auth_guard (HA ingress, or X-Amira-Token).
"""

import os
import re
import subprocess
import time

from flask import Blueprint, Response, jsonify, request

terminal_bp = Blueprint("terminal", __name__)

UPLOAD_DIR = "/data/terminal-images"
MAX_BYTES = 20 * 1024 * 1024
KEEP_SECONDS = 14 * 24 * 3600
_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}


def _prune() -> None:
    cutoff = time.time() - KEEP_SECONDS
    try:
        for name in os.listdir(UPLOAD_DIR):
            p = os.path.join(UPLOAD_DIR, name)
            if os.path.isfile(p) and os.path.getmtime(p) < cutoff:
                os.remove(p)
    except OSError:
        pass


@terminal_bp.route("/api/terminal/upload", methods=["POST"])
def api_terminal_upload():
    f = request.files.get("image")
    if f is None:
        return jsonify({"success": False, "error": "no image"}), 400
    ext = _EXT.get((f.mimetype or "").lower())
    if not ext:
        return jsonify({"success": False, "error": f"unsupported type {f.mimetype}"}), 415
    data = f.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return jsonify({"success": False, "error": "image larger than 20 MB"}), 413
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    _prune()
    path = os.path.join(UPLOAD_DIR, f"pasted-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(3).hex()}{ext}")
    with open(path, "wb") as out:
        out.write(data)
    typed = False
    if request.form.get("type_path", "1") == "1":
        # -l = literal text; path is generated above so it holds no shell/tmux syntax
        r = subprocess.run(["tmux", "send-keys", "-t", "claude", "-l", path + " "],
                           capture_output=True, timeout=5)
        typed = r.returncode == 0
    return jsonify({"success": True, "path": path, "typed": typed, "size": len(data)})


_PAGE = r"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Amira terminal</title>
<style>
 html,body{margin:0;height:100%;background:#1e1e1e;color:#ddd;font:13px system-ui,sans-serif}
 #bar{height:30px;display:flex;align-items:center;gap:10px;padding:0 10px;background:#2b2b2b;border-bottom:1px solid #444}
 #bar a,#bar button{color:#ddd;background:#3a3a3a;border:1px solid #555;border-radius:4px;padding:3px 9px;text-decoration:none;cursor:pointer;font:inherit}
 #status{opacity:.8;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
 #term{width:100%;height:calc(100% - 31px);border:0;display:block}
 #drop{position:fixed;inset:0;display:none;align-items:center;justify-content:center;background:rgba(30,120,255,.25);font-size:22px;pointer-events:none}
 body.dragging #drop{display:flex}
</style></head><body>
<div id="bar">
 <a href="./" target="_self">&larr; Chat</a>
 <button id="pick">Image&hellip;</button><input id="file" type="file" accept="image/*" hidden>
 <a href="./ttyd/" target="_blank">Pop out</a>
 <span id="status">Paste (Ctrl+V) or drop an image: it is uploaded and its path typed into Claude.</span>
</div>
<iframe id="term" src="./ttyd/"></iframe>
<div id="drop">Drop image to send to Claude</div>
<script>
const st=document.getElementById('status'), frame=document.getElementById('term');
function say(t){st.textContent=t;}
async function send(file){
  if(!file||!file.type.startsWith('image/')) return false;
  say('Uploading '+(file.name||'image')+' ...');
  const fd=new FormData(); fd.append('image',file,file.name||'pasted.png');
  try{
    const r=await fetch('./api/terminal/upload',{method:'POST',body:fd,credentials:'same-origin'});
    const j=await r.json();
    if(!j.success) throw new Error(j.error||r.status);
    say((j.typed?'Typed into Claude: ':'Saved (not typed): ')+j.path);
  }catch(e){say('Upload failed: '+e.message);}
  try{frame.contentWindow.focus();}catch(e){}
  return true;
}
function onPaste(e){
  const items=(e.clipboardData||{}).items||[];
  for(const it of items){ if(it.kind==='file'&&it.type.startsWith('image/')){
    e.preventDefault(); e.stopImmediatePropagation(); send(it.getAsFile()); return; } }
}
document.addEventListener('paste',onPaste,true);
// Same-origin iframe: catch image pastes while the terminal has focus too.
frame.addEventListener('load',()=>{ try{ frame.contentWindow.document.addEventListener('paste',onPaste,true);}catch(e){} });
let dc=0;
addEventListener('dragenter',e=>{e.preventDefault();dc++;document.body.classList.add('dragging');});
addEventListener('dragleave',e=>{if(--dc<=0){dc=0;document.body.classList.remove('dragging');}});
addEventListener('dragover',e=>e.preventDefault());
addEventListener('drop',e=>{e.preventDefault();dc=0;document.body.classList.remove('dragging');
  for(const f of e.dataTransfer.files) send(f);});
document.getElementById('pick').onclick=()=>document.getElementById('file').click();
document.getElementById('file').onchange=e=>{for(const f of e.target.files) send(f); e.target.value='';};
</script></body></html>"""


@terminal_bp.route("/term", methods=["GET"])
def terminal_page():
    return Response(_PAGE, mimetype="text/html")
