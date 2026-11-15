# Build-only analysis; runtime enters Alice exclusively through its public CLI.
import json
from importlib.metadata import distribution
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

packaging = Path(SPECPATH)
root = packaging.parents[2]
staging = packaging.parent / "build" / "staging"
inventory = json.loads((staging / "build-info.json").read_text())
datas = [(str(root / item["source"]), str(Path(item["target"]).parent))
         for item in inventory["resources"]]
datas += [(str(staging / "build-info.json"), ".")]
datas += copy_metadata("openai", recursive=True)
sdk_modules = sorted({str(file)[:-3].replace("/", ".").removesuffix(".__init__")
                      for file in distribution("openai").files
                      if file.parts[0] == "openai" and file.suffix == ".py"})

analysis = Analysis(
    [str(packaging / "backend_entry.py")], pathex=[str(root)],
    binaries=[], datas=datas,
    hiddenimports=collect_submodules("alice") + sdk_modules,
    hookspath=[], runtime_hooks=[],
    excludes=["matplotlib", "numpy", "PIL", "tkinter", "pytest", "IPython"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz, analysis.scripts, [], exclude_binaries=True, name="alice-backend",
    console=True, debug=False, strip=False, upx=False,
    target_arch="arm64", codesign_identity=None, entitlements_file=None,
)
collection = COLLECT(exe, analysis.binaries, analysis.datas,
                     strip=False, upx=False, name="alice-backend")
