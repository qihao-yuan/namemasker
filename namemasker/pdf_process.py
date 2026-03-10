"""
Process PDF files with vision-based type matching.
All pages are rendered to images -> vision API detects target regions by type
-> coordinates mapped back to PDF space -> masked.
"""
import os
import io
import tempfile
import fitz  # PyMuPDF
from PIL import Image

from namemasker.masker import (
    expand_box, fill_region, blur_region,
    pixelate_region, checkerboard_region,
)


def _mask_pil_image(im: Image.Image, regions, mode="fill", pad_pct=0.0):
    for (x1, y1, x2, y2) in regions:
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


class PdfProcessor:
    """
    Two-phase PDF processor using unified vision detection:
    Phase 1 (detect): render every page to image, call vision API to detect
                      target regions by type, collect pixel-level bounding boxes
    Phase 2 (save):   render masked images, replace PDF page content
    """

    def __init__(self, input_path, api_key, model, base_url, target="",
                 passes=2, mode="fill", pad_pct=0.0, dpi=200):
        self.input_path = input_path
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.target = target
        self.passes = passes
        self.mode = mode
        self.pad_pct = pad_pct
        self.dpi = dpi

        self.doc = None
        # (page_idx, regions_in_pixel, labels, page_pil_image)
        self.page_results = []

    def detect(self, on_log=None, on_names=None, on_preview=None, is_cancelled=None):
        from namemasker.api_detect import detect_names, DEFAULT_TARGET, DEFAULT_BASE_URL

        target = self.target or DEFAULT_TARGET
        base_url = self.base_url or DEFAULT_BASE_URL

        def log(msg, level="info"):
            if on_log:
                on_log(msg, level)

        def cancelled():
            return is_cancelled and is_cancelled()

        log("打开 PDF...", "info")
        self.doc = fitz.open(self.input_path)
        total = len(self.doc)
        log(f"共 {total} 页, 渲染 DPI={self.dpi}", "info")

        scale = self.dpi / 72

        for i, page in enumerate(self.doc):
            if cancelled():
                return

            log(f"\n--- 第 {i+1}/{total} 页 ---", "info")
            log(f"  渲染为图像...", "info")

            mat = fitz.Matrix(scale, scale)
            pix = page.get_pixmap(matrix=mat)
            page_im = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
            log(f"  图像尺寸: {page_im.width}x{page_im.height}", "info")

            log(f"  调用视觉 API 按类型检测「{target}」...", "info")

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
                page_im.save(tmp_path)

            try:
                results = detect_names(
                    tmp_path, api_key=self.api_key, model=self.model,
                    base_url=base_url, passes=self.passes, target=target,
                )
            finally:
                os.unlink(tmp_path)

            if results:
                regions = [(x1, y1, x2, y2) for (_, x1, y1, x2, y2) in results]
                labels = [label for (label, *_) in results]
                log(f"  发现 {len(regions)} 处: {', '.join(labels)}", "found")
                if on_names:
                    on_names(labels)
            else:
                regions, labels = [], []
                log("  未发现目标", "info")

            if on_preview:
                on_preview(page_im, f"第 {i+1} 页 - {len(regions)} 处目标", regions)

            self.page_results.append((i, regions, labels, page_im))

        total_hits = sum(len(r[1]) for r in self.page_results)
        log(f"\n检测完成: {total} 页中共 {total_hits} 处目标区域", "done")

    def save(self, output_path, on_log=None):
        def log(msg, level="info"):
            if on_log:
                on_log(msg, level)

        if not self.doc:
            log("无 PDF 数据", "error")
            return 0

        scale = self.dpi / 72
        mask_count = 0

        for (page_idx, regions, labels, page_im) in self.page_results:
            if not regions:
                continue

            page = self.doc[page_idx]
            log(f"第 {page_idx+1} 页: 遮盖 {len(regions)} 处...", "info")

            masked = page_im.copy()
            _mask_pil_image(masked, regions, mode=self.mode, pad_pct=self.pad_pct)

            buf = io.BytesIO()
            masked.save(buf, format="PNG")

            page.clean_contents()
            page.insert_image(page.rect, stream=buf.getvalue(), overlay=True)
            mask_count += len(regions)

        log(f"保存到: {output_path}", "info")
        self.doc.save(output_path, garbage=4, deflate=True)
        self.doc.close()
        log(f"保存完成: 共遮盖 {mask_count} 处", "done")
        return mask_count
