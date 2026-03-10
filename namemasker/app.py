"""
GUI: open image -> auto-detect names via API -> draw/adjust boxes -> apply mosaic -> save
Supports batch processing of a folder.
"""
import os
import sys
import json
import glob
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk, ImageDraw, ImageFilter
from namemasker.masker import apply_mosaic, _safe_save, safe_open_image
from namemasker.api_detect import detect_names, DEFAULT_MODEL, DEFAULT_BASE_URL, DEFAULT_TARGET

try:
    from tkinterdnd2 import TkinterDnD, DND_FILES
    HAS_DND = True
except ImportError:
    HAS_DND = False

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tiff", ".webp")
DOC_EXTS = (".docx", ".pdf")
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".namemasker_config.json")

MODE_MAP = {"填充": "fill", "模糊": "blur", "像素化": "pixelate", "棋盘格": "checker"}
MODE_MAP_REV = {v: k for k, v in MODE_MAP.items()}


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_config(cfg: dict):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class App:
    MAX_DISPLAY = 800

    @staticmethod
    def _set_icon(root):
        try:
            icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.ico")
            if os.path.exists(icon_path):
                root.iconbitmap(icon_path)
        except Exception:
            pass

    def __init__(self, root):
        self.root = root
        root.title("NameMasker - 图片隐私遮盖工具")
        root.geometry("960x750")
        root.minsize(700, 520)
        self._set_icon(root)

        self.image_path = None
        self.pil_image = None
        self.scale = 1.0
        self._disp_w = 1
        self._disp_h = 1
        self.photo = None

        self.regions = []
        self._undo_stack = []
        self.drawing = False
        self.start_x = 0
        self.start_y = 0
        self.cur_rect = None
        self._batch_cancel = False
        self._busy = False
        self._preview_active = False
        self._preview_photo = None
        self._batch_out_dir = None

        self._config = _load_config()

        self._build_api_bar()
        self._build_toolbar()
        self._build_canvas()
        self._build_status()
        self._build_shortcuts()
        self._setup_dnd()

        root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ---- config persistence ----

    def _cfg_get(self, key, default=""):
        return self._config.get(key, default)

    def _save_current_config(self):
        self._config["api_key"] = self.entry_key.get().strip()
        self._config["model"] = self.entry_model.get().strip()
        self._config["base_url"] = self.entry_base_url.get().strip()
        self._config["target"] = self.entry_target.get().strip()
        self._config["passes"] = self.spin_passes.get()
        self._config["mode"] = self.var_mode.get()
        self._config["pad_pct"] = self.spin_pad.get()
        _save_config(self._config)

    def _on_close(self):
        self._save_current_config()
        self.root.destroy()

    # ---- busy lock ----

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.btn_open.configure(state=state)
        self.btn_detect.configure(state=state)
        self.btn_save.configure(state=state)
        self.btn_preview.configure(state=state)
        self.btn_doc.configure(state=state)
        self.btn_batch_doc.configure(state=state)
        if busy:
            self.btn_batch.configure(state=tk.NORMAL, text="取消批量", command=self._cancel_batch)
        else:
            self.btn_batch.configure(state=tk.NORMAL, text="批量处理文件夹", command=self._start_batch)

    def _cancel_batch(self):
        self._batch_cancel = True
        self.status.configure(text="正在取消批量处理...")

    # ---- build UI ----

    def _build_api_bar(self):
        bar = ttk.LabelFrame(self.root, text="API 设置", padding=6)
        bar.pack(fill=tk.X, padx=6, pady=(6, 0))

        row1 = ttk.Frame(bar)
        row1.pack(fill=tk.X)
        ttk.Label(row1, text="API Key:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_key = ttk.Entry(row1, width=48, show="*")
        saved_key = self._cfg_get("api_key") or os.environ.get("DASHSCOPE_API_KEY", "")
        if saved_key:
            self.entry_key.insert(0, saved_key)
        self.entry_key.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(row1, text="模型:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_model = ttk.Entry(row1, width=28)
        self.entry_model.insert(0, self._cfg_get("model") or DEFAULT_MODEL)
        self.entry_model.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(row1, text="识别轮数:").pack(side=tk.LEFT, padx=(0, 4))
        self.spin_passes = ttk.Spinbox(row1, from_=1, to=5, width=3)
        self.spin_passes.set(self._cfg_get("passes", "2"))
        self.spin_passes.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(row1, text="(第2轮起查漏)").pack(side=tk.LEFT)

        row2 = ttk.Frame(bar)
        row2.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row2, text="API地址:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_base_url = ttk.Entry(row2, width=50)
        self.entry_base_url.insert(0, self._cfg_get("base_url") or DEFAULT_BASE_URL)
        self.entry_base_url.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(row2, text="识别目标:").pack(side=tk.LEFT, padx=(0, 4))
        self.entry_target = ttk.Entry(row2, width=50)
        self.entry_target.insert(0, self._cfg_get("target") or DEFAULT_TARGET)
        self.entry_target.pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _build_toolbar(self):
        tb = ttk.Frame(self.root, padding=(6, 4, 6, 0))
        tb.pack(fill=tk.X)

        # -- Row 1: actions --
        row1 = ttk.Frame(tb)
        row1.pack(fill=tk.X)
        self.btn_open = ttk.Button(row1, text="打开图片", command=self.open_file)
        self.btn_open.pack(side=tk.LEFT, padx=2)
        self.btn_detect = ttk.Button(row1, text="自动识别", command=self._start_detect)
        self.btn_detect.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        ttk.Button(row1, text="撤销", command=self.undo_last).pack(side=tk.LEFT, padx=2)
        ttk.Button(row1, text="清除所有框", command=self.clear_all).pack(side=tk.LEFT, padx=2)
        ttk.Separator(row1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.btn_preview = ttk.Button(row1, text="预览", command=self._toggle_preview)
        self.btn_preview.pack(side=tk.LEFT, padx=2)
        self.btn_save = ttk.Button(row1, text="保存", command=self.save_file)
        self.btn_save.pack(side=tk.LEFT, padx=2)
        ttk.Separator(row1, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4)
        self.btn_batch = ttk.Button(row1, text="批量处理文件夹", command=self._start_batch)
        self.btn_batch.pack(side=tk.LEFT, padx=2)
        self.btn_doc = ttk.Button(row1, text="处理文档", command=self._start_doc)
        self.btn_doc.pack(side=tk.LEFT, padx=2)
        self.btn_batch_doc = ttk.Button(row1, text="批量处理文档", command=self._start_batch_doc)
        self.btn_batch_doc.pack(side=tk.LEFT, padx=2)
        self.lbl_file = ttk.Label(row1, text="")
        self.lbl_file.pack(side=tk.RIGHT, padx=4)

        # -- Row 2: masking settings --
        row2 = ttk.Frame(tb)
        row2.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row2, text="遮盖方式:").pack(side=tk.LEFT, padx=(0, 2))
        saved_mode = self._cfg_get("mode", "填充")
        if saved_mode not in MODE_MAP:
            saved_mode = "填充"
        self.var_mode = tk.StringVar(value=saved_mode)
        mode_combo = ttk.Combobox(row2, textvariable=self.var_mode, values=list(MODE_MAP.keys()), width=6, state="readonly")
        mode_combo.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(row2, text="边距扩展%:").pack(side=tk.LEFT, padx=(0, 2))
        self.spin_pad = ttk.Spinbox(row2, from_=0, to=100, width=4)
        self.spin_pad.set(self._cfg_get("pad_pct", "0"))
        self.spin_pad.pack(side=tk.LEFT, padx=(0, 4))

    def _build_canvas(self):
        frame = ttk.Frame(self.root)
        frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(frame, bg="#e0e0e0", cursor="cross", highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self._resize_after_id = None
        self.canvas.bind("<Configure>", self._on_canvas_resize)

    def _build_shortcuts(self):
        def _shortcut(fn):
            def handler(e):
                if isinstance(e.widget, (tk.Entry, ttk.Entry, ttk.Spinbox)):
                    return
                fn()
                return "break"
            return handler
        self.root.bind_all("<Control-z>", _shortcut(self.undo_last))
        self.root.bind_all("<Control-y>", _shortcut(self.redo_last))
        self.root.bind_all("<Control-s>", _shortcut(self.save_file))
        self.root.bind_all("<Control-o>", _shortcut(self.open_file))

    def _setup_dnd(self):
        if not HAS_DND:
            return
        self.canvas.drop_target_register(DND_FILES)
        self.canvas.dnd_bind("<<Drop>>", self._on_drop)

    def _on_drop(self, event):
        path = event.data.strip()
        if path.startswith("{") and path.endswith("}"):
            path = path[1:-1]
        if not os.path.isfile(path):
            return
        ext = os.path.splitext(path)[1].lower()
        if ext in IMAGE_EXTS:
            self._load_image(path)
        elif ext in DOC_EXTS:
            self._drop_doc_path = path
            self._start_doc_from_path(path)

    def _build_status(self):
        self.status = ttk.Label(
            self.root,
            text="打开图片 -> 自动识别/手动画框 -> 保存  |  处理文档(Word/PDF)  |  Ctrl+O/S/Z  |  支持拖拽",
            padding=4,
        )
        self.status.pack(fill=tk.X)

    def _get_api_key(self):
        return self.entry_key.get().strip()

    def _get_model(self):
        return self.entry_model.get().strip() or DEFAULT_MODEL

    def _get_passes(self):
        try:
            return max(1, int(self.spin_passes.get()))
        except ValueError:
            return 2

    def _get_base_url(self):
        return self.entry_base_url.get().strip() or DEFAULT_BASE_URL

    def _get_target(self):
        return self.entry_target.get().strip() or DEFAULT_TARGET

    def _get_mask_params(self):
        mode = MODE_MAP.get(self.var_mode.get(), "fill")
        try:
            pad_pct = int(self.spin_pad.get()) / 100.0
        except ValueError:
            pad_pct = 0.3
        return mode, pad_pct

    # ---- single image ops ----

    def open_file(self):
        if self._busy:
            return
        path = filedialog.askopenfilename(
            title="选择图片",
            filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.gif *.tiff *.webp"), ("所有", "*.*")],
        )
        if not path:
            return
        self._load_image(path)

    def _load_image(self, path):
        if self._busy:
            return
        try:
            im = safe_open_image(path)
        except Exception as e:
            messagebox.showerror("打开失败", str(e))
            return
        self.image_path = path
        self.pil_image = im
        self._preview_active = False
        self.clear_all()
        self._display_image()
        self.lbl_file.configure(text=os.path.basename(path))
        self.status.configure(text=f"已打开 {im.width}x{im.height}  |  点「自动识别」或手动画框")

    def _on_canvas_resize(self, event):
        if not self.pil_image:
            return
        if self._resize_after_id:
            self.root.after_cancel(self._resize_after_id)
        self._resize_after_id = self.root.after(150, self._redraw)

    def _redraw(self):
        self._resize_after_id = None
        if not self.pil_image:
            return
        old_scale = self.scale
        self._display_image()
        if old_scale != self.scale:
            self._redraw_regions()

    def _display_image(self, override_im=None):
        self.canvas.delete("img")
        im = override_im or self.pil_image
        if im is None:
            return
        cw = self.canvas.winfo_width() or self.MAX_DISPLAY
        ch = self.canvas.winfo_height() or self.MAX_DISPLAY
        scale = min(cw / im.width, ch / im.height, 1.0)
        dw = max(1, int(im.width * scale))
        dh = max(1, int(im.height * scale))
        disp = im.resize((dw, dh), Image.Resampling.LANCZOS)
        photo = ImageTk.PhotoImage(disp)
        if override_im:
            self._preview_photo = photo
        else:
            self.photo = photo
            self._disp_w = photo.width()
            self._disp_h = photo.height()
            self.scale = scale
        self.canvas.create_image(0, 0, anchor=tk.NW, image=photo, tags="img")
        self.canvas.tag_lower("img")

    def _redraw_regions(self):
        for i, (rid, (rx1, ry1, rx2, ry2), lid) in enumerate(self.regions):
            cx1 = int(rx1 * self.scale)
            cy1 = int(ry1 * self.scale)
            cx2 = int(rx2 * self.scale)
            cy2 = int(ry2 * self.scale)
            self.canvas.coords(rid, cx1, cy1, cx2, cy2)
            if lid:
                mid_x = (cx1 + cx2) / 2
                self.canvas.coords(lid, mid_x, cy1 - 4)

    # ---- preview ----

    def _toggle_preview(self):
        if self._preview_active:
            self._preview_active = False
            self.btn_preview.configure(text="预览")
            self._display_image()
            self._redraw_regions()
            self.status.configure(text=f"已退出预览  |  {len(self.regions)} 个区域")
            return
        if not self.pil_image or not self.regions:
            messagebox.showwarning("提示", "请先打开图片并标记区域")
            return
        mode, pad_pct = self._get_mask_params()
        im = self.pil_image.copy()
        from namemasker.masker import expand_box, fill_region, blur_region, pixelate_region, checkerboard_region
        for (_, (rx1, ry1, rx2, ry2), _) in self.regions:
            x1, y1, x2, y2 = rx1, ry1, rx2, ry2
            if pad_pct > 0:
                x1, y1, x2, y2 = expand_box(x1, y1, x2, y2, pad_pct, im.width, im.height)
            x1, y1 = max(0, int(x1)), max(0, int(y1))
            x2, y2 = min(im.width, int(x2)), min(im.height, int(y2))
            if x2 <= x1 or y2 <= y1:
                continue
            if mode == "fill":
                fill_region(im, x1, y1, x2, y2)
            elif mode == "blur":
                blur_region(im, x1, y1, x2, y2)
            elif mode == "checker":
                checkerboard_region(im, x1, y1, x2, y2)
            else:
                pixelate_region(im, x1, y1, x2, y2)
        self._preview_active = True
        self.btn_preview.configure(text="退出预览")
        self._display_image(override_im=im)
        self.status.configure(text="预览模式  |  再次点击「退出预览」恢复原图")

    # ---- save ----

    def save_file(self):
        if self._busy:
            return
        if not self.image_path:
            messagebox.showwarning("提示", "请先打开一张图片")
            return
        if not self.regions:
            messagebox.showwarning("提示", "请先标记要打码的区域")
            return
        out_path = filedialog.asksaveasfilename(
            title="另存为",
            defaultextension=".png",
            initialfile="masked_" + os.path.basename(self.image_path),
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg *.jpeg"), ("所有", "*.*")],
        )
        if not out_path:
            return
        real_regions = [r for (_, r, _) in self.regions]
        mode, pad_pct = self._get_mask_params()
        try:
            n = apply_mosaic(self.image_path, out_path, real_regions, mode=mode, pad_pct=pad_pct)
            self.status.configure(text=f"已保存: {out_path}  ({n} 处)")
            messagebox.showinfo("完成", f"已处理 {n} 处\n保存至: {out_path}")
        except Exception as e:
            messagebox.showerror("保存失败", str(e))

    # ---- single auto-detect ----

    def _start_detect(self):
        if self._busy:
            return
        if not self.image_path:
            messagebox.showwarning("提示", "请先打开一张图片")
            return
        api_key = self._get_api_key()
        if not api_key:
            messagebox.showwarning("提示", "请填写 API Key")
            return

        self._set_busy(True)
        self.btn_detect.configure(text="识别中...")
        self.root.update()

        def _on_progress(cur, total, msg):
            self.root.after(0, lambda: self.status.configure(text=f"[{cur}/{total}] {msg}"))

        def _worker():
            try:
                results = detect_names(
                    self.image_path,
                    api_key=api_key,
                    model=self._get_model(),
                    base_url=self._get_base_url(),
                    on_progress=_on_progress,
                    passes=self._get_passes(),
                    target=self._get_target(),
                )
                self.root.after(0, lambda r=results: self._on_detect_done(r, None))
            except Exception as exc:
                err = exc
                self.root.after(0, lambda e=err: self._on_detect_done(None, e))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_detect_done(self, results, error):
        self._set_busy(False)
        self.btn_detect.configure(text="自动识别")
        if error:
            self.status.configure(text="识别失败")
            messagebox.showerror("识别失败", str(error))
            return
        if not results:
            self.status.configure(text="未识别到目标内容")
            messagebox.showinfo("结果", "大模型未在图中找到目标内容。\n可手动画框或增加识别轮数重试。")
            return
        for (name, rx1, ry1, rx2, ry2) in results:
            self._add_region_from_real(rx1, ry1, rx2, ry2, label=name)
        names = ", ".join(n for (n, *_) in results)
        self.status.configure(text=f"识别到 {len(results)} 处: {names}  |  右键删除误识别的框")

    def _add_region_from_real(self, rx1, ry1, rx2, ry2, label=""):
        cx1 = int(rx1 * self.scale)
        cy1 = int(ry1 * self.scale)
        cx2 = int(rx2 * self.scale)
        cy2 = int(ry2 * self.scale)
        rid = self.canvas.create_rectangle(cx1, cy1, cx2, cy2, outline="red", width=2, tags="rect")
        lid = None
        if label:
            mid_x = (cx1 + cx2) / 2
            lid = self.canvas.create_text(mid_x, cy1 - 4, text=label, fill="red", font=("Arial", 9), anchor=tk.S, tags="label")
        self.regions.append((rid, (rx1, ry1, rx2, ry2), lid))

    # ---- batch processing ----

    def _start_batch(self):
        if self._busy:
            return
        api_key = self._get_api_key()
        if not api_key:
            messagebox.showwarning("提示", "请先填写 API Key")
            return

        src_dir = filedialog.askdirectory(title="选择要处理的图片文件夹")
        if not src_dir:
            return

        files = []
        for ext in IMAGE_EXTS:
            files.extend(glob.glob(os.path.join(src_dir, "**", f"*{ext}"), recursive=True))
            files.extend(glob.glob(os.path.join(src_dir, "**", f"*{ext.upper()}"), recursive=True))
        files = sorted(set(files))
        if not files:
            messagebox.showinfo("提示", "该文件夹及子文件夹中没有图片文件")
            return

        out_dir = filedialog.askdirectory(title="选择保存目录（处理后的图片保存到此处）")
        if not out_dir:
            return

        ok = messagebox.askokcancel(
            "批量处理确认",
            f"将处理 {len(files)} 张图片（含子文件夹）\n\n"
            f"来源: {src_dir}\n"
            f"保存: {out_dir}\n"
            f"模型: {self._get_model()}\n"
            f"识别轮数: {self._get_passes()}\n\n"
            f"处理中可点「取消批量」停止。",
        )
        if not ok:
            return

        self._batch_cancel = False
        self._batch_out_dir = out_dir
        self._set_busy(True)
        self.root.update()

        model = self._get_model()
        base_url = self._get_base_url()
        passes = self._get_passes()
        mode, pad_pct = self._get_mask_params()
        target = self._get_target()

        def _unique_path(directory, name):
            """Avoid filename collisions in flat output."""
            candidate = os.path.join(directory, name)
            if not os.path.exists(candidate):
                return candidate
            base, ext = os.path.splitext(name)
            i = 1
            while True:
                candidate = os.path.join(directory, f"{base}_{i}{ext}")
                if not os.path.exists(candidate):
                    return candidate
                i += 1

        def _worker():
            success = 0
            fail = 0
            errors = []
            for idx, fpath in enumerate(files):
                if self._batch_cancel:
                    break
                fname = os.path.basename(fpath)
                self.root.after(0, lambda i=idx, n=fname: self.status.configure(
                    text=f"批量处理 [{i+1}/{len(files)}] {n}..."
                ))
                try:
                    results = detect_names(
                        fpath, api_key=api_key, model=model,
                        base_url=base_url, passes=passes,
                        target=target,
                    )
                    out_name = "masked_" + fname
                    base, ext = os.path.splitext(out_name)
                    if ext.lower() not in (".png", ".jpg", ".jpeg"):
                        out_name = base + ".png"
                    out_path = _unique_path(out_dir, out_name)
                    if results:
                        regions = [(x1, y1, x2, y2) for (_, x1, y1, x2, y2) in results]
                        apply_mosaic(fpath, out_path, regions, mode=mode, pad_pct=pad_pct)
                    else:
                        im_orig = safe_open_image(fpath)
                        _safe_save(im_orig, out_path)
                    success += 1
                except Exception as exc:
                    fail += 1
                    errors.append((fname, str(exc)))

            if errors:
                try:
                    log_path = os.path.join(out_dir, "error_log.txt")
                    with open(log_path, "w", encoding="utf-8") as f:
                        for fn, msg in errors:
                            f.write(f"{fn}\n  {msg}\n\n")
                except Exception:
                    pass

            cancelled = self._batch_cancel
            self.root.after(0, lambda: self._on_batch_done(success, fail, len(files), errors, cancelled))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_batch_done(self, success, fail, total, errors, cancelled):
        self._set_busy(False)
        if cancelled:
            msg = f"批量处理已取消: 已完成 {success}/{total}"
        else:
            msg = f"批量处理完成: {success}/{total} 成功"
        if fail:
            msg += f", {fail} 失败"
            detail = "\n".join(f"  {fn}: {e}" for fn, e in errors[:10])
            if len(errors) > 10:
                detail += f"\n  ...共 {len(errors)} 个错误，详见 error_log.txt"
            msg_box = f"{msg}\n\n失败详情:\n{detail}"
        else:
            msg_box = msg
        self.status.configure(text=msg)

        out_dir = self._batch_out_dir
        if messagebox.askyesno("批量处理结果", f"{msg_box}\n\n是否打开输出文件夹?"):
            try:
                os.startfile(out_dir)
            except Exception:
                pass

    # ---- document processing (Word / PDF) ----

    def _start_doc(self):
        if self._busy:
            return
        path = filedialog.askopenfilename(
            title="选择文档",
            filetypes=[
                ("所有支持的文档", "*.docx *.pdf"),
                ("Word 文档", "*.docx"),
                ("PDF 文档", "*.pdf"),
            ],
        )
        if not path:
            return
        self._launch_doc_dialog(path)

    def _start_doc_from_path(self, path):
        if self._busy:
            return
        self._launch_doc_dialog(path)

    def _launch_doc_dialog(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext not in (".docx", ".pdf"):
            messagebox.showerror("错误", "仅支持 .docx 和 .pdf 文件")
            return
        api_key = self.entry_key.get().strip()
        if not api_key:
            messagebox.showwarning("提示", "请先填写 API Key")
            return

        model = self.entry_model.get().strip() or DEFAULT_MODEL
        base_url = self._get_base_url()
        target = self.entry_target.get().strip()
        passes = int(self.spin_passes.get())
        mode_cn = self.var_mode.get()
        mode = MODE_MAP.get(mode_cn, "fill")
        pad_pct = float(self.spin_pad.get()) / 100.0

        from namemasker.doc_dialog import DocProgressDialog

        self._doc_processor = None
        self._doc_out_path = None
        self._doc_ext = ext

        dlg = DocProgressDialog(
            self.root,
            title=f"处理文档 - {os.path.basename(path)}",
            on_save=lambda: self._doc_save_phase(dlg),
        )
        self._doc_dlg = dlg
        dlg.start_progress()

        def _thread_log(msg, level="info"):
            self.root.after(0, lambda m=msg, l=level: dlg.log(m, l))

        def _thread_names(names):
            self.root.after(0, lambda n=names: dlg.log_names(n))

        def _thread_preview(im, caption, boxes):
            self.root.after(0, lambda i=im, c=caption, b=boxes: dlg.add_preview(i, c, b))

        def _detect_worker():
            try:
                if ext == ".docx":
                    from namemasker.doc_process import DocxProcessor
                    proc = DocxProcessor(
                        path, api_key=api_key, model=model, base_url=base_url,
                        target=target, passes=passes, mode=mode, pad_pct=pad_pct,
                    )
                    proc.detect(
                        on_log=_thread_log, on_names=_thread_names,
                        on_preview=_thread_preview, is_cancelled=lambda: dlg.is_cancelled,
                    )
                else:
                    from namemasker.pdf_process import PdfProcessor
                    proc = PdfProcessor(
                        path, api_key=api_key, model=model, base_url=base_url,
                        target=target, passes=passes, mode=mode, pad_pct=pad_pct,
                    )
                    proc.detect(
                        on_log=_thread_log, on_names=_thread_names,
                        on_preview=_thread_preview, is_cancelled=lambda: dlg.is_cancelled,
                    )
                self._doc_processor = proc

                if dlg.is_cancelled:
                    self.root.after(0, lambda: dlg.set_error("已取消"))
                else:
                    self.root.after(0, lambda: dlg.set_done(
                        "检测完成 - 确认无误后点击「确认保存」"))
            except ImportError as exc:
                err = str(exc)
                self.root.after(0, lambda e=err: dlg.set_error(
                    f"缺少依赖: {e}\n请安装: pip install python-docx PyMuPDF"))
            except Exception as exc:
                err = str(exc)
                self.root.after(0, lambda e=err: dlg.set_error(e))

        threading.Thread(target=_detect_worker, daemon=True).start()

    def _doc_save_phase(self, dlg):
        if not self._doc_processor:
            dlg.log("无可保存内容", "warn")
            return

        out_path = filedialog.asksaveasfilename(
            title="保存到",
            initialfile=f"masked_{os.path.basename(self._doc_processor.input_path)}",
            defaultextension=self._doc_ext,
            filetypes=[("同类型文件", f"*{self._doc_ext}")],
        )
        if not out_path:
            return

        dlg.log("\n开始保存...", "info")
        dlg.btn_save.configure(state="disabled")

        def _save_worker():
            try:
                proc = self._doc_processor
                def _log(msg, level="info"):
                    self.root.after(0, lambda m=msg, l=level: dlg.log(m, l))
                proc.save(out_path, on_log=_log)
                self.root.after(0, lambda: self._doc_save_done(dlg, out_path))
            except Exception as exc:
                err = str(exc)
                self.root.after(0, lambda e=err: dlg.log(f"保存失败: {e}", "error"))

        threading.Thread(target=_save_worker, daemon=True).start()

    def _doc_save_done(self, dlg, out_path):
        dlg.log(f"\n文件已保存: {out_path}", "done")
        self.status.configure(text=f"文档已保存: {out_path}")
        if messagebox.askyesno("保存成功", "是否打开输出文件?", parent=dlg):
            try:
                os.startfile(out_path)
            except Exception:
                pass

    # ---- batch document processing ----

    def _start_batch_doc(self):
        if self._busy:
            return
        src_dir = filedialog.askdirectory(title="选择包含文档的文件夹")
        if not src_dir:
            return
        out_dir = filedialog.askdirectory(title="选择输出文件夹")
        if not out_dir:
            return

        api_key = self.entry_key.get().strip()
        if not api_key:
            messagebox.showwarning("提示", "请先填写 API Key")
            return

        files = []
        for ext in (".docx", ".pdf"):
            files.extend(glob.glob(os.path.join(src_dir, "**", f"*{ext}"), recursive=True))
        files = sorted(set(files))

        if not files:
            messagebox.showinfo("提示", "所选文件夹中无 .docx / .pdf 文件")
            return

        model = self.entry_model.get().strip() or DEFAULT_MODEL
        base_url = self._get_base_url()
        target = self.entry_target.get().strip()
        passes = int(self.spin_passes.get())
        mode_cn = self.var_mode.get()
        mode = MODE_MAP.get(mode_cn, "fill")
        pad_pct = float(self.spin_pad.get()) / 100.0

        if not messagebox.askyesno("批量处理文档",
                f"找到 {len(files)} 个文档\n输出到: {out_dir}\n\n开始处理?"):
            return

        from namemasker.doc_dialog import DocProgressDialog
        dlg = DocProgressDialog(
            self.root,
            title=f"批量处理文档 - {len(files)} 个文件",
            on_cancel=lambda: setattr(self, '_batch_doc_cancel', True),
        )
        dlg.btn_save.pack_forget()
        dlg.start_progress()
        self._batch_doc_cancel = False

        def _worker():
            success, fail = 0, 0
            errors = []
            for i, fpath in enumerate(files):
                if self._batch_doc_cancel:
                    break
                fname = os.path.basename(fpath)
                ext = os.path.splitext(fpath)[1].lower()
                rel = os.path.relpath(fpath, src_dir)
                out_path = os.path.join(out_dir, rel)
                os.makedirs(os.path.dirname(out_path), exist_ok=True)

                self.root.after(0, lambda f=fname, n=i: dlg.log(
                    f"\n[{n+1}/{len(files)}] {f}", "info"))

                try:
                    if ext == ".docx":
                        from namemasker.doc_process import DocxProcessor
                        proc = DocxProcessor(
                            fpath, api_key=api_key, model=model, base_url=base_url,
                            target=target, passes=passes, mode=mode, pad_pct=pad_pct,
                        )
                    else:
                        from namemasker.pdf_process import PdfProcessor
                        proc = PdfProcessor(
                            fpath, api_key=api_key, model=model, base_url=base_url,
                            target=target, passes=passes, mode=mode, pad_pct=pad_pct,
                        )

                    def _log(msg, level="info"):
                        self.root.after(0, lambda m=msg, l=level: dlg.log(m, l))

                    def _preview(im, caption, boxes):
                        self.root.after(0, lambda i=im, c=caption, b=boxes: dlg.add_preview(i, c, b))

                    proc.detect(
                        on_log=_log,
                        on_preview=_preview,
                        is_cancelled=lambda: self._batch_doc_cancel,
                    )
                    if self._batch_doc_cancel:
                        break
                    proc.save(out_path, on_log=_log)
                    success += 1
                except Exception as exc:
                    err_msg = str(exc)
                    errors.append((fname, err_msg))
                    fail += 1
                    self.root.after(0, lambda f=fname, e=err_msg: dlg.log(
                        f"  失败: {e}", "error"))

            cancelled = self._batch_doc_cancel
            def _done():
                if cancelled:
                    msg = f"已取消: 完成 {success}/{len(files)}"
                else:
                    msg = f"全部完成: {success}/{len(files)} 成功"
                if fail:
                    msg += f", {fail} 失败"
                dlg.set_done(msg)
                if not cancelled and messagebox.askyesno("批量处理完成",
                        f"{msg}\n\n是否打开输出文件夹?", parent=dlg):
                    try:
                        os.startfile(out_dir)
                    except Exception:
                        pass
            self.root.after(0, _done)

        threading.Thread(target=_worker, daemon=True).start()

    # ---- manual drawing ----

    def _canvas_to_real(self, cx, cy):
        if self.pil_image and self._disp_w > 0 and self._disp_h > 0:
            rx = int(cx * self.pil_image.width / self._disp_w)
            ry = int(cy * self.pil_image.height / self._disp_h)
            return rx, ry
        return int(cx / self.scale), int(cy / self.scale)

    def _event_coords(self, event):
        return self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)

    def _on_press(self, event):
        if not self.pil_image or self._busy:
            return
        if self._preview_active:
            self._toggle_preview()
        cx, cy = self._event_coords(event)
        self.drawing = True
        self.start_x = cx
        self.start_y = cy
        self.cur_rect = self.canvas.create_rectangle(
            cx, cy, cx, cy,
            outline="red", width=2, dash=(4, 4), tags="rect",
        )

    def _on_drag(self, event):
        if not self.drawing or self.cur_rect is None:
            return
        cx, cy = self._event_coords(event)
        self.canvas.coords(self.cur_rect, self.start_x, self.start_y, cx, cy)
        rx1, ry1 = self._canvas_to_real(self.start_x, self.start_y)
        rx2, ry2 = self._canvas_to_real(cx, cy)
        rw = abs(rx2 - rx1)
        rh = abs(ry2 - ry1)
        self.status.configure(text=f"画框中: {rw} x {rh} px")

    def _on_release(self, event):
        if not self.drawing or self.cur_rect is None:
            return
        self.drawing = False
        cx, cy = self._event_coords(event)
        x1, y1 = min(self.start_x, cx), min(self.start_y, cy)
        x2, y2 = max(self.start_x, cx), max(self.start_y, cy)
        if abs(x2 - x1) < 4 or abs(y2 - y1) < 4:
            self.canvas.delete(self.cur_rect)
            self.cur_rect = None
            return
        self.canvas.coords(self.cur_rect, x1, y1, x2, y2)
        self.canvas.itemconfigure(self.cur_rect, dash=(), outline="red", width=2)
        rx1, ry1 = self._canvas_to_real(x1, y1)
        rx2, ry2 = self._canvas_to_real(x2, y2)
        self.regions.append((self.cur_rect, (rx1, ry1, rx2, ry2), None))
        self.cur_rect = None
        self.status.configure(text=f"已标记 {len(self.regions)} 个区域  |  右键删除框")

    def _on_right_click(self, event):
        if self._busy:
            return
        cx, cy = self._event_coords(event)
        items = self.canvas.find_overlapping(cx - 3, cy - 3, cx + 3, cy + 3)
        for item in items:
            for i, (rid, _, lid) in enumerate(self.regions):
                if rid == item:
                    self.canvas.delete(rid)
                    if lid:
                        self.canvas.delete(lid)
                    self.regions.pop(i)
                    self.status.configure(text=f"已删除1个框，剩余 {len(self.regions)} 个区域")
                    return

    # ---- undo/clear ----

    def undo_last(self):
        if not self.regions or self._busy:
            return
        item = self.regions.pop()
        rid, coords, lid = item
        self.canvas.delete(rid)
        if lid:
            self.canvas.delete(lid)
        self._undo_stack.append(item)
        self.status.configure(text=f"已撤销，剩余 {len(self.regions)} 个  |  Ctrl+Y 恢复")

    def redo_last(self):
        if not self._undo_stack or self._busy or not self.pil_image:
            return
        _, coords, _ = self._undo_stack.pop()
        rx1, ry1, rx2, ry2 = coords
        cx1 = int(rx1 * self.pil_image.width / self._disp_w * self._disp_w / self.pil_image.width * self.scale) if self.scale else rx1
        cy1 = int(ry1 * self.scale) if hasattr(self, 'scale') else ry1
        # Redraw using real coords -> canvas coords
        sx = self._disp_w / self.pil_image.width if self.pil_image.width > 0 else 1
        sy = self._disp_h / self.pil_image.height if self.pil_image.height > 0 else 1
        cx1, cy1 = int(rx1 * sx), int(ry1 * sy)
        cx2, cy2 = int(rx2 * sx), int(ry2 * sy)
        rid = self.canvas.create_rectangle(cx1, cy1, cx2, cy2, outline="red", width=2)
        self.regions.append((rid, coords, None))
        self.status.configure(text=f"已恢复，共 {len(self.regions)} 个区域")

    def clear_all(self):
        for rid, _, lid in self.regions:
            self.canvas.delete(rid)
            if lid:
                self.canvas.delete(lid)
        self.regions.clear()
        self._undo_stack.clear()
        self.status.configure(text="已清除所有框")


def main():
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
