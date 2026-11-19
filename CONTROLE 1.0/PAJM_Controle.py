"""
PAJM Controle -- ponto de entrada do programa de controle da PAJM.

O programa de controle opera a maquina e contem um fatiador (STL para
camadas), a impressao de imagem e a importacao de pacotes .pajm gerados por
um CAM externo.

Rodar em desenvolvimento (ambiente virtual fora do OneDrive):
    %USERPROFILE%\\.venvs\\pajm\\Scripts\\python.exe PAJM_Controle.py

Gerar o executavel:
    build.bat
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def main() -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    app.setApplicationName("PAJM Controle")

    # Erro nao tratado nunca pode sumir em silencio num software de maquina.
    def excecao(tipo, valor, tb):
        texto = "".join(traceback.format_exception(tipo, valor, tb))
        sys.stderr.write(texto)
        QMessageBox.critical(None, "Erro inesperado", texto[-2000:])

    sys.excepthook = excecao

    from pajm.profile import Perfil
    from pajm.ui.main_window import JanelaPrincipal

    perfil = Perfil()

    # Perfil salvo ao lado do executavel tem prioridade sobre o embutido.
    local = Path(__file__).resolve().parent / "perfil_pajm.json"
    if local.exists():
        try:
            perfil = Perfil.carregar(local)
        except Exception as e:
            QMessageBox.warning(
                None, "Perfil",
                f"Nao consegui ler {local.name}, usando o perfil padrao.\n\n{e}")

    # Calibracao e preferencias de operacao (ajustes_pajm.json)
    try:
        perfil.carregar_ajustes()
    except Exception as e:
        QMessageBox.warning(
            None, "Ajustes",
            f"Nao consegui ler os ajustes salvos, usando os padroes.\n\n{e}")

    janela = JanelaPrincipal(perfil)
    janela.show()
    janela.verificar_integridade()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
