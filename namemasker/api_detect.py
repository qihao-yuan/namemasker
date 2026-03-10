"""
Detect person names in an image using Qwen2.5-VL via DashScope OpenAI-compatible API.
Returns list of (name, x1, y1, x2, y2) in original image pixel coordinates.

Strategy: run full image multiple passes, then send the masked result back
for a verification pass to catch anything missed.
"""
import os
import re
import io
import json
import math
import time
import base64
from PIL import Image, ImageDraw
from openai import OpenAI, APITimeoutError, APIConnectionError
from namemasker.masker import safe_open_image

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen2.5-vl-72b-instruct"

FACTOR = 28
MIN_PIXELS = 4 * FACTOR * FACTOR
MAX_PIXELS = 16384 * FACTOR * FACTOR

PROMPT_TEMPLATE_DETECT = """请仔细观察这张图片，找出图中所有的{target}。

要求：
1. 不要遗漏任何一处，包括小字、边角位置
2. bbox_2d 必须紧紧贴合目标边缘，不要包含多余的空白区域
3. 每个目标单独一个框，不要把多个目标合成一个框
4. name 字段填写识别到的具体内容（纯文本，不要加任何格式符号）

返回 JSON 数组，格式：
[{{"name": "具体内容", "bbox_2d": [x1, y1, x2, y2]}}]

没有找到则返回 []"""

PROMPT_TEMPLATE_VERIFY = """图中白色/红色方块是已遮盖区域。
请检查是否还有未被遮盖的{target}，仔细检查每个角落。

bbox_2d 必须紧贴目标边缘，每个目标单独一个框。
返回 JSON 数组：[{{"name": "内容", "bbox_2d": [x1, y1, x2, y2]}}]
全部已遮盖则返回 []"""

DEFAULT_TARGET = "中文人名（包括姓名、昵称、网名、用户名等可以识别为具体个人身份的文字）"


def _smart_resize(width: int, height: int):
    if min(width, height) < 1:
        return FACTOR, FACTOR
    if max(width, height) / min(width, height) > 200:
        raise ValueError("Image aspect ratio too extreme")
    current_pixels = width * height
    if current_pixels > MAX_PIXELS:
        scale = math.sqrt(MAX_PIXELS / current_pixels)
        width = int(width * scale)
        height = int(height * scale)
    if current_pixels < MIN_PIXELS:
        scale = math.sqrt(MIN_PIXELS / current_pixels)
        width = int(width * scale)
        height = int(height * scale)
    rw = max(FACTOR, round(width / FACTOR) * FACTOR)
    rh = max(FACTOR, round(height / FACTOR) * FACTOR)
    if rw * rh > MAX_PIXELS:
        scale = math.sqrt(MAX_PIXELS / (rw * rh))
        rw = int(rw * scale / FACTOR) * FACTOR
        rh = int(rh * scale / FACTOR) * FACTOR
    return rw, rh


def _pil_to_base64(im: Image.Image, fmt: str = "PNG") -> str:
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _encode_image_base64(image_path: str) -> str:
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


def _guess_mime(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".gif": "image/gif", ".webp": "image/webp", ".bmp": "image/bmp",
    }.get(ext, "image/jpeg")


def _parse_response(text: str):
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    return []


def _call_api(client: OpenAI, model: str, data_url: str, prompt: str,
              max_retries: int = 2, timeout: int = 120):
    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": [{"type": "text", "text": "你是一个精确的图片文字定位助手。"}],
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {"url": data_url},
                                "min_pixels": MIN_PIXELS,
                                "max_pixels": MAX_PIXELS,
                            },
                            {"type": "text", "text": prompt},
                        ],
                    },
                ],
                temperature=0.1,
                timeout=timeout,
            )
            raw = resp.choices[0].message.content or ""
            return _parse_response(raw)
        except (APITimeoutError, APIConnectionError):
            if attempt < max_retries:
                time.sleep(2)
            else:
                raise


def _convert_boxes(items, model_w, model_h, orig_w, orig_h, offset_x=0, offset_y=0):
    results = []
    for item in items:
        name = re.sub(r"[*_`#~]", "", item.get("name", "")).strip()
        bbox = item.get("bbox_2d")
        if not name or not bbox or len(bbox) != 4:
            continue
        mx1, my1, mx2, my2 = bbox
        x1 = int(mx1 / model_w * orig_w) + offset_x
        y1 = int(my1 / model_h * orig_h) + offset_y
        x2 = int(mx2 / model_w * orig_w) + offset_x
        y2 = int(my2 / model_h * orig_h) + offset_y
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        if x2 - x1 > 2 and y2 - y1 > 2:
            results.append((name, x1, y1, x2, y2))
    return results


def _iou(a, b):
    ix1 = max(a[0], b[0])
    iy1 = max(a[1], b[1])
    ix2 = min(a[2], b[2])
    iy2 = min(a[3], b[3])
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter == 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return inter / (area_a + area_b - inter)


def _deduplicate(results, iou_thresh=0.3):
    if not results:
        return results
    kept = []
    for (name, x1, y1, x2, y2) in results:
        dup = False
        for (kn, kx1, ky1, kx2, ky2) in kept:
            if name == kn and _iou((x1, y1, x2, y2), (kx1, ky1, kx2, ky2)) > iou_thresh:
                dup = True
                break
        if not dup:
            kept.append((name, x1, y1, x2, y2))
    return kept


EDGE_RATIO = 0.3


def _compute_edge_strips(img_w, img_h, ratio=EDGE_RATIO):
    """
    Return edge crop regions: left, right, top, bottom strips.
    Each strip is ratio * that dimension wide/tall, full length on the other axis.
    Only yields strips for dimensions > 600px (small images don't need it).
    Returns list of (crop_x, crop_y, crop_w, crop_h).
    """
    strips = []
    if img_w > 600:
        sw = int(img_w * ratio)
        strips.append((0, 0, sw, img_h))                  # left
        strips.append((img_w - sw, 0, sw, img_h))         # right
    if img_h > 600:
        sh = int(img_h * ratio)
        strips.append((0, 0, img_w, sh))                  # top
        strips.append((0, img_h - sh, img_w, sh))         # bottom
    return strips


def _mark_regions_on_image(im: Image.Image, regions, pad_pct=0.3):
    """Draw red filled rectangles over detected regions to create a 'masked' preview."""
    marked = im.copy()
    draw = ImageDraw.Draw(marked)
    for (_, x1, y1, x2, y2) in regions:
        x1, x2 = min(x1, x2), max(x1, x2)
        y1, y2 = min(y1, y2), max(y1, y2)
        w, h = x2 - x1, y2 - y1
        if w < 1 or h < 1:
            continue
        px, py = int(w * pad_pct), int(h * pad_pct)
        bx1 = max(0, x1 - px)
        by1 = max(0, y1 - py)
        bx2 = min(im.width, x2 + px)
        by2 = min(im.height, y2 + py)
        if bx2 <= bx1 or by2 <= by1:
            continue
        draw.rectangle((bx1, by1, bx2, by2), fill=(255, 80, 80))
    return marked


def detect_names(
    image_path: str,
    api_key: str = "",
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    on_progress=None,
    passes: int = 2,
    target: str = DEFAULT_TARGET,
):
    """
    Detect target content via combined strategy:
      1. Full image detection
      2. Edge strip detection (left/right/top/bottom crops for edge text)
      3. Multi-pass verification (mask found items, ask model to check for missed ones)
    target: description of what to find, e.g. "中文人名", "手机号", "身份证号" etc.
    Returns list of (name_str, x1, y1, x2, y2) in original image pixel coords.
    """
    if not api_key:
        api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        raise ValueError("未提供 API Key。请设置环境变量 DASHSCOPE_API_KEY 或在界面中填写。")

    prompt_detect = PROMPT_TEMPLATE_DETECT.format(target=target)
    prompt_verify = PROMPT_TEMPLATE_VERIFY.format(target=target)

    im = safe_open_image(image_path)
    img_w, img_h = im.size
    model_w, model_h = _smart_resize(img_w, img_h)

    client = OpenAI(api_key=api_key, base_url=base_url)

    edge_strips = _compute_edge_strips(img_w, img_h)
    total_steps = 1 + len(edge_strips) + max(0, passes - 1)
    step = 0

    def _progress(msg):
        nonlocal step
        step += 1
        if on_progress:
            on_progress(step, total_steps, msg)

    all_results = []

    # -- step 1: full image --
    _progress("识别全图...")
    b64 = _encode_image_base64(image_path)
    mime = _guess_mime(image_path)
    data_url = f"data:{mime};base64,{b64}"
    items = _call_api(client, model, data_url, prompt_detect)
    all_results.extend(_convert_boxes(items, model_w, model_h, img_w, img_h))

    # -- step 2: edge strips --
    strip_names = ["左边缘", "右边缘", "上边缘", "下边缘"]
    for i, (sx, sy, sw, sh) in enumerate(edge_strips):
        label = strip_names[i] if i < len(strip_names) else f"边缘{i+1}"
        _progress(f"识别{label}...")
        crop = im.crop((sx, sy, sx + sw, sy + sh))
        crop_b64 = _pil_to_base64(crop, "PNG")
        crop_url = f"data:image/png;base64,{crop_b64}"
        crop_items = _call_api(client, model, crop_url, prompt_detect)
        crop_mw, crop_mh = _smart_resize(sw, sh)
        all_results.extend(_convert_boxes(crop_items, crop_mw, crop_mh, sw, sh, offset_x=sx, offset_y=sy))

    # -- step 3: verification passes --
    for p in range(1, passes):
        current = _deduplicate(all_results)
        if not current:
            _progress(f"查漏第{p}轮: 上轮无结果，再次识别...")
            items = _call_api(client, model, data_url, prompt_detect)
            new_results = _convert_boxes(items, model_w, model_h, img_w, img_h)
        else:
            _progress(f"查漏第{p}轮: 遮盖已找到的 {len(current)} 处，检查遗漏...")
            marked = _mark_regions_on_image(im, current)
            marked_b64 = _pil_to_base64(marked, "PNG")
            marked_url = f"data:image/png;base64,{marked_b64}"
            items = _call_api(client, model, marked_url, prompt_verify)
            new_results = _convert_boxes(items, model_w, model_h, img_w, img_h)
        all_results.extend(new_results)

    # -- clamp & deduplicate --
    clamped = []
    for (name, x1, y1, x2, y2) in all_results:
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(img_w, x2)
        y2 = min(img_h, y2)
        if x2 - x1 > 2 and y2 - y1 > 2:
            clamped.append((name, x1, y1, x2, y2))

    return _deduplicate(clamped)
