"""
Railway start script.
Reads PORT env var (Railway injects this) and starts gunicorn.
"""
import os
import subprocess

port = os.environ.get("PORT", "8080")
print(f"[start.py] Starting gunicorn on port {port}")

subprocess.run([
    "gunicorn",
    "app:app",
    "--bind", f"0.0.0.0:{port}",
])
