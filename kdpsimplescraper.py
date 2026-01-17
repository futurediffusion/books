"""
Gutenberg HTML → clean TXT (UTF-8) with a simple Glassmorph PySide6 UI.

Goals:
- Download HTML (or load local .html/.htm)
- Extract headings, paragraphs, lists, blockquotes, <pre>, and poetry-ish blocks
- Preserve meaningful line breaks (poems, pre, blockquotes)
- BUT: collapse accidental newlines inside normal paragraphs (browser ignores them; parsers shouldn't keep them)
- Repair classic mojibake (UTF-8 mis-decoded as Latin-1/CP1252)
- Export UTF-8 .txt

Install:
  pip install pyside6 beautifulsoup4 charset-normalizer

Run:
  python gutenberg_cleaner_app.py
"""

from __future__ import annotations

import html as html_lib
import pathlib
import re
import sys
import urllib.request
from urllib.parse import urljoin, urlparse, urlunparse
from dataclasses import dataclass
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag
from PySide6 import QtCore, QtWidgets

try:
    from charset_normalizer import from_bytes as cn_from_bytes
except Exception:
    cn_from_bytes = None


# ------------------------------
# Text utilities
# ------------------------------

MOJIBAKE_HINT_RE = re.compile(r"[ÃÂâ€˜â€™â€œâ€�â€¢â€¦]|\\x[0-9a-fA-F]{2}")

def repair_mojibake(s: str) -> str:
    """Repair classic 'UTF-8 bytes decoded as Latin-1' mojibake."""
    if not s:
        return s
    if not MOJIBAKE_HINT_RE.search(s):
        return s
    try:
        repaired = s.encode("latin-1", errors="strict").decode("utf-8", errors="strict")
        return repaired
    except Exception:
        return s


def normalize_spaces_keep_newlines(s: str) -> str:
    """Normalize whitespace but keep \n. Good for poetry/pre/quotes."""
    if not s:
        return s
    s = s.replace("\u00a0", " ")  # NBSP
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[\t\f\v]+", " ", s)
    # collapse multiple spaces (not newlines)
    s = re.sub(r"[ ]{2,}", " ", s)
    return s


def normalize_spaces_singleline(s: str) -> str:
    """Normalize whitespace and collapse any newlines to spaces. Good for normal paragraphs."""
    if not s:
        return s
    s = s.replace("\u00a0", " ")
    s = s.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    s = re.sub(r"[\t\f\v]+", " ", s)
    s = re.sub(r"[ ]{2,}", " ", s)
    return s.strip()


def strip_line(s: str) -> str:
    return s.strip(" \t")


def is_noise_line(line: str) -> bool:
    """Conservative: keep most content. Extend if you want to drop Gutenberg boilerplate."""
    t = line.strip()
    if not t:
        return False
    # Don't aggressively remove; false positives are expensive.
    return False


# ------------------------------
# Extraction options
# ------------------------------

@dataclass
class ExtractOptions:
    keep_br_as_newline: bool = True
    preserve_preformatted: bool = True
    preserve_poetry: bool = True
    keep_footnotes: bool = True  # placeholder; you can wire this later


POETRY_CLASS_HINTS = {"poetry", "verse", "stanza", "poem"}


def looks_like_poetry_block(tag: Tag) -> bool:
    cls = " ".join(tag.get("class", [])).lower()
    if any(h in cls for h in POETRY_CLASS_HINTS):
        return True
    # Gutenberg often: <div class="poetry"> or <p class="poetry">
    if tag.name in {"div", "p"} and "poetry" in cls:
        return True
    return False


def remove_non_content(soup: BeautifulSoup) -> None:
    # Drop scripts/styles/nav.
    for bad in soup.select("script, style, nav, header, footer"):
        bad.decompose()

    # Drop obvious PG header/footer blocks if present.
    for bad in soup.select("div#pg-header, div#pg-footer"):
        bad.decompose()

    # Remove images/figures (text-only export)
    for bad in soup.select("img, svg, figure"):
        bad.decompose()


def select_main_container(soup: BeautifulSoup) -> Tag:
    """Pick best container for actual book body; fallback to <body>."""
    selectors = [
        "div#body", "div#main", "div#content", "div#pg-body",
        "div#book", "div.book", "div#text", "div.text",
        "div#chapter", "div.chapter"
    ]
    for sel in selectors:
        t = soup.select_one(sel)
        if t and len(t.get_text(strip=True)) > 1000:
            return t
    return soup.body if soup.body else soup


# ------------------------------
# Core extraction
# ------------------------------

BLOCK_TAGS = ["h1","h2","h3","h4","h5","p","pre","blockquote","ul","ol","hr","div"]

def extract_clean_text(html_text: str, opt: ExtractOptions) -> str:
    """Convert Gutenberg-ish HTML into clean TXT while preserving structure."""

    html_text = repair_mojibake(html_text)

    soup = BeautifulSoup(html_text, "html.parser")
    remove_non_content(soup)
    container = select_main_container(soup)

    blocks: list[str] = []

    def add_block(text_in: str, preserve_newlines: bool) -> None:
        if not text_in:
            return
        t = html_lib.unescape(text_in)
        t = repair_mojibake(t)

        if preserve_newlines:
            t = normalize_spaces_keep_newlines(t)
            lines = [strip_line(x) for x in t.split("\n")]
            # trim outer empty lines
            while lines and not lines[0]:
                lines.pop(0)
            while lines and not lines[-1]:
                lines.pop()
            if not lines:
                return
            # conservative noise filtering line by line
            lines = [ln for ln in lines if not is_noise_line(ln)]
            if not lines:
                return
            block = "\n".join(lines)
        else:
            block = normalize_spaces_singleline(t)
            if not block:
                return

        blocks.append(block)

    def handle(tag: Tag) -> None:
        name = (tag.name or "").lower()

        # headings
        if name in {"h1","h2","h3","h4","h5"}:
            txt = tag.get_text(" ", strip=True)
            if txt:
                add_block(txt.upper() if len(txt) <= 80 else txt, preserve_newlines=False)
            return

        if name == "hr":
            blocks.append("")  # section break
            return

        if name in {"ul","ol"}:
            items = []
            for li in tag.find_all("li", recursive=False):
                it = li.get_text(" ", strip=True)
                if it:
                    items.append(f"- {it}")
            if items:
                add_block("\n".join(items), preserve_newlines=True)
            return

        if name == "pre" and opt.preserve_preformatted:
            txt = tag.get_text("\n", strip=False)
            txt = txt.replace("\r\n", "\n").replace("\r", "\n")
            txt = re.sub(r"\n{3,}", "\n\n", txt)
            add_block(txt, preserve_newlines=True)
            return

        if name == "blockquote":
            txt = tag.get_text("\n", strip=False)
            txt = txt.replace("\r\n", "\n").replace("\r", "\n")
            txt = re.sub(r"\n{3,}", "\n\n", txt)
            add_block(txt, preserve_newlines=True)
            return

        # Poetry-like blocks
        if opt.preserve_poetry and looks_like_poetry_block(tag):
            txt = tag.get_text("\n", strip=False)  # keep <br> as newline
            txt = txt.replace("\r\n", "\n").replace("\r", "\n")
            txt = re.sub(r"\n{3,}", "\n\n", txt)
            add_block(txt, preserve_newlines=True)
            return

        # paragraph-ish: p and some divs
        if name in {"p","div"}:
            # Avoid flattening container divs that hold other blocks directly
            if name == "div":
                nested = tag.find(
                    ["p","h1","h2","h3","h4","h5","pre","ul","ol","blockquote"],
                    recursive=False
                )
                if nested is not None:
                    return

            br_count = len(tag.find_all("br"))
            if opt.keep_br_as_newline and br_count > 0:
                txt = tag.get_text("\n", strip=False)
                add_block(txt, preserve_newlines=True)
            else:
                # IMPORTANT: do NOT preserve accidental newlines inside paragraph.
                txt = tag.get_text(" ", strip=False)
                add_block(txt, preserve_newlines=False)
            return

    # Safer traversal: process only meaningful block tags in document order.
    # Using find_all keeps order, avoids .descendants double-processing text.
    for el in container.find_all(BLOCK_TAGS):
        if isinstance(el, Tag):
            handle(el)

    # Stitch with blank lines between blocks
    out = "\n\n".join([b for b in blocks if b is not None])
    # reduce excessive blank lines
    out = re.sub(r"\n{4,}", "\n\n\n", out)
    # strip trailing spaces per line
    out = "\n".join([line.rstrip() for line in out.split("\n")])

    return out.strip() + "\n"


# ------------------------------
# Download / load helpers
# ------------------------------

def fetch_url_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) GutenbergCleaner/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def decode_html_bytes(data: bytes) -> str:
    """Decode bytes robustly, favoring UTF-8 (what Gutenberg uses)."""
    try:
        return data.decode("utf-8", errors="strict")
    except Exception:
        pass

    if cn_from_bytes is not None:
        try:
            best = cn_from_bytes(data).best()
            if best is not None:
                return str(best)
        except Exception:
            pass

    return data.decode("cp1252", errors="replace")


WIKISOURCE_EXCLUDED_NAMESPACES = {
    "special",
    "help",
    "file",
    "category",
    "talk",
    "template",
    "portal",
}

WIKISOURCE_TEXT_NOISE = (
    "retrieved from",
    "public domain",
    "categories",
)


def _strip_fragment(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(fragment=""))


def _ensure_action_render(url: str) -> str:
    parsed = urlparse(url)
    query = parsed.query
    if "action=render" in query:
        return url
    new_query = f"{query}&action=render" if query else "action=render"
    return urlunparse(parsed._replace(query=new_query))


def _is_wikisource_url(url: str) -> bool:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    return host == "wikisource.org" or host.endswith(".wikisource.org")


def extract_wikisource_chapter_links(html_text: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html_text, "html.parser")
    container = soup.select_one("#mw-content-text") or soup.select_one("div#content") or soup
    base_parsed = urlparse(base_url)
    base_root = f"{base_parsed.scheme}://{base_parsed.netloc}"
    links: list[str] = []
    seen: set[str] = set()

    for a in container.select("a[href]"):
        href = a.get("href", "").strip()
        if not href:
            continue
        if href.startswith("/wiki/"):
            href = href
        elif href.startswith(base_root + "/wiki/"):
            href = href[len(base_root):]
        else:
            continue

        href = _strip_fragment(href)
        title = href.split("/wiki/", 1)[-1]
        if ":" in title:
            namespace = title.split(":", 1)[0].lower()
            if namespace in WIKISOURCE_EXCLUDED_NAMESPACES:
                continue

        abs_url = urljoin(base_root, href)
        if urlparse(abs_url).netloc != base_parsed.netloc:
            continue
        if abs_url in seen:
            continue
        if _strip_fragment(abs_url) == _strip_fragment(base_url):
            continue
        seen.add(abs_url)
        links.append(abs_url)

    return links


def _extract_wikisource_title(soup: BeautifulSoup, content: Optional[Tag]) -> str:
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    if content:
        heading = content.find(["h1", "h2", "h3", "h4", "h5", "h6"])
        if heading and heading.get_text(strip=True):
            return heading.get_text(" ", strip=True)
    title_tag = soup.title.get_text(" ", strip=True) if soup.title else ""
    return title_tag or "Capítulo"


def _clean_wikisource_content(html_text: str, opt: ExtractOptions) -> tuple[str, str]:
    soup = BeautifulSoup(html_text, "html.parser")
    content = soup.select_one("#mw-content-text") or soup.select_one("div#content")
    if content is None:
        content = soup.body or soup

    for bad in content.select(
        ".mw-editsection, .toc, #toc, .navbox, .vertical-navbox, .metadata, "
        ".sistersitebox, .authority-control, .mw-references-wrap, .reflist, "
        ".reference, .catlinks, #catlinks, .printfooter, .noprint, .ws-noexport, "
        ".hatnote, .thumb, .gallery, .ambox"
    ):
        bad.decompose()

    title = _extract_wikisource_title(soup, content)
    cleaned = extract_clean_text(str(content), opt)
    if not cleaned:
        return title, ""

    lines = []
    for line in cleaned.splitlines():
        if any(noise in line.strip().lower() for noise in WIKISOURCE_TEXT_NOISE):
            continue
        lines.append(line)
    filtered = "\n".join(lines).strip()
    return title, (filtered + "\n" if filtered else "")


def _format_wikisource_section(title: str, url: str, body: str) -> str:
    divider = "============================================================"
    header = f"\n\n{divider}\n{title}\n{url}\n{divider}\n\n"
    return f"{header}{body.rstrip()}\n"


def _collect_wikisource_chapters(index_url: str, opt: ExtractOptions) -> list[tuple[str, str, str]]:
    data = fetch_url_bytes(index_url)
    html_text = decode_html_bytes(data)
    first_level = extract_wikisource_chapter_links(html_text, index_url)
    if not first_level:
        return []

    chapters: list[tuple[str, str, str]] = []
    seen: set[str] = set()

    for link in first_level:
        if link in seen:
            continue
        seen.add(link)
        render_url = _ensure_action_render(link)
        volume_html = decode_html_bytes(fetch_url_bytes(render_url))
        sublinks = extract_wikisource_chapter_links(volume_html, link)

        if len(sublinks) >= 4:
            for sublink in sublinks:
                if sublink in seen:
                    continue
                seen.add(sublink)
                sub_render = _ensure_action_render(sublink)
                chapter_html = decode_html_bytes(fetch_url_bytes(sub_render))
                title, body = _clean_wikisource_content(chapter_html, opt)
                if body:
                    chapters.append((title, sublink, body))
        else:
            title, body = _clean_wikisource_content(volume_html, opt)
            if body:
                chapters.append((title, link, body))

    return chapters


# ------------------------------
# UI
# ------------------------------

class GlassButton(QtWidgets.QPushButton):
    def __init__(self, text: str):
        super().__init__(text)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        self.setMinimumHeight(40)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gutenberg HTML → Clean TXT (UTF-8)")
        self.resize(980, 680)
        self._last_text: Optional[str] = None
        self._build_ui()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        central.setObjectName("bg")
        self.setCentralWidget(central)

        root = QtWidgets.QVBoxLayout(central)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        title = QtWidgets.QLabel("Gutenberg HTML Cleaner")
        title.setStyleSheet("font-size: 22px; font-weight: 700;")

        subtitle = QtWidgets.QLabel(
            "Descarga HTML o carga un archivo local, extrae texto útil y exporta TXT UTF-8.\n"
            "Colapsa saltos accidentales en párrafos (fix 'by / nature') y preserva poesía/pre/citas."
        )
        subtitle.setStyleSheet("opacity: 0.85;")
        subtitle.setWordWrap(True)

        card = QtWidgets.QFrame()
        card.setObjectName("card")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(10)

        # URL row
        url_row = QtWidgets.QHBoxLayout()
        self.url_edit = QtWidgets.QLineEdit()
        self.url_edit.setPlaceholderText("Pega el URL HTML (ej: https://www.gutenberg.org/files/10661/10661-h/10661-h.htm)")
        self.url_edit.setText("https://www.gutenberg.org/files/10661/10661-h/10661-h.htm")
        self.btn_download = GlassButton("Descargar + Convertir")
        self.btn_download.clicked.connect(self.on_download_convert)
        url_row.addWidget(self.url_edit, 1)
        url_row.addWidget(self.btn_download, 0)

        # Local file row
        file_row = QtWidgets.QHBoxLayout()
        self.file_edit = QtWidgets.QLineEdit()
        self.file_edit.setPlaceholderText("…o carga un .htm/.html local")
        self.btn_browse = GlassButton("Elegir archivo")
        self.btn_browse.clicked.connect(self.on_browse)
        self.btn_convert_file = GlassButton("Convertir archivo")
        self.btn_convert_file.clicked.connect(self.on_convert_file)
        file_row.addWidget(self.file_edit, 1)
        file_row.addWidget(self.btn_browse, 0)
        file_row.addWidget(self.btn_convert_file, 0)

        # Output row
        out_row = QtWidgets.QHBoxLayout()
        self.out_edit = QtWidgets.QLineEdit()
        self.out_edit.setPlaceholderText("Ruta de salida .txt")
        self.out_edit.setText(str(pathlib.Path.cwd() / "book_clean.txt"))
        self.btn_out = GlassButton("Elegir salida")
        self.btn_out.clicked.connect(self.on_choose_output)
        self.btn_save = GlassButton("Guardar TXT")
        self.btn_save.clicked.connect(self.on_save)
        self.btn_save.setEnabled(False)
        out_row.addWidget(self.out_edit, 1)
        out_row.addWidget(self.btn_out, 0)
        out_row.addWidget(self.btn_save, 0)

        # Options
        opt_grid = QtWidgets.QGridLayout()
        self.cb_pre = QtWidgets.QCheckBox("Preservar <pre> (poesía/tablas)")
        self.cb_pre.setChecked(True)
        self.cb_poetry = QtWidgets.QCheckBox("Detectar bloques de poesía")
        self.cb_poetry.setChecked(True)
        self.cb_br = QtWidgets.QCheckBox("Respetar <br> como salto de línea")
        self.cb_br.setChecked(True)
        self.btn_wikisource = GlassButton("Wikisource: Descargar obra completa")
        self.btn_wikisource.clicked.connect(self.on_download_wikisource)

        opt_grid.addWidget(self.cb_pre, 0, 0)
        opt_grid.addWidget(self.cb_poetry, 0, 1)
        opt_grid.addWidget(self.cb_br, 1, 0)
        opt_grid.addWidget(self.btn_wikisource, 1, 1)

        # Preview
        self.preview = QtWidgets.QPlainTextEdit()
        self.preview.setPlaceholderText("Aquí aparecerá el texto limpio…")
        self.preview.setMinimumHeight(280)
        self.preview.setLineWrapMode(QtWidgets.QPlainTextEdit.NoWrap)

        self.status = QtWidgets.QLabel("Listo.")
        self.status.setStyleSheet("opacity: 0.8;")

        card_layout.addLayout(url_row)
        card_layout.addLayout(file_row)
        card_layout.addLayout(out_row)
        card_layout.addLayout(opt_grid)
        card_layout.addWidget(self.preview)
        card_layout.addWidget(self.status)

        root.addWidget(title)
        root.addWidget(subtitle)
        root.addWidget(card, 1)

        self.setStyleSheet(
            """
            QWidget#bg {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 rgba(40, 20, 70, 255),
                    stop:1 rgba(15, 60, 65, 255)
                );
                color: rgba(245,245,245,235);
                font-family: Segoe UI;
                font-size: 13px;
            }
            QFrame#card {
                background: rgba(255,255,255,18);
                border: 1px solid rgba(255,255,255,28);
                border-radius: 18px;
            }
            QLineEdit, QPlainTextEdit {
                background: rgba(0,0,0,55);
                border: 1px solid rgba(255,255,255,25);
                border-radius: 12px;
                padding: 10px;
                selection-background-color: rgba(255,255,255,60);
            }
            QPlainTextEdit { border-radius: 14px; }
            QPushButton {
                background: rgba(255,255,255,18);
                border: 1px solid rgba(255,255,255,28);
                border-radius: 12px;
                padding: 10px 14px;
                font-weight: 600;
            }
            QPushButton:hover { background: rgba(255,255,255,26); }
            QPushButton:pressed { background: rgba(255,255,255,14); }
            QPushButton:disabled {
                background: rgba(255,255,255,10);
                color: rgba(255,255,255,120);
            }
            QCheckBox { spacing: 8px; }
            """
        )

    def _options(self) -> ExtractOptions:
        return ExtractOptions(
            keep_br_as_newline=self.cb_br.isChecked(),
            preserve_preformatted=self.cb_pre.isChecked(),
            preserve_poetry=self.cb_poetry.isChecked(),
        )

    def _set_busy(self, busy: bool, msg: str = ""):
        self.btn_download.setEnabled(not busy)
        self.btn_browse.setEnabled(not busy)
        self.btn_convert_file.setEnabled(not busy)
        self.btn_out.setEnabled(not busy)
        self.btn_wikisource.setEnabled(not busy)
        self.btn_save.setEnabled((not busy) and bool(self._last_text))
        if msg:
            self.status.setText(msg)

    def on_browse(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Elegir HTML", "", "HTML (*.htm *.html);;All (*.*)"
        )
        if path:
            self.file_edit.setText(path)

    def on_choose_output(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Guardar TXT", self.out_edit.text(), "Text (*.txt)"
        )
        if path:
            if not path.lower().endswith(".txt"):
                path += ".txt"
            self.out_edit.setText(path)

    def on_convert_file(self):
        path = self.file_edit.text().strip()
        if not path:
            self.status.setText("No hay archivo local seleccionado.")
            return

        p = pathlib.Path(path)
        if not p.exists():
            self.status.setText("Ese archivo no existe.")
            return

        self._set_busy(True, "Leyendo archivo…")
        QtWidgets.QApplication.processEvents()

        data = p.read_bytes()
        html_text = decode_html_bytes(data)
        cleaned = extract_clean_text(html_text, self._options())

        self._last_text = cleaned
        self.preview.setPlainText(cleaned)
        self._set_busy(False, f"Convertido desde archivo. Líneas: {cleaned.count(chr(10))}")
        self.btn_save.setEnabled(True)

    def on_download_convert(self):
        url = self.url_edit.text().strip()
        if not url:
            self.status.setText("Pega un URL primero.")
            return

        self._set_busy(True, "Descargando HTML…")
        QtWidgets.QApplication.processEvents()

        try:
            data = fetch_url_bytes(url)
        except Exception as e:
            self._set_busy(False, f"Fallo descargando: {e}")
            return

        self._set_busy(True, "Decodificando + limpiando…")
        QtWidgets.QApplication.processEvents()

        html_text = decode_html_bytes(data)
        cleaned = extract_clean_text(html_text, self._options())

        self._last_text = cleaned
        self.preview.setPlainText(cleaned)
        self._set_busy(False, f"Listo. Caracteres: {len(cleaned):,} | Líneas: {cleaned.count(chr(10))}")
        self.btn_save.setEnabled(True)

    def on_download_wikisource(self):
        url = self.url_edit.text().strip()
        if not url:
            self.status.setText("Pega un URL primero.")
            return
        if not _is_wikisource_url(url):
            self.status.setText("Ese URL no parece de Wikisource.")
            return

        self._download_wikisource_work(url)

    def _download_wikisource_work(self, index_url: str) -> None:
        self._set_busy(True, "Descargando índice de Wikisource…")
        QtWidgets.QApplication.processEvents()

        try:
            chapters = _collect_wikisource_chapters(index_url, self._options())
        except Exception as e:
            self._set_busy(False, f"Fallo descargando índice: {e}")
            return

        if not chapters:
            self._set_busy(False, "No se encontraron capítulos en Wikisource.")
            return

        combined_parts = [
            _format_wikisource_section(title, url, body)
            for title, url, body in chapters
        ]
        combined = "".join(combined_parts).strip() + "\n"
        self._last_text = combined
        self.preview.setPlainText(combined)
        self._set_busy(False, f"Wikisource completo. Capítulos: {len(chapters)} | Caracteres: {len(combined):,}")
        self.btn_save.setEnabled(True)

    def on_save(self):
        if not self._last_text:
            self.status.setText("Nada que guardar.")
            return

        out_path = pathlib.Path(self.out_edit.text().strip())
        if not out_path.parent.exists():
            self.status.setText("Carpeta de salida no existe.")
            return

        try:
            out_path.write_text(self._last_text, encoding="utf-8", newline="\n")
            self.status.setText(f"Guardado: {out_path} (UTF-8)")
        except Exception as e:
            self.status.setText(f"Error guardando: {e}")


def main():
    app = QtWidgets.QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
