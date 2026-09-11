"""Portable checks; the Swift adapter still requires a real Mac build/run."""
import json
import plistlib
from pathlib import Path

import pytest

from platforms.safari.prepare_safari import converter_command, patch_project, validate_extension
from scripts.build_extensions import build


def make_extension(tmp_path):
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "manifest.json").write_text(json.dumps({"permissions": ["nativeMessaging"]}))
    return extension


def test_rejects_wrong_browser_and_private_files(tmp_path):
    extension = make_extension(tmp_path)
    assert validate_extension(extension) == extension
    (extension / "secrets_bridge.json").write_text("{}")
    with pytest.raises(ValueError, match="Private"):
        validate_extension(extension)
    (extension / "secrets_bridge.json").unlink()
    (extension / "manifest.json").write_text(json.dumps({"permissions": ["nativeMessaging"], "key": "x"}))
    with pytest.raises(ValueError, match="Safari build"):
        validate_extension(extension)


def test_accepts_real_safari_package_but_rejects_firefox_package(tmp_path):
    safari, _ = build("safari", tmp_path / "safari")
    assert validate_extension(safari) == safari
    firefox, _ = build("firefox", tmp_path / "firefox")
    with pytest.raises(ValueError, match="Safari build"):
        validate_extension(firefox)


def test_converter_copies_sources_without_overwriting_projects(tmp_path):
    command = converter_command("safari-web-extension-converter", tmp_path / "source with spaces", tmp_path / "new")
    assert command[:2] == ["xcrun", "safari-web-extension-converter"]
    assert "--copy-resources" in command
    assert "--macos-only" in command
    assert "--force" not in command
    assert str(tmp_path / "source with spaces") in command


@pytest.mark.parametrize("shared", [False, True])
def test_patch_limits_permissions_to_native_extension(tmp_path, shared):
    project = tmp_path / "Archivage X Safari.xcodeproj"
    project.mkdir()
    extension = tmp_path / "Archivage X Safari Extension"
    extension.mkdir()
    sources = tmp_path / "Shared (Extension)" if shared else extension
    sources.mkdir(exist_ok=True)
    (sources / "SafariWebExtensionHandler.swift").write_text("// Apple template")
    entitlements = extension / "extension.entitlements"
    entitlements.write_bytes(plistlib.dumps({"com.apple.security.app-sandbox": True}))
    info = extension / "Info.plist"
    info.write_bytes(plistlib.dumps({"NSExtension": {"NSExtensionPointIdentifier": "com.apple.Safari.web-extension"}}))
    assert patch_project(tmp_path) == project
    rights = plistlib.loads(entitlements.read_bytes())
    assert rights == {"com.apple.security.app-sandbox": True, "com.apple.security.network.client": True}
    ats = plistlib.loads(info.read_bytes())["NSAppTransportSecurity"]
    assert ats == {"NSAllowsLocalNetworking": True}
    handler = (sources / "SafariWebExtensionHandler.swift").read_text()
    assert "companion_required" in handler
    assert "Process(" not in handler


def test_unexpected_xcode_layout_is_not_silently_accepted(tmp_path):
    with pytest.raises(RuntimeError, match="Unexpected Xcode template"):
        patch_project(tmp_path)
