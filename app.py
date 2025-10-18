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

@app.route("/start_install", methods=["POST"])
@login_required
def start_install():
    data = request.json
    kind = data.get("kind", "server")
    dry_run = data.get("dry", False)
    task_id = str(uuid.uuid4())
    tasks[task_id] = queue.Queue()

    server_cmds = [
        "sudo apt update -y",
        "sudo apt install -y software-properties-common apt-transport-https curl gnupg lsb-release",
        "sudo add-apt-repository -y ppa:wireshark-dev/stable",
        "sudo apt update -y && apt install -y wireshark",
        "sudo usermod -a -G wireshark $(whoami)",
        "sudo chgrp wireshark /usr/bin/dumpcap && chmod 750 /usr/bin/dumpcap",
        "sudo setcap cap_net_raw,cap_net_admin=eip /usr/bin/dumpcap",
        "sudo add-apt-repository -y ppa:gns3/ppa",
        "sudo apt update -y && apt install -y gns3-server gns3-gui",
        "sudo dpkg --add-architecture i386 && apt update -y",
        "sudo curl -fsSL https://get.docker.com -o /tmp/get-docker.sh && sh /tmp/get-docker.sh",
        "sudo apt install -y ubridge qemu-kvm qemu-utils libvirt-daemon-system libvirt-clients virtinst bridge-utils gns3-iou",
        "for g in ubridge libvirt kvm wireshark docker; do usermod -aG $g $(whoami); done",
        "mkdir -p /opt/gns3/projects && chown -R $(whoami):$(whoami) /opt/gns3",
   
        "gns3 --version && docker --version"
    ]

    gui_cmds = ["sudo apt update -y && sudo apt install -y gns3-gui"]

    cmds = server_cmds if kind == "server" else gui_cmds
    threading.Thread(target=run_commands, args=(task_id, cmds, dry_run), daemon=True).start()
    return jsonify({"task_id": task_id, "stream_url": url_for("stream", task_id=task_id)})

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
