#!/usr/bin/env python3
"""
resolve-prep - transcodifica mídia que o DaVinci Resolve (free) não decodifica
(HEVC 4:2:2 10 bits da Canon R6 Mark II, entre outros) para H.264 ou H.265
all-intra, sempre em MP4.

Interface PySide6. A conversão em si mora no transcoder.py.

Roda sozinho: se não estiver dentro da venv/, cria a venv ao lado deste
arquivo, instala o requirements.txt e se reexecuta lá dentro.

Requer ffmpeg e ffprobe no PATH (ou em tools\\ffmpeg-*\\bin, onde o run.bat
deixa a cópia portátil no Windows).
    Windows: dê dois cliques em run.bat. Ou: winget install Gyan.FFmpeg
    Arch:    sudo pacman -S ffmpeg
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_BOOTSTRAP_FLAG = "RESOLVE_PREP_BOOTSTRAPPED"


def _venv_python(venv_dir: Path) -> Path:
    return venv_dir / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _pip_install(python: Path) -> None:
    print("[resolve-prep] instalando dependências")
    subprocess.run([str(python), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=False)
    try:
        subprocess.run(
            [str(python), "-m", "pip", "install", "-q", "-r", str(HERE / "requirements.txt")],
            check=True,
        )
    except subprocess.CalledProcessError:
        sys.exit("resolve-prep: pip falhou. Verifique sua conexão e tente de novo.")


def _ensure_venv() -> None:
    """Garante que rodamos no python de venv/ com as dependências instaladas.

    Se venv/ existe e não somos ela, reexecuta lá dentro. Se não existe, cria
    e instala o requirements.txt antes, para o duplo clique no Windows
    funcionar sem preparar nada na mão.
    """
    venv_dir = HERE / "venv"
    venv_py = _venv_python(venv_dir)

    if Path(sys.prefix).resolve() == venv_dir.resolve():
        try:
            import PySide6  # noqa: F401
        except ImportError:
            _pip_install(venv_py)
        return

    if os.environ.get(_BOOTSTRAP_FLAG):
        sys.exit(
            "resolve-prep: a venv existe mas não consegui rodar dentro dela.\n"
            f'  Rode manualmente: "{venv_py}" -m pip install -r requirements.txt'
        )

    if not venv_py.is_file():
        print(f"[resolve-prep] criando venv em {venv_dir}")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        except subprocess.CalledProcessError:
            sys.exit(
                "resolve-prep: falhei em criar a venv.\n"
                "  No Debian/Ubuntu instale o pacote python3-venv.\n"
                "  No Windows reinstale o Python marcando 'Add to PATH'."
            )
        _pip_install(venv_py)

    script = str(Path(__file__).resolve())
    env = {**os.environ, _BOOTSTRAP_FLAG: "1"}
    if os.name == "nt":
        # No Windows o execv não substitui o processo: quem nos chamou
        # (run.bat, atalho) acharia que o programa já terminou.
        raise SystemExit(subprocess.call([str(venv_py), script, *sys.argv[1:]], env=env))
    os.execve(str(venv_py), [str(venv_py), script, *sys.argv[1:]], env)


_ensure_venv()

# --------------------------------------------------------------------------
# A partir daqui já estamos dentro da venv
# --------------------------------------------------------------------------

import json  # noqa: E402

from PySide6.QtCore import Qt, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QBrush, QColor, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

import transcoder  # noqa: E402
from dark_theme import COLORS, apply_theme, set_default_font  # noqa: E402
from transcoder import DEFAULT_PRESET, PRESET_BY_KEY, PRESETS, MediaInfo, Preset, TranscodeError  # noqa: E402

FILTRO_ARQUIVOS = (
    "Vídeos (" + " ".join(f"*{ext}" for ext in sorted(transcoder.VIDEO_EXTS)) + ");;"
    "Todos os arquivos (*)"
)
COLUNAS = ("Arquivo", "Codec", "Resolução", "FPS", "Chroma", "Bits", "Range", "Status")
COL_ARQUIVO, COL_CODEC, COL_RES, COL_FPS, COL_CHROMA, COL_BITS, COL_RANGE, COL_STATUS = range(8)
LENDO = "lendo..."  # status de uma linha que ainda não passou pelo ffprobe

# --------------------------------------------------------------------------
# Configuração persistente (config.json ao lado do programa)
# --------------------------------------------------------------------------

CONFIG_FILE = transcoder.app_dir() / "config.json"


def carregar_config() -> dict:
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def salvar_config(config: dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4, ensure_ascii=False)
    except OSError as exc:
        print(f"[resolve-prep] não consegui salvar o config.json: {exc}")


# --------------------------------------------------------------------------
# Workers
# --------------------------------------------------------------------------


class LeitorWorker(QThread):
    """Roda o ffprobe nos arquivos novos fora da thread da interface."""

    lido = Signal(object)  # MediaInfo

    def __init__(self, ffprobe: str, caminhos: list[Path]) -> None:
        super().__init__()
        self._ffprobe = ffprobe
        self._caminhos = caminhos
        self._cancelado = False

    def cancelar(self) -> None:
        self._cancelado = True

    def run(self) -> None:
        for caminho in self._caminhos:
            if self._cancelado:
                break
            self.lido.emit(transcoder.probe(self._ffprobe, caminho))


class ConversorWorker(QThread):
    """Roda a fila de conversões fora da thread da interface."""

    arquivo_iniciado = Signal(int, str, str)       # índice, nome, destino
    progresso = Signal(int, float, str)            # índice, fração, velocidade
    arquivo_terminado = Signal(int, str, str)      # índice, estado, mensagem
    fila_terminada = Signal(int, int, int, bool)   # convertidos, falhas, pulados, cancelado

    def __init__(
        self, ffmpeg: str, fila: list[MediaInfo], raiz: Path | None, preset: Preset
    ) -> None:
        super().__init__()
        self._ffmpeg = ffmpeg
        self._fila = fila
        self._raiz = raiz
        self._preset = preset
        self._cancelado = False

    def cancelar(self) -> None:
        self._cancelado = True

    def run(self) -> None:
        ok = falhas = pulados = 0
        produzidos: set[Path] = set()

        for n, info in enumerate(self._fila):
            if self._cancelado:
                break

            if not info.readable:
                pulados += 1
                self.arquivo_terminado.emit(n, "pulado", f"o ffprobe não leu o arquivo ({info.status})")
                continue

            destino = transcoder.output_path(info.path, self._raiz)

            # Duas pastas de origem com o mesmo nome (cartões diferentes, por
            # exemplo) cairiam na mesma pasta de saída e se sobrescreveriam.
            if destino in produzidos:
                pulados += 1
                self.arquivo_terminado.emit(n, "pulado", f"destino repetido: {destino}")
                continue

            try:
                mesmo_arquivo = destino.resolve() == info.path.resolve()
            except OSError:
                mesmo_arquivo = False
            if mesmo_arquivo:
                pulados += 1
                self.arquivo_terminado.emit(n, "pulado", "a saída sobrescreveria o original")
                continue

            if destino.exists() and destino.stat().st_mtime >= info.path.stat().st_mtime:
                pulados += 1
                produzidos.add(destino)
                self.arquivo_terminado.emit(n, "existe", str(destino))
                continue

            self.arquivo_iniciado.emit(n, info.path.name, str(destino))
            try:
                transcoder.convert(
                    self._ffmpeg,
                    info,
                    destino,
                    self._preset,
                    on_progress=lambda fracao, vel, i=n: self.progresso.emit(i, fracao, vel),
                    should_cancel=lambda: self._cancelado,
                )
            except TranscodeError as exc:
                # Cancelar é uma decisão do usuário, não uma falha do arquivo.
                if self._cancelado:
                    self.arquivo_terminado.emit(n, "cancelado", "interrompido")
                    break
                falhas += 1
                self.arquivo_terminado.emit(n, "falha", str(exc))
            except Exception as exc:  # noqa: BLE001 - a fila não pode morrer por um arquivo
                falhas += 1
                self.arquivo_terminado.emit(n, "falha", f"Erro inesperado: {exc}")
            else:
                ok += 1
                produzidos.add(destino)
                self.arquivo_terminado.emit(n, "ok", str(destino))

        self.fila_terminada.emit(ok, falhas, pulados, self._cancelado)


# --------------------------------------------------------------------------
# Janela
# --------------------------------------------------------------------------


class Janela(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("resolve-prep")
        # Só a largura é fixada: a altura mínima vem do layout, senão a
        # janela aceita encolher abaixo do que os grupos precisam e eles se
        # sobrepõem.
        self.setMinimumWidth(920)
        self.resize(1000, 860)
        self.setAcceptDrops(True)

        self._config = carregar_config()
        raiz = self._config.get("pasta_saida")
        self._raiz: Path | None = Path(raiz) if raiz else None

        self._fila: list[MediaInfo] = []
        self._pendentes: list[Path] = []
        self._leitor: LeitorWorker | None = None
        self._worker: ConversorWorker | None = None
        self._total = 0

        try:
            self._ffmpeg, self._ffprobe = transcoder.find_ffmpeg()
            self._erro_ffmpeg = ""
        except TranscodeError as exc:
            self._ffmpeg = self._ffprobe = ""
            self._erro_ffmpeg = str(exc)

        raiz_widget = QWidget()
        layout = QVBoxLayout(raiz_widget)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        titulo = QLabel("resolve-prep")
        titulo.setObjectName("title")
        subtitulo = QLabel(
            "Transcodifica o que o DaVinci Resolve free não abre (HEVC 4:2:2 10 bits da "
            "Canon, por exemplo) para H.264 ou H.265 all-intra em MP4, com cor e "
            "timecode preservados."
        )
        subtitulo.setObjectName("subtitle")
        subtitulo.setWordWrap(True)
        layout.addWidget(titulo)
        layout.addWidget(subtitulo)

        separador = QFrame()
        separador.setObjectName("separator")
        separador.setFrameShape(QFrame.Shape.HLine)
        layout.addWidget(separador)

        layout.addWidget(self._grupo_arquivos(), stretch=3)
        layout.addWidget(self._grupo_opcoes())
        layout.addWidget(self._grupo_progresso())
        layout.addWidget(self._grupo_log(), stretch=1)
        layout.addLayout(self._barra_acoes())

        self.setCentralWidget(raiz_widget)

        if self._erro_ffmpeg:
            self._escrever(self._erro_ffmpeg)
        else:
            self._escrever(f"ffmpeg: {self._ffmpeg}")
            self._escrever("Arraste pastas ou arquivos para a janela, ou use os botões acima.")
        self._atualizar_estado()

        # Só depois que a janela aparece, para o diálogo abrir por cima dela.
        if "pasta_saida" not in self._config and not self._erro_ffmpeg:
            QTimer.singleShot(0, self._primeira_configuracao)

    # ------------------------------------------------------------------ UI

    def _grupo_arquivos(self) -> QGroupBox:
        grupo = QGroupBox("Arquivos")
        layout = QVBoxLayout(grupo)

        self.tabela = QTableWidget(0, len(COLUNAS))
        self.tabela.setHorizontalHeaderLabels(COLUNAS)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.verticalHeader().setVisible(False)
        cabecalho = self.tabela.horizontalHeader()
        cabecalho.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        cabecalho.setSectionResizeMode(COL_ARQUIVO, QHeaderView.ResizeMode.Stretch)
        cabecalho.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Interactive)
        cabecalho.resizeSection(COL_STATUS, 150)
        self.tabela.setMinimumHeight(160)
        layout.addWidget(self.tabela, stretch=1)
        QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), self.tabela, self._remover_selecionados)

        self.lbl_resumo = QLabel("Arraste pastas ou arquivos para a janela.")
        self.lbl_resumo.setObjectName("subtitle")
        self.lbl_resumo.setWordWrap(True)
        layout.addWidget(self.lbl_resumo)

        botoes = QHBoxLayout()
        self.btn_add_arquivos = QPushButton("Adicionar arquivos")
        self.btn_add_arquivos.clicked.connect(self._escolher_arquivos)
        self.btn_add_pasta = QPushButton("Adicionar pasta")
        self.btn_add_pasta.clicked.connect(self._escolher_pasta_entrada)
        self.btn_remover = QPushButton("Remover selecionados")
        self.btn_remover.setObjectName("secondary")
        self.btn_remover.clicked.connect(self._remover_selecionados)
        self.btn_limpar = QPushButton("Limpar lista")
        self.btn_limpar.setObjectName("danger")
        self.btn_limpar.clicked.connect(self._limpar)
        botoes.addWidget(self.btn_add_arquivos)
        botoes.addWidget(self.btn_add_pasta)
        botoes.addWidget(self.btn_remover)
        botoes.addWidget(self.btn_limpar)
        botoes.addStretch(1)
        layout.addLayout(botoes)
        return grupo

    def _grupo_opcoes(self) -> QGroupBox:
        grupo = QGroupBox("Opções")
        grade = QGridLayout(grupo)
        grade.setColumnStretch(1, 1)

        # Nada de QLabel com quebra de linha segurando caminho: num grid o
        # rótulo quebrado reporta altura mínima de uma linha e acaba cortado.
        grade.addWidget(QLabel("Preset"), 0, 0)
        self.combo_preset = QComboBox()
        for preset in PRESETS:
            self.combo_preset.addItem(preset.label, preset.key)
        indice = self.combo_preset.findData(self._config.get("preset", DEFAULT_PRESET))
        self.combo_preset.setCurrentIndex(max(0, indice))
        self.combo_preset.currentIndexChanged.connect(self._ao_mudar_preset)
        grade.addWidget(self.combo_preset, 0, 1)

        self.lbl_preset = QLabel("")
        self.lbl_preset.setObjectName("subtitle")
        grade.addWidget(self.lbl_preset, 1, 1)

        grade.addWidget(QLabel("Saída"), 2, 0)
        linha_saida = QHBoxLayout()
        self.campo_saida = QLineEdit()
        self.campo_saida.setReadOnly(True)
        self.btn_saida = QPushButton("Escolher pasta padrão")
        self.btn_saida.setObjectName("secondary")
        self.btn_saida.clicked.connect(self._escolher_pasta_saida)
        self.btn_saida_lado = QPushButton("Ao lado da origem")
        self.btn_saida_lado.setObjectName("secondary")
        self.btn_saida_lado.clicked.connect(self._usar_lado_da_origem)
        linha_saida.addWidget(self.campo_saida, stretch=1)
        linha_saida.addWidget(self.btn_saida)
        linha_saida.addWidget(self.btn_saida_lado)
        grade.addLayout(linha_saida, 2, 1)

        self.lbl_saida_exemplo = QLabel("")
        self.lbl_saida_exemplo.setObjectName("subtitle")
        grade.addWidget(self.lbl_saida_exemplo, 3, 1)

        self._ao_mudar_preset()
        return grupo

    def _grupo_progresso(self) -> QGroupBox:
        grupo = QGroupBox("Andamento")
        layout = QVBoxLayout(grupo)

        self.lbl_status = QLabel("Pronto para converter.")
        self.lbl_status.setObjectName("status")
        self.lbl_status.setWordWrap(True)
        layout.addWidget(self.lbl_status)

        self.barra_arquivo = QProgressBar()
        self.barra_arquivo.setFormat("Arquivo atual: %p%")
        layout.addWidget(self.barra_arquivo)

        self.barra_fila = QProgressBar()
        self.barra_fila.setFormat("Fila: %p%")
        layout.addWidget(self.barra_fila)
        return grupo

    def _grupo_log(self) -> QGroupBox:
        grupo = QGroupBox("Registro")
        layout = QVBoxLayout(grupo)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(90)
        # Senão o QPlainTextEdit engole o drop e cola o caminho como texto.
        self.log.setAcceptDrops(False)
        layout.addWidget(self.log)
        return grupo

    def _barra_acoes(self) -> QHBoxLayout:
        linha = QHBoxLayout()
        linha.addStretch(1)
        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.setObjectName("danger")
        self.btn_cancelar.clicked.connect(self._cancelar)
        self.btn_converter = QPushButton("Converter")
        self.btn_converter.clicked.connect(self._converter)
        linha.addWidget(self.btn_cancelar)
        linha.addWidget(self.btn_converter)
        return linha

    # --------------------------------------------------------- configuração

    def _primeira_configuracao(self) -> None:
        resposta = QMessageBox.question(
            self,
            "Pasta padrão de saída",
            "Para cada pasta de origem o programa cria uma pasta "
            f"\"<nome>{transcoder.OUTPUT_SUFFIX}\" com os arquivos convertidos.\n\n"
            "Quer escolher agora onde essas pastas serão criadas? A escolha fica "
            "salva e pode ser alterada depois em \"Escolher pasta padrão\".\n\n"
            "Se responder Não, cada pasta convertida é criada ao lado da pasta de origem.",
        )
        if resposta == QMessageBox.StandardButton.Yes:
            self._escolher_pasta_saida()
        else:
            self._definir_raiz(None)

    def _escolher_pasta_saida(self) -> None:
        inicio = self._raiz or self._config.get("ultima_pasta") or str(Path.home())
        pasta = QFileDialog.getExistingDirectory(self, "Pasta padrão de saída", str(inicio))
        if pasta:
            self._definir_raiz(Path(pasta))

    def _usar_lado_da_origem(self) -> None:
        self._definir_raiz(None)

    def _definir_raiz(self, raiz: Path | None) -> None:
        self._raiz = raiz
        self._config["pasta_saida"] = str(raiz) if raiz else None
        salvar_config(self._config)
        self._atualizar_estado()

    def _ao_mudar_preset(self) -> None:
        self.lbl_preset.setText(self._preset().description)
        self._config["preset"] = self._preset().key
        salvar_config(self._config)
        self._atualizar_resumo()

    def _preset(self) -> Preset:
        return PRESET_BY_KEY[self.combo_preset.currentData()]

    # ------------------------------------------------------------- arquivos

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - assinatura do Qt
        if event.mimeData().hasUrls() and self._worker is None:
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802 - assinatura do Qt
        if self._worker is not None:
            return
        caminhos = [Path(url.toLocalFile()) for url in event.mimeData().urls() if url.isLocalFile()]
        self._adicionar(caminhos)
        event.acceptProposedAction()

    def _pasta_inicial(self) -> str:
        return self._config.get("ultima_pasta") or str(Path.home())

    def _lembrar_pasta(self, caminho: Path) -> None:
        self._config["ultima_pasta"] = str(caminho if caminho.is_dir() else caminho.parent)
        salvar_config(self._config)

    def _escolher_arquivos(self) -> None:
        caminhos, _ = QFileDialog.getOpenFileNames(
            self, "Adicionar arquivos", self._pasta_inicial(), FILTRO_ARQUIVOS
        )
        if caminhos:
            self._lembrar_pasta(Path(caminhos[0]))
            self._adicionar([Path(c) for c in caminhos])

    def _escolher_pasta_entrada(self) -> None:
        pasta = QFileDialog.getExistingDirectory(self, "Adicionar pasta", self._pasta_inicial())
        if pasta:
            self._lembrar_pasta(Path(pasta))
            self._adicionar([Path(pasta)])

    def _adicionar(self, caminhos: list[Path]) -> None:
        if self._erro_ffmpeg:
            QMessageBox.critical(self, "ffmpeg não encontrado", self._erro_ffmpeg)
            return
        na_fila = {info.path for info in self._fila}
        novos = [c for c in transcoder.collect(caminhos) if c not in na_fila]
        if not novos:
            if caminhos:
                self._escrever("Nada novo: nenhum vídeo nas pastas ou já estão na fila.")
            return

        for caminho in novos:
            self._fila.append(MediaInfo(path=caminho, status=LENDO))
            linha = self.tabela.rowCount()
            self.tabela.insertRow(linha)
            self._preencher_linha(linha, self._fila[-1])
        self._pendentes += novos
        self._iniciar_leitura()
        self._atualizar_estado()

    def _preencher_linha(self, linha: int, info: MediaInfo) -> None:
        lido = info.status != LENDO and info.readable
        rng = ("full" if info.full_range else "limited") if lido else "?"
        valores = (
            info.path.name,
            info.codec,
            info.resolution,
            f"{info.fps:g}" if info.fps else "?",
            info.chroma,
            info.depth,
            rng,
            info.status,
        )
        for coluna, valor in enumerate(valores):
            item = QTableWidgetItem(valor)
            if coluna == COL_ARQUIVO:
                item.setToolTip(str(info.path))
            else:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            # Cores só da paleta do tema: azul para o que o Resolve não abre
            # (é o que este programa existe para resolver) e o tom de erro
            # para o que o ffprobe não leu.
            if info.flagged and coluna in (COL_CODEC, COL_CHROMA, COL_BITS, COL_STATUS):
                item.setForeground(QBrush(QColor(COLORS["accent"])))
            elif not info.readable:
                item.setForeground(QBrush(QColor(COLORS["danger_text"])))
            self.tabela.setItem(linha, coluna, item)

    def _definir_status(self, linha: int, texto: str, erro: bool = False) -> None:
        item = self.tabela.item(linha, COL_STATUS)
        if item is None:
            return
        item.setText(texto)
        if erro:
            item.setForeground(QBrush(QColor(COLORS["danger_text"])))

    def _iniciar_leitura(self) -> None:
        if self._leitor is not None or not self._pendentes:
            return
        self._leitor = LeitorWorker(self._ffprobe, list(self._pendentes))
        self._pendentes.clear()
        self._leitor.lido.connect(self._ao_ler)
        self._leitor.finished.connect(self._ao_leitor_encerrar)
        self._leitor.start()

    def _ao_ler(self, info: MediaInfo) -> None:
        # Procura pelo caminho: o usuário pode ter removido linhas enquanto
        # o ffprobe rodava, o que desalinha qualquer índice guardado.
        linha = next((i for i, atual in enumerate(self._fila) if atual.path == info.path), None)
        if linha is None:
            return
        self._fila[linha] = info
        self._preencher_linha(linha, info)
        self._atualizar_resumo()

    def _ao_leitor_encerrar(self) -> None:
        leitor, self._leitor = self._leitor, None
        if leitor is not None:
            leitor.deleteLater()
        self._iniciar_leitura()  # o que chegou enquanto lia
        self._atualizar_estado()

    def _remover_selecionados(self) -> None:
        if self._worker is not None:
            return
        linhas = sorted({indice.row() for indice in self.tabela.selectedIndexes()}, reverse=True)
        for linha in linhas:
            self.tabela.removeRow(linha)
            del self._fila[linha]
        self._atualizar_estado()

    def _limpar(self) -> None:
        if self._worker is not None:
            return
        self.tabela.setRowCount(0)
        self._fila.clear()
        self._pendentes.clear()
        self.barra_arquivo.setValue(0)
        self.barra_fila.setValue(0)
        self.lbl_status.setText("Pronto para converter.")
        self._atualizar_estado()

    # ------------------------------------------------------------ conversão

    def _converter(self) -> None:
        if not self._fila or self._worker or self._leitor:
            return
        if self._raiz is not None and not self._raiz.is_dir():
            QMessageBox.warning(
                self,
                "Pasta de saída",
                f"A pasta padrão de saída não existe:\n{self._raiz}\n\n"
                "Escolha outra em \"Escolher pasta padrão\" ou use \"Salvar ao lado da origem\".",
            )
            return

        self.barra_arquivo.setValue(0)
        self.barra_fila.setValue(0)
        self._escrever("")
        self._escrever(f"Convertendo {len(self._fila)} arquivo(s) com {self._preset().label}.")

        self._total = len(self._fila)
        self._worker = ConversorWorker(self._ffmpeg, list(self._fila), self._raiz, self._preset())
        self._worker.arquivo_iniciado.connect(self._ao_iniciar)
        self._worker.progresso.connect(self._ao_progredir)
        self._worker.arquivo_terminado.connect(self._ao_terminar_arquivo)
        self._worker.fila_terminada.connect(self._ao_terminar_fila)
        # Só solta a referência depois que a thread realmente encerrou: largar
        # o objeto ainda rodando faz o Qt abortar o programa.
        self._worker.finished.connect(self._ao_thread_encerrar)
        self._worker.start()
        self._atualizar_estado()

    def _cancelar(self) -> None:
        if self._worker:
            self._worker.cancelar()
            self.lbl_status.setText("Cancelando...")
            self.btn_cancelar.setEnabled(False)

    def _ao_iniciar(self, indice: int, nome: str, destino: str) -> None:
        self.lbl_status.setText(f"Convertendo {indice + 1} de {self._total}: {nome}")
        self.barra_arquivo.setValue(0)
        self._definir_status(indice, "convertendo")
        self._escrever(f"--- {nome} -> {destino}")

    def _ao_progredir(self, indice: int, fracao: float, velocidade: str) -> None:
        self.barra_arquivo.setValue(int(fracao * 100))
        self.barra_fila.setValue(int((indice + fracao) / max(1, self._total) * 100))
        if velocidade:
            nome = self._fila[indice].path.name
            self.lbl_status.setText(
                f"Convertendo {indice + 1} de {self._total}: {nome} ({velocidade})"
            )

    def _ao_terminar_arquivo(self, indice: int, estado: str, mensagem: str) -> None:
        nome = self._fila[indice].path.name
        if estado == "ok":
            try:
                tamanho = transcoder.human_size(Path(mensagem).stat().st_size)
            except OSError:
                tamanho = "?"
            self._definir_status(indice, f"pronto · {tamanho}")
            self._escrever(f"Pronto: {mensagem} ({tamanho})")
        elif estado == "existe":
            self._definir_status(indice, "já existe")
            self._escrever(f"Já existe, pulado: {mensagem}")
        elif estado == "pulado":
            self._definir_status(indice, "pulado")
            self._escrever(f"Pulado ({nome}): {mensagem}")
        elif estado == "cancelado":
            self._definir_status(indice, "cancelado")
            self._escrever(f"Cancelado: {nome}")
        else:
            self._definir_status(indice, "falhou", erro=True)
            self._escrever(f"Falhou ({nome}): {mensagem}")
        if estado != "cancelado":
            self.barra_fila.setValue(int((indice + 1) / max(1, self._total) * 100))

    def _ao_terminar_fila(self, ok: int, falhas: int, pulados: int, cancelado: bool) -> None:
        partes = [f"{ok} convertido(s)"]
        if falhas:
            partes.append(f"{falhas} com falha")
        if pulados:
            partes.append(f"{pulados} pulado(s)")
        resumo = ", ".join(partes)
        if cancelado:
            self.lbl_status.setText(f"Cancelado. {resumo} antes de parar.")
        else:
            self.barra_fila.setValue(100)
            self.lbl_status.setText(f"Terminado: {resumo}.")
        self._escrever(self.lbl_status.text())

    def _ao_thread_encerrar(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            worker.deleteLater()
        self.btn_cancelar.setEnabled(True)
        self._atualizar_estado()

    # ---------------------------------------------------------------- estado

    def _escrever(self, texto: str) -> None:
        self.log.appendPlainText(texto)

    def _atualizar_resumo(self) -> None:
        if not self._fila:
            self.lbl_resumo.setText("Arraste pastas ou arquivos para a janela.")
            return
        lendo = sum(1 for info in self._fila if info.status == LENDO)
        legiveis = [info for info in self._fila if info.readable and info.status != LENDO]
        sinalizados = sum(1 for info in legiveis if info.flagged)
        preset = self._preset()
        estimado = sum(transcoder.estimate_output(info, preset) for info in legiveis)
        texto = f"{len(self._fila)} arquivo(s)"
        if lendo:
            texto += f" · lendo {lendo}..."
        texto += f" · {sinalizados} que o Resolve free não abre"
        if estimado:
            texto += f" · saída estimada em {transcoder.human_size(estimado)} com {preset.label}"
        self.lbl_resumo.setText(texto)

    def _atualizar_estado(self) -> None:
        rodando = self._worker is not None
        lendo = self._leitor is not None
        sem_ffmpeg = bool(self._erro_ffmpeg)

        self.btn_converter.setEnabled(bool(self._fila) and not rodando and not lendo and not sem_ffmpeg)
        self.btn_cancelar.setEnabled(rodando)
        # Mexer na fila durante a conversão desalinharia os índices que o
        # worker está usando para reportar o andamento.
        for botao in (self.btn_add_arquivos, self.btn_add_pasta):
            botao.setEnabled(not rodando and not sem_ffmpeg)
        for botao in (self.btn_remover, self.btn_limpar):
            botao.setEnabled(not rodando and bool(self._fila))
        for widget in (self.combo_preset, self.btn_saida, self.btn_saida_lado):
            widget.setEnabled(not rodando)

        if self._raiz is not None:
            self.campo_saida.setText(str(self._raiz))
            self.campo_saida.setToolTip(str(self._raiz))
        else:
            self.campo_saida.setText("Sem pasta padrão: ao lado de cada pasta de origem")
            self.campo_saida.setToolTip("")
        self.btn_saida_lado.setEnabled(not rodando and self._raiz is not None)

        if self._fila:
            primeiro = self._fila[0].path
            destino = transcoder.output_path(primeiro, self._raiz)
            onde = "na pasta padrão" if self._raiz is not None else f"ao lado da pasta {primeiro.parent.name}"
            self.lbl_saida_exemplo.setText(
                f"Ex.: {primeiro.name} vai para {destino.parent.name}/{destino.name}, {onde}"
            )
            self.lbl_saida_exemplo.setToolTip(str(destino))
        else:
            self.lbl_saida_exemplo.setText(
                f"Os convertidos ficam em uma pasta \"<nome da pasta de origem>{transcoder.OUTPUT_SUFFIX}\"."
            )
            self.lbl_saida_exemplo.setToolTip("")
        self._atualizar_resumo()

    def closeEvent(self, event) -> None:  # noqa: N802 - assinatura do Qt
        if self._worker:
            resposta = QMessageBox.question(
                self, "Conversão em andamento", "Cancelar a conversão e sair?"
            )
            if resposta != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self._worker.cancelar()
            self._worker.wait(10000)
        if self._leitor:
            self._leitor.cancelar()
            self._leitor.wait(5000)
        event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    set_default_font(app)
    janela = Janela()
    janela.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
