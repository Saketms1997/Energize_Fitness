# PyInstaller spec: a windowed one-folder build (no console, no Python needed on the target PC).
# Build with:  powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
from pathlib import Path

ROOT = Path(SPECPATH).parent

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # Alembic loads migration scripts from files at runtime, so they ship as data.
        (str(ROOT / "database" / "migrations"), "database/migrations"),
        (str(ROOT / "assets"), "assets"),
    ],
    hiddenimports=[
        "keyring.backends.Windows", "psycopg_binary", "sqlalchemy.dialects.postgresql.psycopg",
        "argon2", "pymongo", "certifi", "openpyxl", "reportlab.graphics.barcode", "qrcode.image.pil",
        "matplotlib.backends.backend_qtagg", "pyodbc",
    ],
    excludes=["tkinter", "pytest", "responses", "mongomock", "IPython", "notebook", "pandas", "scipy"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="GymManagement",
    console=False,
    icon=str(ROOT / "assets" / "icons" / "app.ico"),
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="GymManagement")
