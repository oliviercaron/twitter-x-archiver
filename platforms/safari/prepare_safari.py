#!/usr/bin/env python3
"""Create Safari's macOS Xcode project from the portable extension build.

Requires macOS and full Xcode. The output must be new: regeneration never
overwrites signing changes or a developer's edited Xcode project.
"""
import argparse
import json
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_NAME = "Archivage X Safari"
BUNDLE_ID = "org.archivex.app"


def validate_extension(extension):
    extension = Path(extension).resolve()
    manifest = json.loads((extension / "manifest.json").read_text(encoding="utf-8"))
    if "nativeMessaging" not in manifest.get("permissions", []):
        raise ValueError("The Safari build must declare nativeMessaging.")
    settings = manifest.get("browser_specific_settings", {})
    if "key" in manifest or not isinstance(settings, dict) or set(settings) - {"safari"}:
        raise ValueError("Use the Safari build, not the Chrome or Firefox sources.")
    for path in extension.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symlinks are not distributable: {path}")
        name = path.name.lower()
        if (name in {"data", "resultats", ".git", ".env", "accounts.db"}
                or "cookie" in name or name.startswith("secrets")):
            raise ValueError(f"Private or collected data must not be packaged: {path}")
    return extension


def converter_command(tool, extension, output, bundle_id=BUNDLE_ID):
    return ["xcrun", tool, str(extension), "--project-location", str(output),
            "--app-name", APP_NAME, "--bundle-identifier", bundle_id,
            "--macos-only", "--swift", "--copy-resources", "--no-open", "--no-prompt"]


def find_converter():
    # Xcode 26 calls it packager; earlier versions call it converter.
    for name in ("safari-web-extension-packager", "safari-web-extension-converter"):
        result = subprocess.run(["xcrun", "--find", name], capture_output=True, text=True)
        if result.returncode == 0:
            return name
    raise RuntimeError("Install full Xcode and select it with xcode-select first.")


def patch_project(output):
    """Keep Apple's project/template; replace only the native adapter and rights."""
    output = Path(output)
    handlers = list(output.rglob("SafariWebExtensionHandler.swift"))
    projects = list(output.rglob("*.xcodeproj"))
    if len(handlers) != 1 or len(projects) != 1:
        raise RuntimeError("Unexpected Xcode template: expected one macOS handler and project.")
    handler = handlers[0]
    shutil.copyfile(HERE / "SafariWebExtensionHandler.swift", handler)
    # Newer templates split Shared (Extension) Swift from macOS (Extension)
    # metadata. Identify the extension by its plist, never a localized folder.
    info_paths = []
    for candidate in output.rglob("Info.plist"):
        with candidate.open("rb") as stream:
            info = plistlib.load(stream)
        if info.get("NSExtension", {}).get("NSExtensionPointIdentifier") == "com.apple.Safari.web-extension":
            info_paths.append(candidate)
    if len(info_paths) != 1:
        raise RuntimeError("Cannot identify extension Info.plist; review the Xcode project.")
    entitlements = list(info_paths[0].parent.glob("*.entitlements"))
    if len(entitlements) != 1:
        raise RuntimeError("Cannot identify extension entitlements; review the Xcode project.")
    path = entitlements[0]
    with path.open("rb") as stream:
        rights = plistlib.load(stream)
    rights["com.apple.security.app-sandbox"] = True
    rights["com.apple.security.network.client"] = True
    with path.open("wb") as stream:
        plistlib.dump(rights, stream)

    # The default template has no local-network ATS declaration. Limit the
    # exception to local resources; never enable NSAllowsArbitraryLoads.
    path = info_paths[0]
    with path.open("rb") as stream:
        info = plistlib.load(stream)
    info.setdefault("NSAppTransportSecurity", {})["NSAllowsLocalNetworking"] = True
    with path.open("wb") as stream:
        plistlib.dump(info, stream)
    return projects[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension", type=Path, required=True,
                        help="Safari folder produced by build_extensions.py")
    parser.add_argument("--output", type=Path, required=True, help="New Xcode output folder")
    parser.add_argument("--bundle-id", default=BUNDLE_ID)
    parser.add_argument("--build", action="store_true", help="Also compile an unsigned Debug app")
    args = parser.parse_args(argv)
    if sys.platform != "darwin":
        parser.error("Safari's native app must be built on macOS with Xcode.")
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z0-9-]+)+", args.bundle_id):
        parser.error("Use a reverse-DNS bundle identifier, for example org.archivex.app.")
    try:
        extension = validate_extension(args.extension)
        output = args.output.resolve()
        if output.exists():
            raise ValueError("Output already exists; choose a fresh folder to preserve your project.")
        subprocess.run(converter_command(find_converter(), extension, output, args.bundle_id), check=True)
        project = patch_project(output)
        if args.build:
            subprocess.run(["xcodebuild", "-project", str(project), "-scheme", APP_NAME,
                            "-configuration", "Debug", "-derivedDataPath", str(output / "DerivedData"),
                            "CODE_SIGN_IDENTITY=-", "CODE_SIGNING_ALLOWED=NO", "build"], check=True)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Safari preparation failed: {exc}\n")
    print(f"Xcode project: {project}")
    print("Open it in Xcode, select signing for both targets, and build/run on this Mac.")
    print("Start the Archivage X companion separately before archiving in Safari.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
