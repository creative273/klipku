"""Preset gaya caption. 6 bawaan (desain sendiri, font OFL yang dibundel) + preset buatan user."""
import db

DEFAULT_ID = "builtin-tegas-kuning"

# font_size diukur pada tinggi output 1920 px
BUILTIN = [
    {"id": "builtin-tegas-kuning", "name": "Tegas Kuning", "font_family": "Poppins ExtraBold", "font_size": 92,
     "color": "#FFFFFF", "highlight_color": "#FFD400", "outline_color": "#000000", "outline_px": 6,
     "bold": 0, "uppercase": 1, "box": 0, "words_per_line": 3, "position_y": 0.70},
    {"id": "builtin-putih-bersih", "name": "Putih Bersih", "font_family": "Poppins SemiBold", "font_size": 78,
     "color": "#FFFFFF", "highlight_color": "#FFFFFF", "outline_color": "#000000", "outline_px": 4,
     "bold": 0, "uppercase": 0, "box": 0, "words_per_line": 4, "position_y": 0.72},
    {"id": "builtin-kotak-hitam", "name": "Kotak Hitam", "font_family": "Poppins ExtraBold", "font_size": 80,
     "color": "#FFFFFF", "highlight_color": "#FFD400", "outline_color": "#000000", "outline_px": 14,
     "bold": 0, "uppercase": 1, "box": 1, "words_per_line": 3, "position_y": 0.70},
    {"id": "builtin-anton-hijau", "name": "Anton Hijau", "font_family": "Anton", "font_size": 120,
     "color": "#FFFFFF", "highlight_color": "#3DFF8A", "outline_color": "#000000", "outline_px": 6,
     "bold": 0, "uppercase": 1, "box": 0, "words_per_line": 2, "position_y": 0.68},
    {"id": "builtin-bebas-merah", "name": "Bebas Merah", "font_family": "Bebas Neue", "font_size": 140,
     "color": "#FFFFFF", "highlight_color": "#FF4040", "outline_color": "#000000", "outline_px": 6,
     "bold": 0, "uppercase": 1, "box": 0, "words_per_line": 3, "position_y": 0.68},
    {"id": "builtin-satu-kata", "name": "Satu Kata", "font_family": "Archivo Black", "font_size": 128,
     "color": "#FFFFFF", "highlight_color": "#FFD400", "outline_color": "#000000", "outline_px": 7,
     "bold": 0, "uppercase": 1, "box": 0, "words_per_line": 1, "position_y": 0.66},
]

FIELDS = ["name", "font_family", "font_size", "color", "highlight_color", "outline_color", "outline_px",
          "bold", "uppercase", "box", "words_per_line", "position_y"]

FONT_FILES = {
    "Poppins ExtraBold": "Poppins-ExtraBold.ttf",
    "Poppins SemiBold": "Poppins-SemiBold.ttf",
    "Anton": "Anton-Regular.ttf",
    "Bebas Neue": "BebasNeue-Regular.ttf",
    "Archivo Black": "ArchivoBlack-Regular.ttf",
}


def seed():
    """Pasang/perbarui preset bawaan. Preset buatan user tidak disentuh."""
    for st in BUILTIN:
        cols = ["id", *FIELDS, "builtin", "created_at", "updated_at"]
        vals = [st["id"], *[st[f] for f in FIELDS], 1, db.now(), db.now()]
        db.exec_(
            f"INSERT INTO caption_styles({', '.join(cols)}) VALUES({', '.join('?' * len(cols))}) "
            "ON CONFLICT(id) DO UPDATE SET " + ", ".join(f"{f}=excluded.{f}" for f in FIELDS),
            vals,
        )


def get(style_id):
    s = db.one("SELECT * FROM caption_styles WHERE id=?", (style_id or DEFAULT_ID,))
    return s or db.one("SELECT * FROM caption_styles WHERE id=?", (DEFAULT_ID,))


def all_():
    return db.all_("SELECT * FROM caption_styles ORDER BY builtin DESC, created_at")
