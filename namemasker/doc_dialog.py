"""
Document processing progress dialog with real-time log, zoomable previews, and progress bar.
"""
import tkinter as tk
from tkinter import ttk, scrolledtext
from PIL import Image, ImageTk, ImageDraw


THUMB_MAX = 280


class ImageViewer(tk.Toplevel):
    """Pop-up window for viewing a full-size image with zoom controls."""

    def __init__(self, parent, pil_image, title=""):
        super().__init__(parent)
        self.title(title or "预览")
        self._src = pil_image
        self._zoom = 1.0
        self._photo = None
        self.geometry("800x600")
        self.resizable(True, True)

        tb = ttk.Frame(self, padding=4)
        tb.pack(fill=tk.X)
        ttk.Button(tb, text="放大 +", command=lambda: self._set_zoom(1.25)).pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text="缩小 -", command=lambda: self._set_zoom(0.8)).pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text="原始大小", command=lambda: self._set_zoom_abs(1.0)).pack(side=tk.LEFT, padx=2)
        ttk.Button(tb, text="适应窗口", command=self._fit).pack(side=tk.LEFT, padx=2)
        self._lbl_zoom = ttk.Label(tb, text="100%")
        self._lbl_zoom.pack(side=tk.LEFT, padx=8)

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True)
        self._canvas = tk.Canvas(frame, bg="#2d2d2d", highlightthickness=0)
        self._vbar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self._canvas.yview)
        self._hbar = ttk.Scrollbar(self, orient=tk.HORIZONTAL, command=self._canvas.xview)
        self._canvas.configure(xscrollcommand=self._hbar.set, yscrollcommand=self._vbar.set)
        self._vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._hbar.pack(fill=tk.X)

        self._canvas.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Configure>", lambda e: self._render())
        self.after(50, self._fit)

    def _set_zoom(self, factor):
        self._zoom = max(0.1, min(10.0, self._zoom * factor))
        self._render()

    def _set_zoom_abs(self, z):
        self._zoom = z
        self._render()

    def _fit(self):
        cw = self._canvas.winfo_width() or 780
        ch = self._canvas.winfo_height() or 560
        if cw < 10 or ch < 10:
            return
        self._zoom = min(cw / self._src.width, ch / self._src.height, 1.0)
        self._render()

    def _render(self):
        w = max(1, int(self._src.width * self._zoom))
        h = max(1, int(self._src.height * self._zoom))
        resized = self._src.resize((w, h), Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(resized)
        self._canvas.delete("all")
        self._canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)
        self._canvas.configure(scrollregion=(0, 0, w, h))
        self._lbl_zoom.configure(text=f"{int(self._zoom * 100)}%")

    def _on_wheel(self, event):
        if event.delta > 0:
            self._set_zoom(1.15)
        else:
            self._set_zoom(0.87)


class DocProgressDialog(tk.Toplevel):
    """
    A dialog showing:
    - Left: scrollable real-time log
    - Right: scrollable preview thumbnails (click to zoom)
    - Bottom: progress bar + action buttons
    """

    def __init__(self, parent, title="文档处理", on_cancel=None, on_save=None):
        super().__init__(parent)
        self.title(title)
        self.geometry("960x620")
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._on_cancel = on_cancel
        self._on_save = on_save
        self._photo_refs = []
        self._full_images = []
        self._cancelled = False
        self._done = False
        self._build_ui()
        self.transient(parent)
        self.grab_set()

    def _build_ui(self):
        main = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        # -- Left: log area --
        left = ttk.LabelFrame(main, text="处理日志", padding=4)
        self.log_text = scrolledtext.ScrolledText(
            left, wrap=tk.WORD, font=("Consolas", 9), state=tk.DISABLED,
            bg="#1e1e1e", fg="#d4d4d4", insertbackground="#d4d4d4",
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.tag_configure("info", foreground="#d4d4d4")
        self.log_text.tag_configure("found", foreground="#4ec9b0")
        self.log_text.tag_configure("warn", foreground="#ce9178")
        self.log_text.tag_configure("error", foreground="#f44747")
        self.log_text.tag_configure("done", foreground="#6a9955")
        main.add(left, weight=3)

        # -- Right: preview area --
        right = ttk.LabelFrame(main, text="预览 (点击放大)", padding=4)
        canvas_frame = ttk.Frame(right)
        canvas_frame.pack(fill=tk.BOTH, expand=True)
        self.preview_canvas = tk.Canvas(canvas_frame, bg="#2d2d2d", highlightthickness=0)
        self.preview_scrollbar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.preview_canvas.yview)
        self.preview_canvas.configure(yscrollcommand=self.preview_scrollbar.set)
        self.preview_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.preview_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.preview_inner = ttk.Frame(self.preview_canvas)
        self.preview_canvas.create_window((0, 0), window=self.preview_inner, anchor=tk.NW)
        self.preview_inner.bind("<Configure>", lambda e: self.preview_canvas.configure(
            scrollregion=self.preview_canvas.bbox("all")))
        self.preview_canvas.bind("<MouseWheel>", self._on_mousewheel)
        main.add(right, weight=2)

        # -- Bottom: progress bar + buttons --
        bottom = ttk.Frame(self, padding=6)
        bottom.pack(fill=tk.X)
        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=300)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        self.btn_cancel = ttk.Button(bottom, text="取消", command=self._cancel)
        self.btn_cancel.pack(side=tk.RIGHT, padx=4)
        self.btn_save = ttk.Button(bottom, text="确认保存", command=self._save, state=tk.DISABLED)
        self.btn_save.pack(side=tk.RIGHT, padx=4)

    def start_progress(self):
        self.progress.start(15)

    def stop_progress(self):
        self.progress.stop()
        self.progress.configure(mode="determinate", value=100)

    # ---- public API (called from main thread via root.after) ----

    def log(self, msg: str, level: str = "info"):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n", level)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def log_names(self, names: list):
        if not names:
            self.log("  (未发现敏感信息)", "warn")
            return
        self.log(f"  发现 {len(names)} 项:", "found")
        for n in names:
            self.log(f"    - {n}", "found")

    def add_preview(self, pil_image: Image.Image, caption: str = "", boxes=None):
        im = pil_image.copy()

        if boxes:
            draw = ImageDraw.Draw(im)
            for (x1, y1, x2, y2) in boxes:
                draw.rectangle([x1, y1, x2, y2], outline="red", width=max(2, im.width // 200))

        full_im = im.copy()
        idx = len(self._full_images)
        self._full_images.append((full_im, caption))

        ratio = min(THUMB_MAX / im.width, THUMB_MAX / im.height, 1.0)
        if ratio < 1.0:
            im = im.resize((int(im.width * ratio), int(im.height * ratio)), Image.LANCZOS)

        photo = ImageTk.PhotoImage(im)
        self._photo_refs.append(photo)

        frame = ttk.Frame(self.preview_inner)
        frame.pack(fill=tk.X, pady=4, padx=4)
        lbl_img = tk.Label(frame, image=photo, bg="#2d2d2d", cursor="hand2")
        lbl_img.pack()
        lbl_img.bind("<Button-1>", lambda e, i=idx: self._open_viewer(i))
        if caption:
            lbl_cap = ttk.Label(frame, text=caption, wraplength=THUMB_MAX, font=("", 8))
            lbl_cap.pack()

        self.preview_canvas.configure(scrollregion=self.preview_canvas.bbox("all"))
        self.preview_canvas.yview_moveto(1.0)

    def set_done(self, summary: str):
        self._done = True
        self.stop_progress()
        self.log(summary, "done")
        self.btn_save.configure(state=tk.NORMAL)
        self.btn_cancel.configure(text="关闭")

    def set_error(self, msg: str):
        self._done = True
        self.stop_progress()
        self.log(f"错误: {msg}", "error")
        self.btn_cancel.configure(text="关闭")

    @property
    def is_cancelled(self):
        return self._cancelled

    # ---- internal ----

    def _open_viewer(self, idx):
        if idx < len(self._full_images):
            im, caption = self._full_images[idx]
            ImageViewer(self, im, title=caption)

    def _on_mousewheel(self, event):
        self.preview_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _cancel(self):
        if self._done:
            self.destroy()
            return
        self._cancelled = True
        self.log("用户取消...", "warn")
        self.btn_cancel.configure(state=tk.DISABLED)
        if self._on_cancel:
            self._on_cancel()

    def _save(self):
        self.btn_save.configure(state=tk.DISABLED)
        if self._on_save:
            self._on_save()

    def _on_close(self):
        if not self._done:
            self._cancelled = True
        self.destroy()
