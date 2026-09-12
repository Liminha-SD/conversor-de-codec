"""
Transcodificação para o DaVinci Resolve (free): lê os metadados com o ffprobe,
monta o comando do ffmpeg e acompanha o progresso. Nada de interface aqui.

A saída é sempre .mp4 de verdade: o muxer mp4 do ffmpeg só aceita H.264/H.265
(ProRes e DNxHR exigem .mov). All-intra deixa o scrub e o corte fluidos no
Resolve, quase como um codec intermediário, num arquivo bem menor.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

VIDEO_EXTS = {".mp4", ".mov", ".mxf", ".mkv", ".m4v", ".avi", ".mts", ".m2ts"}
OUTPUT_EXT = ".mp4"
OUTPUT_SUFFIX = " - convertido"

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class TranscodeError(RuntimeError):
    """Falha ao converter um arquivo. A mensagem já vem pronta para o usuário."""


# --------------------------------------------------------------------------
# Presets
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    description: str
    args: tuple[str, ...]
    mbps: int  # estimativa de bitrate em 1080p59.94, para o cálculo de espaço
    tag: str = ""  # fourcc opcional; vazio = o padrão do muxer


PRESETS: tuple[Preset, ...] = (
    Preset(
        "h264_max",
        "H.264 Max",
        "8 bits 4:2:0, all-intra quase sem perda (CRF 8). A melhor qualidade que o "
        "Resolve free abre; arquivos grandes.",
        (
            "-c:v", "libx264", "-preset", "slow", "-crf", "8",
            # -g 1 torna todo frame um keyframe: é o que dá o scrub leve.
            "-g", "1", "-profile:v", "high", "-pix_fmt", "yuv420p",
        ),
        180,
    ),
    Preset(
        "h264_hq",
        "H.264 HQ",
        "8 bits 4:2:0, all-intra (CRF 12). Metade do tamanho do Max, para quando o "
        "espaço aperta.",
        (
            "-c:v", "libx264", "-preset", "medium", "-crf", "12",
            "-g", "1", "-profile:v", "high", "-pix_fmt", "yuv420p",
        ),
        90,
    ),
    Preset(
        "h265_hq",
        "H.265 HQ",
        "8 bits 4:2:0, all-intra. Cerca de metade do tamanho do H.264 HQ, encode mais lento.",
        (
            "-c:v", "libx265", "-preset", "medium", "-crf", "16",
            "-g", "1", "-pix_fmt", "yuv420p",
        ),
        50,
        # hev1 (o padrão) trava em vários players; hvc1 é o que abre em todos.
        "hvc1",
    ),
    Preset(
        "h265_10bit",
        "H.265 10 bits",
        "10 bits 4:2:0, all-intra. Preserva a profundidade, mas o Resolve free pode recusar.",
        (
            "-c:v", "libx265", "-preset", "medium", "-crf", "16",
            "-g", "1", "-pix_fmt", "yuv420p10le",
        ),
        60,
        "hvc1",
    ),
    Preset(
        "h264_proxy",
        "H.264 Proxy",
        "Long-GOP leve, só para corte e visualização. Não serve para o grading final.",
        (
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
        ),
        12,
    ),
)

PRESET_BY_KEY = {p.key: p for p in PRESETS}
DEFAULT_PRESET = "h264_max"  # a melhor qualidade que o Resolve free abre com certeza


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

# Chroma/profundidade que o Resolve free não decodifica no Windows.
NEEDS_TRANSCODE = {("4:2:2", "10"), ("4:2:2", "12"), ("4:4:4", "10"), ("4:4:4", "12")}

# Um valor que o ffprobe reporta mas o filtro setparams não conhece derruba a
# conversão inteira, então só repassamos o que está nestas listas
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

# Estados em que o ffprobe não leu um vídeo: a conversão pula o arquivo.
UNREADABLE = ("ilegível", "sem vídeo")


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
    def readable(self) -> bool:
        return self.status not in UNREADABLE

    @property
    def flagged(self) -> bool:
        """True se o Resolve free provavelmente não abre este arquivo."""
        return (self.chroma, self.depth) in NEEDS_TRANSCODE


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, errors="replace", creationflags=CREATE_NO_WINDOW
    )


def app_dir() -> Path:
    """Pasta do programa: ao lado do .exe (PyInstaller) ou do script."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def find_ffmpeg() -> tuple[str, str]:
    """Localiza (ffmpeg, ffprobe): no PATH ou na cópia portátil em tools/.

    A cópia em tools/ é a que o run.bat baixa no Windows quando não há ffmpeg
    instalado; sem isto ela só seria encontrada quando iniciado pelo run.bat.
    """
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg and ffprobe:
        return ffmpeg, ffprobe
    for bin_dir in sorted(app_dir().glob("tools/ffmpeg-*/bin")):
        ffmpeg = shutil.which("ffmpeg", path=str(bin_dir))
        ffprobe = shutil.which("ffprobe", path=str(bin_dir))
        if ffmpeg and ffprobe:
            return ffmpeg, ffprobe
    raise TranscodeError(
        "ffmpeg e ffprobe não foram encontrados no PATH.\n"
        "Windows: winget install Gyan.FFmpeg (ou rode o run.bat, que baixa uma cópia)\n"
        "Arch: sudo pacman -S ffmpeg"
    )


def probe(ffprobe: str, path: Path) -> MediaInfo:
    info = MediaInfo(path=path)
    res = _run(
        [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    )
    if res.returncode != 0:
        info.status = "ilegível"
        return info

    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        info.status = "ilegível"
        return info

    streams = data.get("streams", [])
    fmt = data.get("format", {})

    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        info.status = "sem vídeo"
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

    info.status = "converter" if info.flagged else "ok no Resolve"
    return info


# --------------------------------------------------------------------------
# Arquivos de entrada e saída
# --------------------------------------------------------------------------


def collect(paths: list[Path]) -> list[Path]:
    """Pastas contribuem seus vídeos (sem recursão); arquivos avulsos entram
    como vieram, de qualquer extensão - o ffprobe decide se são legíveis."""
    found: dict[Path, None] = {}  # dict para deduplicar mantendo a ordem
    for path in paths:
        if path.is_dir():
            for f in sorted(path.iterdir()):
                if f.is_file() and f.suffix.lower() in VIDEO_EXTS:
                    found[f] = None
        elif path.is_file():
            found[path] = None
    return list(found)


def output_dir(source: Path, root: Path | None) -> Path:
    """Pasta onde a conversão de `source` é salva.

    Sempre uma pasta "<nome da pasta de origem> - convertido": dentro de
    `root` quando há uma pasta padrão configurada, ou ao lado da pasta de
    origem quando não há.
    """
    folder = source.parent
    name = folder.name or "raiz"  # raiz do disco não tem nome
    return (root if root is not None else folder.parent) / f"{name}{OUTPUT_SUFFIX}"


def output_path(source: Path, root: Path | None) -> Path:
    return output_dir(source, root) / f"{source.stem}{OUTPUT_EXT}"


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(num_bytes) < 1024:
            return f"{num_bytes:.1f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.1f} PB"


def estimate_output(info: MediaInfo, preset: Preset) -> float:
    """Bytes estimados, escalando o bitrate de referência pela área e pelo fps."""
    if not info.duration or not info.width:
        return 0.0
    area_ratio = (info.width * info.height) / (1920 * 1080)
    fps_ratio = (info.fps / 59.94) if info.fps else 1.0
    mbps = preset.mbps * area_ratio * fps_ratio
    return mbps * 1_000_000 / 8 * info.duration


# --------------------------------------------------------------------------
# Conversão
# --------------------------------------------------------------------------


def build_cmd(ffmpeg: str, info: MediaInfo, dst: Path, preset: Preset) -> list[str]:
    # -loglevel error para o x265 não despejar linhas de info no stdout, que
    # o parser de progresso trataria como erro.
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-i", str(info.path)]

    filters = []
    # Canon grava full range (0-1023). H.264/H.265 de edição são video range
    # (64-940). Sem esta conversão o preto esmaga e o branco estoura no Resolve.
    if info.full_range:
        filters.append("scale=in_range=full:out_range=limited")
    # As opções -color_* abaixo não bastam: o filtergraph propaga as
    # propriedades do frame e sobrescreve o que o encoder recebeu, e o arquivo
    # sai com primaries/trc "unknown". setparams etiqueta na saída do grafo.
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
        # mp4 não aceita PCM; AAC 320k é transparente o bastante para edição.
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


TIME_RE = re.compile(r"out_time=(\d+):(\d\d):(\d\d(?:\.\d+)?)")
SPEED_RE = re.compile(r"speed=\s*([\d.]+x)")


def convert(
    ffmpeg: str,
    info: MediaInfo,
    dst: Path,
    preset: Preset,
    on_progress: Callable[[float, str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> None:
    """Converte `info.path` em `dst`, chamando on_progress(fração, velocidade).

    Se should_cancel() ficar True, o ffmpeg é encerrado e o arquivo parcial
    apagado; a função levanta TranscodeError com a mensagem "cancelado".
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_cmd(ffmpeg, info, dst, preset)
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
        raise TranscodeError(f"não consegui iniciar o ffmpeg: {exc}") from exc

    assert proc.stdout is not None
    cancelled = False
    speed = ""
    for line in proc.stdout:
        if should_cancel and should_cancel():
            cancelled = True
            proc.terminate()
            break

        if match := SPEED_RE.search(line):
            speed = match.group(1)
        match = TIME_RE.search(line)
        if match and info.duration:
            h, m, s = match.groups()
            elapsed = int(h) * 3600 + int(m) * 60 + float(s)
            if on_progress:
                on_progress(min(1.0, elapsed / info.duration), speed)
        elif "=" not in line and line.strip():
            errors.append(line.rstrip())

    proc.wait()

    if cancelled:
        dst.unlink(missing_ok=True)
        raise TranscodeError("cancelado")
    if proc.returncode != 0:
        dst.unlink(missing_ok=True)
        tail = " · ".join(errors[-3:]) if errors else f"ffmpeg saiu com código {proc.returncode}"
        raise TranscodeError(f"{tail}\n$ {subprocess.list2cmdline(cmd)}")
