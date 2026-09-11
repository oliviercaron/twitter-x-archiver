# Install Twitter / X Tweet Archiver

## Windows: the ready-to-use download

Download **twitter-x-archiver-windows.zip** from the [latest release](https://github.com/oliviercaron/twitter-x-archiver/releases/latest). Extract the whole folder somewhere you can keep it.

1. Run **INSTALL.cmd** once.
2. Open `chrome://extensions`, `edge://extensions` or `brave://extensions`.
3. Turn on **Developer mode**, choose **Load unpacked**, and select **extensions/chrome** from the download.
4. Sign in to X in that browser, then run **START.cmd** to open the archive.
5. Allow access to X and the local app if your browser asks.

Keep `app/`, `extensions/` and the shortcuts together. The executable needs its bundled libraries. Windows may show a warning because the app is not code-signed. If you move the installation folder, run INSTALL.cmd again. UNINSTALL.cmd removes the browser registration and keeps your archives.

## Your files

No folder choice is needed during setup. A new installation uses your personal application-data folder. Open **Archive storage** to see the path or change it. The app copies and checks the archive before switching, and keeps the previous copy.

The export folder is chosen separately. An export contains `tweets.csv`, `observations.csv`, media and source metadata. The CSVs use semicolons and UTF-8.

## Firefox

Install the local app first. Open `about:debugging#/runtime/this-firefox`, choose **Load Temporary Add-on**, and select **extensions/firefox/manifest.json**. The separate Firefox ZIP contains the same files.

Firefox 140 or later is required. This unsigned build is temporary and is removed when Firefox restarts. Permanent installation still requires Mozilla signing.

## From source: Windows or macOS

Install Python 3.12, download the source package, and extract it. On Windows, run `launchers/windows/INSTALL.cmd`, then `launchers/windows/START.cmd`.

On macOS, open Terminal in the extracted folder:

```sh
sh launchers/macos/install.command
sh launchers/macos/start.command
```

For Chrome, load the **extension/** folder from the source package. To generate Firefox files, run `python scripts/build_extensions.py --browser firefox --output dist/extensions`, then load `dist/extensions/firefox/manifest.json`.

The source setup creates its own Python environment. macOS support has not yet been validated on Apple hardware; the Windows executable does not run on a Mac.

## Safari

Safari requires an extension built with Xcode. See [the Safari guide](../platforms/safari/README.md). These are experimental sources, not a signed or tested Safari download.

## Optional video details

Videos can be saved without FFmpeg. Install FFmpeg and make `ffprobe` available in your PATH for measured codecs, dimensions, duration and frame rate. Without it, the corresponding technical validation is marked as unavailable.
