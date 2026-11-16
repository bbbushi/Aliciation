"""Prepare reviewable GitHub Release assets; never uploads or modifies an app.

Requires a matching private key outside the checkout. Existing signed app and
build report remain immutable. Only trusted, locally built bundles are accepted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile

DESKTOP = Path(__file__).resolve().parents[1]
KEYDIR = Path.home() / "Library/Application Support/Alice Release Keys"


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key", type=Path, default=KEYDIR / "updater.key")
    parser.add_argument("--public", type=Path, default=KEYDIR / "updater.pub")
    parser.add_argument("--notes", type=Path, required=True)
    args = parser.parse_args()
    latest = json.loads((DESKTOP / "build/latest-release.json").read_text())
    app = Path(latest["app"])
    report = json.loads(Path(latest["report"]).read_text())
    public = (DESKTOP / "packaging/updater.pub").read_text().strip()
    if args.public.read_text().strip() != public:
        raise ValueError("Signing public key differs from the key embedded in Alice")
    if report["updater_pubkey_sha256"] != hashlib.sha256(public.encode()).hexdigest():
        raise ValueError("Bundle update key differs from current source configuration")
    if args.key.is_symlink() or not args.key.is_file() or args.key.stat().st_mode & 0o077:
        raise ValueError("Private key must be a private ordinary file outside the checkout")
    if args.key.resolve().is_relative_to(DESKTOP.parents[1]):
        raise ValueError("Keep the private key outside the checkout")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)], check=True)
    folder = app.parent / "github-release"
    folder.mkdir(mode=0o700, exist_ok=False)
    version = report["version"]
    archive = folder / f"Alice-{version}-darwin-aarch64.app.tar.gz"
    # Add files explicitly so hard-linked staging copies cannot produce tar links.
    with tarfile.open(archive, "w:gz", dereference=False) as tar:
        for path in [app, *sorted(app.rglob("*"))]:
            name = Path("Alice.app") / path.relative_to(app)
            info = tar.gettarinfo(str(path), name.as_posix())
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            if path.is_symlink():
                if not path.resolve().is_relative_to(app.resolve()):
                    raise ValueError("Application symlink escapes bundle")
                tar.addfile(info)
            elif path.is_file():
                info.type = tarfile.REGTYPE
                info.linkname = ""
                info.size = path.stat().st_size
                with path.open("rb") as stream:
                    tar.addfile(info, stream)
            elif path.is_dir():
                tar.addfile(info)
            else:
                raise ValueError("Unsupported bundle file type")
    signature = archive.with_name(archive.name + ".sig")
    signer = DESKTOP / "src-tauri/target/release/alice-update-signer"
    subprocess.run([str(signer), "sign", str(args.key), str(args.public), str(archive), str(signature), version], check=True)
    subprocess.run([str(signer), "verify", str(args.public), str(archive), str(signature)], check=True)
    notes = args.notes.read_text(encoding="utf-8")
    manifest = {"version": version, "notes": notes, "pub_date": datetime.now(timezone.utc).isoformat(),
                "platforms": {"darwin-aarch64": {"signature": signature.read_text().strip(),
                    "url": f"https://github.com/bbbushi/Aliciation/releases/download/v{version}/{archive.name}"}}}
    payload = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    if len(payload.encode()) > 64 * 1024 or archive.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("Release assets exceed the client's size limits")
    (folder / "latest.json").write_text(payload, encoding="utf-8")
    (folder / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    dmg = Path(latest["dmg"])
    os.link(dmg, folder / dmg.name)
    files = [archive, signature, folder / "latest.json", folder / dmg.name, folder / "RELEASE_NOTES.md"]
    (folder / "SHA256SUMS.txt").write_text("".join(f"{sha(p)}  {p.name}\n" for p in files), encoding="utf-8")
    print("GitHub Release assets ready:", folder)
    print("Tag:", "v" + version)
    print("Publish version assets as a prerelease; copy latest.json to the desktop-dev prerelease channel.")
    print("No upload performed.")


if __name__ == "__main__":
    main()
