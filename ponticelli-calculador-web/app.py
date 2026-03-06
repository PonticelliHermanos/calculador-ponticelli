#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Calculador de Materiales – Ponticelli Hnos

Web app sin dependencias externas de Node/npm.
- Servidor HTTP: stdlib http.server
- Config editable: JSON (por defecto copia de /mnt/data/ponticelli_materials_config.json)
- PDF: reportlab (instalado)

Run:
  export ADMIN_PASSWORD='...'
  python app.py
  abrir http://localhost:8000
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "static"
DATA_DIR = APP_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# Logo obligatorio
LOGO_PATH = STATIC_DIR / "logo.jpg"

DEFAULT_CONFIG_SRC = Path("/mnt/data/ponticelli_materials_config.json")
CONFIG_PATH = DATA_DIR / "config.json"


def _ensure_config() -> None:
    if not CONFIG_PATH.exists():
        if DEFAULT_CONFIG_SRC.exists():
            CONFIG_PATH.write_bytes(DEFAULT_CONFIG_SRC.read_bytes())
        else:
            raise FileNotFoundError(
                "No se encuentra el config inicial. Falta /mnt/data/ponticelli_materials_config.json"
            )


def load_config() -> Dict[str, Any]:
    _ensure_config()
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def save_config(cfg: Dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


@dataclass
class CalcLine:
    material_id: str
    name: str
    unit: str
    exact_qty: float
    sale_unit: str
    buy_qty: float
    notes: str


def _round_up_to_pack(exact: float, pack: Optional[float]) -> Tuple[float, str]:
    if pack is None or pack <= 0:
        return exact, ""
    buy = (int((exact + pack - 1e-9) // pack) + (0 if abs((exact / pack) - round(exact / pack)) < 1e-9 else 0))
    # Above line is not reliable; do simple ceil
    import math

    buy = math.ceil(exact / pack)
    buy_qty = buy * pack
    extra = buy_qty - exact
    note = f"Redondeo por pack de {pack:g}: se compra {buy} pack(s) (= {buy_qty:g}). Sobrante estimado {extra:g}."
    return buy_qty, note


def _ceil(x: float) -> int:
    import math

    return int(math.ceil(x - 1e-12))


def _first_fit_decreasing(cuts: List[float], stock_len: float) -> List[List[float]]:
    """Simple corte: First-Fit Decreasing. Devuelve lista de barras, cada barra con cortes."""
    cuts = sorted([c for c in cuts if c > 0], reverse=True)
    bars: List[List[float]] = []
    remaining: List[float] = []
    for c in cuts:
        placed = False
        for i in range(len(bars)):
            if remaining[i] + 1e-9 >= c:
                bars[i].append(c)
                remaining[i] -= c
                placed = True
                break
        if not placed:
            bars.append([c])
            remaining.append(stock_len - c)
    return bars


def _fmt(n: float) -> str:
    # Español AR: decimal con coma
    s = f"{n:.4f}".rstrip("0").rstrip(".")
    return s.replace(".", ",")


def coverage_report(cfg: Dict[str, Any]) -> Dict[str, Any]:
    materials = cfg.get("materials", [])
    material_ids = {m.get("id") for m in materials if m.get("id")}

    used_ids = set()
    for p in cfg.get("projects", []):
        for mid in p.get("materials", []) or []:
            used_ids.add(mid)
        # options can include materialIds
        for opt in (p.get("options") or []):
            for mid in (opt.get("materialIds") or []):
                used_ids.add(mid)

    extras_ids = set(material_ids)  # Todos ofrecidos en "Extras" por diseño
    app_total_ids = used_ids | extras_ids

    missing = sorted(list(material_ids - app_total_ids))

    return {
        "B_total": len(material_ids),
        "used_in_projects": len(used_ids),
        "offered_in_extras": len(extras_ids),
        "app_total": len(app_total_ids),
        "faltantes": missing,
        "faltantes_count": len(missing),
    }


def calc_project(cfg: Dict[str, Any], project_id: str, variant: Dict[str, Any], measures: Dict[str, float]) -> Dict[str, Any]:
    """Calcula materiales/consumibles.

    Estrategia:
    - Cada proyecto define (en JSON) reglas simples por 'type'.
    - Para esta primera versión, usamos una implementación genérica por tipo:
        * 'roof_sheet': techo de chapa (area -> chapas + tornillos)
        * 'metal_frame': proyectos metálicos (metros de perfil -> barras)
        * 'drywall': m2 -> placas + tornillos + masilla
      Si el proyecto no tiene type reconocible, genera lista vacía y deja 'Extras'.

    Consumibles: se estiman con consumablesRules del config (parametrizable).
    """

    projects = {p["id"]: p for p in cfg.get("projects", []) if p.get("id")}
    if project_id not in projects:
        raise ValueError("Proyecto inválido")

    project = projects[project_id]
    ptype = project.get("calcType") or project.get("type") or "generic"
    # Mapear tipos del config (base/inferred) a tipos de cálculo
    name_l = (project.get("name") or "").lower()
    if ptype in ("base", "inferred", "generic"):
        if 'techo' in name_l and 'chapa' in name_l:
            ptype = 'roof_sheet'
        elif 'seco' in name_l or 'drywall' in name_l or 'steel framing' in name_l or 'cielorraso' in name_l or 'tabique' in name_l:
            ptype = 'drywall'
        else:
            ptype = 'metal_frame'

    materials_by_id = {m["id"]: m for m in cfg.get("materials", []) if m.get("id")}

    lines: List[CalcLine] = []
    breakdown: List[str] = []
    assumptions: List[str] = []

    # Helpers
    def add_line(mid: str, exact: float, notes: str = ""):
        m = materials_by_id.get(mid)
        if not m:
            return
        unit = m.get("uom") or ""
        sale_unit = m.get("saleUnit") or unit
        pack = m.get("packSize")

        buy_qty = exact
        round_note = ""

        # Redondeos según unidad de venta
        if sale_unit in ("Uni", "BAR", "Ro."):
            buy_qty = float(_ceil(exact))
            if abs(buy_qty - exact) > 1e-9:
                round_note = f"Se redondea a unidad entera: {buy_qty:g}."
        elif sale_unit == "Kgs":
            # kg: redondeo a 0,1 kg por defecto
            step = (cfg.get("app", {}).get("defaults", {}) or {}).get("kgRoundStep", 0.1)
            import math

            buy_qty = math.ceil(exact / step) * step
            if abs(buy_qty - exact) > 1e-9:
                round_note = f"Redondeo por paso {step:g} kg: {buy_qty:g}."
        elif pack:
            buy_qty, round_note = _round_up_to_pack(exact, float(pack))

        full_notes = "; ".join([n for n in [notes, round_note] if n])
        lines.append(
            CalcLine(
                material_id=mid,
                name=m.get("name") or "",
                unit=unit,
                exact_qty=exact,
                sale_unit=sale_unit,
                buy_qty=buy_qty,
                notes=full_notes,
            )
        )

    # --- Cálculos por tipo ---
    if ptype == "roof_sheet":
        largo = float(measures.get("largo_m", 0))
        ancho = float(measures.get("ancho_m", 0))
        area = largo * ancho
        breakdown.append(f"Área = largo × ancho = {_fmt(largo)} × {_fmt(ancho)} = {_fmt(area)} m²")

        sheet_mid = variant.get("sheetMaterialId")
        if sheet_mid and sheet_mid in materials_by_id:
            m = materials_by_id[sheet_mid]
            # Si la chapa viene como unidad con largo/ancho embebidos, usamos lengthPerUnitM y un ancho efectivo.
            effective_width = float(variant.get("effectiveWidthM", cfg.get("app", {}).get("defaults", {}).get("roofEffectiveWidthM", 1.0)))
            length = float(m.get("lengthPerUnitM") or 0)

            if (m.get("saleUnit") == "Uni") and length > 0 and effective_width > 0:
                cover_per_sheet = length * effective_width
                sheets_exact = area / cover_per_sheet
                breakdown.append(
                    f"Cobertura por chapa = largo chapa × ancho efectivo = {_fmt(length)} × {_fmt(effective_width)} = {_fmt(cover_per_sheet)} m²"
                )
                breakdown.append(f"Chapas (teórico) = área / cobertura = {_fmt(area)} / {_fmt(cover_per_sheet)} = {_fmt(sheets_exact)}")
                add_line(sheet_mid, sheets_exact, notes="Chapa principal")
                assumptions.append(f"Ancho efectivo de chapa: {_fmt(effective_width)} m (editable en Admin).")
            elif m.get("uom") == "M²":
                add_line(sheet_mid, area, notes="Chapa por m²")
            else:
                # fallback: 1 unidad por m²
                add_line(sheet_mid, area, notes="Supuesto: 1 unidad por m²")
                assumptions.append("Supuesto: material principal calculado como 1 unidad por m² (ajustable en Admin).")

        # Tornillos autoperforantes por m²
        tpm2 = float(cfg.get("app", {}).get("consumablesRules", {}).get("tornillos_por_m2", 8))
        screws = area * tpm2
        breakdown.append(f"Tornillos = área × tornillos/m² = {_fmt(area)} × {_fmt(tpm2)} = {_fmt(screws)} un")
        # Buscar un material "tornillo" en catálogo (si existe). Si no existe, se lista como item virtual en notas.
        screw_mid = variant.get("screwMaterialId")
        if screw_mid and screw_mid in materials_by_id:
            add_line(screw_mid, screws, notes="Tornillos autoperforantes")
        else:
            assumptions.append("No se encontró ítem específico de tornillos en el catálogo para este proyecto. Podés elegirlo en Extras.")

    elif ptype == "metal_frame":
        # Un marco genérico: perímetro (2*(ancho+alto)) y travesaños opcional.
        ancho = float(measures.get("ancho_m", 0))
        alto = float(measures.get("alto_m", 0))
        travesanos = int(measures.get("travesanos", 0))
        perim = 2 * (ancho + alto)
        breakdown.append(f"Perímetro = 2×(ancho+alto) = 2×({_fmt(ancho)}+{_fmt(alto)}) = {_fmt(perim)} m")
        if travesanos > 0:
            extra = travesanos * ancho
            perim_total = perim + extra
            breakdown.append(f"Travesaños = {travesanos} × ancho = {travesanos} × {_fmt(ancho)} = {_fmt(extra)} m")
            breakdown.append(f"Metros totales de perfil = {_fmt(perim)} + {_fmt(extra)} = {_fmt(perim_total)} m")
        else:
            perim_total = perim
            breakdown.append(f"Metros totales de perfil = {_fmt(perim_total)} m")

        perfil_mid = variant.get("profileMaterialId")
        if perfil_mid and perfil_mid in materials_by_id:
            m = materials_by_id[perfil_mid]
            sale_unit = m.get("saleUnit")
            # Si se vende por BAR, optimizamos cortes con lengthPerUnitM o default 6m
            stock_len = float(m.get("lengthPerUnitM") or cfg.get("app", {}).get("defaults", {}).get("barLengthM", 6.0))

            # En este proyecto simple, asumimos cortes: 2x alto + 2x ancho + travesaños x ancho
            cuts = [alto, alto, ancho, ancho] + ([ancho] * travesanos)
            if sale_unit == "BAR":
                bars = _first_fit_decreasing(cuts, stock_len)
                exact_bars = len(bars)
                buy_bars = float(exact_bars)
                waste = sum((stock_len - sum(b)) for b in bars)
                breakdown.append(f"Largo comercial por barra = {_fmt(stock_len)} m")
                breakdown.append(f"Cortes pedidos (m): {', '.join(_fmt(c) for c in cuts)}")
                breakdown.append(f"Optimización (FFD): barras necesarias = {exact_bars}")
                breakdown.append(f"Sobrante estimado total = {_fmt(waste)} m")

                notes = f"Cortes optimizados en {exact_bars} barra(s) de {_fmt(stock_len)} m. Sobrante estimado {_fmt(waste)} m."
                add_line(perfil_mid, buy_bars, notes=notes)
                assumptions.append("Optimización de cortes: First-Fit Decreasing (simple, rápida).")
            else:
                # vendido por metro
                add_line(perfil_mid, perim_total, notes="Perfil principal")

        # Consumibles de soldadura / corte
        weld_m = float(cfg.get("app", {}).get("defaults", {}).get("weldPerimeterFactor", 1.0)) * perim_total
        cut_m = perim_total
        e_per_m = float(cfg.get("app", {}).get("consumablesRules", {}).get("electrodos_por_m", 0.05))
        d_per_m = float(cfg.get("app", {}).get("consumablesRules", {}).get("discos_por_m_corte", 0.02))
        breakdown.append(f"Metros de soldadura (estim.) = {_fmt(weld_m)} m")
        breakdown.append(f"Electrodos = soldadura × electrodos/m = {_fmt(weld_m)} × {_fmt(e_per_m)} = {_fmt(weld_m*e_per_m)}")
        breakdown.append(f"Metros de corte (estim.) = {_fmt(cut_m)} m")
        breakdown.append(f"Discos = corte × discos/m = {_fmt(cut_m)} × {_fmt(d_per_m)} = {_fmt(cut_m*d_per_m)}")

        # No forzamos IDs: si existe material elegido en variante lo usamos
        if variant.get("electrodeMaterialId") in materials_by_id:
            add_line(variant["electrodeMaterialId"], weld_m * e_per_m, notes="Electrodos (supuesto)")
        else:
            assumptions.append("Electrodos/discos: se calculan por supuestos editables. Elegilos en Admin o en Extras.")

        if variant.get("cutDiscMaterialId") in materials_by_id:
            add_line(variant["cutDiscMaterialId"], cut_m * d_per_m, notes="Discos de corte (supuesto)")

    elif ptype == "drywall":
        largo = float(measures.get("largo_m", 0))
        alto = float(measures.get("alto_m", 0))
        area = largo * alto
        breakdown.append(f"Área de pared/placa = largo × alto = {_fmt(largo)} × {_fmt(alto)} = {_fmt(area)} m²")

        placa_mid = variant.get("boardMaterialId")
        if placa_mid and placa_mid in materials_by_id:
            m = materials_by_id[placa_mid]
            # Si es unidad, intentamos inferir m² por unidad desde packSize/lengthPerUnitM y ancho (default 1.2)
            if m.get("saleUnit") == "Uni":
                ancho_placa = float(variant.get("boardWidthM", 1.2))
                largo_placa = float(m.get("lengthPerUnitM") or 2.4)
                cover = ancho_placa * largo_placa
                exact = area / cover
                breakdown.append(f"Cobertura por placa = {_fmt(ancho_placa)} × {_fmt(largo_placa)} = {_fmt(cover)} m²")
                breakdown.append(f"Placas (teórico) = área / cobertura = {_fmt(area)} / {_fmt(cover)} = {_fmt(exact)}")
                add_line(placa_mid, exact, notes="Placas")
                assumptions.append("Dimensiones de placa (editable en Admin).")
            else:
                add_line(placa_mid, area, notes="Placa por m²")

        # Tornillos por m²
        tpm2 = float(cfg.get("app", {}).get("consumablesRules", {}).get("tornillos_por_m2_drywall", 25))
        screws = area * tpm2
        breakdown.append(f"Tornillos (drywall) = {_fmt(area)} × {_fmt(tpm2)} = {_fmt(screws)} un")
        if variant.get("screwMaterialId") in materials_by_id:
            add_line(variant["screwMaterialId"], screws, notes="Tornillos")
        else:
            assumptions.append("Tornillos drywall: no hay ítem asignado. Se puede elegir en Extras o definir en Admin.")

    else:
        assumptions.append("Este proyecto no tiene fórmula configurada aún. Podés usar Extras para armar el presupuesto.")

    # Ordenar líneas por nombre
    lines_sorted = sorted(lines, key=lambda x: (x.name or "", x.material_id))

    return {
        "project": {"id": project_id, "name": project.get("name")},
        "lines": [
            {
                "materialId": ln.material_id,
                "material": ln.name,
                "unidad": ln.unit,
                "cantidadExacta": ln.exact_qty,
                "unidadVenta": ln.sale_unit,
                "cantidadComprar": ln.buy_qty,
                "observaciones": ln.notes,
            }
            for ln in lines_sorted
        ],
        "breakdown": breakdown,
        "assumptions": assumptions,
    }


def generate_pdf(
    *,
    cfg: Dict[str, Any],
    client_name: str,
    project_name: str,
    calc: Dict[str, Any],
    out_path: Path,
) -> None:
    if not LOGO_PATH.exists():
        raise FileNotFoundError(
            "No se pudo integrar el logo. Falta static/logo.jpg. "
            "Subí el logo o verificá la ruta."
        )

    # Fuente (fallback a Helvetica si no hay)
    try:
        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        if Path(font_path).exists():
            pdfmetrics.registerFont(TTFont("DejaVu", font_path))
            base_font = "DejaVu"
        else:
            base_font = "Helvetica"
    except Exception:
        base_font = "Helvetica"

    c = canvas.Canvas(str(out_path), pagesize=A4)
    w, h = A4

    def header_footer(page_title: str):
        # Header
        c.saveState()
        c.setFillColor(colors.HexColor("#111827"))
        c.rect(0, h - 18 * mm, w, 18 * mm, fill=1, stroke=0)
        c.drawImage(str(LOGO_PATH), 8 * mm, h - 16 * mm, width=28 * mm, height=12 * mm, preserveAspectRatio=True, mask='auto')
        c.setFillColor(colors.white)
        c.setFont(base_font, 11)
        c.drawString(40 * mm, h - 11.5 * mm, "Ponticelli Hnos")
        c.setFont(base_font, 9)
        c.drawRightString(w - 8 * mm, h - 11.5 * mm, page_title)

        # Footer
        c.setFillColor(colors.HexColor("#6b7280"))
        c.setFont(base_font, 8)
        c.drawString(8 * mm, 8 * mm, "Calculador de Materiales – Ponticelli Hnos")
        c.drawRightString(w - 8 * mm, 8 * mm, datetime.now().strftime("%d/%m/%Y"))
        c.restoreState()

    # --- Portada ---
    c.setFillColor(colors.white)
    c.rect(0, 0, w, h, fill=1, stroke=0)
    c.drawImage(str(LOGO_PATH), 20 * mm, h - 55 * mm, width=60 * mm, height=25 * mm, preserveAspectRatio=True, mask='auto')

    c.setFont(base_font, 22)
    c.setFillColor(colors.HexColor("#111827"))
    c.drawString(20 * mm, h - 70 * mm, "Ponticelli Hnos")
    c.setFont(base_font, 16)
    c.drawString(20 * mm, h - 82 * mm, "Presupuesto")

    c.setFont(base_font, 11)
    y = h - 105 * mm
    c.setFillColor(colors.HexColor("#374151"))
    c.drawString(20 * mm, y, f"Fecha: {datetime.now().strftime('%d/%m/%Y')}")
    y -= 7 * mm
    c.drawString(20 * mm, y, f"Cliente: {client_name or '-'}")
    y -= 7 * mm
    c.drawString(20 * mm, y, f"Proyecto: {project_name}")

    c.showPage()

    # --- Cuerpo ---
    header_footer("Detalle")

    margin_x = 8 * mm
    y = h - 24 * mm

    c.setFont(base_font, 12)
    c.setFillColor(colors.HexColor("#111827"))
    c.drawString(margin_x, y, "Materiales y consumibles")
    y -= 8 * mm

    # Tabla
    cols = [
        ("Material", 70 * mm),
        ("Unidad", 15 * mm),
        ("Cant. exacta", 22 * mm),
        ("Unidad venta", 20 * mm),
        ("Cant. a comprar", 25 * mm),
        ("Obs.", w - margin_x - (70 + 15 + 22 + 20 + 25) * mm - margin_x),
    ]

    def draw_table_header(ypos: float):
        c.setFillColor(colors.HexColor("#e5e7eb"))
        c.rect(margin_x, ypos - 6 * mm, w - 2 * margin_x, 7 * mm, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#111827"))
        c.setFont(base_font, 8.5)
        x = margin_x
        for title, width in cols:
            c.drawString(x + 1.5 * mm, ypos - 4.5 * mm, title)
            x += width
        return ypos - 8 * mm

    y = draw_table_header(y)

    c.setFont(base_font, 8.3)
    c.setFillColor(colors.black)

    def wrap(text: str, max_chars: int) -> List[str]:
        if not text:
            return [""]
        words = text.split()
        lines = []
        cur = ""
        for w_ in words:
            if len(cur) + len(w_) + 1 <= max_chars:
                cur = (cur + " " + w_).strip()
            else:
                lines.append(cur)
                cur = w_
        if cur:
            lines.append(cur)
        return lines or [""]

    row_h = 6.5 * mm
    for ln in calc.get("lines", []):
        if y < 25 * mm:
            c.showPage()
            header_footer("Detalle")
            y = h - 24 * mm
            y = draw_table_header(y)
            c.setFont(base_font, 8.3)

        x = margin_x
        mat_lines = wrap(str(ln.get("material", "")), 38)
        obs_lines = wrap(str(ln.get("observaciones", "")), 32)
        max_lines = max(len(mat_lines), len(obs_lines), 1)
        height = max_lines * 3.6 * mm

        # row background
        c.setFillColor(colors.white)
        c.rect(margin_x, y - height + 1 * mm, w - 2 * margin_x, height, fill=1, stroke=1)
        c.setFillColor(colors.black)

        # Material
        for i, t in enumerate(mat_lines):
            c.drawString(x + 1.5 * mm, y - (i + 1) * 3.6 * mm + 1.5 * mm, t)
        x += cols[0][1]

        c.drawString(x + 1.5 * mm, y - 3.6 * mm + 1.5 * mm, str(ln.get("unidad", "")))
        x += cols[1][1]

        c.drawRightString(x + cols[2][1] - 1.5 * mm, y - 3.6 * mm + 1.5 * mm, _fmt(float(ln.get("cantidadExacta", 0))))
        x += cols[2][1]

        c.drawString(x + 1.5 * mm, y - 3.6 * mm + 1.5 * mm, str(ln.get("unidadVenta", "")))
        x += cols[3][1]

        c.drawRightString(x + cols[4][1] - 1.5 * mm, y - 3.6 * mm + 1.5 * mm, _fmt(float(ln.get("cantidadComprar", 0))))
        x += cols[4][1]

        for i, t in enumerate(obs_lines):
            c.drawString(x + 1.5 * mm, y - (i + 1) * 3.6 * mm + 1.5 * mm, t)

        y -= height

    # Supuestos y redondeos
    y -= 6 * mm
    c.setFont(base_font, 11)
    c.setFillColor(colors.HexColor("#111827"))
    c.drawString(margin_x, y, "Supuestos y redondeos")
    y -= 6 * mm

    c.setFont(base_font, 9)
    c.setFillColor(colors.black)

    items = list(calc.get("assumptions", []) or [])
    if not items:
        items = ["Sin supuestos adicionales."]

    for it in items:
        if y < 20 * mm:
            c.showPage()
            header_footer("Supuestos")
            y = h - 24 * mm
        c.drawString(margin_x, y, f"• {it}")
        y -= 5 * mm

    c.showPage()

    # Desglose
    header_footer("Cómo se calculó")
    y = h - 24 * mm
    c.setFont(base_font, 12)
    c.setFillColor(colors.HexColor("#111827"))
    c.drawString(margin_x, y, "Ver cómo se calculó")
    y -= 8 * mm

    c.setFont(base_font, 9)
    c.setFillColor(colors.black)
    for b in calc.get("breakdown", []) or []:
        if y < 20 * mm:
            c.showPage()
            header_footer("Cómo se calculó")
            y = h - 24 * mm
            c.setFont(base_font, 9)
        c.drawString(margin_x, y, f"• {b}")
        y -= 5 * mm

    c.save()


class Handler(SimpleHTTPRequestHandler):
    # Servir desde static por defecto
    def translate_path(self, path: str) -> str:
        p = urlparse(path).path
        # Descargas PDF
        if p.startswith("/out/"):
            out_full = (DATA_DIR / "out" / p.split("/out/", 1)[1]).resolve()
            if str(out_full).startswith(str((DATA_DIR / "out").resolve())) and out_full.exists() and out_full.is_file():
                return str(out_full)
        # Estáticos
        if p == "/":
            p = "/index.html"
        full = (STATIC_DIR / p.lstrip("/"))
        if full.exists() and full.is_file():
            return str(full)
        return super().translate_path(path)

    def _send_json(self, payload: Any, status: int = 200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> Dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return {}

    def _is_admin(self) -> bool:
        pwd = os.environ.get("ADMIN_PASSWORD", "")
        if not pwd:
            return False
        token = self.headers.get("X-Admin-Password", "")
        return token == pwd

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/health":
            if not LOGO_PATH.exists():
                self._send_json({"ok": False, "error": "Falta logo en static/logo.jpg"}, status=500)
                return
            self._send_json({"ok": True, "logo": True})
            return

        if parsed.path == "/api/projects":
            cfg = load_config()
            projects = cfg.get("projects", [])
            out = []
            for p in projects:
                name_l = (p.get('name') or '').lower()
                ptype = p.get('calcType') or p.get('type') or 'generic'
                if ptype in ('base','inferred','generic'):
                    if 'techo' in name_l and 'chapa' in name_l:
                        ptype = 'roof_sheet'
                    elif 'seco' in name_l or 'drywall' in name_l or 'steel framing' in name_l or 'cielorraso' in name_l or 'tabique' in name_l:
                        ptype = 'drywall'
                    else:
                        ptype = 'metal_frame'
                out.append({"id": p["id"], "name": p["name"], "type": ptype})
            self._send_json({"projects": out})
            return

        if parsed.path == "/api/materials":
            cfg = load_config()
            q = (parse_qs(parsed.query).get("q") or [""])[0].strip().lower()
            cat = (parse_qs(parsed.query).get("category") or [""])[0].strip().lower()
            items = cfg.get("materials", [])
            res = []
            for m in items:
                name = (m.get("name") or "").lower()
                if q and q not in name:
                    continue
                if cat and (m.get("category") or "").lower() != cat:
                    continue
                res.append({
                    "id": m.get("id"),
                    "name": m.get("name"),
                    "category": m.get("category"),
                    "uom": m.get("uom"),
                    "saleUnit": m.get("saleUnit"),
                    "packSize": m.get("packSize"),
                    "lengthPerUnitM": m.get("lengthPerUnitM"),
                })
                if len(res) >= 50:
                    break
            self._send_json({"materials": res})
            return

        if parsed.path == "/api/admin/coverage":
            if not self._is_admin():
                self._send_json({"error": "No autorizado"}, status=401)
                return
            cfg = load_config()
            self._send_json({"coverage": coverage_report(cfg)})
            return

        if parsed.path == "/api/admin/config":
            if not self._is_admin():
                self._send_json({"error": "No autorizado"}, status=401)
                return
            cfg = load_config()
            self._send_json({"config": cfg})
            return

        # Static
        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/calc":
            body = self._read_json()
            cfg = load_config()
            try:
                result = calc_project(
                    cfg,
                    project_id=str(body.get("projectId", "")),
                    variant=body.get("variant") or {},
                    measures=body.get("measures") or {},
                )
                self._send_json({"ok": True, "result": result})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        if parsed.path == "/api/pdf":
            body = self._read_json()
            cfg = load_config()
            try:
                calc = body.get("calc") or {}
                client_name = str(body.get("clientName") or "")
                project_name = str(body.get("projectName") or "")

                out_dir = DATA_DIR / "out"
                out_dir.mkdir(exist_ok=True)
                fname = f"presupuesto_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"
                out_path = out_dir / fname
                generate_pdf(cfg=cfg, client_name=client_name, project_name=project_name, calc=calc, out_path=out_path)

                # devolvemos url de descarga
                self._send_json({"ok": True, "url": f"/out/{fname}"})
            except Exception as e:
                self._send_json({"ok": False, "error": str(e)}, status=400)
            return

        if parsed.path == "/api/admin/config":
            if not self._is_admin():
                self._send_json({"error": "No autorizado"}, status=401)
                return
            body = self._read_json()
            cfg = body.get("config")
            if not isinstance(cfg, dict):
                self._send_json({"error": "Config inválida"}, status=400)
                return
            # Validación mínima: materiales deben existir
            if not isinstance(cfg.get("materials"), list) or len(cfg.get("materials")) == 0:
                self._send_json({"error": "Config inválida: materials vacío"}, status=400)
                return
            save_config(cfg)
            self._send_json({"ok": True, "coverage": coverage_report(cfg)})
            return

        self._send_json({"error": "No encontrado"}, status=404)


def main():
    if not LOGO_PATH.exists():
        print("ERROR: No se pudo integrar el logo. Falta static/logo.jpg")
        sys.exit(2)

    _ensure_config()

    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0")

    httpd = ThreadingHTTPServer=HTTPServer(("0.0.0.0", port), Handler)
    print(f"Ponticelli Calculador corriendo en http://localhost:{port}")
    print(f"Config editable: {CONFIG_PATH}")
    if os.environ.get("ADMIN_PASSWORD"):
        print("Admin: habilitado (usa header X-Admin-Password)")
    else:
        print("Admin: deshabilitado (setear ADMIN_PASSWORD)")

    httpd.serve_forever()


if __name__ == "__main__":
    main()
