"""Configuración de voice-core: valores por defecto en código, sobrescritos por `config/voice-core.toml`.

`config/voice-core.toml` y `config/glosario.txt` son personales (no van a git). Si no existen se usan las plantillas
`config/voice-core.example.toml` y `config/glosario.example.txt`.
"""
from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr

from voice_core.contratos import ModoMotor

REPOS_MODELOS = {
    "large-v3": "Systran/faster-whisper-large-v3",
    "large-v3-turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
    "small": "Systran/faster-whisper-small",
}


class _Seccion(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AjustesServidor(_Seccion):
    host: str = "127.0.0.1"
    puerto: int = Field(8765, ge=1, le=65535)


class AjustesDatos(_Seccion):
    carpeta: Path = Path("datos")
    guardar_audio: bool = True


class AjustesMotor(_Seccion):
    modelo: str = "large-v3"
    carpeta_modelos: Path = Path.home() / ".cache" / "transcriptor-rt" / "modelos"
    compute_gpu: str = "int8_float16"
    compute_cpu: str = "int8"
    hilos_cpu: int = Field(8, ge=1)
    idioma: str = "es"
    glosario: Path | None = Path("config/glosario.txt")
    beam_final: int = Field(5, ge=1)
    beam_parcial: int = Field(1, ge=1)
    gpu: str = Field("5080", description="Parte del nombre de la ÚNICA GPU que puede usar el motor, o su UUID «GPU-…»")
    cuda_dll_dirs: list[Path] = Field(default_factory=list, description="Vacío = %CUDA_PATH%\\bin")
    modo: ModoMotor = "auto"
    vram_minima_mib: int = Field(3072, ge=0, description="VRAM libre necesaria en la 5080 para arrancar en GPU")
    vram_ceder_mib: int = Field(1024, ge=0, description="En espera, si la VRAM libre baja de esto, se apaga")
    inactividad_min: float = Field(2.0, gt=0, description="Minutos en en_espera (modelo en la GPU) sin uso")
    reposo_min: float = Field(30.0, ge=0, description="Minutos después con el modelo en RAM; 0 = apagar directamente")
    arranque_max_s: float = Field(120.0, gt=0)


class AjustesSegmentacion(_Seccion):
    umbral: float = Field(0.5, gt=0, lt=1)
    umbral_bajo: float = Field(0.35, gt=0, lt=1)
    silencio_fin_ms: int = Field(1000, ge=100)
    max_frase_s: float = Field(28.0, gt=1, le=29.5)
    parcial_cada_s: float = Field(0.4, gt=0)
    parcial_min_s: float = Field(0.8, ge=0)
    preroll_ms: int = Field(300, ge=0)
    min_voz_ms: int = Field(250, ge=0)


class AjustesAudio(_Seccion):
    dispositivo: str = Field("", description="Parte del nombre del micrófono; vacío = el de Windows por defecto")


class AjustesHotkeys(_Seccion):
    dictar: str = "ctrl+alt+d"
    tarea: str = "ctrl+alt+t"
    liberar: str = "ctrl+alt+l"
    sonidos: bool = True


class AjustesOverlay(_Seccion):
    lanzar_servicio: bool = Field(True, description="Si voice-core no responde, el overlay lo lanza oculto")
    ancho: int = Field(680, ge=320, le=2400, description="Ancho inicial; luego se ajusta arrastrando los bordes")
    letra: float = Field(13.0, ge=9, le=24, description="Tamaño inicial del texto; luego Ctrl+rueda del ratón")
    visible_tras_s: float = Field(15.0, ge=0, description="Segundos que queda el panel tras terminar un dictado")
    copiar_al_terminar: bool = True
    opacidad: float = Field(0.94, gt=0.2, le=1.0)
    mostrar: str = "ctrl+alt+o"
    pegar: str = "ctrl+alt+v"
    capturar: str = "ctrl+alt+s"


class Ajustes(_Seccion):
    servidor: AjustesServidor = AjustesServidor()
    datos: AjustesDatos = AjustesDatos()
    motor: AjustesMotor = AjustesMotor()
    segmentacion: AjustesSegmentacion = AjustesSegmentacion()
    audio: AjustesAudio = AjustesAudio()
    hotkeys: AjustesHotkeys = AjustesHotkeys()
    overlay: AjustesOverlay = AjustesOverlay()
    raiz: Path = Field(default_factory=Path.cwd, description="Base para resolver rutas relativas")
    _uuid_gpu: str | None = PrivateAttr(default=None)
    _gpu_resuelta: bool = PrivateAttr(default=False)

    @property
    def gpu_uuid(self) -> str | None:
        """UUID de la GPU permitida (se resuelve una vez por NVML); None = no hay GPU permitida, solo CPU."""
        if not self._gpu_resuelta:
            from voice_core.gpu import resolver_gpu

            self._uuid_gpu = resolver_gpu(self.motor.gpu)
            self._gpu_resuelta = True
        return self._uuid_gpu

    def ruta(self, p: Path) -> Path:
        p = p.expanduser()
        return p if p.is_absolute() else self.raiz / p

    @property
    def carpeta_datos(self) -> Path:
        return self.ruta(self.datos.carpeta)

    @property
    def ruta_modelo(self) -> Path:
        directo = Path(self.motor.modelo).expanduser()
        if directo.is_dir():
            return directo
        repo = REPOS_MODELOS.get(self.motor.modelo, self.motor.modelo)
        return self.motor.carpeta_modelos.expanduser() / repo.replace("/", "--")

    @property
    def texto_glosario(self) -> str | None:
        if self.motor.glosario is None:
            return None
        ruta = self.ruta(self.motor.glosario)
        if not ruta.is_file():
            ruta = ruta.with_name(f"{ruta.stem}.example{ruta.suffix}")  # plantilla publicada en el repositorio
        if not ruta.is_file():
            return None
        return ruta.read_text(encoding="utf-8-sig").strip() or None

    @property
    def dirs_dll_cuda(self) -> list[Path]:
        if self.motor.cuda_dll_dirs:
            return [self.ruta(d) for d in self.motor.cuda_dll_dirs]
        cuda_path = os.environ.get("CUDA_PATH")
        return [Path(cuda_path) / "bin"] if cuda_path else []


def ruta_config_por_defecto() -> Path:
    """TRT_CONFIG, o config/voice-core.toml (personal), o la plantilla config/voice-core.example.toml."""
    if "TRT_CONFIG" in os.environ:
        return Path(os.environ["TRT_CONFIG"])
    personal = Path("config/voice-core.toml")
    return personal if personal.is_file() else Path("config/voice-core.example.toml")


def cargar(ruta: Path | None = None) -> Ajustes:
    """Lee el TOML si existe. Las rutas relativas se resuelven contra la carpeta padre de `config/`."""
    ruta = (ruta or ruta_config_por_defecto()).resolve()
    if not ruta.is_file():
        return Ajustes(raiz=Path.cwd())
    datos = tomllib.loads(ruta.read_text(encoding="utf-8-sig"))
    raiz = ruta.parent.parent if ruta.parent.name == "config" else ruta.parent
    return Ajustes.model_validate({**datos, "raiz": raiz})
