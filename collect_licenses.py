"""Copy dependency notices for binary redistribution."""
import importlib.metadata as metadata
from pathlib import Path
import shutil
import sys

output = Path(__file__).parent / 'third-party-licenses'
output.mkdir(exist_ok=True)
for dist in metadata.distributions():
    name = dist.metadata['Name']
    target = output / name
    target.mkdir(exist_ok=True)
    for file in dist.files or []:
        lower = str(file).lower()
        if any(word in Path(lower).name for word in ('license', 'copying', 'notice')) or lower.endswith('.dist-info/metadata'):
            source = Path(dist.locate_file(file))
            if source.is_file():
                relative = Path(*file.parts[-3:])
                dest = target / relative
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, dest)
for name in ('LICENSE.txt', 'tcl/tcl8.6/license.terms', 'tcl/tk8.6/license.terms'):
    source = Path(sys.base_prefix) / name
    if source.is_file():
        shutil.copy2(source, output / name.replace('/', '-'))
print(f'License notices collected: {output}')
