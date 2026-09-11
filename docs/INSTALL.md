# Install Twitter / X Tweet Archiver

## Windows with Chrome, Edge or Brave

Download the Windows ZIP from the latest [GitHub release](https://github.com/oliviercaron/twitter-x-archiver/releases/latest), then extract the whole folder somewhere you can keep it. Do not run the app from inside the ZIP or move the `.exe` by itself.

1. Run `INSTALL.cmd` once. The release also includes the original French shortcut, `INSTALLER D ABORD.cmd`.
2. Open your browser's extensions page: `chrome://extensions`, `edge://extensions` or `brave://extensions`.
3. Turn on **Developer mode**, choose **Load unpacked**, and select the `chrome-extension` folder from the extracted download.
4. Sign in to X in that browser.
5. Run `START.cmd` (also included as `DEMARRER.cmd`). The archive opens at `http://127.0.0.1:18765`.
6. Allow access to X and the local app if your browser asks.

Windows may warn you because this small release is not code-signed. If you move the application folder later, run `INSTALL.cmd` again so the browser bridge knows its new location.

## Where files are stored

You do not have to choose a folder during setup. A new installation uses:

- Windows: `%LOCALAPPDATA%\Archivage X\archives`
- macOS: `~/Library/Application Support/Archivage X/archives`
- Linux: `${XDG_DATA_HOME:-~/.local/share}/archivage-x/archives`

Open **Archive storage** in the app to view or change this location. The export folder is chosen separately. If you change the archive location, the app copies and checks the data before switching and keeps the previous folder.

## Firefox

Install the local app first, then open `about:debugging#/runtime/this-firefox`. Choose **Load Temporary Add-on** and select `firefox-extension/manifest.json`.

This build is unsigned, so Firefox removes it when the browser restarts. A permanent Firefox installation needs a package signed by Mozilla. Firefox 140 or later is required.

## macOS with Chrome

Download the source package and install Python 3.12 from python.org. In Terminal, open the extracted folder and run:

```sh
sh installer.command
sh demarrer.command
```

Then load `chrome-extension` as an unpacked extension in Chrome. The Windows `.exe` does not run on macOS.

## Safari

The repository includes experimental Safari/Xcode source in `safari/`. It must be built on a Mac with Xcode and used with the local companion app. It has not yet been compiled, signed or tested on a real Mac, so it is not a ready Safari download.

## Optional video details

The app saves videos without FFmpeg. Install FFmpeg and make `ffprobe` available in your system `PATH` if you also want measured codec, dimensions, duration and frame-rate details. Without it, those technical fields are marked as unavailable.

