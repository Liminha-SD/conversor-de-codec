#!/usr/bin/env python3
"""
resolve-prep - TUI para transcodificar midia que o DaVinci Resolve (free) nao
decodifica (HEVC 4:2:2 10-bit da Canon R6 Mark II, entre outros) para H.264 ou
H.265 all-intra, sempre em MP4.

A saida e sempre .mp4 de verdade: o muxer mp4 do ffmpeg so aceita H.264/H.265
(ProRes e DNxHR exigem .mov). All-intra deixa o scrub e o corte fluidos no
Resolve, quase como um codec intermediario, num arquivo bem menor.

Roda sozinho: se nao estiver dentro de uma venv com as dependencias, cria
".venv" ao lado deste arquivo, instala o que precisa e se reexecuta la dentro.

Requer ffmpeg e ffprobe no PATH.
    Windows: de dois cliques em run.bat - ele instala Python e ffmpeg se
             faltarem e sobe o programa. Ou: winget install Gyan.FFmpeg
    Arch:    sudo pacman -S ffmpeg
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Bootstrap da venv - precisa rodar antes de qualquer import de terceiros
# --------------------------------------------------------------------------

REQUIREMENTS = ["textual>=1.0"]
_BOOTSTRAP_FLAG = "RESOLVE_PREP_BOOTSTRAPPED"


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _in_venv() -> bool:
    return sys.prefix != sys.base_prefix


def _deps_ok() -> bool:
    try:
        import textual  # noqa: F401
    except ImportError:
        return False
    return True


def _ensure_venv() -> None:
    """Garante que estamos rodando numa venv com as dependencias instaladas."""
    if _in_venv() and _deps_ok():
        return

    script = Path(__file__).resolve()
    venv_dir = script.parent / ".venv"
    py = _venv_python(venv_dir)

    if os.environ.get(_BOOTSTRAP_FLAG):
        sys.exit(
            "resolve-prep: a venv foi criada mas as dependencias nao subiram.\n"
            f'  Rode manualmente: "{py}" -m pip install {" ".join(REQUIREMENTS)}'
        )

    if not py.exists():
        print(f"[resolve-prep] criando venv em {venv_dir}")
        try:
            subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
        except subprocess.CalledProcessError:
            sys.exit(
                "resolve-prep: falhei em criar a venv.\n"
                "  No Debian/Ubuntu instale o pacote python3-venv.\n"
                "  No Windows reinstale o Python marcando 'Add to PATH'."
            )

    print("[resolve-prep] instalando dependencias")
    subprocess.run([str(py), "-m", "pip", "install", "-q", "--upgrade", "pip"], check=False)
    try:
        subprocess.run([str(py), "-m", "pip", "install", "-q", *REQUIREMENTS], check=True)
    except subprocess.CalledProcessError:
        sys.exit("resolve-prep: pip falhou. Verifique sua conexao e tente de novo.")

    env = {**os.environ, _BOOTSTRAP_FLAG: "1"}
    print("[resolve-prep] subindo a interface\n")
    raise SystemExit(subprocess.call([str(py), str(script), *sys.argv[1:]], env=env))


_ensure_venv()

# --------------------------------------------------------------------------
# A partir daqui ja estamos dentro da venv
# --------------------------------------------------------------------------

import json  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
from dataclasses import dataclass  # noqa: E402

from textual import on, work  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.containers import Horizontal, Vertical  # noqa: E402
from textual.widgets import (  # noqa: E402
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    ProgressBar,
    RichLog,
    Select,
    Static,
)
from textual.worker import get_current_worker  # noqa: E402

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".mkv", ".m4v", ".avi"}
OUTPUT_EXT = ".mp4"


# --------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    args: tuple[str, ...]
    mbps: int  # estimativa de bitrate em 1080p59.94, para o calculo de espaco
    tag: str = ""  # fourcc opcional; vazio = o padrao do muxer


PRESETS: tuple[Preset, ...] = (
    Preset(
        "h264_hq",
        "H.264 HQ - 8 bits 4:2:0, all-intra, padrao para o Resolve free",
        (
            "-c:v", "libx264", "-preset", "medium", "-crf", "12",
            # -g 1 torna todo frame um keyframe: e o que da o scrub leve.
            "-g", "1", "-profile:v", "high", "-pix_fmt", "yuv420p",
        ),
        90,
    ),
    Preset(
        "h264_max",
        "H.264 Max - 8 bits 4:2:0, all-intra quase sem perda, grading pesado",
        (
            "-c:v", "libx264", "-preset", "slow", "-crf", "8",
            "-g", "1", "-profile:v", "high", "-pix_fmt", "yuv420p",
        ),
        180,
    ),
    Preset(
        "h265_hq",
        "H.265 HQ - 8 bits 4:2:0, all-intra, ~metade do tamanho do H.264",
        (
            "-c:v", "libx265", "-preset", "medium", "-crf", "16",
            "-g", "1", "-pix_fmt", "yuv420p",
        ),
        50,
        # hev1 (o padrao) trava em varios players; hvc1 e o que abre em todos.
        "hvc1",
    ),
    Preset(
        "h265_10bit",
        "H.265 10 bits 4:2:0 - preserva os 10 bits (o Resolve free pode recusar)",
        (
            "-c:v", "libx265", "-preset", "medium", "-crf", "16",
            "-g", "1", "-pix_fmt", "yuv420p10le",
        ),
        60,
        "hvc1",
    ),
    Preset(
        "h264_proxy",
        "H.264 Proxy - long-GOP leve, so para corte e visualizacao",
        (
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
        ),
        12,
    ),
)

PRESET_BY_KEY = {p.key: p for p in PRESETS}


# --------------------------------------------------------------------------
# Leitura de metadados
# --------------------------------------------------------------------------

PIX_FMT = {
    "yuv420p": ("4:2:0", "8"),
    "yuvj420p": ("4:2:0", "8"),
    "yuv422p": ("4:2:2", "8"),
    "yuvj422p": ("4:2:2", "8"),
    "yuv444p": ("4:4:4", "8"),
    "yuv420p10le": ("4:2:0", "10"),
    "yuv422p10le": ("4:2:2", "10"),
    "yuv444p10le": ("4:4:4", "10"),
    "yuv422p12le": ("4:2:2", "12"),
    "yuv444p12le": ("4:4:4", "12"),
}

# Chroma/profundidade que o Resolve free nao decodifica no Windows.
NEEDS_TRANSCODE = {("4:2:2", "10"), ("4:2:2", "12"), ("4:4:4", "10"), ("4:4:4", "12")}

# Um valor que o ffprobe reporta mas o filtro setparams nao conhece derruba a
# conversao inteira, entao so repassamos o que esta nestas listas
# (ffmpeg -h filter=setparams). Fora delas, fica o default bt709.
COLOR_VALUES = {
    "colorspace": {
        "gbr", "bt709", "fcc", "bt470bg", "smpte170m", "smpte240m", "ycgco",
        "ycgco-re", "ycgco-ro", "bt2020nc", "bt2020c", "smpte2085",
        "chroma-derived-nc", "chroma-derived-c", "ictcp", "ipt-c2",
    },
    "primaries": {
        "bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "film",
        "bt2020", "smpte428", "smpte431", "smpte432", "jedec-p22", "ebu3213",
    },
    "trc": {
        "bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "linear",
        "log100", "log316", "iec61966-2-4", "bt1361e", "iec61966-2-1",
        "bt2020-10", "bt2020-12", "smpte2084", "smpte428", "arib-std-b67",
    },
}


@dataclass
class MediaInfo:
    path: Path
    codec: str = "?"
    width: int = 0
    height: int = 0
    fps: float = 0.0
    chroma: str = "?"
    depth: str = "?"
    full_range: bool = False
    colorspace: str = "bt709"
    primaries: str = "bt709"
    trc: str = "bt709"
    duration: float = 0.0
    timecode: str | None = None
    status: str = "pendente"

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}" if self.width else "?"

    @property
    def flagged(self) -> bool:
        """True se o Resolve free provavelmente nao abre este arquivo."""
        return (self.chroma, self.depth) in NEEDS_TRANSCODE


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", creationflags=CREATE_NO_WINDOW
    )


def probe(ffprobe: str, path: Path) -> MediaInfo:
    info = MediaInfo(path=path)
    res = _run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    )
    if res.returncode != 0:
        info.status = "ilegivel"
        return info

    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        info.status = "ilegivel"
        return info

    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        info.status = "sem video"
        return info

    info.codec = video.get("codec_name", "?").upper()
    info.width = int(video.get("width") or 0)
    info.height = int(video.get("height") or 0)

    num, _, den = (video.get("r_frame_rate") or "0/1").partition("/")
    try:
        info.fps = round(int(num) / int(den), 3) if int(den) else 0.0
    except (ValueError, ZeroDivisionError):
        info.fps = 0.0

    info.chroma, info.depth = PIX_FMT.get(video.get("pix_fmt", ""), ("?", "?"))
    info.full_range = video.get("color_range") == "pc"

    for attr, key in (("colorspace", "color_space"), ("primaries", "color_primaries"), ("trc", "color_transfer")):
        val = video.get(key)
        if val in COLOR_VALUES[attr]:
            setattr(info, attr, val)

    try:
        info.duration = float(fmt.get("duration") or video.get("duration") or 0.0)
    except ValueError:
        info.duration = 0.0

    tc = (fmt.get("tags") or {}).get("timecode")
    if not tc:
        for s in streams:
            tc = (s.get("tags") or {}).get("timecode")
            if tc:
                break
    info.timecode = tc

    return info


def build_cmd(ffmpeg: str, info: MediaInfo, dst: Path, preset: Preset) -> list[str]:
    # -loglevel error para o x265 nao despejar linhas de info no stdout, que
    # o parser de progresso trataria como erro.
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(info.path)]

    filters = []
    # Canon grava full range (0-1023). H.264/H.265 de edicao sao video range
    # (64-940). Sem esta conversao o preto esmaga e o branco estoura no Resolve.
    if info.full_range:
        filters.append("scale=in_range=full:out_range=limited")
    # As opcoes -color_* abaixo nao bastam: o filtergraph propaga as
    # propriedades do frame e sobrescreve o que o encoder recebeu, e o arquivo
    # sai com primaries/trc "unknown". setparams etiqueta na saida do grafo.
    filters.append(
        "setparams=range=tv"
        f":colorspace={info.colorspace}"
        f":color_primaries={info.primaries}"
        f":color_trc={info.trc}"
    )
    cmd += ["-vf", ",".join(filters)]

    cmd += list(preset.args)
    if preset.tag:
        cmd += ["-tag:v", preset.tag]
    cmd += [
        "-color_range", "tv",
        "-colorspace", info.colorspace,
        "-color_primaries", info.primaries,
        "-color_trc", info.trc,
        # mp4 nao aceita PCM; AAC 320k e transparente o bastante para edicao.
        "-c:a", "aac",
        "-b:a", "320k",
        "-ar", "48000",
    ]
    if info.timecode:
        cmd += ["-timecode", info.timecode]
    # write_colr grava as tags de cor no container; sem isso o Resolve pode
    # ignorar o bt709 e interpretar a imagem errado.
    cmd += ["-movflags", "+write_colr+faststart", "-progress", "pipe:1", "-nostats", str(dst)]
    return cmd


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def estimate_output(info: MediaInfo, preset: Preset) -> float:
    """Bytes estimados, escalando o bitrate de referencia pela area e pelo fps."""
    if not info.duration or not info.width:
        return 0.0
    area_ratio = (info.width * info.height) / (1920 * 1080)
    fps_ratio = (info.fps / 59.94) if info.fps else 1.0
    mbps = preset.mbps * area_ratio * fps_ratio
    return mbps * 1_000_000 / 8 * info.duration


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

TIME_RE = re.compile(r"out_time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")

# (rotulo exibido, chave usada em update_cell)
COLUMNS = (
    ("Arquivo", "file"),
    ("Codec", "codec"),
    ("Resolução", "res"),
    ("FPS", "fps"),
    ("Chroma", "chroma"),
    ("Bits", "depth"),
    ("Range", "range"),
    ("Status", "status"),
)


class ResolvePrep(App):
    TITLE = "resolve-prep"
    SUB_TITLE = "HEVC 4:2:2 10-bit -> H.264 / H.265 all-intra em MP4"

    CSS = """
    Screen { layout: vertical; }

    #config { height: auto; padding: 1 2 0 2; }
    .row { height: 3; align-vertical: middle; }
    .lbl { width: 10; content-align: right middle; padding-right: 2; color: $text-muted; }
    #config Input { width: 1fr; }
    #preset { width: 1fr; }
    #scan { margin-left: 2; min-width: 14; }

    #files { height: 1fr; min-height: 8; margin: 1 2; }

    #progress-row { height: 3; padding: 0 2; align-vertical: middle; }
    #bar { width: 1fr; }
    #progress-label { width: auto; padding-left: 2; color: $text-muted; }

    #log { height: 10; margin: 0 2; border: round $panel; padding: 0 1; }

    #actions { height: 3; padding: 0 2 1 2; }
    #actions Button { margin-right: 2; min-width: 16; }
    """

    BINDINGS = [
        ("s", "scan", "Escanear"),
        ("c", "convert", "Converter"),
        ("x", "cancel", "Cancelar"),
        ("q", "quit", "Sair"),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.ffmpeg = shutil.which("ffmpeg") or ""
        self.ffprobe = shutil.which("ffprobe") or ""
        self.files: list[MediaInfo] = []
        self.row_keys: list = []
        self._proc: subprocess.Popen | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="config"):
            with Horizontal(classes="row"):
                yield Label("Entrada", classes="lbl")
                yield Input(placeholder=r"pasta com os arquivos brutos", id="src")
            with Horizontal(classes="row"):
                yield Label("Saída", classes="lbl")
                yield Input(placeholder=r"pasta onde salvar os convertidos", id="dst")
            with Horizontal(classes="row"):
                yield Label("Preset", classes="lbl")
                yield Select(
                    [(p.label, p.key) for p in PRESETS],
                    value="h264_hq",
                    allow_blank=False,
                    id="preset",
                )
                yield Button("Escanear", variant="primary", id="scan")
        yield DataTable(id="files", zebra_stripes=True, cursor_type="row")
        with Horizontal(id="progress-row"):
            yield ProgressBar(total=100, show_eta=False, id="bar")
            yield Static("parado", id="progress-label")
        yield RichLog(id="log", highlight=True, markup=True, wrap=True)
        with Horizontal(id="actions"):
            yield Button("Converter", variant="success", id="convert", disabled=True)
            yield Button("Cancelar", variant="error", id="cancel", disabled=True)
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#files", DataTable)
        for label, key in COLUMNS:
            table.add_column(label, key=key)
        log = self.query_one("#log", RichLog)

        if not self.ffmpeg or not self.ffprobe:
            log.write("[bold red]ffmpeg/ffprobe não encontrados no PATH.[/]")
            log.write("  Windows: [bold]winget install Gyan.FFmpeg[/]")
            log.write("  Arch:    [bold]sudo pacman -S ffmpeg[/]")
            self.query_one("#scan", Button).disabled = True
        else:
            log.write(f"ffmpeg em [dim]{self.ffmpeg}[/]")
            log.write("Informe a pasta de entrada e pressione [bold]Escanear[/].")

        self.query_one("#src", Input).focus()

    # -- helpers ----------------------------------------------------------

    def write_log(self, msg: str) -> None:
        """Escreve no painel de log. Nao pode se chamar 'log': App.log e do Textual."""
        self.query_one("#log", RichLog).write(msg)

    def current_preset(self) -> Preset:
        return PRESET_BY_KEY[self.query_one("#preset", Select).value]

    def set_label(self, text: str) -> None:
        self.query_one("#progress-label", Static).update(text)

    def set_row(self, index: int, column: str, value: str) -> None:
        table = self.query_one("#files", DataTable)
        table.update_cell(self.row_keys[index], column, value)

    @on(Input.Changed, "#src")
    def suggest_output(self, event: Input.Changed) -> None:
        """Sugere uma pasta irmã quando a saída ainda está vazia."""
        dst = self.query_one("#dst", Input)
        if dst.value.strip():
            return
        src = Path(event.value.strip().strip('"'))
        if event.value.strip() and src.name:
            dst.placeholder = str(src.parent / f"{src.name} - convertido")

    # -- escanear ---------------------------------------------------------

    def action_scan(self) -> None:
        self.scan()

    @on(Button.Pressed, "#scan")
    def _on_scan(self) -> None:
        self.scan()

    def scan(self) -> None:
        raw = self.query_one("#src", Input).value.strip().strip('"')
        if not raw:
            self.notify("Informe a pasta de entrada.", severity="warning")
            return
        src = Path(raw).expanduser()
        if not src.is_dir():
            self.notify(f"Pasta não encontrada: {src}", severity="error")
            return

        found = sorted(p for p in src.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTS)
        if not found:
            self.notify("Nenhum arquivo de vídeo nessa pasta.", severity="warning")
            return

        self.write_log(f"\n[bold]Lendo {len(found)} arquivo(s)…[/]")
        self.scan_worker(found)

    @work(thread=True, exclusive=True, group="scan")
    def scan_worker(self, paths: list[Path]) -> None:
        worker = get_current_worker()
        infos: list[MediaInfo] = []
        for path in paths:
            if worker.is_cancelled:
                return
            infos.append(probe(self.ffprobe, path))
        self.call_from_thread(self.populate, infos)

    def populate(self, infos: list[MediaInfo]) -> None:
        self.files = infos
        self.row_keys.clear()

        table = self.query_one("#files", DataTable)
        table.clear()

        preset = self.current_preset()
        total_out = 0.0
        flagged = 0

        for info in infos:
            rng = "full" if info.full_range else "limited"
            if info.flagged:
                flagged += 1
                info.status = "converter"
                codec_cell = f"[bold yellow]{info.codec}[/]"
                chroma_cell = f"[bold yellow]{info.chroma}[/]"
            else:
                info.status = "ok no Resolve"
                codec_cell, chroma_cell = info.codec, info.chroma

            key = table.add_row(
                info.path.name,
                codec_cell,
                info.resolution,
                f"{info.fps:g}" if info.fps else "?",
                chroma_cell,
                info.depth,
                rng,
                info.status,
            )
            self.row_keys.append(key)
            total_out += estimate_output(info, preset)

        self.write_log(
            f"{len(infos)} arquivo(s) · [bold yellow]{flagged}[/] precisam de conversão · "
            f"saída estimada em [bold]{human_size(total_out)}[/] com {preset.key}"
        )
        if flagged:
            self.write_log("[dim]Amarelo = chroma/profundidade que o Resolve free não decodifica.[/]")

        self.query_one("#convert", Button).disabled = not infos

    @on(Select.Changed, "#preset")
    def _on_preset(self) -> None:
        if self.files:
            self.populate(self.files)

    # -- converter --------------------------------------------------------

    def action_convert(self) -> None:
        self.start_conversion()

    @on(Button.Pressed, "#convert")
    def _on_convert(self) -> None:
        self.start_conversion()

    def start_conversion(self) -> None:
        if not self.files:
            self.notify("Escaneie uma pasta primeiro.", severity="warning")
            return

        raw = self.query_one("#dst", Input).value.strip().strip('"')
        if not raw:
            raw = self.query_one("#dst", Input).placeholder
        if not raw or raw.startswith("pasta onde"):
            self.notify("Informe a pasta de saída.", severity="warning")
            return

        dst_dir = Path(raw).expanduser()
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.notify(f"Não consegui criar a pasta de saída: {exc}", severity="error")
            return

        self.query_one("#convert", Button).disabled = True
        self.query_one("#scan", Button).disabled = True
        self.query_one("#cancel", Button).disabled = False
        self.write_log(f"\n[bold]Saída:[/] {dst_dir}")

        self.convert_worker(dst_dir, self.current_preset())

    @work(thread=True, exclusive=True, group="convert")
    def convert_worker(self, dst_dir: Path, preset: Preset) -> None:
        worker = get_current_worker()
        bar = self.query_one("#bar", ProgressBar)
        total = len(self.files)
        done = failed = skipped = 0

        for index, info in enumerate(self.files):
            if worker.is_cancelled:
                break

            if info.status == "ilegivel":
                skipped += 1
                continue

            dst = dst_dir / f"{info.path.stem}{OUTPUT_EXT}"

            # Entrada e saida em mp4: se a pasta for a mesma, o ffmpeg
            # escreveria por cima do original.
            try:
                same_file = dst.resolve() == info.path.resolve()
            except OSError:
                same_file = False
            if same_file:
                skipped += 1
                self.call_from_thread(self.set_row, index, "status", "[yellow]mesmo arquivo[/]")
                self.call_from_thread(
                    self.write_log,
                    f"[yellow]{info.path.name}:[/] pulado — a saída sobrescreveria o original.",
                )
                continue

            if dst.exists() and dst.stat().st_mtime >= info.path.stat().st_mtime:
                skipped += 1
                self.call_from_thread(self.set_row, index, "status", "já existe")
                continue

            self.call_from_thread(
                self.set_label, f"{index + 1}/{total} · {info.path.name}"
            )
            self.call_from_thread(self.set_row, index, "status", "convertendo")
            self.call_from_thread(bar.update, total=100, progress=0)

            cmd = build_cmd(self.ffmpeg, info, dst, preset)
            self.call_from_thread(self.write_log, f"[dim]$ {subprocess.list2cmdline(cmd)}[/]")

            ok, tail = self._run_ffmpeg(cmd, info, worker)

            if worker.is_cancelled:
                self.call_from_thread(self.set_row, index, "status", "cancelado")
                dst.unlink(missing_ok=True)
                break

            if ok:
                done += 1
                size = human_size(dst.stat().st_size) if dst.exists() else "?"
                self.call_from_thread(self.set_row, index, "status", f"pronto · {size}")
            else:
                failed += 1
                dst.unlink(missing_ok=True)
                self.call_from_thread(self.set_row, index, "status", "[red]falhou[/]")
                self.call_from_thread(self.write_log, f"[red]{info.path.name}:[/] {tail}")

        self.call_from_thread(self.finish, done, failed, skipped, worker.is_cancelled)

    def _run_ffmpeg(self, cmd: list[str], info: MediaInfo, worker) -> tuple[bool, str]:
        bar = self.query_one("#bar", ProgressBar)
        errors: list[str] = []

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors="replace",
                bufsize=1,
                creationflags=CREATE_NO_WINDOW,
            )
        except OSError as exc:
            return False, str(exc)

        self._proc = proc
        assert proc.stdout is not None

        for line in proc.stdout:
            if worker.is_cancelled:
                proc.terminate()
                break

            match = TIME_RE.search(line)
            if match and info.duration:
                h, m, s = match.groups()
                elapsed = int(h) * 3600 + int(m) * 60 + float(s)
                pct = min(100.0, elapsed / info.duration * 100)
                self.call_from_thread(bar.update, progress=pct)
            elif "=" not in line and line.strip():
                errors.append(line.rstrip())

        proc.wait()
        self._proc = None
        tail = " · ".join(errors[-3:]) if errors else f"código {proc.returncode}"
        return proc.returncode == 0, tail

    def finish(self, done: int, failed: int, skipped: int, cancelled: bool) -> None:
        self.query_one("#convert", Button).disabled = False
        self.query_one("#scan", Button).disabled = False
        self.query_one("#cancel", Button).disabled = True
        self.query_one("#bar", ProgressBar).update(progress=0)

        verb = "Cancelado" if cancelled else "Concluído"
        self.set_label(verb.lower())
        self.write_log(
            f"[bold]{verb}:[/] {done} convertido(s), {failed} falha(s), {skipped} pulado(s)."
        )
        self.notify(f"{verb}: {done} convertido(s), {failed} falha(s).")

    # -- cancelar ---------------------------------------------------------

    def action_cancel(self) -> None:
        self.cancel_run()

    @on(Button.Pressed, "#cancel")
    def _on_cancel(self) -> None:
        self.cancel_run()

    def cancel_run(self) -> None:
        self.workers.cancel_group(self, "convert")
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self.write_log("[yellow]Cancelando…[/]")


def main() -> None:
    ResolvePrep().run()


if __name__ == "__main__":
    main()