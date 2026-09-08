import pathlib
inbox = pathlib.Path(r"\\wsl.localhost\Ubuntu\home\user\paodan\data\inbox")
# when run under Windows python with CWD=UNC, relative also works; use relative
from pathlib import Path
inbox2 = Path("data/inbox")
print("cwd", Path.cwd())
print("list inbox root:")
for p in sorted(inbox2.iterdir()):
    print(" ", p.name, "DIR" if p.is_dir() else p.stat().st_size)
emls = sorted(inbox2.rglob("*.eml"))
print("total eml recursive:", len(emls))
for e in emls:
    print(" ", e.as_posix())
