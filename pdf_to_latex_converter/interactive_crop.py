"""
interactive_crop.py
-------------------
Tool di ritaglio interattivo per le immagini delle slide.

USO:
    python interactive_crop.py [cartella_immagini]

    Esempio: python interactive_crop.py ../images

CONTROLLI:
    - Trascina il mouse sull'immagine per selezionare l'area di ritaglio
    - [✔ Conferma & Salva]  → salva il ritaglio, sovrascrivendo il file originale
    - [✖ Rifiuta / Salta]   → passa all'immagine successiva senza modifiche
    - [↺ Reset]             → cancella la selezione corrente
    - Freccia sinistra/destra → vai all'immagine precedente/successiva
"""

import os
import sys
import glob
import pathlib
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk

# ─────────────────────────────────────────────────────────────────────────────
# Configurazione
# ─────────────────────────────────────────────────────────────────────────────
SUPPORTED_EXTENSIONS = ("*.png", "*.jpg", "*.jpeg", "*.bmp", "*.tiff", "*.webp")
SELECTION_COLOR = "#00CFFF"
# UI_CHROME_H: spazio riservato a header + istruzioni + info bar + btn bar + status bar
_UI_CHROME_H = 195
_UI_CHROME_W = 40

# Palette colori
BG_DARK       = "#0d1117"   # sfondo principale
BG_HEADER     = "#161b22"   # barra superiore
BG_TOOLBAR    = "#1a2332"   # barra pulsanti
BG_CANVAS     = "#090d13"   # sfondo canvas
ACCENT_GREEN  = "#3fb950"   # confirm
ACCENT_RED    = "#f85149"   # reject
ACCENT_ORANGE = "#d29922"   # reset / nav
ACCENT_BLUE   = "#58a6ff"   # nav arrows / highlight
FG_PRIMARY    = "#f0f6fc"   # testo principale
FG_SECONDARY  = "#8b949e"   # testo secondario
FG_SELECTION  = "#00CFFF"   # colore selezione


# ─────────────────────────────────────────────────────────────────────────────
# Classe principale
# ─────────────────────────────────────────────────────────────────────────────
class InteractiveCropper:
    def __init__(self, root: tk.Tk, image_files: list[str]):
        self.root = root
        self.image_files = image_files
        self.index = 0

        # Stato selezione
        self.sel_start = None
        self.sel_end = None
        self.dragging = False
        self.scale = 1.0

        self.pil_img: Image.Image | None = None
        self.tk_img: ImageTk.PhotoImage | None = None

        # ── Calcola dimensioni canvas in base alla risoluzione schermo ─────────
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        # Lascia margine per la taskbar di Windows (~40px) + chrome UI
        self.canvas_max_w = min(screen_w - _UI_CHROME_W, 1600)
        self.canvas_max_h = min(screen_h - _UI_CHROME_H - 40, 900)

        # Imposta geometria finestra (tutto lo schermo meno taskbar)
        win_w = self.canvas_max_w + _UI_CHROME_W
        win_h = self.canvas_max_h + _UI_CHROME_H
        self.root.geometry(f"{win_w}x{win_h}+0+0")

        self._build_ui()
        self._load_image()

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        self.root.title("✂  Ritaglio Interattivo Immagini")
        self.root.configure(bg=BG_DARK)
        self.root.resizable(True, True)

        # ── Barra superiore ──────────────────────────────────────────────────
        header = tk.Frame(self.root, bg=BG_HEADER, pady=8)
        header.pack(fill="x")

        # Contatore immagini (sinistra)
        left_header = tk.Frame(header, bg=BG_HEADER)
        left_header.pack(side="left", padx=14)

        self.lbl_count = tk.Label(
            left_header, text="",
            bg=BG_HEADER, fg=ACCENT_BLUE,
            font=("Segoe UI", 12, "bold")
        )
        self.lbl_count.pack(side="left")

        # Nome file (centro)
        self.lbl_file = tk.Label(
            header, text="",
            bg=BG_HEADER, fg=FG_PRIMARY,
            font=("Segoe UI", 12, "bold"), anchor="center"
        )
        self.lbl_file.pack(side="left", fill="x", expand=True)

        # Hint tastiera (destra)
        lbl_hint = tk.Label(
            header, text="◀ ▶  frecce per navigare",
            bg=BG_HEADER, fg=FG_SECONDARY,
            font=("Segoe UI", 9), padx=14
        )
        lbl_hint.pack(side="right")

        # ── Istruzioni (banner) ──────────────────────────────────────────────
        instructions_bar = tk.Frame(self.root, bg="#1c2b3a", pady=5)
        instructions_bar.pack(fill="x")

        tk.Label(
            instructions_bar,
            text="🖱  Trascina sull'immagine per selezionare l'area da ritagliare",
            bg="#1c2b3a", fg="#79c0ff",
            font=("Segoe UI", 10), padx=12
        ).pack(side="left")

        # ── Canvas ────────────────────────────────────────────────────────────
        canvas_frame = tk.Frame(self.root, bg=BG_CANVAS, padx=10, pady=8)
        canvas_frame.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            canvas_frame,
            cursor="crosshair",
            bg=BG_CANVAS,
            highlightthickness=2,
            highlightbackground="#30363d",
            width=self.canvas_max_w,
            height=self.canvas_max_h
        )
        self.canvas.pack(fill="both", expand=True)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.root.bind("<Left>",  lambda e: self._navigate(-1))
        self.root.bind("<Right>", lambda e: self._navigate(1))

        # ── Info selezione ────────────────────────────────────────────────────
        info_bar = tk.Frame(self.root, bg=BG_DARK, pady=4)
        info_bar.pack(fill="x")

        self.lbl_sel = tk.Label(
            info_bar,
            text="⬜  Nessuna selezione — trascina per selezionare un'area",
            bg=BG_DARK, fg=FG_SECONDARY,
            font=("Segoe UI", 10)
        )
        self.lbl_sel.pack()

        # ── Separatore ────────────────────────────────────────────────────────
        tk.Frame(self.root, bg="#30363d", height=1).pack(fill="x")

        # ── Barra pulsanti ────────────────────────────────────────────────────
        btn_bar = tk.Frame(self.root, bg=BG_TOOLBAR, pady=12)
        btn_bar.pack(fill="x")

        # Centro i pulsanti
        btn_inner = tk.Frame(btn_bar, bg=BG_TOOLBAR)
        btn_inner.pack(anchor="center")

        btn_cfg = {
            "font":   ("Segoe UI", 11, "bold"),
            "padx":   22,
            "pady":   9,
            "bd":     0,
            "cursor": "hand2",
            "relief": "flat",
        }

        # ◀ Precedente
        self.btn_prev = tk.Button(
            btn_inner, text="◀",
            bg="#21262d", fg=ACCENT_BLUE, activebackground="#30363d",
            command=lambda: self._navigate(-1), **btn_cfg
        )
        self.btn_prev.pack(side="left", padx=6)

        # ▶ Successiva
        self.btn_next = tk.Button(
            btn_inner, text="▶",
            bg="#21262d", fg=ACCENT_BLUE, activebackground="#30363d",
            command=lambda: self._navigate(1), **btn_cfg
        )
        self.btn_next.pack(side="left", padx=6)

        # Separatore visivo
        tk.Frame(btn_inner, bg="#30363d", width=2, height=38).pack(side="left", padx=10)

        # ↺ Reset selezione
        self.btn_reset = tk.Button(
            btn_inner, text="↺  Reset selezione",
            bg="#21262d", fg=ACCENT_ORANGE, activebackground="#30363d",
            command=self._reset_selection, **btn_cfg
        )
        self.btn_reset.pack(side="left", padx=6)

        # Separatore visivo
        tk.Frame(btn_inner, bg="#30363d", width=2, height=38).pack(side="left", padx=10)

        # ✔ Conferma & Salva (o salta se nessuna selezione)
        self.btn_confirm = tk.Button(
            btn_inner, text="✔  Conferma & Salva",
            bg="#1a3d2a", fg=ACCENT_GREEN, activebackground="#25543a",
            command=self._confirm, **btn_cfg
        )
        self.btn_confirm.pack(side="left", padx=6)

        # Separatore visivo
        tk.Frame(btn_inner, bg="#30363d", width=2, height=38).pack(side="left", padx=10)

        # 🗑 Elimina immagine
        self.btn_delete = tk.Button(
            btn_inner, text="🗑  Elimina immagine",
            bg="#3d1a00", fg="#ff7b00", activebackground="#5a2800",
            command=self._delete_image, **btn_cfg
        )
        self.btn_delete.pack(side="left", padx=6)

        # ── Barra stato (fondo) ───────────────────────────────────────────────
        status_bar = tk.Frame(self.root, bg="#0d1117", pady=3)
        status_bar.pack(fill="x", side="bottom")
        tk.Label(
            status_bar,
            text="✔ Salva ritaglio (o salta se nessuna selezione)  |  🗑 Elimina file  |  ↺ Cancella selezione  |  ◀ ▶ Naviga",
            bg="#0d1117", fg="#484f58",
            font=("Segoe UI", 8)
        ).pack()

    # ── Caricamento immagine ──────────────────────────────────────────────────
    def _load_image(self):
        if not self.image_files:
            messagebox.showinfo("Fine", "Nessuna immagine da processare.")
            self.root.quit()
            return

        path = self.image_files[self.index]
        self.pil_img = Image.open(path).convert("RGB")

        cw, ch = self.canvas_max_w, self.canvas_max_h
        iw, ih = self.pil_img.size
        self.scale = min(cw / iw, ch / ih, 1.0)

        disp_w = int(iw * self.scale)
        disp_h = int(ih * self.scale)

        self.canvas.config(width=disp_w, height=disp_h)

        resized = self.pil_img.resize((disp_w, disp_h), Image.LANCZOS)
        self.tk_img = ImageTk.PhotoImage(resized)

        self._reset_selection()
        self._redraw()

        self.lbl_file.config(text=f"📄  {os.path.basename(path)}")
        self.lbl_count.config(
            text=f"  {self.index + 1} / {len(self.image_files)}  "
        )

        # Aggiorna stato dei pulsanti di navigazione
        self.btn_prev.config(state=tk.NORMAL if self.index > 0 else tk.DISABLED)
        self.btn_next.config(
            state=tk.NORMAL if self.index < len(self.image_files) - 1 else tk.DISABLED
        )

    def _redraw(self):
        """Ridisegna il canvas con l'immagine e il rettangolo di selezione."""
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.tk_img)

        if self.sel_start and self.sel_end:
            x1, y1 = self.sel_start
            x2, y2 = self.sel_end
            nx1, ny1 = min(x1, x2), min(y1, y2)
            nx2, ny2 = max(x1, x2), max(y1, y2)

            cw = self.canvas.winfo_width() or self.canvas_max_w
            ch = self.canvas.winfo_height() or self.canvas_max_h
            self._draw_overlay(nx1, ny1, nx2, ny2, cw, ch)

            # Rettangolo selezione
            self.canvas.create_rectangle(
                nx1, ny1, nx2, ny2,
                outline=SELECTION_COLOR, width=2, dash=(8, 4)
            )
            # Angoli evidenziati
            corner_size = 10
            for cx, cy in [(nx1, ny1), (nx2, ny1), (nx1, ny2), (nx2, ny2)]:
                self.canvas.create_rectangle(
                    cx - corner_size // 2, cy - corner_size // 2,
                    cx + corner_size // 2, cy + corner_size // 2,
                    fill=SELECTION_COLOR, outline=""
                )

            # Dimensioni originali del crop
            scale = self.scale
            orig_w = int((nx2 - nx1) / scale)
            orig_h = int((ny2 - ny1) / scale)
            self.lbl_sel.config(
                text=f"✅  Selezione attiva: {orig_w} × {orig_h} px  —  "
                     f"premi ✔ per confermare o ✖ per saltare",
                fg=FG_SELECTION
            )
        else:
            self.lbl_sel.config(
                text="⬜  Nessuna selezione — trascina sull'immagine per selezionare un'area",
                fg=FG_SECONDARY
            )

    def _draw_overlay(self, x1, y1, x2, y2, cw, ch):
        """Disegna overlay scuro semitrasparente fuori dalla selezione."""
        gray = "#000000"
        stipple = "gray50"
        rects = [
            (0,  0,  cw, y1),
            (0,  y2, cw, ch),
            (0,  y1, x1, y2),
            (x2, y1, cw, y2),
        ]
        for r in rects:
            if r[2] > r[0] and r[3] > r[1]:
                self.canvas.create_rectangle(
                    *r, fill=gray, outline="", stipple=stipple
                )

    # ── Mouse events ─────────────────────────────────────────────────────────
    def _on_press(self, event):
        self.sel_start = (event.x, event.y)
        self.sel_end = None
        self.dragging = True

    def _on_drag(self, event):
        if self.dragging:
            self.sel_end = (event.x, event.y)
            self._redraw()

    def _on_release(self, event):
        self.dragging = False
        self.sel_end = (event.x, event.y)
        self._redraw()

    # ── Azioni pulsanti ───────────────────────────────────────────────────────
    def _confirm(self):
        """Salva il ritaglio se c'è una selezione, altrimenti salta al successivo."""
        if not self.sel_start or not self.sel_end:
            # Nessuna selezione: comportamento identico a Salta
            self._navigate(1)
            return

        x1, y1 = self.sel_start
        x2, y2 = self.sel_end
        nx1, ny1 = min(x1, x2), min(y1, y2)
        nx2, ny2 = max(x1, x2), max(y1, y2)

        s = self.scale
        orig_box = (
            int(nx1 / s), int(ny1 / s),
            int(nx2 / s), int(ny2 / s)
        )

        path = self.image_files[self.index]
        cropped = self.pil_img.crop(orig_box)
        cropped.save(path)
        print(f"[OK] Salvato: {path}")
        self.lbl_sel.config(text="✅  Ritaglio salvato!", fg=ACCENT_GREEN)
        self.root.after(600, lambda: self._navigate(1))

    def _reject(self):
        """Salta l'immagine corrente senza modifiche."""
        self._navigate(1)

    def _delete_image(self):
        """Elimina definitivamente il file corrente dal disco e passa al successivo."""
        path = self.image_files[self.index]
        fname = os.path.basename(path)
        confirm = messagebox.askyesno(
            "Elimina immagine",
            f"Sei sicuro di voler eliminare definitivamente:\n\n{fname}\n\nL'operazione non è reversibile.",
            icon="warning",
        )
        if not confirm:
            return

        try:
            os.remove(path)
            print(f"[DEL] Eliminato: {path}")
        except OSError as e:
            messagebox.showerror("Errore", f"Impossibile eliminare il file:\n{e}")
            return

        # Rimuove dalla lista e carica la prossima (o chiude se era l'ultima)
        self.image_files.pop(self.index)
        if not self.image_files:
            messagebox.showinfo("Fine sessione", "Tutte le immagini sono state elaborate. ✅")
            self.root.quit()
            return
        # Se era l'ultima, torna indietro di una posizione
        if self.index >= len(self.image_files):
            self.index = len(self.image_files) - 1
        self._load_image()

    def _reset_selection(self):
        self.sel_start = None
        self.sel_end = None
        self._redraw()

    def _navigate(self, direction: int):
        new_index = self.index + direction
        if 0 <= new_index < len(self.image_files):
            self.index = new_index
            self._load_image()
        elif new_index >= len(self.image_files):
            messagebox.showinfo("Fine sessione", "Hai processato tutte le immagini! ✅")
            self.root.quit()


# ─────────────────────────────────────────────────────────────────────────────
# Entry point (uso standalone)
# ─────────────────────────────────────────────────────────────────────────────
def collect_images(folder: str) -> list[str]:
    files = []
    for ext in SUPPORTED_EXTENSIONS:
        files.extend(glob.glob(os.path.join(folder, ext)))
    files.sort()
    return files


def launch_cropper(image_folder: str):
    """
    Lancia l'interfaccia grafica del cropper.
    Può essere chiamata da main.py dopo la conversione.
    """
    image_files = collect_images(image_folder)
    if not image_files:
        print(f"[INFO] Nessuna immagine trovata in: {image_folder}")
        return

    print(f"[INFO] Avvio cropper per {len(image_files)} immagini in: {image_folder}")

    root = tk.Tk()
    # La geometria viene impostata dinamicamente dentro __init__ dopo
    # aver interrogato winfo_screenwidth/height
    app = InteractiveCropper(root, image_files)
    root.mainloop()


def main():
    if len(sys.argv) < 2:
        # Cerca automaticamente la cartella output/*/images/ più recente
        script_dir = pathlib.Path(__file__).resolve().parent
        output_root = script_dir / "output"

        if not output_root.is_dir():
            print(f"[ERRORE] Cartella output non trovata: {output_root}")
            print("         Esegui prima la conversione di un PDF con main.py.")
            sys.exit(1)

        # Trova tutte le sottocartelle images/ dentro output/
        candidates = sorted(
            output_root.glob("*/images"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        candidates = [c for c in candidates if c.is_dir() and any(c.iterdir())]

        if not candidates:
            print("[ERRORE] Nessuna cartella images/ con immagini trovata in output/.")
            print("         Esegui prima la conversione di un PDF con main.py.")
            sys.exit(1)

        img_folder = str(candidates[0])
        print(f"[INFO] Cartella selezionata automaticamente: {img_folder}")
    else:
        img_folder = os.path.abspath(sys.argv[1])

    if not os.path.isdir(img_folder):
        print(f"[ERRORE] Cartella non trovata: {img_folder}")
        sys.exit(1)

    launch_cropper(img_folder)


if __name__ == "__main__":
    main()
