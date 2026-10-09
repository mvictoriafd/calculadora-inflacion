#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Actualiza los archivos de datos de la calculadora:

  data/ipc.json  IPC nivel general nacional (INDEC). Fuente: API de Series de Tiempo
                 de datos.gob.ar, serie 148.3_INIVELNAL_DICI_M_26 (base dic 2016 = 100).
                 Solo agrega meses nuevos, encadenando sobre el último valor guardado.

  data/cac.json  Indicador CAC (Cámara Argentina de la Construcción): costo de
                 construcción general, materiales y mano de obra. Fuente: planilla
                 "Años anteriores" que Cifras Online publica en Drive. Se vuelve a
                 leer completa en cada corrida (así se toman las revisiones de los
                 datos provisorios). Los meses que la planilla todavía no tenga se
                 pueden cargar a mano en data/cac_manual.csv.

Uso:
  python scripts/update_data.py                 # actualiza todo desde internet
  python scripts/update_data.py --only ipc
  python scripts/update_data.py --cac-file planilla.xls --ipc-file api.json   # pruebas

Termina con código 1 si alguna fuente falló (los datos buenos anteriores no se tocan).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import json
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

IPC_URL = ("https://apis.datos.gob.ar/series/api/series/"
           "?ids=148.3_INIVELNAL_DICI_M_26&format=json&limit=1000")
CIFRAS_PAGE = "https://www.cifrasonline.com.ar/indice-cac/"
CIFRAS_FILE_ID = "1CGVDk9hzVZv4-YM8yH0CXP49uxjGNvrS"   # respaldo si la página cambia

CAC_SERIES = ("general", "materiales", "mano_obra")
CAC_LABELS = {"general": "Costo de construcción", "materiales": "Materiales",
              "mano_obra": "Mano de obra"}
DENOMINACIONES = ("costo de construcción", "materiales", "mano de obra")


# ----------------------------------------------------------------- utilidades
def mi(ym: str) -> int:
    """'2026-08' -> número absoluto de mes."""
    y, m = ym.split("-")
    return int(y) * 12 + int(m) - 1


def ym(i: int) -> str:
    return f"{i // 12:04d}-{i % 12 + 1:02d}"


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (actualizador calculadora-inflacion)"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def to_float(x):
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = re.sub(r"[^0-9,.\-]", "", str(x)).replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def dump_json(obj: dict) -> str:
    """Un renglón por clave: legible en los diffs de git y compacto."""
    items = [json.dumps(k) + ":" + json.dumps(v, ensure_ascii=False, separators=(",", ":"))
             for k, v in obj.items()]
    return "{\n" + ",\n".join(items) + "\n}\n"


def save_if_changed(path: Path, obj: dict) -> bool:
    """Escribe solo si cambió algo además de la fecha 'updated'."""
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        a = {k: v for k, v in old.items() if k != "updated"}
        b = {k: v for k, v in obj.items() if k != "updated"}
        if a == b:
            return False
    obj["updated"] = dt.date.today().isoformat()
    path.write_text(dump_json(obj), encoding="utf-8")
    return True


# ------------------------------------------------------------------------ IPC
def parse_ipc_api(raw: bytes) -> dict[str, float]:
    text = raw.decode("utf-8-sig").strip()
    out: dict[str, float] = {}
    if text.startswith("{"):
        for row in json.loads(text)["data"]:
            if row[1] is not None:
                out[str(row[0])[:7]] = float(row[1])
    else:  # CSV: indice_tiempo,valor
        for line in text.splitlines()[1:]:
            parts = line.split(",")
            if len(parts) >= 2 and parts[1].strip():
                out[parts[0][:7]] = float(parts[1])
    if not out:
        raise ValueError("la API del IPC no devolvió datos")
    return out


def update_ipc(data_dir: Path, raw: bytes) -> str:
    path = data_dir / "ipc.json"
    cur = json.loads(path.read_text(encoding="utf-8"))
    start, vals = mi(cur["start"]), cur["values"]
    last = start + len(vals) - 1
    api = parse_ipc_api(raw)

    # 1) Control: la serie guardada y la de la API tienen que variar igual en los
    #    últimos meses en común (si no, se está mirando otra serie o hubo un cambio de base).
    common = sorted(m for m in (mi(k) for k in api) if start <= m <= last and (m - 1) in {mi(k) for k in api})
    for m in common[-24:]:
        r_api = api[ym(m)] / api[ym(m - 1)]
        r_own = vals[m - start] / vals[m - 1 - start]
        if abs(r_api / r_own - 1) > 2e-4:
            raise ValueError(
                f"el IPC de la API no coincide con la serie guardada en {ym(m)} "
                f"(API {r_api:.5f} vs guardada {r_own:.5f}). No se actualiza.")
    if len(common) < 6 and mi(max(api)) > last:
        raise ValueError("no hay meses suficientes en común para validar la API del IPC")

    # 2) Agregar meses nuevos encadenando la variación mensual de la API
    added = []
    m = last + 1
    while ym(m) in api and ym(m - 1) in api:
        vals.append(round(vals[-1] * api[ym(m)] / api[ym(m - 1)], 6))
        added.append(ym(m))
        m += 1
    if not added:
        return f"IPC: sin novedades (último mes: {ym(last)})."
    cur["values"] = vals
    save_if_changed(path, cur)
    return f"IPC: agregados {', '.join(added)}."


# ------------------------------------------------------------------------ CAC
def cac_download() -> bytes:
    ids: list[str] = []
    try:
        html = http_get(CIFRAS_PAGE).decode("utf-8", "ignore")
        ids += re.findall(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,})", html)
    except Exception as e:  # la página puede estar caída; probamos con el ID conocido
        print(f"  (aviso: no pude leer la página de Cifras: {e})")
    ids.append(CIFRAS_FILE_ID)
    errors = []
    for fid in dict.fromkeys(ids):
        for url in (f"https://drive.google.com/uc?export=download&id={fid}",
                    f"https://docs.google.com/spreadsheets/d/{fid}/export?format=xlsx"):
            try:
                raw = http_get(url)
                if raw[:2] == b"PK" or raw[:4] == b"\xd0\xcf\x11\xe0":
                    return raw
                errors.append(f"{url}: no es un archivo Excel")
            except Exception as e:
                errors.append(f"{url}: {e}")
    raise ValueError("no pude descargar la planilla de Cifras. " + " | ".join(errors[:3]))


def read_rows(raw: bytes) -> list[list]:
    if raw[:2] == b"PK":  # .xlsx
        from openpyxl import load_workbook
        ws = load_workbook(io.BytesIO(raw), data_only=True).worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    import xlrd           # .xls
    book = xlrd.open_workbook(file_contents=raw)
    sh = book.sheet_by_index(0)
    rows = []
    for r in range(sh.nrows):
        row = []
        for c in range(sh.ncols):
            cell = sh.cell(r, c)
            if cell.ctype == xlrd.XL_CELL_DATE:
                row.append(dt.datetime(*xlrd.xldate_as_tuple(cell.value, book.datemode)))
            elif cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
                row.append(None)
            else:
                row.append(cell.value)
        rows.append(row)
    return rows


def _txt(x) -> str:
    return re.sub(r"\s+", " ", str(x)).strip().lower() if x is not None else ""


def parse_cac_rows(rows: list[list]):
    """La planilla agrupa tres filas por mes: general / materiales / mano de obra.
    El mes está escrito en la fila del medio (materiales); un '*' al lado = provisorio."""
    start = den_col = None
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            if _txt(cell) == DENOMINACIONES[0]:
                start, den_col = r, c
                break
        if start is not None:
            break
    if start is None or den_col < 2:
        raise ValueError("no reconozco el formato de la planilla de Cifras")

    months, provisional = [], []
    raw = {k: ([], []) for k in CAC_SERIES}   # (índices, variaciones)
    r = start
    while r + 2 < len(rows):
        trio = rows[r:r + 3]
        if tuple(_txt(t[den_col]) for t in trio) != DENOMINACIONES:
            break
        fecha = trio[1][den_col - 2]
        if not isinstance(fecha, (dt.datetime, dt.date)):
            raise ValueError(f"fila {r + 2}: falta la fecha del mes")
        key = f"{fecha.year:04d}-{fecha.month:02d}"
        months.append(key)
        if _txt(trio[1][den_col - 1]) == "*":
            provisional.append(key)
        for name, t in zip(CAC_SERIES, trio):
            raw[name][0].append(t[den_col + 2])
            raw[name][1].append(t[den_col + 3])
        r += 3

    if len(months) < 60:
        raise ValueError(f"la planilla tiene solo {len(months)} meses; se esperaban más de 180")
    for a, b in zip(months, months[1:]):
        if mi(b) - mi(a) != 1:
            raise ValueError(f"hay un hueco en la planilla entre {a} y {b}")
    return months, provisional, raw


def clean_series(name: str, months: list[str], idx_raw: list, var_raw: list, notes: list[str]):
    """Detecta y corrige errores de carga de un solo mes comparando cada índice con la
    variación mensual que la propia planilla informa (en % en años viejos, en fracción
    en los nuevos)."""
    label = CAC_LABELS[name]
    n = len(months)
    vals = [to_float(x) for x in idx_raw]
    reps = [to_float(x) for x in var_raw]
    for i, x in enumerate(idx_raw):
        if isinstance(x, str) and vals[i] is not None:
            notes.append(f"{label} {months[i]}: el valor venía como texto ({x!r}); se leyó {vals[i]}.")

    def r_of(rep, unit):
        return rep / 100 if unit == "pct" else rep

    unit = "pct"
    units = [unit] * n
    flagged = [False] * n
    for i in range(1, n):
        v, p, rep = vals[i], vals[i - 1], reps[i]
        if v is None or p is None or rep is None:
            flagged[i] = True
            units[i] = unit
            continue
        real = v / p - 1
        tol = 0.0025 if v >= 1000 else 0.004
        ok_pct = abs(real - rep / 100) <= tol
        ok_frac = abs(real - rep) <= tol
        if ok_pct and not ok_frac:
            unit = "pct"
        elif ok_frac and not ok_pct:
            unit = "frac"
        units[i] = unit
        flagged[i] = not (ok_pct or ok_frac)

    i = 1
    while i < n:
        if flagged[i]:
            if i + 1 < n and flagged[i + 1]:           # firma de un dato mal cargado
                est = []
                if vals[i - 1] is not None and reps[i] is not None:
                    est.append(vals[i - 1] * (1 + r_of(reps[i], units[i])))
                if vals[i + 1] is not None and reps[i + 1] is not None:
                    est.append(vals[i + 1] / (1 + r_of(reps[i + 1], units[i + 1])))
                if est:
                    new = round(sum(est) / len(est), 1)
                    notes.append(f"{label} {months[i]}: dato inconsistente en la planilla "
                                 f"({idx_raw[i]!r}); se estimó {new} con las variaciones informadas.")
                    vals[i] = new
                    flagged[i] = flagged[i + 1] = False
                    i += 2
                    continue
            notes.append(f"{label} {months[i]}: la variación informada no coincide con el índice; "
                         f"se mantuvo el índice. Conviene revisarlo.")
        i += 1

    if any(v is None for v in vals):
        raise ValueError(f"{label}: quedaron meses sin valor numérico")
    return vals


def read_manual(path: Path):
    """cac_manual.csv: mes,general,materiales,mano_obra (variación mensual en %)."""
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8-sig").splitlines()
             if ln.strip() and not ln.lstrip().startswith("#")]
    rows = []
    for rec in csv.DictReader(lines):
        mes = (rec.get("mes") or "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}", mes):
            raise ValueError(f"cac_manual.csv: mes inválido {mes!r} (usar AAAA-MM)")
        pcts = {}
        for s in CAC_SERIES:
            v = to_float(rec.get(s))
            if v is None:
                raise ValueError(f"cac_manual.csv: falta el valor de {s} para {mes}")
            pcts[s] = v
        rows.append((mes, pcts))
    return sorted(rows)


def build_cac(rows: list[list], manual: list) -> dict:
    months, provisional, raw = parse_cac_rows(rows)
    notes: list[str] = []
    series = {k: clean_series(k, months, raw[k][0], raw[k][1], notes) for k in CAC_SERIES}

    for mes, pcts in manual:
        if mi(mes) <= mi(months[-1]):
            continue                       # la planilla ya lo trae: manda la planilla
        if mi(mes) != mi(months[-1]) + 1:
            raise ValueError(f"cac_manual.csv: falta cargar {ym(mi(months[-1]) + 1)} antes de {mes}")
        for k in CAC_SERIES:
            series[k].append(round(series[k][-1] * (1 + pcts[k] / 100), 1))
        months.append(mes)
        provisional.append(mes)
        notes.append(f"{mes}: cargado a mano desde data/cac_manual.csv (variación mensual del informe de Cifras).")

    return {"nombre": "Indicador CAC (Cámara Argentina de la Construcción), base dic 2014 = 100",
            "start": months[0], "series": series, "provisional": provisional,
            "notes": notes, "updated": ""}


def update_cac(data_dir: Path, raw: bytes) -> str:
    path = data_dir / "cac.json"
    new = build_cac(read_rows(raw), read_manual(data_dir / "cac_manual.csv"))
    if path.exists():
        old = json.loads(path.read_text(encoding="utf-8"))
        if len(new["series"]["general"]) < len(old["series"]["general"]):
            raise ValueError("la planilla nueva tiene menos meses que la guardada; no se actualiza")
    n = len(new["series"]["general"])
    last = ym(mi(new["start"]) + n - 1)
    changed = save_if_changed(path, new)
    avisos = f" ({len(new['notes'])} aviso/s en cac.json)" if new["notes"] else ""
    return f"CAC: {'actualizado' if changed else 'sin cambios'}; último mes {last}{avisos}."


# ----------------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=["ipc", "cac"])
    ap.add_argument("--ipc-file", help="usar este archivo (JSON/CSV de la API) en vez de internet")
    ap.add_argument("--cac-file", help="usar esta planilla (.xls/.xlsx) en vez de internet")
    ap.add_argument("--data-dir", default=str(ROOT / "data"))
    args = ap.parse_args()
    data_dir = Path(args.data_dir)
    failed = False

    if args.only in (None, "ipc"):
        try:
            raw = Path(args.ipc_file).read_bytes() if args.ipc_file else http_get(IPC_URL)
            print(update_ipc(data_dir, raw))
        except Exception as e:
            failed = True
            print(f"::error::IPC no se actualizó: {e}")

    if args.only in (None, "cac"):
        try:
            raw = Path(args.cac_file).read_bytes() if args.cac_file else cac_download()
            print(update_cac(data_dir, raw))
        except Exception as e:
            failed = True
            print(f"::error::CAC no se actualizó: {e}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
