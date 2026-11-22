"""Tipos de salida de la percepción. Todos en mm de la mesa (origen arriba a la izquierda de la superficie proyectable)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Cuerpo:
    """Una bola de plumavit o el engranaje, visto desde arriba."""

    tipo: str  # "bola" | "engranaje"
    centro_mm: tuple[float, float]
    radio_mm: float
    puntaje: float = 1.0  # 0..1: qué tan seguro es que sea ese tipo de cuerpo
    dientes: int = 0  # solo engranaje: dientes contados en el borde

    def dict(self) -> dict:
        return asdict(self)


@dataclass
class Asignacion:
    """Qué planeta es cada bola. `dudas` lista las bolas donde el tamaño no alcanza para decidir."""

    planetas: dict[str, int]  # nombre del planeta → índice en la lista de cuerpos
    sol: int | None  # índice del engranaje, si apareció
    dudas: list[dict] = field(default_factory=list)  # [{cuerpo, opciones: [planeta, …], motivo}]
    sobrantes: list[int] = field(default_factory=list)  # bolas sin planeta (más bolas que planetas)

    def dict(self) -> dict:
        return asdict(self)


@dataclass
class Pose:
    """Movimiento rígido del grupo: punto_nuevo = R(theta) · punto_viejo + t."""

    theta_deg: float
    t_mm: tuple[float, float]
    inliers: int  # bolas que calzan después de mover
    total: int  # bolas en el cuadro de referencia
    error_mm: float  # error medio de las que calzan
    ok: bool  # hay pose confiable (al menos 3 bolas o 2 con tamaños claros)
    pares: list[tuple[int, int]] = field(default_factory=list)  # (índice en referencia, índice en actual)

    def dict(self) -> dict:
        return asdict(self)


@dataclass
class Lamina:
    """Una lámina impresa del sistema solar: rectángulo girado, con el Sol hacia `+x` de la lámina."""

    centro_mm: tuple[float, float]
    tam_mm: tuple[float, float]  # (largo, ancho): el largo va del lado de los planetas chicos al Sol
    angulo_deg: float  # giro del eje largo de la lámina (hacia el Sol) respecto de +x de la mesa
    esquinas_mm: list[tuple[float, float]]
    puntaje: float = 1.0

    def dict(self) -> dict:
        return asdict(self)
