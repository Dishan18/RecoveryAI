#!/usr/bin/env python3
"""
RecoveryAI — standalone dev startup helper.
Run: python devstart.py
Starts backend (uvicorn) and frontend (npm run dev) concurrently.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent


def main():
    procs = []
    try:
        print("🚀  Starting RecoveryAI backend (port 8000)…")
        backend = subprocess.Popen(
            [
                str(ROOT / ".venv" / "Scripts" / "uvicorn"),
                "main:app",
                "--reload",
                "--port", "8000",
            ],
            cwd=ROOT / "backend",
        )
        procs.append(backend)

        print("🌐  Starting RecoveryAI frontend (port 3000)…")
        frontend = subprocess.Popen(
            ["npm", "run", "dev"],
            cwd=ROOT / "frontend",
            shell=True,
        )
        procs.append(frontend)

        print("\n✅  Both services are starting up.")
        print("    Backend  → http://localhost:8000")
        print("    Frontend → http://localhost:3000")
        print("    API Docs → http://localhost:8000/docs")
        print("\nPress Ctrl+C to stop.\n")

        for p in procs:
            p.wait()
    except KeyboardInterrupt:
        print("\n🛑  Shutting down…")
        for p in procs:
            p.terminate()
        sys.exit(0)


if __name__ == "__main__":
    main()
