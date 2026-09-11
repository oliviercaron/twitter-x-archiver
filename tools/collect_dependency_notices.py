"""Copy installed runtime/build license notices for a release (run in its venv)."""
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import shutil
import sys

root=Path(__file__).resolve().parent.parent
out=root/'docs'/'third-party'
out.mkdir(parents=True,exist_ok=True)
rows=[]
for dist in sorted(metadata.distributions(),key=lambda d:d.metadata.get('Name','').lower()):
    name=dist.metadata.get('Name','unknown')
    if name.lower() in {'pip','pytest','iniconfig','pluggy'}:continue
    slug=re.sub(r'[^a-z0-9._-]+','-',name.lower())
    files=[]
    for item in dist.files or []:
        parts=Path(str(item)).parts
        if '..' in parts or not any('.dist-info' in part for part in parts):continue
        if not any(re.match(r'^(licenses?|copying|notice|authors)([._-]|$)',part,re.I) for part in parts):continue
        source=Path(dist.locate_file(item))
        if not source.is_file():continue
        target=out/slug/Path(*parts[1:])
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        files.append(target.relative_to(out).as_posix())
    rows.append({'name':name,'version':dist.version,'notices':files})
python_license=Path(sys.base_prefix)/'LICENSE.txt'
if python_license.is_file():shutil.copyfile(python_license,out/'Python-LICENSE.txt')
(out/'index.json').write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
missing=[r['name'] for r in rows if not r['notices']]
(root/'docs'/'THIRD_PARTY.md').write_text('''# Third-party components

The app uses Python, twscrape, curl_cffi, PyArrow, Pillow, NumPy, SciPy and other libraries. The Windows package also contains their runtime dependencies. PyInstaller builds the executable.

These components keep their own licenses. The project’s MIT license does not replace them. The [third-party directory](third-party/) preserves the license notices provided by installed runtime and build packages; [index.json](third-party/index.json) records their versions. It may include build tools that are not shipped at runtime. Python’s license is included separately.

To regenerate these notices, run `python tools/collect_dependency_notices.py` in the environment used for the build.
''',encoding='utf-8')
print(json.dumps({'packages':len(rows),'notice_files':sum(len(r['notices']) for r in rows),'missing_notices':missing}))
