"""Copy the sibling client source into the HACS-installable integration.

Run from any directory: python tools/sync_client.py
"""

from pathlib import Path
import argparse
import hashlib
import json


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT.parent / "gcom-client" / "gcom_client"
TARGET = ROOT / "custom_components" / "gcom" / "_vendor" / "gcom_client"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the local vendor snapshot differs")
    args = parser.parse_args()
    if not (SOURCE / "__init__.py").is_file():
        raise SystemExit(f"Client source is missing: {SOURCE}")
    source_files = {path.name for path in SOURCE.glob("*.py")}
    source_license = ROOT.parent / "gcom-client" / "LICENSE"
    files = {name: (SOURCE / name).read_bytes() for name in sorted(source_files)}
    if source_license.is_file():
        files["LICENSE"] = source_license.read_bytes()
    metadata = {
        "source": "sibling gcom-client project",
        "files_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    metadata_bytes = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode()
    if args.check:
        expected = {**files, "VENDOR-SOURCE.json": metadata_bytes}
        actual = {path.name: path.read_bytes() for path in TARGET.iterdir() if path.is_file() and path.name != "__pycache__"} if TARGET.exists() else {}
        if actual != expected:
            raise SystemExit("Vendor snapshot differs; run python tools/sync_client.py")
        print("Vendor snapshot matches")
        return
    TARGET.mkdir(parents=True, exist_ok=True)
    for stale in TARGET.iterdir():
        if stale.is_file() and stale.name not in files and stale.name != "VENDOR-SOURCE.json":
            stale.unlink()
    for name, data in files.items():
        (TARGET / name).write_bytes(data)
    (TARGET / "VENDOR-SOURCE.json").write_bytes(metadata_bytes)
    print(f"Synced {len(files)} vendor files")


if __name__ == "__main__":
    main()
