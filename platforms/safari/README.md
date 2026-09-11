# Safari adapter

This folder contains an experimental native adapter and a script that prepares a macOS Xcode project using Apple's Safari Web Extension tools. The browser code comes from the same `extension/` sources as Chrome and Firefox.

The Python preparation is tested with mocks. Swift compilation, permissions, cookie access and real archiving still need validation on a Mac. There is no compiled or signed Safari app in the releases. iPhone and iPad are not supported by the desktop companion.

## Build on a Mac

Install and open the full Xcode application, then run these commands from the repository root:

```sh
python3 scripts/build_extensions.py --browser safari --output dist/extensions
python3 platforms/safari/prepare_safari.py --extension dist/extensions/safari --output build/safari-xcode
```

The script uses Apple's packager, or the older converter when necessary. It creates a fresh project, installs the native message handler and enables outgoing network access. Use a new output directory so existing signing settings are preserved.

The optional `--build` flag attempts an unsigned Debug build. Open the resulting project in Xcode to configure signing for the app and extension, run the app, then enable its extension in Safari and grant access to X and the local archive page. `--bundle-id` changes the default app identifier, `org.archivex.app`.

Start the Python companion with `sh launchers/macos/start.command` before archiving. The Safari adapter checks the local service; it does not launch Python from the extension sandbox.

Apple's documentation: [Safari extensions](https://developer.apple.com/safari/extensions/).
