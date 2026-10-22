"""Banco de F4: los mismos dictados de tarea por varios modelos de LM Studio, con el prompt y el esquema reales del
servicio (tasks_service.parser). Mide la carga, el tiempo por dictado y los aciertos frente a lo esperado.

Uso (desde la raíz, con el servidor de LM Studio en marcha):
    .venv\\Scripts\\python spikes\\f4\\banco.py openai/gpt-oss-20b qwen/qwen3.5-35b-a3b
    .venv\\Scripts\\python spikes\\f4\\banco.py openai/gpt-oss-20b --param reasoning_effort=low

Cada modelo se descarga antes (para medir la carga en frío) y después (para dejar la GPU libre).
Resultados en datos/f4/banco-<modelo>[-<etiqueta>].json.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import unicodedata
from datetime import date
from pathlib import Path

from tasks_service.llm import LMS_POR_DEFECTO, ClienteLlm, ErrorLlm
from tasks_service.parser import Propuesta, RespuestaInvalida, categorias_efectivas, esquema, interpretar, mensajes

RAIZ = Path(__file__).resolve().parents[2]
CALENTAR = "Comprar café."


def normal(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode().lower()


def coincide(esperada: dict, p: Propuesta) -> bool:
    texto = normal(f"{p.title} {p.notes or ''}")
    return all(any(normal(alt) in texto for alt in clave.split("|")) for clave in esperada["claves"])


def puntuar(esperadas: list[dict], obtenidas: list[Propuesta]) -> dict:
    libres = list(obtenidas)
    r = {"esperadas": sum(not e.get("opcional") for e in esperadas), "encontradas": 0, "categoria_ok": 0,
         "fechas_puntuadas": 0, "fecha_ok": 0, "fallos": []}
    for e in esperadas:
        p = next((p for p in libres if coincide(e, p)), None)
        if p is None:
            if not e.get("opcional"):
                r["fallos"].append(f"falta «{e['claves'][0]}»")
            continue
        libres.remove(p)
        if not e.get("opcional"):
            r["encontradas"] += 1
        if p.category in e["categoria"]:
            r["categoria_ok"] += 1
        else:
            r["fallos"].append(f"«{p.title}»: {p.category} (esperada {'/'.join(e['categoria'])})")
        if e["fecha"] != "*":
            r["fechas_puntuadas"] += 1
            validas = e["fecha"] if isinstance(e["fecha"], list) else [e["fecha"]]
            if p.due in validas:
                r["fecha_ok"] += 1
            else:
                r["fallos"].append(f"«{p.title}»: fecha {p.due} (esperada {' o '.join(map(str, validas))})")
    r["sobrantes"] = [p.title for p in libres]
    r["fallos"] += [f"sobra «{t}»" for t in r["sobrantes"]]
    r["categorias_puntuadas"] = len(obtenidas) - len(libres)
    return r


def vram() -> dict[str, int]:
    salida = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.used", "--format=csv,noheader,nounits"],
                            capture_output=True, text=True, check=False).stdout
    return {n.strip().replace("NVIDIA GeForce ", ""): int(m) for n, m in (l.split(",") for l in salida.splitlines())}


def lms(*args: str) -> None:
    subprocess.run([str(LMS_POR_DEFECTO), *args], capture_output=True, check=False)


def probar(modelo: str, banco: dict, parametros: dict, etiqueta: str) -> dict:
    cats = categorias_efectivas(banco["categorias"])
    nombres = list(cats)
    fecha = date.fromisoformat(banco["fecha"])
    cliente = ClienteLlm("http://127.0.0.1:1234", modelo, ttl_s=300, espera_s=600, parametros=parametros)
    lms("unload", modelo)
    antes = vram()
    carga = cliente.completar(mensajes(CALENTAR, cats, fecha), esquema(nombres))
    despues = vram()
    print(f"\n=== {modelo} {etiqueta} · carga en frío + primera respuesta: {carga.segundos:.1f} s · VRAM "
          + " · ".join(f"{g} +{despues[g] - antes.get(g, 0)} MiB" for g in despues))
    filas = []
    for d in banco["dictados"]:
        try:
            r = cliente.completar(mensajes(d["texto"], cats, fecha), esquema(nombres))
            obtenidas, valido = interpretar(r.contenido, nombres), True
        except (ErrorLlm, RespuestaInvalida) as e:
            print(f"  {d['id']}: ERROR {e}")
            filas.append({"id": d["id"], "error": str(e), "valido": False, **puntuar(d["esperado"], [])})
            continue
        p = puntuar(d["esperado"], obtenidas)
        filas.append({"id": d["id"], "segundos": round(r.segundos, 2), "tokens_salida": r.tokens_salida,
                      "tokens_razonamiento": r.tokens_razonamiento, "valido": valido, "crudo": r.contenido,
                      "tareas": [t.__dict__ for t in obtenidas], **p})
        estado = "ok" if not p["fallos"] else "; ".join(p["fallos"])
        print(f"  {d['id']} {r.segundos:5.1f} s {r.tokens_salida or 0:4d} tok "
              f"(razon. {r.tokens_razonamiento or 0:3d}) · {len(obtenidas)} tareas · {estado}")
    lms("unload", modelo)
    tiempos = [f["segundos"] for f in filas if "segundos" in f]
    total = {k: sum(f[k] for f in filas) for k in ("esperadas", "encontradas", "categoria_ok", "categorias_puntuadas",
                                                   "fechas_puntuadas", "fecha_ok")}
    sobrantes = sum(len(f["sobrantes"]) for f in filas)
    vacios_ok = sum(1 for f, d in zip(filas, banco["dictados"]) if not d["esperado"] and not f.get("sobrantes")
                    and f["valido"])
    resumen = {
        "modelo": modelo, "etiqueta": etiqueta, "parametros": parametros,
        "carga_s": round(carga.segundos, 1), "vram_mib": {g: despues[g] - antes.get(g, 0) for g in despues},
        "mediana_s": round(statistics.median(tiempos), 2) if tiempos else None,
        "media_s": round(statistics.mean(tiempos), 2) if tiempos else None,
        "max_s": round(max(tiempos), 2) if tiempos else None,
        "json_valido": f"{sum(f['valido'] for f in filas)}/{len(filas)}",
        "recall": f"{total['encontradas']}/{total['esperadas']}",
        "sobrantes": sobrantes,
        "categoria": f"{total['categoria_ok']}/{total['categorias_puntuadas']}",
        "fecha": f"{total['fecha_ok']}/{total['fechas_puntuadas']}",
        "sin_tareas_ok": f"{vacios_ok}/{sum(1 for d in banco['dictados'] if not d['esperado'])}",
        "dictados_perfectos": f"{sum(not f['fallos'] and f['valido'] for f in filas)}/{len(filas)}",
    }
    print("  → " + " · ".join(f"{k} {v}" for k, v in resumen.items() if k not in ("modelo", "etiqueta", "parametros")))
    destino = RAIZ / "datos" / "f4" / f"banco-{modelo.replace('/', '_')}{'-' + etiqueta if etiqueta else ''}.json"
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({"resumen": resumen, "dictados": filas}, ensure_ascii=False, indent=2),
                       encoding="utf-8")
    return resumen


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("modelos", nargs="+")
    ap.add_argument("--param", action="append", default=[], help="clave=valor extra en el cuerpo de la petición")
    ap.add_argument("--etiqueta", default="")
    a = ap.parse_args()
    parametros = {}
    for p in a.param:
        clave, valor = p.split("=", 1)
        parametros[clave] = json.loads(valor) if valor[:1] in "[{0123456789-" or valor in ("true", "false") else valor
    banco = json.loads((Path(__file__).with_name("dictados.json")).read_text(encoding="utf-8"))
    resumenes = [probar(m, banco, parametros, a.etiqueta) for m in a.modelos]
    print("\n" + json.dumps(resumenes, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
