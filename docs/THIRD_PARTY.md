# Third-party components

The app uses Python, twscrape, curl_cffi, PyArrow, Pillow, NumPy, SciPy and other libraries. The Windows package also contains their runtime dependencies. PyInstaller builds the executable.

These components keep their own licenses. The project’s MIT license does not replace them. The [third-party directory](third-party/) preserves the license notices provided by installed runtime and build packages; [index.json](third-party/index.json) records their versions. It may include build tools that are not shipped at runtime. Python’s license is included separately.

To regenerate these notices, run `python tools/collect_dependency_notices.py` in the environment used for the build.
