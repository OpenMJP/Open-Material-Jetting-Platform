# -*- mode: python ; coding: utf-8 -*-
"""
Receita do PyInstaller para o PAJM Controle.

Gera UM executavel unico. O custo e o primeiro clique de cada boot levar
alguns segundos descompactando em %TEMP%; a partir do segundo e instantaneo.

As exclusoes existem por dois motivos: tamanho, e evitar que o PyInstaller
tente empacotar coisa que so foi usada em desenvolvimento (leitura dos PDFs
do datasheet, por exemplo).
"""

excluidos = [
    # So foram usados em desenvolvimento, para ler os documentos do cabecote.
    "pypdf",
    # PDF na impressao de imagem: por decisao de 27/10 fica FORA do executavel, porque
    # custa 19 MB (74 contra 55). O codigo continua suportando; para religar,
    # tire daqui e ponha de volta em hiddenimports.
    "pymupdf", "fitz",
    # nao usamos nada disto
    "tkinter", "matplotlib", "scipy", "pandas", "IPython", "jupyter",
    "PIL", "pytest", "setuptools", "pip",
    # modulos Qt que o programa nao toca
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtMultimedia", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtNetwork",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtPdf",
]

a = Analysis(
    ["PAJM_Controle.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=["pyqtgraph.opengl", "OpenGL.platform.win32",
                   "serial", "serial.tools.list_ports"],
    hookspath=[],
    runtime_hooks=[],
    excludes=excluidos,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="PAJM_Controle",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,          # sem janela de terminal atras
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
