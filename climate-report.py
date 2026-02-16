"""
climate_report.py
─────────────────────────────────────────────────────────────
Reads a Markdown-formatted table of temperature / humidity
readings, produces a vector SVG chart via matplotlib, and
assembles a newspaper-front-page PDF using ReportLab.

Open-source libraries used
  • matplotlib  – SVG chart generation
  • reportlab   – PDF typesetting
  • pandas       – Markdown-table parsing helper

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

import matplotlib
matplotlib.use("Agg")                           # headless backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.dates as mdates
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
# svglib not available; we embed a high-DPI PNG buffer instead


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
# STEP 2 – Generate the SVG chart  (dual-axis, colour-coded by room)
# ──────────────────────────────────────────────────────────────────────────────

ROOM_COLORS = {
    "Living Room": "#e05c2d",
    "Bedroom":     "#2d6ee0",
    "Kitchen":     "#27a65c",
}
DEFAULT_COLOR = "#888888"


def generate_chart_svg(df: pd.DataFrame) -> bytes:
    """Return a high-DPI PNG byte-string of the dual-axis chart."""

    # Identify columns by keywords
    time_col  = next((c for c in df.columns if "time"  in c.lower()), df.columns[0])
    loc_col   = next((c for c in df.columns if any(k in c.lower() for k in ("location","room","place","zone"))), None)
    temp_col  = next((c for c in df.columns if "temp"  in c.lower()), None)
    hum_col   = next((c for c in df.columns if "humid" in c.lower()), None)

    if temp_col is None or hum_col is None:
        raise ValueError("Cannot detect Temperature and Humidity columns.")

    # Convert time strings like "06:00" to numeric hours for axis
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

    # ── figure layout ────────────────────────────────────────────────────────
    fig, ax1 = plt.subplots(figsize=(10, 4.6), dpi=150)
    ax2 = ax1.twinx()

    fig.patch.set_facecolor("#fafaf8")
    ax1.set_facecolor("#fafaf8")

    # ── background shading ───────────────────────────────────────────────────
    ax1.axvspan(0, 7,  alpha=0.06, color="#4488cc", zorder=0)
    ax1.axvspan(22, 24, alpha=0.06, color="#4488cc", zorder=0)

    # ── comfort-zone band ────────────────────────────────────────────────────
    ax1.axhspan(19, 23, alpha=0.08, color="#e05c2d", zorder=0, label="_comfort")
    ax1.text(23.3, 21, "Comfort\nzone", fontsize=6.5, color="#c04810",
             va="center", ha="left", style="italic")

    # ── humidity band ────────────────────────────────────────────────────────
    ax2.axhspan(40, 60, alpha=0.07, color="#2d6ee0", zorder=0)

    # Sort by time
    df.sort_values("_hour", inplace=True)

    # ── temperature line ─────────────────────────────────────────────────────
    ax1.plot(df["_hour"], df["_temp"],
             color="#888", linewidth=1.4, linestyle="--",
             zorder=2, alpha=0.5)

    # ── per-location scatter markers ─────────────────────────────────────────
    for loc in df["_loc"].unique():
        sub = df[df["_loc"] == loc]
        c = ROOM_COLORS.get(loc, DEFAULT_COLOR)
        ax1.scatter(sub["_hour"], sub["_temp"],
                    color=c, s=90, zorder=5, edgecolors="white",
                    linewidths=0.8, label=loc)

    # ── humidity bars ────────────────────────────────────────────────────────
    bar_colors = [ROOM_COLORS.get(r, DEFAULT_COLOR) for r in df["_loc"]]
    bars = ax2.bar(df["_hour"], df["_hum"],
                   width=0.55, color=bar_colors, alpha=0.22,
                   zorder=1, edgecolor="none")

    # ── value annotations ────────────────────────────────────────────────────
    for _, row in df.iterrows():
        ax1.annotate(
            f"{row['_temp']:.1f}°",
            (row["_hour"], row["_temp"]),
            textcoords="offset points", xytext=(0, 9),
            fontsize=6.5, ha="center", color="#333",
            fontweight="bold",
        )

    # ── axes styling ─────────────────────────────────────────────────────────
    hours = [0, 3, 6, 9, 12, 15, 18, 21, 24]
    labels = ["Midnight","3 AM","6 AM","9 AM","Noon","3 PM","6 PM","9 PM","Midnight"]
    ax1.set_xticks(hours)
    ax1.set_xticklabels(labels, fontsize=7.5, color="#444")
    ax1.set_xlim(0, 24)
    ax1.set_ylim(14, 28)
    ax2.set_ylim(35, 80)

    ax1.set_ylabel("Temperature (°C)", fontsize=8.5, color="#c04810", labelpad=8)
    ax2.set_ylabel("Humidity (%)", fontsize=8.5, color="#2d5fb0", labelpad=8)

    ax1.tick_params(axis="y", labelcolor="#c04810", labelsize=8)
    ax2.tick_params(axis="y", labelcolor="#2d5fb0", labelsize=8)

    ax1.yaxis.label.set_color("#c04810")
    ax2.yaxis.label.set_color("#2d5fb0")

    for spine in ["top"]:
        ax1.spines[spine].set_visible(False)
        ax2.spines[spine].set_visible(False)
    ax1.spines["left"].set_color("#c04810")
    ax2.spines["right"].set_color("#2d5fb0")
    ax1.spines["bottom"].set_color("#ccc")

    ax1.grid(axis="y", linestyle=":", linewidth=0.6, color="#ddd", alpha=0.8)
    ax1.set_axisbelow(True)

    # ── legend ───────────────────────────────────────────────────────────────
    loc_handles, loc_labels = ax1.get_legend_handles_labels()
    temp_line = mpatches.Patch(color="#e05c2d", alpha=0.5, label="Temperature (°C)")
    hum_bar   = mpatches.Patch(color="#2d6ee0", alpha=0.35, label="Humidity (%)")
    leg = ax1.legend(
        loc_handles + [hum_bar],
        loc_labels + ["Humidity (%)"],
        fontsize=7.5, frameon=True, framealpha=0.9,
        edgecolor="#ddd", loc="upper right",
        bbox_to_anchor=(1.0, 1.0),
        handletextpad=0.5, borderpad=0.6,
    )

    # ── title ────────────────────────────────────────────────────────────────
    ax1.set_title(
        "Indoor Climate — 24-hour Reading  ·  Temperature & Relative Humidity by Room",
        fontsize=9, loc="left", pad=10, color="#222",
        fontweight="bold",
    )

    plt.tight_layout(pad=1.2)

    # ── save as high-DPI PNG in memory ───────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=220)
    plt.close(fig)
    buf.seek(0)
    return buf.read()


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


def chart_image(png_bytes: bytes, width_pt: float) -> RLImage:
    """Wrap PNG bytes as a ReportLab Image at the given display width."""
    buf = io.BytesIO(png_bytes)
    # Let ReportLab read the image dimensions, then scale to width_pt
    from PIL import Image as PILImage
    im = PILImage.open(buf)
    w_px, h_px = im.size
    aspect = h_px / w_px
    buf.seek(0)
    return RLImage(buf, width=width_pt, height=width_pt * aspect)


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
    masthead = "The Home Dispatch"
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


def build_pdf(df: pd.DataFrame, png_bytes: bytes, out_path: str):
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
        "House Temperatures Surge Past 24°C in Afternoon Hours as Humidity Dips to Record Low",
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

    # ── 2. Full-width chart ───────────────────────────────────────────────────
    chart_w = CONTENT_W
    try:
        img = chart_image(png_bytes, chart_w)   # png_bytes holds high-DPI PNG data
        story.append(img)
    except Exception as e:
        story.append(Paragraph(f"[Chart could not be rendered: {e}]", styles["body"]))

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
        # Column 3 – data table + sidebar teaser
        [
            Paragraph("RAW READINGS AT A GLANCE", styles["kicker"]),
            stats_table(df, styles),
            Spacer(1, 6),
            Paragraph(
                "All readings obtained via calibrated DHT-22 sensors. "
                "Timestamps reflect local civil time. Data logged to a "
                "Raspberry Pi home server and cross-checked against a "
                "reference thermometer in each room.",
                styles["caption"],
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

    # ── 4. Build the PDF ──────────────────────────────────────────────────────
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

    print("Generating high-DPI chart (PNG) …")
    png_bytes = generate_chart_svg(df)

    out_path = "output.pdf"
    print(f"Composing newspaper-style PDF → {out_path} …")
    build_pdf(df, png_bytes, out_path)


if __name__ == "__main__":
    main()
