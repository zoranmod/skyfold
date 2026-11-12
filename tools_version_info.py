"""Write the Windows version resource for the exe (product name, version, licence, project page).

An exe without these details looks anonymous to Windows and to antivirus heuristics.
Usage: python tools_version_info.py <output file>
"""
import sys
from pathlib import Path

from skyfold.config import VERSION

parts = [int(x) for x in VERSION.split(".")][:4]
parts += [0] * (4 - len(parts))
tup = ", ".join(str(n) for n in parts)

Path(sys.argv[1]).write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup}), mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Skyfold (open source)'),
      StringStruct('FileDescription', 'Skyfold - iCloud sync for Windows'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'Skyfold'),
      StringStruct('LegalCopyright', 'GPL-3.0-or-later - https://github.com/zoranmod/skyfold'),
      StringStruct('OriginalFilename', 'Skyfold.exe'),
      StringStruct('ProductName', 'Skyfold'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
""", encoding="utf-8")
