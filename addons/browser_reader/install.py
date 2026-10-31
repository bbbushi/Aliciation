"""Install the optional browser reader into its own environment, never Alice's."""
from pathlib import Path
import json
import subprocess
import venv


def main():
    root = Path(__file__).resolve().parent
    env = root / ".venv"
    venv.EnvBuilder(with_pip=True).create(env)
    python = env / "Scripts" / "python.exe"
    subprocess.run([str(python), "-m", "pip", "install", "--index-url", "https://pypi.org/simple",
                    "-r", str(root / "requirements.txt")], check=True)
    version = subprocess.check_output([str(python), "-c",
        "from importlib.metadata import version; print(version('playwright'))"], text=True).strip()
    (env / "alice-runtime.json").write_text(json.dumps({"version": 1, "playwright": version}), encoding="utf-8")
    print("Browser reader installed. Uses installed Microsoft Edge; no browser download.")


if __name__ == "__main__":
    main()
