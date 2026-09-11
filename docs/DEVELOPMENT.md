# Working on the app

The repository has one shared browser extension and one Python application. Browser downloads are generated from the shared source.

| Folder | What it contains |
| --- | --- |
| `extension/` | The button and browser integration for X |
| `src/twitter_x_archiver/` | Downloads, storage, CSV exports and the local web interface |
| `scripts/` | Release builders, dependency notices and the demo capture script |
| `launchers/` | Windows and macOS install/start shortcuts |
| `platforms/safari/` | Experimental Safari adapter and Xcode preparation |
| `tests/` | Tests with synthetic posts and temporary archives |
| `docs/` | Installation help, screenshots and third-party notices |

`pyproject.toml` declares the Python dependencies and command-line tools. Generated browser copies and build folders stay out of Git.

## Run from source

Use Python 3.12 and open a terminal in the repository:

```sh
python -m venv .venv
# Activate .venv for your operating system, then:
python -m pip install -e ".[dev]"
python -m twitter_x_archiver
```

The installed commands are `twitter-x-archiver`, `twitter-x-import` and `twitter-x-install`. To import a text file containing one post URL per line:

```sh
twitter-x-import posts.txt --include-replies
```

Register the browser bridge with `twitter-x-install`, then load `extension/` into Chrome. Native registrations are stored in the user profile. The app listens on `127.0.0.1:18765`.

## Test

```sh
python -m pytest tests
```

The Python suite uses temporary archives. Browser UI tests use Node.js and Playwright; set `PLAYWRIGHT_PATH` and `CHROME_PATH` if needed. The dashboard, storage and category tests use mocked data. The older `test_results_ui.cjs` requires an explicit `ALLOW_ARCHIVE_EXPORT_TEST=1` and `RESULTS_TEST_DIR` because it exports from a real local archive. `test_extension.cjs` needs port 18765 free for its own test bridge.

## Build a release

```sh
python scripts/build_extensions.py --browser all --output dist/extensions
python scripts/build_distribution.py --output dist/source-download
python scripts/build_exe.py --output dist/windows-download
```

Choose a new output folder each time. Build the Windows executable with Python on Windows. Its download contains three shortcuts, `app/`, `extensions/` and the documentation. There is no prebuilt macOS or Safari app yet.

Run `python scripts/collect_dependency_notices.py` in the build environment to collect dependency licenses. See [THIRD_PARTY.md](THIRD_PARTY.md). The demo can be regenerated with `scripts/generate_demo.cjs` using sample data.
