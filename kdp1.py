"""
KDP Book Cleaner - Herramienta definitiva para extraer y limpiar libros desde HTML
Versión mejorada con batch processing, detección automática de estructura y múltiples formatos de exportación
"""

from __future__ import annotations
import sys
import re
from pathlib import Path
from typing import Optional, List, Dict
from datetime import datetime

from PySide6.QtCore import Qt, QThread, Signal, QSize
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLineEdit, QPushButton, QTextEdit, QFileDialog, QLabel,
    QProgressBar, QTabWidget, QListWidget, QSplitter, QGroupBox,
    QCheckBox, QSpinBox, QMessageBox, QComboBox
)
from PySide6.QtGui import QFont, QTextCursor

import requests
from charset_normalizer import from_bytes
from bs4 import BeautifulSoup

# ============================================================================
# CORE FUNCTIONALITY
# ============================================================================

class BookFetcher:
    """Descarga y decodifica HTML/texto de manera robusta"""
    
    @staticmethod
    def fetch_url(url: str, timeout: int = 30) -> str:
        headers = {
            "User-Agent": "Mozilla/5.0 (KDP-Editor/2.0; +https://example.local)"
        }
        r = requests.get(url, headers=headers, timeout=timeout)
        r.raise_for_status()
        
        if r.encoding and r.encoding.lower() not in ["iso-8859-1", "windows-1252"]:
            return r.text
        
        best = from_bytes(r.content).best()
        if best is None:
            return r.content.decode("utf-8", errors="replace")
        return str(best)


class BookExtractor:
    """Extrae texto limpio desde HTML con soporte especial para Gutenberg"""
    
    GUTENBERG_START_RE = re.compile(
        r"\*\*\*\s*START OF (THE )?PROJECT GUTENBERG EBOOK.*\*\*\*", 
        re.IGNORECASE
    )
    GUTENBERG_END_RE = re.compile(
        r"\*\*\*\s*END OF (THE )?PROJECT GUTENBERG EBOOK.*\*\*\*", 
        re.IGNORECASE
    )
    
    @classmethod
    def extract_main_text(cls, html: str) -> str:
        # Intento 1: Gutenberg directo en HTML
        m1 = cls.GUTENBERG_START_RE.search(html)
        m2 = cls.GUTENBERG_END_RE.search(html)
        if m1 and m2 and m2.start() > m1.end():
            chunk = html[m1.end():m2.start()]
            if "<" in chunk and ">" in chunk:
                soup = BeautifulSoup(chunk, "lxml")
                return soup.get_text("\n")
            return chunk
        
        # Intento 2: parsear DOM y buscar marcadores
        soup = BeautifulSoup(html, "lxml")
        full_text = soup.get_text("\n")
        m1 = cls.GUTENBERG_START_RE.search(full_text)
        m2 = cls.GUTENBERG_END_RE.search(full_text)
        if m1 and m2 and m2.start() > m1.end():
            return full_text[m1.end():m2.start()]
        
        # Fallback genérico: extraer contenido principal
        return cls._generic_extract(soup)
    
    @staticmethod
    def _generic_extract(soup: BeautifulSoup) -> str:
        # Remover elementos no deseados
        for tag in soup.find_all(['script', 'style', 'nav', 'header', 'footer', 'aside']):
            tag.decompose()
        
        out_lines: List[str] = []
        for el in soup.find_all(["h1", "h2", "h3", "h4", "p", "blockquote", "li"]):
            t = el.get_text(" ", strip=True)
            if not t:
                continue
            if el.name in {"h1", "h2", "h3", "h4"}:
                out_lines.append("")
                out_lines.append(t.upper())
                out_lines.append("")
            else:
                out_lines.append(t)
                out_lines.append("")
        
        return "\n".join(out_lines).strip()


class BookCleaner:
    """Limpia y normaliza texto de libros"""
    
    SEPARATOR_LINE_RE = re.compile(
        r"^\s*(\*+\s*\*+\s*\*+|[-_=]{3,}|•{3,}|·{3,}|—{3,}|_{3,})\s*$"
    )
    BARE_NUMBER_RE = re.compile(r"^\s*\d+\s*$")
    MULTI_BLANKS_RE = re.compile(r"\n{3,}")
    CHAPTER_RE = re.compile(
        r"^(CHAPTER|CAPÍTULO|CAP\.|PARTE|PART)\s+([IVXLCDM]+|\d+)",
        re.IGNORECASE
    )
    
    @classmethod
    def clean_text(cls, text: str, aggressive: bool = False) -> str:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        lines = text.split("\n")
        cleaned: List[str] = []
        
        for line in lines:
            s = line.replace("\u00a0", " ").strip()
            
            # Quitar separadores
            if cls.SEPARATOR_LINE_RE.match(s):
                continue
            
            # Quitar números sueltos
            if cls.BARE_NUMBER_RE.match(s):
                continue
            
            # Quitar líneas muy cortas con caracteres especiales
            if len(s) > 0 and len(s) <= 2 and all(ch in "*-_=·•" for ch in s):
                continue
            
            # Modo agresivo: quitar líneas muy cortas
            if aggressive and len(s) > 0 and len(s) < 10 and not cls.CHAPTER_RE.match(s):
                continue
            
            cleaned.append(s)
        
        out = "\n".join(cleaned)
        out = re.sub(r"[ \t]+\n", "\n", out)
        out = re.sub(r"\n[ \t]+", "\n", out)
        out = cls.MULTI_BLANKS_RE.sub("\n\n", out)
        
        return out.strip() + "\n"
    
    @classmethod
    def detect_structure(cls, text: str) -> Dict[str, any]:
        """Detecta título, autor y capítulos automáticamente"""
        lines = text.split("\n")
        structure = {
            "title": None,
            "author": None,
            "translator": None,
            "chapters": []
        }
        
        # Buscar título y autor en las primeras 50 líneas
        for i, line in enumerate(lines[:50]):
            line = line.strip()
            if not line:
                continue
            
            if structure["title"] is None and len(line) > 5 and len(line) < 200:
                structure["title"] = line
            elif "by " in line.lower() or "por " in line.lower():
                structure["author"] = line
            elif "translat" in line.lower() or "traducc" in line.lower():
                structure["translator"] = line
        
        # Buscar capítulos
        for i, line in enumerate(lines):
            if cls.CHAPTER_RE.match(line.strip()):
                structure["chapters"].append({
                    "line": i,
                    "title": line.strip()
                })
        
        return structure


class BookExporter:
    """Exporta libros a diferentes formatos"""
    
    @staticmethod
    def export_txt(text: str, path: Path) -> None:
        path.write_text(text, encoding="utf-8")
    
    @staticmethod
    def export_html(text: str, path: Path, title: str = "Libro") -> None:
        paragraphs = text.split("\n\n")
        html_body = ""
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            if BookCleaner.CHAPTER_RE.match(para):
                html_body += f"<h2>{para}</h2>\n"
            elif len(para) < 100 and para.isupper():
                html_body += f"<h3>{para}</h3>\n"
            else:
                para_html = para.replace("\n", "<br>\n")
                html_body += f"<p>{para_html}</p>\n"
        
        html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{
            font-family: Georgia, serif;
            max-width: 800px;
            margin: 0 auto;
            padding: 2rem;
            line-height: 1.6;
        }}
        h2 {{
            margin-top: 3rem;
            page-break-before: always;
        }}
        p {{
            text-align: justify;
            margin: 1rem 0;
        }}
    </style>
</head>
<body>
    <h1>{title}</h1>
    {html_body}
</body>
</html>"""
        path.write_text(html, encoding="utf-8")
    
    @staticmethod
    def export_markdown(text: str, path: Path, structure: Dict = None) -> None:
        paragraphs = text.split("\n\n")
        md_content = ""
        
        if structure and structure.get("title"):
            md_content += f"# {structure['title']}\n\n"
            if structure.get("author"):
                md_content += f"**{structure['author']}**\n\n"
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            if BookCleaner.CHAPTER_RE.match(para):
                md_content += f"\n## {para}\n\n"
            elif len(para) < 100 and para.isupper():
                md_content += f"### {para}\n\n"
            else:
                md_content += f"{para}\n\n"
        
        path.write_text(md_content, encoding="utf-8")


# ============================================================================
# WORKER THREAD
# ============================================================================

class ProcessWorker(QThread):
    """Worker thread para procesar URLs sin bloquear la UI"""
    
    progress = Signal(int, str)
    finished = Signal(str, dict)
    error = Signal(str)
    
    def __init__(self, url: str, aggressive: bool = False):
        super().__init__()
        self.url = url
        self.aggressive = aggressive
    
    def run(self):
        try:
            self.progress.emit(10, "Descargando HTML...")
            html = BookFetcher.fetch_url(self.url)
            
            self.progress.emit(40, "Extrayendo texto...")
            raw = BookExtractor.extract_main_text(html)
            
            self.progress.emit(70, "Limpiando texto...")
            cleaned = BookCleaner.clean_text(raw, self.aggressive)
            
            self.progress.emit(90, "Analizando estructura...")
            structure = BookCleaner.detect_structure(cleaned)
            
            self.progress.emit(100, "¡Completado!")
            self.finished.emit(cleaned, structure)
            
        except Exception as e:
            self.error.emit(str(e))


# ============================================================================
# UI STYLING
# ============================================================================

GLASS_QSS = """
QMainWindow {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 #0a0e1a, stop:0.3 #1a1f35, stop:0.7 #2a1f3a, stop:1 #1a0f2a);
}

#Card {
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 20px;
}

QLineEdit, QTextEdit {
    background: rgba(255, 255, 255, 0.10);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 12px;
    padding: 10px;
    color: #E8F0FF;
    selection-background-color: rgba(120, 180, 255, 0.35);
}

QLabel {
    color: rgba(232, 240, 255, 0.95);
    font-weight: 500;
}

QPushButton {
    background: rgba(255, 255, 255, 0.12);
    border: 1px solid rgba(255, 255, 255, 0.20);
    border-radius: 12px;
    padding: 10px 18px;
    color: #E8F0FF;
    font-weight: 600;
    font-size: 13px;
}

QPushButton:hover {
    background: rgba(255, 255, 255, 0.20);
    border: 1px solid rgba(255, 255, 255, 0.30);
}

QPushButton:pressed {
    background: rgba(120, 180, 255, 0.30);
}

QPushButton:disabled {
    background: rgba(255, 255, 255, 0.05);
    color: rgba(232, 240, 255, 0.3);
    border: 1px solid rgba(255, 255, 255, 0.08);
}

QTabWidget::pane {
    background: rgba(255, 255, 255, 0.05);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 12px;
}

QTabBar::tab {
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    padding: 8px 16px;
    color: rgba(232, 240, 255, 0.7);
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    margin-right: 4px;
}

QTabBar::tab:selected {
    background: rgba(120, 180, 255, 0.25);
    color: #E8F0FF;
}

QProgressBar {
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 8px;
    text-align: center;
    color: #E8F0FF;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 rgba(120, 180, 255, 0.7), stop:1 rgba(150, 120, 255, 0.7));
    border-radius: 7px;
}

QGroupBox {
    color: rgba(232, 240, 255, 0.9);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 10px;
    margin-top: 10px;
    padding-top: 10px;
    font-weight: 600;
}

QGroupBox::title {
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 5px;
}

QCheckBox {
    color: rgba(232, 240, 255, 0.9);
    spacing: 8px;
}

QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 4px;
    border: 2px solid rgba(255, 255, 255, 0.3);
    background: rgba(255, 255, 255, 0.08);
}

QCheckBox::indicator:checked {
    background: rgba(120, 180, 255, 0.5);
    border-color: rgba(120, 180, 255, 0.8);
}

QSpinBox, QComboBox {
    background: rgba(255, 255, 255, 0.10);
    border: 1px solid rgba(255, 255, 255, 0.18);
    border-radius: 8px;
    padding: 6px;
    color: #E8F0FF;
}

QListWidget {
    background: rgba(255, 255, 255, 0.08);
    border: 1px solid rgba(255, 255, 255, 0.15);
    border-radius: 10px;
    color: #E8F0FF;
    padding: 5px;
}

QListWidget::item {
    padding: 8px;
    border-radius: 6px;
}

QListWidget::item:selected {
    background: rgba(120, 180, 255, 0.25);
}
"""


# ============================================================================
# MAIN WINDOW
# ============================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("KDP Book Cleaner Pro - Herramienta Definitiva")
        self.resize(1300, 850)
        
        self._last_text: Optional[str] = None
        self._last_structure: Optional[Dict] = None
        self._worker: Optional[ProcessWorker] = None
        
        self._setup_ui()
    
    def _setup_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(20, 20, 20, 20)
        root_layout.setSpacing(16)
        
        # Card principal
        card = QWidget(objectName="Card")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(20, 20, 20, 20)
        card_layout.setSpacing(14)
        
        # Header con URL
        header = self._create_header()
        card_layout.addLayout(header)
        
        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        self.progress_bar.setFixedHeight(24)
        card_layout.addWidget(self.progress_bar)
        
        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: rgba(120, 180, 255, 0.9);")
        self.status_label.setVisible(False)
        card_layout.addWidget(self.status_label)
        
        # Tabs
        tabs = self._create_tabs()
        card_layout.addWidget(tabs, 1)
        
        # Botones de acción
        actions = self._create_actions()
        card_layout.addLayout(actions)
        
        root_layout.addWidget(card, 1)
        self.setCentralWidget(root)
    
    def _create_header(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        
        url_label = QLabel("URL:")
        url_label.setFixedWidth(40)
        
        self.url_edit = QLineEdit()
        self.url_edit.setPlaceholderText("Pega aquí la URL del libro HTML (ej: Gutenberg)...")
        self.url_edit.setText("https://www.gutenberg.org/files/10661/10661-h/10661-h.htm")
        
        self.btn_process = QPushButton("🚀 Procesar y Guardar")
        self.btn_process.setFixedWidth(180)
        
        # Carpeta de destino
        folder_group = QGroupBox("Carpeta de destino")
        folder_layout = QHBoxLayout(folder_group)
        folder_layout.setContentsMargins(10, 15, 10, 10)
        
        self.folder_path = QLineEdit()
        self.folder_path.setReadOnly(True)
        self.folder_path.setText(str(Path.home() / "Libros_Procesados"))
        self.folder_path.setPlaceholderText("Selecciona carpeta de destino...")
        
        self.btn_select_folder = QPushButton("📁")
        self.btn_select_folder.setFixedWidth(50)
        self.btn_select_folder.setToolTip("Seleccionar carpeta")
        
        folder_layout.addWidget(self.folder_path, 1)
        folder_layout.addWidget(self.btn_select_folder)
        
        # Opciones
        options_group = QGroupBox("Opciones")
        options_layout = QHBoxLayout(options_group)
        options_layout.setContentsMargins(10, 15, 10, 10)
        
        self.aggressive_check = QCheckBox("Limpieza agresiva")
        self.aggressive_check.setToolTip("Elimina líneas muy cortas y fragmentadas")
        
        options_layout.addWidget(self.aggressive_check)
        options_layout.addStretch()
        
        layout.addWidget(url_label)
        layout.addWidget(self.url_edit, 1)
        layout.addWidget(self.btn_process)
        
        # Segunda fila con carpeta y opciones
        layout2 = QHBoxLayout()
        layout2.addWidget(folder_group, 2)
        layout2.addWidget(options_group, 1)
        
        main_layout = QVBoxLayout()
        main_layout.addLayout(layout)
        main_layout.addLayout(layout2)
        
        self.btn_process.clicked.connect(self.on_process)
        self.btn_select_folder.clicked.connect(self.on_select_folder)
        
        # Crear la carpeta por defecto si no existe
        default_folder = Path.home() / "Libros_Procesados"
        default_folder.mkdir(exist_ok=True)
        
        return main_layout
    
    def _create_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        
        # Tab 1: Preview
        self.preview = QTextEdit()
        self.preview.setPlaceholderText("Aquí aparecerá el texto limpio del libro...\n\n"
                                       "📚 Soporta Project Gutenberg y otros sitios HTML\n"
                                       "✨ Limpieza automática de separadores y basura\n"
                                       "🎯 Detección de estructura (título, autor, capítulos)")
        self.preview.setFont(QFont("Georgia", 11))
        tabs.addTab(self.preview, "📄 Vista Previa")
        
        # Tab 2: Estructura
        structure_widget = QWidget()
        structure_layout = QVBoxLayout(structure_widget)
        structure_layout.setContentsMargins(10, 10, 10, 10)
        
        self.structure_info = QTextEdit()
        self.structure_info.setReadOnly(True)
        self.structure_info.setPlaceholderText("Información de estructura aparecerá aquí...")
        self.structure_info.setMaximumHeight(150)
        
        chapters_label = QLabel("Capítulos detectados:")
        self.chapters_list = QListWidget()
        
        structure_layout.addWidget(QLabel("📖 Información del libro:"))
        structure_layout.addWidget(self.structure_info)
        structure_layout.addWidget(chapters_label)
        structure_layout.addWidget(self.chapters_list, 1)
        
        tabs.addTab(structure_widget, "📊 Estructura")
        
        # Tab 3: Estadísticas
        stats_widget = QWidget()
        stats_layout = QVBoxLayout(stats_widget)
        stats_layout.setContentsMargins(10, 10, 10, 10)
        
        self.stats_text = QTextEdit()
        self.stats_text.setReadOnly(True)
        self.stats_text.setPlaceholderText("Estadísticas del texto procesado...")
        
        stats_layout.addWidget(self.stats_text)
        
        tabs.addTab(stats_widget, "📈 Estadísticas")
        
        return tabs
    
    def _create_actions(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        
        export_label = QLabel("Formato guardado:")
        
        self.format_combo = QComboBox()
        self.format_combo.addItems(["TXT", "HTML", "Markdown"])
        self.format_combo.setFixedWidth(120)
        
        self.btn_copy = QPushButton("📋 Copiar")
        self.btn_copy.setEnabled(False)
        self.btn_copy.setFixedWidth(130)
        
        self.btn_clear = QPushButton("🗑️ Limpiar")
        self.btn_clear.setFixedWidth(130)
        
        layout.addWidget(export_label)
        layout.addWidget(self.format_combo)
        layout.addStretch()
        layout.addWidget(self.btn_copy)
        layout.addWidget(self.btn_clear)
        
        self.btn_copy.clicked.connect(self.on_copy)
        self.btn_clear.clicked.connect(self.on_clear)
        
        return layout
    
    def on_select_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Seleccionar carpeta de destino",
            self.folder_path.text()
        )
        if folder:
            self.folder_path.setText(folder)
    
    def on_process(self):
        url = self.url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, "Error", "Por favor ingresa una URL")
            return
        
        # Limpiar estado previo
        self.preview.clear()
        self.structure_info.clear()
        self.chapters_list.clear()
        self.stats_text.clear()
        self._last_text = None
        self._last_structure = None
        
        # Mostrar progress
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.status_label.setVisible(True)
        self.btn_process.setEnabled(False)
        
        # Iniciar worker
        self._worker = ProcessWorker(url, self.aggressive_check.isChecked())
        self._worker.progress.connect(self.on_progress)
        self._worker.finished.connect(self.on_finished)
        self._worker.error.connect(self.on_error)
        self._worker.start()
    
    def on_progress(self, value: int, message: str):
        self.progress_bar.setValue(value)
        self.status_label.setText(message)
    
    def on_finished(self, text: str, structure: Dict):
        self._last_text = text
        self._last_structure = structure
        
        # Mostrar preview
        self.preview.setPlainText(text)
        
        # Mostrar estructura
        if structure["title"]:
            info = f"Título: {structure['title']}\n"
            if structure["author"]:
                info += f"Autor: {structure['author']}\n"
            if structure["translator"]:
                info += f"{structure['translator']}\n"
            self.structure_info.setPlainText(info)
        
        # Mostrar capítulos
        for chapter in structure["chapters"]:
            self.chapters_list.addItem(f"Línea {chapter['line']}: {chapter['title']}")
        
        # Mostrar estadísticas
        self._show_stats(text)
        
        # Habilitar botones
        self.btn_copy.setEnabled(True)
        self.btn_process.setEnabled(True)
        
        # Guardar automáticamente
        self._auto_save()
    
    def _auto_save(self):
        """Guarda automáticamente el archivo en la carpeta seleccionada"""
        if not self._last_text:
            return
        
        try:
            # Obtener carpeta de destino
            folder = Path(self.folder_path.text())
            if not folder.exists():
                folder.mkdir(parents=True, exist_ok=True)
            
            # Generar nombre de archivo
            default_name = "libro_limpio"
            if self._last_structure and self._last_structure.get("title"):
                title = self._last_structure["title"]
                # Limpiar título para nombre de archivo
                title = re.sub(r'[<>:"/\\|?*]', '', title)
                title = title.replace('\n', ' ').replace('\r', '')
                default_name = title[:80].strip()
            
            # Obtener formato
            format_type = self.format_combo.currentText().lower()
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{default_name}_{timestamp}.{format_type}"
            filepath = folder / filename
            
            # Guardar según formato
            if format_type == "txt":
                BookExporter.export_txt(self._last_text, filepath)
            elif format_type == "html":
                title = self._last_structure.get("title", "Libro") if self._last_structure else "Libro"
                BookExporter.export_html(self._last_text, filepath, title)
            elif format_type == "markdown":
                BookExporter.export_markdown(self._last_text, filepath, self._last_structure)
            
            # Ocultar progress y mostrar éxito
            self.progress_bar.setVisible(False)
            self.status_label.setText(f"✅ Guardado en: {filepath.name}")
            
            QMessageBox.information(
                self, 
                "¡Éxito!", 
                f"Libro procesado y guardado correctamente\n\n"
                f"📁 Ubicación: {filepath}\n\n"
                f"📊 Estadísticas:\n"
                f"   • Caracteres: {len(self._last_text):,}\n"
                f"   • Líneas: {len(self._last_text.splitlines()):,}\n"
                f"   • Capítulos: {len(self._last_structure['chapters'])}"
            )
            
        except Exception as e:
            self.progress_bar.setVisible(False)
            self.status_label.setText(f"❌ Error al guardar")
            QMessageBox.critical(self, "Error al guardar", f"No se pudo guardar el archivo:\n\n{e}")
    
    def on_error(self, error_msg: str):
        self.btn_process.setEnabled(True)
        self.progress_bar.setVisible(False)
        self.status_label.setText(f"❌ Error: {error_msg}")
        self.preview.setPlainText(f"Error al procesar:\n\n{error_msg}")
        QMessageBox.critical(self, "Error", f"No se pudo procesar el libro:\n\n{error_msg}")
    
        def _show_stats(self, text: str):
        lines = [l for l in text.splitlines() if l.strip()]
        words = text.split()
        chars = len(text)
        chars_no_spaces = len(text.replace(" ", "").replace("\n", ""))

        paragraphs = [p for p in text.split("\n\n") if p.strip()]

        avg_para_length = (sum(len(p) for p in paragraphs) / len(paragraphs)) if paragraphs else 0

        words_per_line = (len(words) / len(lines)) if lines else 0
        words_per_para = (len(words) / len(paragraphs)) if paragraphs else 0

        stats = f"""
    📊 ESTADÍSTICAS DEL TEXTO
    {'='*50}

    📝 Contenido:
       • Caracteres (con espacios): {chars:,}
       • Caracteres (sin espacios): {chars_no_spaces:,}
       • Palabras: {len(words):,}
       • Líneas: {len(lines):,}
       • Párrafos: {len(paragraphs):,}

    📏 Promedios:
       • Palabras por línea: {words_per_line:.1f}
       • Caracteres por párrafo: {avg_para_length:.0f}
       • Palabras por párrafo: {words_per_para:.1f}

    📖 Estimaciones de lectura:
       • Tiempo (250 ppm): {len(words) / 250:.0f} minutos
       • Páginas (250 palabras/pág): {len(words) / 250:.0f} páginas

    {'='*50}
    """
        self.stats_text.setPlainText(stats)
    
    def on_copy(self):
        if not self._last_text:
            return
        
        clipboard = QApplication.clipboard()
        clipboard.setText(self._last_text)
        self.status_label.setText("✅ Texto copiado al portapapeles")
        self.status_label.setVisible(True)
    
    def on_clear(self):
        self.preview.clear()
        self.structure_info.clear()
        self.chapters_list.clear()
        self.stats_text.clear()
        self.url_edit.clear()
        self._last_text = None
        self._last_structure = None
        self.btn_copy.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.status_label.setVisible(False)


# ============================================================================
# MAIN
# ============================================================================

def main():
    app = QApplication(sys.argv)
    app.setStyleSheet(GLASS_QSS)
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()