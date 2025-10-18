import os, subprocess, threading, queue, uuid, datetime
from flask import Flask, render_template, request, session, redirect, url_for, jsonify, Response, flash
from functools import wraps
from config import Config
from logging.handlers import RotatingFileHandler
import logging

app = Flask(__name__, static_folder="static", template_folder="templates")
app.config.from_object(Config)

# --- Logging setup ---
os.makedirs(Config.LOG_DIR, exist_ok=True)
log_file = os.path.join(Config.LOG_DIR, "app.log")
handler = RotatingFileHandler(log_file, maxBytes=5*1024*1024, backupCount=5)
handler.setLevel(logging.INFO)
app.logger.addHandler(handler)

# --- Auth decorator ---
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated

# --- Task management ---
tasks = {}

def enqueue(q, msg):
    q.put(msg + "\n")

def run_commands(task_id, commands, dry_run=False):
    q = tasks[task_id]
    for cmd in commands:
        enqueue(q, f"$ {cmd}")
        if dry_run:
            enqueue(q, "[DRY-RUN] Skipping execution.")
            continue
        proc = subprocess.Popen(["bash", "-lc", cmd],
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT,
                                text=True)
        for line in proc.stdout:
            enqueue(q, line.rstrip())
        proc.wait()
        if proc.returncode != 0:
            enqueue(q, f"[WARN] Command exited with {proc.returncode}")
    enqueue(q, "[DONE] All commands completed.")
    enqueue(q, "__FINISHED__")

# --- Routes ---
@app.route("/")
@login_required
def index():
    return render_template("index.html", server_ip=request.host.split(":")[0])

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = request.form["username"]
        pw = request.form["password"]
        if user == Config.ADMIN_USERNAME and pw == Config.ADMIN_PASSWORD:
            session["user"] = user
            return redirect(url_for("index"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))
######################################################################

@app.route("/start_install", methods=["POST"])
@login_required
def start_install():
    """
    Smart GNS3 starter (minimal):
    - Installs GNS3 server and GUI using Python virtual environment
    - Starts GNS3 server if already installed
    """
    data = request.json
    kind = data.get("kind", "server")
    dry_run = data.get("dry", False)
    task_id = str(uuid.uuid4())
    tasks[task_id] = queue.Queue()
    cmds = []

    gns3_venv_path = "$HOME/gns3-venv"

    if kind == "server":
        # Check & start GNS3 server using virtualenv
        cmds.append(
            f"if [ -x {gns3_venv_path}/bin/gns3server ]; then "
            f"echo '[INFO] GNS3 server already installed in venv. Starting...'; "
            f"nohup {gns3_venv_path}/bin/gns3server --host 0.0.0.0 --port 3080 >/dev/null 2>&1 & "
            "else "
            f"echo '[INFO] GNS3 server not found. Installing in Python venv...' && "
            f"sudo apt update -y && sudo apt install -y python3-pip python3-venv python3-setuptools && "
            f"python3 -m venv {gns3_venv_path} && source {gns3_venv_path}/bin/activate && "
            f"pip install --upgrade pip && pip install gns3-server gns3-gui && "
            f"mkdir -p /opt/gns3/projects && chown -R $(whoami):$(whoami) /opt/gns3/projects && "
            f"nohup {gns3_venv_path}/bin/gns3server --host 0.0.0.0 --port 3080 >/dev/null 2>&1 & "
            "fi"
        )
        # Show versions
        cmds.append(f"{gns3_venv_path}/bin/gns3server --version || echo '[INFO] GNS3 server not available'")
        cmds.append(f"{gns3_venv_path}/bin/gns3 --version || echo '[INFO] GNS3 GUI not available'")

    elif kind == "gui":
        # Start GUI if installed in venv
        cmds.append(
            f"if [ -x {gns3_venv_path}/bin/gns3 ]; then "
            f"echo '[INFO] GNS3 GUI already installed in venv. Starting...'; "
            f"{gns3_venv_path}/bin/gns3 & "
            "else "
            f"echo '[INFO] GNS3 GUI not found. Installing in Python venv...' && "
            f"sudo apt update -y && sudo apt install -y python3-pip python3-venv python3-setuptools && "
            f"python3 -m venv {gns3_venv_path} && source {gns3_venv_path}/bin/activate && "
            f"pip install --upgrade pip && pip install gns3-gui && "
            f"{gns3_venv_path}/bin/gns3 & "
            "fi"
        )

    threading.Thread(target=run_commands, args=(task_id, cmds, dry_run), daemon=True).start()
    return jsonify({"task_id": task_id, "stream_url": url_for("stream", task_id=task_id)})


#############################################################################################

@app.route("/stream/<task_id>")
@login_required
def stream(task_id):
    if task_id not in tasks:
        return "Invalid task", 404
    def event_stream():
        q = tasks[task_id]
        while True:
            line = q.get()
            if line == "__FINISHED__":
                yield f"data:{line}\n\n"
                break
            yield f"data:{line}\n\n"
    return Response(event_stream(), mimetype="text/event-stream")

@app.route("/provision", methods=["POST"])
@login_required
def provision():
    base = Config.PROJECTS_BASE
    prefix = Config.STUDENT_PREFIX
    count = Config.STUDENT_COUNT
    os.makedirs(base, exist_ok=True)
    for i in range(1, count+1):
        p = os.path.join(base, f"{prefix}{i:02d}")
        os.makedirs(p, exist_ok=True)
    return jsonify({"msg": f"{count} student project folders created at {base}."})

@app.route("/dryrun", methods=["POST"])
@login_required
def dryrun():
    """Just return commands as text without running them."""
    kind = request.json.get("kind", "server")
    cmds = ["apt update", "add-apt-repository ppa:gns3/ppa", "apt install gns3-server gns3-gui"] if kind=="server" else ["apt install gns3-gui"]
    return jsonify({"commands": cmds})

# Error handlers
@app.errorhandler(500)
def internal_error(e):
    app.logger.error(f"Error: {e}")
    return "Internal error", 500

if __name__ == "__main__":
    app.secret_key = Config.SECRET_KEY
    app.run(host="0.0.0.0", port=5000)
