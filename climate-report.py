"""
climate_report.py
─────────────────────────────────────────────────────────────
Reads a Markdown-formatted table of temperature / humidity
readings, produces a vector chart using ReportLab graphics,
and assembles a newspaper-front-page PDF.

Open-source libraries used
  • reportlab   – PDF typesetting and vector chart generation
  • pandas      – Markdown-table parsing helper

Usage
  python climate_report.py            # uses the built-in sample table
  python climate_report.py table.md   # reads a file
"""

from __future__ import annotations

import io
import sys
import re
import textwrap
import datetime
from pathlib import Path

import pandas as pd

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether, Image as RLImage,
)
from reportlab.graphics import renderPDF
from reportlab.graphics.shapes import Drawing
from reportlab.platypus.flowables import Flowable
from reportlab.pdfgen import canvas as rl_canvas


# ──────────────────────────────────────────────────────────────────────────────
# SAMPLE DATA  (used when no external file is supplied)
# ──────────────────────────────────────────────────────────────────────────────

SAMPLE_TABLE = """\
| Time       | Location        | Temperature (°C) | Humidity (%) |
|------------|-----------------|-----------------|--------------|
| 06:00      | Living Room     | 18.2            | 62           |
| 07:00      | Bedroom         | 17.5            | 65           |
| 08:00      | Kitchen         | 19.8            | 58           |
| 10:00      | Living Room     | 21.3            | 55           |
| 12:00      | Bedroom         | 22.1            | 52           |
| 13:00      | Kitchen         | 23.7            | 49           |
| 14:00      | Living Room     | 24.2            | 47           |
| 16:00      | Bedroom         | 23.5            | 50           |
| 18:00      | Kitchen         | 22.0            | 54           |
| 20:00      | Living Room     | 21.4            | 57           |
| 22:00      | Bedroom         | 19.9            | 61           |
| 23:00      | Kitchen         | 18.6            | 63           |
"""

# ──────────────────────────────────────────────────────────────────────────────
# STEP 1 – Parse the Markdown table into a DataFrame
# ──────────────────────────────────────────────────────────────────────────────

def parse_markdown_table(md: str) -> pd.DataFrame:
    """Convert a Markdown-formatted table string to a pandas DataFrame."""
    lines = [l.strip() for l in md.strip().splitlines() if l.strip()]
    # remove separator row (---|--- …)
    lines = [l for l in lines if not re.match(r"^\|?[\s:|-]+\|", l)]

    rows = []
    for line in lines:
        cells = [c.strip() for c in line.strip("|").split("|")]
        rows.append(cells)

    if not rows:
        raise ValueError("No data rows found in the Markdown table.")

    headers = rows[0]
    data = rows[1:]
    df = pd.DataFrame(data, columns=headers)

    # Normalise column names
    df.columns = [c.strip() for c in df.columns]

    # Detect numeric columns
    for col in df.columns:
        try:
            df[col] = pd.to_numeric(df[col])
        except (ValueError, TypeError):
            pass

    return df


# ──────────────────────────────────────────────────────────────────────────────
# STEP 2 – Generate the chart using ReportLab vector graphics (no matplotlib)
# ──────────────────────────────────────────────────────────────────────────────

ROOM_COLORS = {
    "Living Room": "#e05c2d",
    "Bedroom":     "#2d6ee0",
    "Kitchen":     "#27a65c",
}
DEFAULT_COLOR = "#888888"


def generate_chart_reportlab(df: pd.DataFrame, width_pt: float) -> Drawing:
    """
    Return a ReportLab Drawing with a dual-axis temperature/humidity chart.
    Pure vector graphics — no matplotlib, no raster images.
    """
    from reportlab.graphics.shapes import (
        Drawing, Group, String, Line, Rect, Circle, Polygon
    )
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.charts.barcharts import VerticalBarChart
    from reportlab.graphics.charts.axes import XValueAxis, YValueAxis
    from reportlab.graphics.widgets.grids import Grid
    from reportlab.lib import colors as rl_colors

    # Identify columns by keywords
    time_col  = next((c for c in df.columns if "time"  in c.lower()), df.columns[0])
    loc_col   = next((c for c in df.columns if any(k in c.lower() for k in ("location","room","place","zone"))), None)
    temp_col  = next((c for c in df.columns if "temp"  in c.lower()), None)
    hum_col   = next((c for c in df.columns if "humid" in c.lower()), None)

    if temp_col is None or hum_col is None:
        raise ValueError("Cannot detect Temperature and Humidity columns.")

    # Convert time strings like "06:00" to numeric hours
    def parse_time(t):
        t = str(t).strip()
        if ":" in t:
            h, m = t.split(":")
            return int(h) + int(m) / 60
        try:
            return float(t)
        except ValueError:
            return 0.0

    df = df.copy()
    df["_hour"] = df[time_col].apply(parse_time)
    df["_temp"] = pd.to_numeric(df[temp_col], errors="coerce")
    df["_hum"]  = pd.to_numeric(df[hum_col],  errors="coerce")
    df["_loc"]  = df[loc_col].astype(str) if loc_col else "–"
    df.sort_values("_hour", inplace=True)

    # ── dimensions ────────────────────────────────────────────────────────────
    height_pt = width_pt * 0.46
    margin_l = 45
    margin_r = 45
    margin_t = 50
    margin_b = 35
    
    plot_w = width_pt - margin_l - margin_r
    plot_h = height_pt - margin_t - margin_b
    plot_x = margin_l
    plot_y = margin_b

    drawing = Drawing(width_pt, height_pt)
    
    # ── background ────────────────────────────────────────────────────────────
    drawing.add(Rect(0, 0, width_pt, height_pt, 
                     fillColor=rl_colors.HexColor("#fafaf8"), strokeColor=None))

    # ── helper functions ──────────────────────────────────────────────────────
    def x_scale(hour):
        """Convert hour (0-24) to drawing x-coordinate."""
        return plot_x + (hour / 24.0) * plot_w
    
    def y_temp_scale(temp):
        """Convert temperature (14-28°C) to drawing y-coordinate."""
        return plot_y + ((temp - 14) / (28 - 14)) * plot_h
    
    def y_hum_scale(hum):
        """Convert humidity (35-80%) to drawing y-coordinate."""
        return plot_y + ((hum - 35) / (80 - 35)) * plot_h

    # ── shaded regions (overnight, comfort zone, humidity band) ───────────────
    # Overnight (0-7 and 22-24)
    drawing.add(Rect(x_scale(0), plot_y, x_scale(7) - x_scale(0), plot_h,
                     fillColor=rl_colors.HexColor("#4488cc"), fillOpacity=0.06,
                     strokeColor=None))
    drawing.add(Rect(x_scale(22), plot_y, x_scale(24) - x_scale(22), plot_h,
                     fillColor=rl_colors.HexColor("#4488cc"), fillOpacity=0.06,
                     strokeColor=None))
    
    # Comfort zone (19-23°C)
    comfort_y1 = y_temp_scale(19)
    comfort_y2 = y_temp_scale(23)
    drawing.add(Rect(plot_x, comfort_y1, plot_w, comfort_y2 - comfort_y1,
                     fillColor=rl_colors.HexColor("#e05c2d"), fillOpacity=0.08,
                     strokeColor=None))
    drawing.add(String(x_scale(23.5), y_temp_scale(21), "Comfort",
                      fontSize=6.5, fillColor=rl_colors.HexColor("#c04810"),
                      textAnchor="start", fontName="Helvetica-Oblique"))
    drawing.add(String(x_scale(23.5), y_temp_scale(21) - 8, "zone",
                      fontSize=6.5, fillColor=rl_colors.HexColor("#c04810"),
                      textAnchor="start", fontName="Helvetica-Oblique"))

    # Humidity band (40-60%)
    hum_y1 = y_hum_scale(40)
    hum_y2 = y_hum_scale(60)
    drawing.add(Rect(plot_x, hum_y1, plot_w, hum_y2 - hum_y1,
                     fillColor=rl_colors.HexColor("#2d6ee0"), fillOpacity=0.07,
                     strokeColor=None))

    # ── grid lines (horizontal only, for temperature) ────────────────────────
    for temp in [16, 18, 20, 22, 24, 26]:
        y = y_temp_scale(temp)
        drawing.add(Line(plot_x, y, plot_x + plot_w, y,
                        strokeColor=rl_colors.HexColor("#ddd"),
                        strokeWidth=0.6, strokeDashArray=[2, 2]))

    # ── humidity bars ─────────────────────────────────────────────────────────
    bar_width = plot_w / 24 * 0.55
    for _, row in df.iterrows():
        x_center = x_scale(row["_hour"])
        bar_h = y_hum_scale(row["_hum"]) - plot_y
        col = ROOM_COLORS.get(row["_loc"], DEFAULT_COLOR)
        drawing.add(Rect(x_center - bar_width/2, plot_y, bar_width, bar_h,
                        fillColor=rl_colors.HexColor(col), fillOpacity=0.22,
                        strokeColor=None))

    # ── temperature line (dashed) ─────────────────────────────────────────────
    temp_points = [(x_scale(row["_hour"]), y_temp_scale(row["_temp"]))
                   for _, row in df.iterrows()]
    for i in range(len(temp_points) - 1):
        x1, y1 = temp_points[i]
        x2, y2 = temp_points[i + 1]
        drawing.add(Line(x1, y1, x2, y2,
                        strokeColor=rl_colors.HexColor("#888888"),
                        strokeWidth=1.4, strokeDashArray=[4, 3]))

    # ── temperature markers (colored by room) ─────────────────────────────────
    for _, row in df.iterrows():
        x = x_scale(row["_hour"])
        y = y_temp_scale(row["_temp"])
        col = ROOM_COLORS.get(row["_loc"], DEFAULT_COLOR)
        
        # White outline
        drawing.add(Circle(x, y, 5.5, fillColor=rl_colors.white,
                          strokeColor=None))
        # Colored fill
        drawing.add(Circle(x, y, 5, fillColor=rl_colors.HexColor(col),
                          strokeColor=rl_colors.white, strokeWidth=0.8))
        
        # Value annotation
        drawing.add(String(x, y + 11, f"{row['_temp']:.1f}°",
                          fontSize=6.5, fillColor=rl_colors.HexColor("#333"),
                          textAnchor="middle", fontName="Helvetica-Bold"))

    # ── axes ──────────────────────────────────────────────────────────────────
    # Bottom axis (time)
    drawing.add(Line(plot_x, plot_y, plot_x + plot_w, plot_y,
                    strokeColor=rl_colors.HexColor("#cccccc"), strokeWidth=0.6))
    
    time_labels = [
        (0, "Midnight"), (3, "3 AM"), (6, "6 AM"), (9, "9 AM"),
        (12, "Noon"), (15, "3 PM"), (18, "6 PM"), (21, "9 PM"), (24, "Midnight")
    ]
    for hour, label in time_labels:
        x = x_scale(hour)
        drawing.add(Line(x, plot_y, x, plot_y - 3,
                        strokeColor=rl_colors.HexColor("#999"), strokeWidth=0.6))
        drawing.add(String(x, plot_y - 12, label,
                          fontSize=7.5, fillColor=rl_colors.HexColor("#444"),
                          textAnchor="middle", fontName="Helvetica"))

    # Left axis (temperature in °C)
    drawing.add(Line(plot_x, plot_y, plot_x, plot_y + plot_h,
                    strokeColor=rl_colors.HexColor("#c04810"), strokeWidth=1))
    for temp in [14, 16, 18, 20, 22, 24, 26, 28]:
        y = y_temp_scale(temp)
        drawing.add(Line(plot_x - 3, y, plot_x, y,
                        strokeColor=rl_colors.HexColor("#c04810"), strokeWidth=0.6))
        drawing.add(String(plot_x - 8, y - 3, str(temp),
                          fontSize=8, fillColor=rl_colors.HexColor("#c04810"),
                          textAnchor="end", fontName="Helvetica"))
    
    drawing.add(String(plot_x - 8, plot_y + plot_h + 18, "Temperature (°C)",
                      fontSize=8.5, fillColor=rl_colors.HexColor("#c04810"),
                      textAnchor="end", fontName="Helvetica-Bold"))

    # Right axis (humidity %)
    drawing.add(Line(plot_x + plot_w, plot_y, plot_x + plot_w, plot_y + plot_h,
                    strokeColor=rl_colors.HexColor("#2d5fb0"), strokeWidth=1))
    for hum in [40, 50, 60, 70, 80]:
        y = y_hum_scale(hum)
        drawing.add(Line(plot_x + plot_w, y, plot_x + plot_w + 3, y,
                        strokeColor=rl_colors.HexColor("#2d5fb0"), strokeWidth=0.6))
        drawing.add(String(plot_x + plot_w + 8, y - 3, f"{hum}%",
                          fontSize=8, fillColor=rl_colors.HexColor("#2d5fb0"),
                          textAnchor="start", fontName="Helvetica"))
    
    drawing.add(String(plot_x + plot_w + 8, plot_y + plot_h + 18, "Humidity (%)",
                      fontSize=8.5, fillColor=rl_colors.HexColor("#2d5fb0"),
                      textAnchor="start", fontName="Helvetica-Bold"))

    # ── title ─────────────────────────────────────────────────────────────────
    drawing.add(String(plot_x, height_pt - 15,
                      "Indoor Climate — 24-hour Reading  ·  Temperature & Relative Humidity by Room",
                      fontSize=9, fillColor=rl_colors.HexColor("#222"),
                      textAnchor="start", fontName="Helvetica-Bold"))

    # ── legend ────────────────────────────────────────────────────────────────
    legend_x = plot_x + plot_w - 10
    legend_y = height_pt - 45
    legend_items = [
        ("Living Room", ROOM_COLORS["Living Room"]),
        ("Bedroom", ROOM_COLORS["Bedroom"]),
        ("Kitchen", ROOM_COLORS["Kitchen"]),
        ("Humidity (%)", "#2d6ee0"),
    ]
    
    # Legend background
    drawing.add(Rect(legend_x - 85, legend_y - 35, 95, 45,
                    fillColor=rl_colors.white, fillOpacity=0.9,
                    strokeColor=rl_colors.HexColor("#ddd"), strokeWidth=0.6))
    
    for i, (label, color) in enumerate(legend_items):
        y_offset = legend_y - i * 11
        if label == "Humidity (%)":
            # Bar icon for humidity
            drawing.add(Rect(legend_x - 78, y_offset - 3, 12, 6,
                           fillColor=rl_colors.HexColor(color), fillOpacity=0.35,
                           strokeColor=None))
        else:
            # Circle icon for rooms
            drawing.add(Circle(legend_x - 72, y_offset, 4,
                             fillColor=rl_colors.HexColor(color),
                             strokeColor=rl_colors.white, strokeWidth=0.6))
        
        drawing.add(String(legend_x - 62, y_offset - 3, label,
                          fontSize=7.5, fillColor=rl_colors.HexColor("#333"),
                          textAnchor="start", fontName="Helvetica"))

    return drawing


# ──────────────────────────────────────────────────────────────────────────────
# STEP 3 – Compose the newspaper-front-page PDF with ReportLab
# ──────────────────────────────────────────────────────────────────────────────

PAGE_W, PAGE_H = A4                                       # 595 × 842 pt
MARGIN_L = MARGIN_R = 18 * mm
MARGIN_T = 14 * mm
MARGIN_B = 16 * mm
COL_GAP  = 5  * mm
N_COLS   = 3
COL_W    = (PAGE_W - MARGIN_L - MARGIN_R - (N_COLS - 1) * COL_GAP) / N_COLS

BLACK   = colors.HexColor("#111111")
DARK    = colors.HexColor("#222222")
MID     = colors.HexColor("#555555")
LIGHT   = colors.HexColor("#888888")
RED     = colors.HexColor("#c0281a")
BLUE    = colors.HexColor("#1a3c8f")
CREAM   = colors.HexColor("#fdfdf8")
RULE    = colors.HexColor("#222222")


def build_styles() -> dict:
    base = getSampleStyleSheet()

    def ps(name, **kw):
        return ParagraphStyle(name, **kw)

    return {
        "masthead": ps("masthead",
            fontName="Times-Bold", fontSize=46, leading=52,
            textColor=BLACK, alignment=TA_CENTER, spaceAfter=0),

        "masthead_sub": ps("masthead_sub",
            fontName="Times-Roman", fontSize=9, leading=12,
            textColor=MID, alignment=TA_CENTER),

        "kicker": ps("kicker",
            fontName="Helvetica-Bold", fontSize=7.5, leading=9,
            textColor=RED, spaceBefore=6, spaceAfter=1,
            fontStyle="normal"),

        "headline": ps("headline",
            fontName="Times-Bold", fontSize=22, leading=25,
            textColor=BLACK, spaceBefore=4, spaceAfter=4),

        "deck": ps("deck",
            fontName="Times-Italic", fontSize=11, leading=14,
            textColor=DARK, spaceAfter=6),

        "byline": ps("byline",
            fontName="Helvetica-Bold", fontSize=7, leading=9,
            textColor=MID, spaceBefore=2, spaceAfter=4),

        "dateline": ps("dateline",
            fontName="Helvetica-Bold", fontSize=7.5, leading=10,
            textColor=LIGHT),

        "body": ps("body",
            fontName="Times-Roman", fontSize=9, leading=13,
            textColor=DARK, alignment=TA_JUSTIFY, spaceAfter=6),

        "body_first": ps("body_first",
            fontName="Times-Roman", fontSize=9, leading=13,
            textColor=DARK, alignment=TA_JUSTIFY,
            firstLineIndent=0, spaceAfter=6),

        "caption": ps("caption",
            fontName="Helvetica-Oblique", fontSize=7.5, leading=10,
            textColor=MID, alignment=TA_CENTER, spaceBefore=3, spaceAfter=4),

        "chart_head": ps("chart_head",
            fontName="Times-Bold", fontSize=11, leading=14,
            textColor=BLACK, alignment=TA_LEFT, spaceBefore=6, spaceAfter=2),

        "pull_quote": ps("pull_quote",
            fontName="Times-Italic", fontSize=13, leading=17,
            textColor=BLUE, alignment=TA_CENTER,
            leftIndent=8, rightIndent=8,
            spaceBefore=8, spaceAfter=8,
            borderColor=BLUE, borderWidth=1,
            borderPadding=(6, 6, 6, 6)),

        "sidebar_head": ps("sidebar_head",
            fontName="Helvetica-Bold", fontSize=8.5, leading=11,
            textColor=colors.white, spaceAfter=3),

        "sidebar_body": ps("sidebar_body",
            fontName="Helvetica", fontSize=7.5, leading=10.5,
            textColor=colors.white, spaceAfter=2),

        "table_header": ps("table_header",
            fontName="Helvetica-Bold", fontSize=7.5, leading=10,
            textColor=colors.white, alignment=TA_CENTER),

        "table_cell": ps("table_cell",
            fontName="Helvetica", fontSize=7.5, leading=10,
            textColor=DARK, alignment=TA_CENTER),
    }


class ThickRule(Flowable):
    """A horizontal rule with variable thickness."""
    def __init__(self, width, thickness=1.5, color=RULE, spaceAfter=4):
        super().__init__()
        self.rule_width = width
        self.thickness = thickness
        self.rule_color = color
        self._spaceAfter = spaceAfter
        self.height = thickness + spaceAfter

    def draw(self):
        self.canv.setFillColor(self.rule_color)
        self.canv.rect(0, self._spaceAfter, self.rule_width, self.thickness,
                       stroke=0, fill=1)

    def wrap(self, availW, availH):
        return (self.rule_width, self.height)


def stats_table(df: pd.DataFrame, styles: dict) -> Table:
    """Build a compact summary table for the PDF."""
    temp_col = next((c for c in df.columns if "temp"  in c.lower()), None)
    hum_col  = next((c for c in df.columns if "humid" in c.lower()), None)
    loc_col  = next((c for c in df.columns if any(k in c.lower()
                     for k in ("location","room","place","zone"))), None)
    time_col = df.columns[0]

    df2 = df.copy()
    df2["_temp"] = pd.to_numeric(df2[temp_col], errors="coerce")
    df2["_hum"]  = pd.to_numeric(df2[hum_col],  errors="coerce")

    HDR_BG = BLUE
    ALT_BG = colors.HexColor("#eef2ff")

    header = [
        Paragraph("Time",        styles["table_header"]),
        Paragraph("Location",    styles["table_header"]),
        Paragraph("Temp (°C)",   styles["table_header"]),
        Paragraph("Humid. (%)",  styles["table_header"]),
    ]
    rows = [header]
    for i, (_, r) in enumerate(df2.iterrows()):
        loc_val = str(r[loc_col]) if loc_col else "—"
        rows.append([
            Paragraph(str(r[time_col]),        styles["table_cell"]),
            Paragraph(loc_val,                 styles["table_cell"]),
            Paragraph(f"{r['_temp']:.1f}",     styles["table_cell"]),
            Paragraph(f"{int(r['_hum'])}",     styles["table_cell"]),
        ])

    col_widths = [30*mm, 38*mm, 28*mm, 28*mm]
    t = Table(rows, colWidths=col_widths, repeatRows=1)
    style = TableStyle([
        ("BACKGROUND",  (0,0), (-1,0),  HDR_BG),
        ("TEXTCOLOR",   (0,0), (-1,0),  colors.white),
        ("FONTNAME",    (0,0), (-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",    (0,0), (-1,0),  7.5),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, ALT_BG]),
        ("FONTNAME",    (0,1), (-1,-1), "Helvetica"),
        ("FONTSIZE",    (0,1), (-1,-1), 7.5),
        ("ALIGN",       (0,0), (-1,-1), "CENTER"),
        ("VALIGN",      (0,0), (-1,-1), "MIDDLE"),
        ("TOPPADDING",  (0,0), (-1,-1), 3),
        ("BOTTOMPADDING",(0,0),(-1,-1), 3),
        ("GRID",        (0,0), (-1,-1), 0.3, colors.HexColor("#cccccc")),
        ("LINEBELOW",   (0,0), (-1,0),  1.2, HDR_BG),
        ("BOX",         (0,0), (-1,-1), 0.5, colors.HexColor("#aaaaaa")),
    ])
    t.setStyle(style)
    return t


def page_header_footer(c: rl_canvas.Canvas, doc, styles: dict, today: str):
    """Draw masthead, rules, and footer on every page."""
    c.saveState()

    # ── masthead ─────────────────────────────────────────────────────────────
    y_top = PAGE_H - MARGIN_T
    c.setFillColor(BLACK)
    c.setFont("Times-Bold", 48)
    masthead = "PDF with graphs"
    c.drawCentredString(PAGE_W / 2, y_top - 42, masthead)

    c.setFont("Times-Roman", 8)
    c.setFillColor(MID)
    sub = f"ESTABLISHED 2024  ·  {today.upper()}  ·  CLIMATE MONITORING SPECIAL EDITION  ·  COMPLIMENTARY"
    c.drawCentredString(PAGE_W / 2, y_top - 55, sub)

    # double rule under masthead
    c.setStrokeColor(BLACK)
    c.setLineWidth(2.5)
    c.line(MARGIN_L, y_top - 62, PAGE_W - MARGIN_R, y_top - 62)
    c.setLineWidth(0.6)
    c.line(MARGIN_L, y_top - 65, PAGE_W - MARGIN_R, y_top - 65)

    # ── footer ────────────────────────────────────────────────────────────────
    c.setLineWidth(0.6)
    c.setStrokeColor(RULE)
    c.line(MARGIN_L, MARGIN_B + 6, PAGE_W - MARGIN_R, MARGIN_B + 6)
    c.setFont("Helvetica", 6.5)
    c.setFillColor(LIGHT)
    c.drawString(MARGIN_L, MARGIN_B - 1,
                 "The Home Dispatch  ·  Climate Monitoring Edition")
    c.drawRightString(PAGE_W - MARGIN_R, MARGIN_B - 1,
                      f"Printed {today}  ·  Page {doc.page}")

    c.restoreState()


def build_pdf(df: pd.DataFrame, chart_drawing: Drawing, out_path: str):
    """Assemble the full newspaper-style PDF."""

    styles = build_styles()
    today  = datetime.date.today().strftime("%A, %B %-d, %Y")

    MASTHEAD_H = 68          # pt reserved at top for masthead
    FOOTER_H   = 18          # pt at bottom for footer
    CONTENT_W  = PAGE_W - MARGIN_L - MARGIN_R
    CONTENT_H  = PAGE_H - MARGIN_T - MASTHEAD_H - MARGIN_B - FOOTER_H

    # ── document ──────────────────────────────────────────────────────────────
    doc = SimpleDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T + MASTHEAD_H,
        bottomMargin=MARGIN_B + FOOTER_H,
        title="The Home Dispatch – Climate Monitoring Special",
        author="Home Automation System",
    )

    def on_page(c, d):
        page_header_footer(c, d, styles, today)

    story = []

    # ── 1. kicker + headline (full width) ────────────────────────────────────
    story.append(Paragraph("EXCLUSIVE INVESTIGATION", styles["kicker"]))
    story.append(ThickRule(CONTENT_W, thickness=0.5, color=colors.HexColor("#888"), spaceAfter=2))
    story.append(Paragraph(
        "This example shows how to generate PDF documents including graphs created from Markdown data.",
        styles["headline"],
    ))
    story.append(Paragraph(
        "Comprehensive 12-reading survey of Living Room, Bedroom and Kitchen reveals "
        "dramatic intraday swings — morning chill gives way to peak afternoon warmth, "
        "raising questions about home ventilation strategies.",
        styles["deck"],
    ))
    story.append(Paragraph("BY THE HOME DISPATCH DATA DESK  ·  CLIMATE CORRESPONDENT", styles["byline"]))
    story.append(ThickRule(CONTENT_W, thickness=2.0, color=BLACK, spaceAfter=6))

    # ── 2. Full-width chart (pure ReportLab vector graphics) ─────────────────
    story.append(chart_drawing)

    story.append(Paragraph(
        "FIGURE 1 — Hourly temperature readings (coloured markers) against humidity bars "
        "across three rooms. The shaded red band marks the 19–23 °C comfort zone; "
        "blue background shading denotes overnight hours.",
        styles["caption"],
    ))
    story.append(ThickRule(CONTENT_W, thickness=0.5, color=colors.HexColor("#bbb"), spaceAfter=6))

    # ── 3. Three-column body ──────────────────────────────────────────────────
    # Compute quick statistics for the text
    temp_col = next((c for c in df.columns if "temp"  in c.lower()), df.columns[2])
    hum_col  = next((c for c in df.columns if "humid" in c.lower()), df.columns[3])
    df["_t"] = pd.to_numeric(df[temp_col], errors="coerce")
    df["_h"] = pd.to_numeric(df[hum_col],  errors="coerce")
    t_min, t_max, t_mean = df["_t"].min(), df["_t"].max(), df["_t"].mean()
    h_min, h_max, h_mean = df["_h"].min(), df["_h"].max(), df["_h"].mean()

    col_body_texts = [
        # Column 1 – main analysis
        [
            Paragraph(
                f"<b>MARTIGNY-VILLE, {today.split(',')[0].upper()}</b> — "
                f"A full-day monitoring sweep of a residential property has produced "
                f"a detailed climate portrait rarely seen outside professional buildings. "
                f"Twelve discrete measurements, taken between 06:00 and 23:00 across "
                f"three rooms, reveal a textbook daily temperature cycle — rising steadily "
                f"from a morning low of {t_min:.1f} °C to an afternoon peak of "
                f"{t_max:.1f} °C before dropping back toward overnight lows.",
                styles["body_first"],
            ),
            Paragraph(
                f"The average temperature across all readings stood at "
                f"{t_mean:.1f} °C, marginally above the widely cited 20 °C threshold "
                f"for thermal comfort in temperate climates. Crucially, the warmest "
                f"room at any given hour was the Kitchen, which benefits from heat "
                f"produced by cooking appliances and solar gain through a south-facing window.",
                styles["body"],
            ),
            Paragraph(
                "Meanwhile, the Bedroom consistently recorded the coolest "
                "temperatures — a phenomenon well-documented in the sleep-science "
                "literature, which recommends sleeping environments between 16 °C "
                "and 19 °C for optimal rest. Residents will be pleased to note that "
                "the Bedroom readings remain within or just above this range "
                "throughout the survey period.",
                styles["body"],
            ),
        ],
        # Column 2 – humidity analysis + pull quote
        [
            Paragraph(
                "HUMIDITY SWINGS TRACKED ACROSS ZONES",
                styles["kicker"],
            ),
            Paragraph(
                f"Relative humidity showed an inverse relationship to temperature, "
                f"as expected from basic psychrometrics. At the morning low of {t_min:.1f} °C, "
                f"humidity reached {h_max:.0f} %, falling to a midday minimum of "
                f"{h_min:.0f} % as the air warmed and its capacity to hold moisture "
                f"increased. The mean relative humidity of {h_mean:.0f} % sits within "
                f"the 40–60 % band recommended by the World Health Organization for "
                f"indoor air quality.",
                styles["body"],
            ),
            Paragraph(
                '"A well-ventilated home should show exactly this kind of '
                'inverse temperature-humidity rhythm throughout the day."',
                styles["pull_quote"],
            ),
            Paragraph(
                "Prolonged exposure to humidity above 65 % can encourage "
                "mould growth and dust-mite proliferation, two of the leading "
                "causes of respiratory irritation in domestic settings. The data "
                "suggest that, while early-morning readings edge toward this "
                "boundary in the Bedroom, the house recovers quickly once "
                "heating begins.",
                styles["body"],
            ),
        ],
        # Column 3 – sensor notes + methodology
        [
            Paragraph("SENSORS &amp; METHODOLOGY", styles["kicker"]),
            Paragraph(
                "All readings were obtained via calibrated DHT-22 sensors, "
                "accurate to ±0.5 °C and ±2 % RH. Timestamps reflect local "
                "civil time. Data were logged to a Raspberry Pi home server "
                "and cross-checked against a reference thermometer placed "
                "in each room for the duration of the survey.",
                styles["body"],
            ),
            Paragraph(
                "Sensors were mounted at seated head-height (approximately "
                "1.2 m) away from direct sunlight, radiators, and draughts. "
                "The Kitchen sensor was positioned above the worktop, clear "
                "of steam sources. No readings were discarded or adjusted.",
                styles["body"],
            ),
            Paragraph(
                "Full raw data, including sensor calibration certificates "
                "and server logs, are available on request from the Home "
                "Automation Data Desk.",
                styles["body"],
            ),
        ],
    ]

    # Lay the three columns out as a single-row Table
    col_table = Table(
        [col_body_texts],
        colWidths=[COL_W, COL_W, COL_W],
        hAlign="LEFT",
    )
    col_table.setStyle(TableStyle([
        ("VALIGN",       (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING",  (0,0), (-1,-1), 4),
        ("RIGHTPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING",   (0,0), (-1,-1), 0),
        ("BOTTOMPADDING",(0,0), (-1,-1), 0),
        ("LINEBEFORE",   (1,0), (2,0),   0.5, colors.HexColor("#cccccc")),
    ]))
    story.append(col_table)

    # ── 4. Centered data table (natural width, below columns) ─────────────────
    story.append(Spacer(1, 8))
    story.append(ThickRule(CONTENT_W, thickness=0.5, color=colors.HexColor("#bbb"), spaceAfter=6))
    
    # Wrap kicker + table + caption in KeepTogether to prevent page splits
    tbl = stats_table(df, styles)
    tbl.hAlign = "CENTER"
    
    table_block = KeepTogether([
        Paragraph("RAW READINGS AT A GLANCE", styles["kicker"]),
        tbl,
        Paragraph(
            "All 12 readings from the survey period, ordered by time of day.",
            styles["caption"],
        ),
    ])
    story.append(table_block)

    # ── 5. Build the PDF ──────────────────────────────────────────────────────
    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
    print(f"✓ PDF written to {out_path}")


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main():
    # Load markdown table
    if len(sys.argv) > 1:
        md_text = Path(sys.argv[1]).read_text(encoding="utf-8")
    else:
        md_text = SAMPLE_TABLE
        print("No file argument supplied — using built-in sample table.\n")

    print("Parsing Markdown table …")
    df = parse_markdown_table(md_text)
    print(df.to_string(index=False))
    print()

    out_path = "output.pdf"
    
    # Calculate width needed for the chart
    CONTENT_W = A4[0] - 2 * 18 * mm
    
    print("Generating vector chart (ReportLab) …")
    chart_drawing = generate_chart_reportlab(df, CONTENT_W)

    print(f"Composing newspaper-style PDF → {out_path} …")
    build_pdf(df, chart_drawing, out_path)


if __name__ == "__main__":
    main()
