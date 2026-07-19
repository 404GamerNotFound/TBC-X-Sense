import json
from pathlib import Path

_PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _manifest() -> dict:
    return json.loads((_PLUGIN_DIR / "manifest.json").read_text(encoding="utf-8"))


def test_manifest_key_matches_standard_source_catalog():
    # Must equal the "xsense-cloud" key registered in TBC-camera-manager's
    # STANDARD_PLUGIN_SOURCES catalog (app/tbc/plugin_sources.py, subdirectory="cloud").
    manifest = _manifest()
    assert manifest["key"] == "xsense-cloud"


def test_manifest_declares_required_account_fields():
    manifest = _manifest()
    field_keys = {field["key"] for field in manifest["account_fields"]}
    assert {"email", "password"}.issubset(field_keys)


def test_manifest_entrypoint_file_exists():
    manifest = _manifest()
    assert (_PLUGIN_DIR / manifest["entrypoint"]).is_file()


def test_manifest_declares_its_own_pip_requirements():
    # Lets TBC install "pycognito" etc. on demand instead of every plugin's
    # dependency having to live in TBC-camera-manager's own requirements.txt
    # forever - see app/tbc/plugin_requirements.py in the main repo.
    manifest = _manifest()
    assert "pycognito" in " ".join(manifest["requirements"])
