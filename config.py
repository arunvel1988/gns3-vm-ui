# config.py
import os

class Config:
    SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "supersecretkey")
    ADMIN_USERNAME = os.environ.get("LAB_ADMIN_USER", "admin")
    ADMIN_PASSWORD = os.environ.get("LAB_ADMIN_PASS", "admin")
    LOG_DIR = "/var/log/gns3lab"
    PROJECTS_BASE = "/opt/gns3/projects"
    STUDENT_COUNT = 60
    STUDENT_PREFIX = "student"
