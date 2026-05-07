"""Local clipping: ffmpeg subclip + OpenCV face-aware vertical crop with karaoke TikTok captions.

Two stages per highlight:
  1. Cut the source video to [start, end] with ffmpeg.
  2. Reframe the cut to the target aspect ratio, tracking faces smoothly, and burning
     highly engaging karaoke-style captions with word-level active highlighting.
"""
import os
import re
import subprocess
from statistics import median
from typing import Dict, List, Optional, Sequence

from ..config import LOCAL_OUTPUT_DIR


def _ratio(aspect_ratio: str) -> float:
    """Parse '9:16' → 9/16, '1:1' → 1.0."""
    try:
        w, h = aspect_ratio.split(":")
        return float(w) / float(h)
    except (ValueError, ZeroDivisionError):
        return 9.0 / 16.0


def _slugify(text: str) -> str:
    """Convert 'Hello World!' to 'hello_world'."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s-]+", "_", text)
    return text


def _cut_subclip(source_path: str, start: float, end: float, out_path: str) -> str:
    """ffmpeg -ss start -to end → re-encoded mp4 with audio."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", source_path,
        "-ss", f"{start:.3f}",
        "-to", f"{end:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    return out_path


def _get_word_captions(segments: List[Dict], start_time: float, end_time: float) -> List[Dict]:
    """Extract individual words with precise start/end times within the subclip."""
    words = []
    for s in segments:
        s_start = float(s["start"])
        s_end = float(s["end"])
        if s_end < start_time or s_start > end_time:
            continue
        
        # Prefer word-level timestamps if provided by Whisper
        if s.get("words"):
            for w in s["words"]:
                w_start = float(w["start"])
                w_end = float(w["end"])
                if w_end < start_time or w_start > end_time:
                    continue
                words.append({
                    "word": w["word"].strip().upper(),
                    "start": w_start,
                    "end": w_end,
                })
        else:
            # Fallback: distribute words evenly across segment duration
            seg_start = max(start_time, s_start)
            seg_end = min(end_time, s_end)
            seg_duration = seg_end - seg_start
            subwords = s["text"].strip().split()
            if not subwords or seg_duration <= 0:
                continue
            word_dur = seg_duration / len(subwords)
            for i, sw in enumerate(subwords):
                w_start = seg_start + i * word_dur
                w_end = w_start + word_dur
                words.append({
                    "word": sw.strip().upper(),
                    "start": w_start,
                    "end": w_end,
                })
    return words


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _median_smooth(values: Sequence[float], radius: int) -> List[float]:
    if radius <= 0 or not values:
        return list(values)
    out = []
    for i in range(len(values)):
        left = max(0, i - radius)
        right = min(len(values), i + radius + 1)
        out.append(float(median(values[left:right])))
    return out


def _ema_with_motion_limits(
    values: Sequence[float],
    start_value: float,
    fps: float,
    max_pixels_per_second: float,
) -> List[float]:
    """Smooth a camera trajectory while limiting per-frame pan speed."""
    if not values:
        return []

    max_step = max(1.0, max_pixels_per_second / max(1.0, fps))
    smoothed = []
    current = start_value
    for target in values:
        delta = target - current
        step = _clamp(delta * 0.12, -max_step, max_step)
        current += step
        smoothed.append(current)
    return smoothed


def _detect_faces(frame, cv2, face_cascade) -> List[Dict]:
    """Return OpenCV Haar face boxes as {x, y, w, h, score}."""
    faces: List[Dict] = []
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    detected = face_cascade.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
    )
    for x, y, bw, bh in detected:
        faces.append({"x": int(x), "y": int(y), "w": int(bw), "h": int(bh), "score": 0.7})
    return faces


def _build_focus_trajectory(
    cap,
    cv2,
    face_cascade,
    src_w: int,
    src_h: int,
    crop_w: int,
    fps: float,
) -> List[float]:
    """Precompute a stable horizontal crop center for every frame."""
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if total_frames <= 0:
        return []

    detector_name = "opencv-haar"
    detect_every_n = max(2, int(round(fps / 6.0)))
    max_match_distance = max(crop_w * 0.45, src_w * 0.12)
    sample_frames = list(range(0, total_frames, detect_every_n))
    tracks: List[Dict] = []

    for frame_idx in sample_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            continue
        faces = _detect_faces(frame, cv2, face_cascade)
        detections = []
        for face in faces:
            area = float(face["w"] * face["h"])
            if area < (src_w * src_h) * 0.002:
                continue
            cx = float(face["x"] + face["w"] / 2.0)
            cy = float(face["y"] + face["h"] / 2.0)
            detections.append({**face, "frame": frame_idx, "cx": cx, "cy": cy, "area": area})

        used_tracks = set()
        for det in sorted(detections, key=lambda d: d["area"], reverse=True):
            best_idx = None
            best_distance = None
            for ti, track in enumerate(tracks):
                if ti in used_tracks:
                    continue
                last = track["detections"][-1]
                if frame_idx - last["frame"] > detect_every_n * 10:
                    continue
                distance = abs(det["cx"] - last["cx"])
                if distance > max_match_distance:
                    continue
                if best_distance is None or distance < best_distance:
                    best_idx = ti
                    best_distance = distance

            if best_idx is None:
                tracks.append({"detections": [det]})
                used_tracks.add(len(tracks) - 1)
            else:
                tracks[best_idx]["detections"].append(det)
                used_tracks.add(best_idx)

    min_center = crop_w / 2.0
    max_center = src_w - crop_w / 2.0
    default_center = _clamp(src_w / 2.0, min_center, max_center)

    if not tracks:
        print(f"[face-track] {detector_name}: no faces found; using center crop", flush=True)
        return [default_center] * total_frames

    def track_score(track: Dict) -> float:
        detections = track["detections"]
        coverage = len(detections) / max(1, len(sample_frames))
        avg_area = sum(d["area"] for d in detections) / len(detections)
        avg_center = sum(d["cx"] for d in detections) / len(detections)
        centrality = 1.0 - min(1.0, abs(avg_center - src_w / 2.0) / max(1.0, src_w / 2.0))
        stability = 1.0
        if len(detections) > 1:
            xs = [d["cx"] for d in detections]
            stability = 1.0 - min(1.0, (max(xs) - min(xs)) / max(1.0, src_w))
        return coverage * 3.0 + (avg_area / max(1.0, src_w * src_h)) * 18.0 + centrality * 0.35 + stability * 0.4

    ranked_tracks = sorted(tracks, key=track_score, reverse=True)
    primary = ranked_tracks[0]
    primary_detections = primary["detections"]

    # If two persistent faces fit inside the vertical crop, frame them as a group.
    group_tracks = [primary]
    if len(ranked_tracks) > 1:
        primary_frames = {d["frame"] for d in primary_detections}
        for candidate in ranked_tracks[1:3]:
            cand_detections = candidate["detections"]
            cand_frames = {d["frame"] for d in cand_detections}
            overlap_ratio = len(primary_frames & cand_frames) / max(1, min(len(primary_frames), len(cand_frames)))
            if overlap_ratio < 0.55:
                continue
            primary_avg_x = sum(d["cx"] for d in primary_detections) / len(primary_detections)
            cand_avg_x = sum(d["cx"] for d in cand_detections) / len(cand_detections)
            if abs(primary_avg_x - cand_avg_x) <= crop_w * 0.72:
                group_tracks.append(candidate)

    keyframes: Dict[int, float] = {}
    if len(group_tracks) > 1:
        by_frame: Dict[int, List[Dict]] = {}
        for track in group_tracks:
            for det in track["detections"]:
                by_frame.setdefault(det["frame"], []).append(det)
        for frame_idx, detections in by_frame.items():
            min_x = min(d["x"] for d in detections)
            max_x = max(d["x"] + d["w"] for d in detections)
            center = (min_x + max_x) / 2.0
            keyframes[frame_idx] = _clamp(center, min_center, max_center)
        mode_note = f"group({len(group_tracks)})"
    else:
        for det in primary_detections:
            keyframes[det["frame"]] = _clamp(det["cx"], min_center, max_center)
        mode_note = "single"

    sorted_keys = sorted(keyframes)
    raw = [default_center] * total_frames
    first_key = sorted_keys[0]
    for i in range(0, min(first_key, total_frames)):
        raw[i] = keyframes[first_key]
    for left, right in zip(sorted_keys, sorted_keys[1:]):
        left_v = keyframes[left]
        right_v = keyframes[right]
        span = max(1, right - left)
        for i in range(left, min(right + 1, total_frames)):
            t = (i - left) / span
            raw[i] = left_v + (right_v - left_v) * t
    last_key = sorted_keys[-1]
    for i in range(last_key, total_frames):
        raw[i] = keyframes[last_key]

    median_radius = max(1, int(round(fps * 0.35)))
    medianed = _median_smooth(raw, median_radius)
    smoothed = _ema_with_motion_limits(
        medianed,
        medianed[0],
        fps=fps,
        max_pixels_per_second=max(80.0, crop_w * 0.85),
    )
    trajectory = [_clamp(v, min_center, max_center) for v in smoothed]

    print(
        f"[face-track] {detector_name}: {len(tracks)} tracks, mode={mode_note}, "
        f"keyframes={len(sorted_keys)}, frames={total_frames}",
        flush=True,
    )
    return trajectory


def _get_font(font_size: int, font_weight: str = "bold"):
    """Load a modern sans-serif system font or fallback."""
    import os
    from PIL import ImageFont
    paths = []
    if font_weight == "bold":
        paths = [
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/System/Library/Fonts/Helvetica-Bold.ttf",
            "/System/Library/Fonts/SFNS.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ]
    else:
        paths = [
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/System/Library/Fonts/Supplemental/Helvetica.ttf",
            "/Library/Fonts/Arial.ttf",
        ]
    
    for path in paths:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, font_size)
            except Exception:
                pass
    return ImageFont.load_default()


def detect_highlight_keywords(text: str) -> List[str]:
    """Identify high-impact keywords to highlight (verbs, emotionally charged words, pronouns, questions)."""
    words = text.strip().upper().replace(".", "").replace(",", "").replace("?", "").replace("!", "").split()
    impact_words = {
        "BANYAK", "ISTRINYA", "NANYA", "NIKAH", "KAWIN", "MEMBUJANG", "VIRAL", "RAHASIA", "TIPS", "TRIK", 
        "SUAMI", "ISTRI", "ANAK", "PENTING", "SALAH", "BENAR", "JALAN", "SELAMAT", "BELAJAR", "BISA", "HARUS",
        "WELCOME", "EPISODE", "BENAR", "LURUS", "KESEMPATAN"
    }
    highlights = []
    for w in words:
        if w in impact_words or len(w) > 6:
            highlights.append(w)
    if not highlights and words:
        highlights.append(max(words, key=len))
    return highlights


def draw_gradient_overlay(draw, out_w: int, out_h: int, pil_img):
    """Draw a cinematic dark fade from the bottom (nearly black at the bottom)."""
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for gy in range(int(out_h * 0.45), out_h):
        factor = ((gy - out_h * 0.45) / (out_h * 0.55)) ** 1.8
        alpha = int(245 * factor)
        overlay_draw.line([(0, gy), (out_w, gy)], fill=(4, 4, 6, alpha))
    return Image.alpha_composite(pil_img.convert("RGBA"), overlay)


def draw_subject_outline(draw, out_w: int, out_h: int, center_x: float, center_y: float):
    """Draw a hand-drawn creator-style accent ring or cutout outline around the speaker's face area."""
    pass


def draw_badge(draw, out_w: int, out_h: int, style: str = "modern_shorts"):
    """Draw an asymmetrical high-energy badge at top-left."""
    badge_x = int(out_w * 0.07)
    badge_y = int(out_h * 0.09)
    badge_font = _get_font(int(out_w * 0.045), "bold")
    badge_text = "SHORTS PILIHAN"
    
    text_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    text_w = text_bbox[2] - text_bbox[0]
    text_h = text_bbox[3] - text_bbox[1]
    
    pad_x = int(out_w * 0.03)
    pad_y = int(out_h * 0.012)
    
    shadow_offset = 5
    draw.rounded_rectangle(
        [badge_x + shadow_offset, badge_y + shadow_offset, badge_x + text_w + pad_x * 2 + shadow_offset, badge_y + text_h + pad_y * 2 + shadow_offset],
        radius=14,
        fill=(0, 0, 0, 180)
    )
    draw.rounded_rectangle(
        [badge_x, badge_y, badge_x + text_w + pad_x * 2, badge_y + text_h + pad_y * 2],
        radius=14,
        fill=(255, 222, 0, 255),  # Viral neon yellow
        outline=(0, 0, 0, 255),
        width=3
    )
    draw.text((badge_x + pad_x, badge_y + pad_y - 2), badge_text, fill=(8, 8, 8, 255), font=badge_font)


def draw_cta(draw, out_w: int, out_h: int, style: str = "modern_shorts"):
    """Draw a minimal, subtle CTA on the lower-right."""
    sub_font = _get_font(int(out_w * 0.035), "bold")
    sub_text = "• TONTON SAMPAI AKHIR"
    sub_bbox = draw.textbbox((0, 0), sub_text, font=sub_font)
    sub_w = sub_bbox[2] - sub_bbox[0]
    sub_h = sub_bbox[3] - sub_bbox[1]
    
    sub_x = out_w - sub_w - int(out_w * 0.07)
    sub_y = out_h - sub_h - int(out_h * 0.06)
    
    for ox, oy in [(-2, -2), (2, -2), (-2, 2), (2, 2), (0, 3)]:
        draw.text((sub_x + ox, sub_y + oy), sub_text, fill=(0, 0, 0, 255), font=sub_font)
    draw.text((sub_x, sub_y), sub_text, fill=(255, 255, 255, 230), font=sub_font)


def draw_dynamic_title(draw, out_w: int, out_h: int, title: str, style: str = "modern_shorts"):
    """Draw oversized, bold, high-contrast, asymmetrical typography with keyword highlighting directly on screen."""
    title_text = title.upper()
    highlights = detect_highlight_keywords(title_text)
    
    font_size = int(out_w * 0.10)
    if len(title_text) > 34:
        font_size = int(out_w * 0.08)
        
    title_font = _get_font(font_size, "bold")
    margin_left = int(out_w * 0.08)
    start_y = int(out_h * 0.58)
    
    words = title_text.split()
    lines = []
    current_line = []
    
    for word in words:
        test_line = " ".join(current_line + [word])
        test_bbox = draw.textbbox((0, 0), test_line, font=title_font)
        test_w = test_bbox[2] - test_bbox[0]
        if test_w <= (out_w - margin_left * 2) and len(current_line) < 2:
            current_line.append(word)
        else:
            if current_line:
                lines.append(current_line)
            current_line = [word]
    if current_line:
        lines.append(current_line)
        
    lines = lines[:3]
    
    line_y = start_y
    for line_idx, line_words in enumerate(lines):
        x = margin_left
        for w_idx, word in enumerate(line_words):
            clean_word = word.replace(".", "").replace(",", "").replace("?", "").replace("!", "")
            is_highlight = clean_word in highlights
            
            fill_color = (255, 222, 0, 255) if is_highlight else (255, 255, 255, 255)
            stroke_w = 6
            stroke_color = (0, 0, 0, 255)
            
            w_bbox = draw.textbbox((0, 0), word, font=title_font)
            w_width = w_bbox[2] - w_bbox[0]
            w_height = w_bbox[3] - w_bbox[1]
            
            draw.text(
                (x + 4, line_y + 4),
                word,
                fill=(0, 0, 0, 180),
                font=title_font
            )
            draw.text(
                (x, line_y),
                word,
                fill=fill_color,
                font=title_font,
                stroke_width=stroke_w,
                stroke_fill=stroke_color
            )
            
            pass
                
            x += w_width + int(out_w * 0.03)
            
        line_y += font_size + int(out_h * 0.015)


def render_thumbnail(
    frame,
    title: str,
    out_w: int,
    out_h: int,
    center_x: float,
    center_y: float,
    style: str = "modern_shorts"
):
    """Redesign the current thumbnail renderer to look modern, dynamic, and attention-grabbing instead of boxy."""
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw
    pil_img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_img, "RGBA")
    
    pil_img = draw_gradient_overlay(draw, out_w, out_h, pil_img)
    draw = ImageDraw.Draw(pil_img, "RGBA")
    
    if center_x > 0 and center_y > 0:
        draw_subject_outline(draw, out_w, out_h, center_x, center_y)
        
    draw_badge(draw, out_w, out_h, style)
    draw_dynamic_title(draw, out_w, out_h, title, style)
    draw_cta(draw, out_w, out_h, style)
    
    return cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2BGR)


def _reframe_vertical(
    in_path: str,
    out_path: str,
    aspect_ratio: str,
    start_time: float,
    word_captions: List[Dict],
    draw_subtitles: bool = True,
    title: Optional[str] = None,
) -> str:
    """Crop video, apply smooth horizontal face tracking, and burn karaoke captions."""
    try:
        import cv2  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "opencv-python is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    target_ratio = _ratio(aspect_ratio)
    cap = cv2.VideoCapture(in_path)
    if not cap.isOpened():
        raise RuntimeError(f"could not open {in_path}")

    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    # Compute largest crop window
    if target_ratio < src_w / src_h:
        crop_h = src_h
        crop_w = int(crop_h * target_ratio)
    else:
        crop_w = src_w
        crop_h = int(crop_w / target_ratio)
    crop_w = max(2, crop_w - (crop_w % 2))
    crop_h = max(2, crop_h - (crop_h % 2))

    # For vertical 9:16, we want standard 1080x1920 Full HD portrait
    if aspect_ratio == "9:16":
        out_w, out_h = 1080, 1920
    else:
        out_w, out_h = crop_w, crop_h

    face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

    focus_trajectory = _build_focus_trajectory(
        cap,
        cv2,
        face_cascade,
        src_w=src_w,
        src_h=src_h,
        crop_w=crop_w,
        fps=fps,
    )

    silent_path = out_path + ".silent.mp4"
    cmd_ffmpeg = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{out_w}x{out_h}",
        "-r", f"{fps:.3f}",
        "-i", "-",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        silent_path
    ]
    writer_proc = subprocess.Popen(cmd_ffmpeg, stdin=subprocess.PIPE)
    y0 = int((src_h - crop_h) // 2)

    # Reset to beginning for the actual frame-writing pass
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    frame_idx = 0
    last_active_word = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        current_time = start_time + (frame_idx / fps)
        if focus_trajectory:
            center_x = focus_trajectory[min(frame_idx, len(focus_trajectory) - 1)]
        else:
            center_x = src_w / 2.0
        x0 = int(round(center_x - crop_w / 2.0))
        x0 = max(0, min(src_w - crop_w, x0))
        frame_idx += 1

        cropped = frame[y0:y0 + crop_h, x0:x0 + crop_w]

        # Upscale cropped frame to standard crisp Full HD (1080x1920) before drawing overlays
        if out_w != crop_w or out_h != crop_h:
            cropped = cv2.resize(cropped, (out_w, out_h), interpolation=cv2.INTER_CUBIC)

        if draw_subtitles:
            # Active word-level highlighting logic (matches Remotion single active word)
            display_word = None
            is_active = False

            for idx, w in enumerate(word_captions):
                if w["start"] <= current_time <= w["end"]:
                    display_word = w
                    is_active = True
                    last_active_word = w
                    break

            if not display_word and last_active_word:
                # Keep showing last word if the gap is small (less than 1.0 seconds)
                if current_time - last_active_word["end"] < 1.0:
                    display_word = last_active_word
                    is_active = False

            # 1. First 24 frames (~0.8s): Draw Modern Creator Thumbnail Teaser
            if frame_idx < 24 and title:
                face_canvas_x = int(((center_x - x0) / crop_w) * out_w)
                face_canvas_y = int(out_h * 0.32)
                cropped = render_thumbnail(cropped, title, out_w, out_h, face_canvas_x, face_canvas_y)

            # 2. Body frames (or active words): Draw Single Word Subtitles
            elif display_word:
                # Convert BGR (OpenCV) to RGB (PIL)
                pil_img = Image.fromarray(cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB))
                draw = ImageDraw.Draw(pil_img, "RGBA")
                
                word_text = display_word["word"].upper()
                sub_font_size = int(out_w * 0.06)
                sub_font = _get_font(sub_font_size, "bold")
                
                sub_bbox = draw.textbbox((0, 0), word_text, font=sub_font)
                sub_w = sub_bbox[2] - sub_bbox[0]
                sub_h = sub_bbox[3] - sub_bbox[1]
                
                sub_x = (out_w - sub_w) // 2
                sub_y = int(out_h * 0.75)  # Centered horizontally, at 75% height (lower third)
                
                word_color = (255, 222, 0, 255) if is_active else (255, 255, 255, 215)
                
                # Draw thick black shadow/outline
                shadow_offsets = [
                    (3, 3), (-3, -3), (3, -3), (-3, 3),
                    (0, 4), (0, -4), (4, 0), (-4, 0)
                ]
                for ox, oy in shadow_offsets:
                    draw.text((sub_x + ox, sub_y + oy), word_text, fill=(0, 0, 0, 255), font=sub_font)
                
                # Draw fill text
                draw.text((sub_x, sub_y), word_text, fill=word_color, font=sub_font)
                
                # Convert RGB (PIL) back to BGR (OpenCV)
                cropped = cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2BGR)

        writer_proc.stdin.write(cropped.tobytes())

    cap.release()
    writer_proc.stdin.close()
    writer_proc.wait()

    # Mux audio back on (copying pristine CRF 18 video stream directly)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", silent_path,
        "-i", in_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-map", "0:v:0", "-map", "1:a:0?",
        "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True)
    os.remove(silent_path)
    return out_path


def _render_with_remotion(
    cropped_video_path: str,
    word_captions: List[Dict],
    out_path: str,
    duration_sec: float,
    fps: float,
    title: Optional[str] = None,
    clip_start_time: float = 0.0,
) -> None:
    """Invokes the Remotion CLI to render premium bouncing titles and progress overlays."""
    import json
    import tempfile

    # Use the clip's start_time as base — NOT the first word's timestamp.
    # The cropped video's frame 0 corresponds to clip_start_time in the
    # original video, so all subtitle times must be relative to that.
    base_time = clip_start_time
    formatted_subtitles = []
    for w in word_captions:
        start_rel = max(0.0, w["start"] - base_time)
        end_rel = max(0.0, w["end"] - base_time)
        formatted_subtitles.append({
            "word": w["word"],
            "start": start_rel,
            "end": end_rel,
        })

    props = {
        "videoUrl": os.path.relpath(os.path.abspath(cropped_video_path), os.path.abspath(".")),
        "subtitles": formatted_subtitles,
        "fps": fps,
        "title": title or "",
    }

    props_fd, props_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(props_fd, "w") as f:
            json.dump(props, f)

        # Remotion composition is hardcoded at 30fps (Root.tsx).
        # duration_frames must match that, NOT the native video fps.
        REMOTION_FPS = 30
        duration_frames = int(duration_sec * REMOTION_FPS)
        cmd = [
            "npx", "remotion", "render", "Shorts",
            os.path.abspath(out_path),
            f"--props={props_path}",
            f"--frames=0-{duration_frames}",
            "--browser=chrome",
        ]
        renderer_dir = os.path.abspath("shorts_renderer")
        subprocess.run(cmd, cwd=renderer_dir, check=True)
    finally:
        if os.path.exists(props_path):
            os.remove(props_path)


def crop_clip_local(
    source_path: str,
    start_time: float,
    end_time: float,
    aspect_ratio: str,
    out_path: str,
    transcript_segments: Optional[List[Dict]] = None,
    title: Optional[str] = None,
) -> str:
    """Cut + reframe one highlight, returning the local mp4 path."""
    cut_path = out_path + ".cut.mp4"
    word_captions = _get_word_captions(transcript_segments or [], start_time, end_time)
    
    from ..config import USE_REMOTION
    
    try:
        _cut_subclip(source_path, start_time, end_time, cut_path)
        if USE_REMOTION and os.path.exists("shorts_renderer"):
            clean_cropped_path = out_path + ".clean.mp4"
            _reframe_vertical(cut_path, clean_cropped_path, aspect_ratio, start_time, word_captions, draw_subtitles=False, title=title)
            
            duration_sec = end_time - start_time
            fps = 30.0
            import cv2  # type: ignore
            cap = cv2.VideoCapture(clean_cropped_path)
            if cap.isOpened():
                fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                cap.release()

            _render_with_remotion(clean_cropped_path, word_captions, out_path, duration_sec, fps, title=title, clip_start_time=start_time)
            if os.path.exists(clean_cropped_path):
                os.remove(clean_cropped_path)
        else:
            _reframe_vertical(cut_path, out_path, aspect_ratio, start_time, word_captions, draw_subtitles=True, title=title)
    finally:
        if os.path.exists(cut_path):
            os.remove(cut_path)
    return out_path


def crop_highlights_local(
    source_path: str,
    highlights: List[Dict],
    aspect_ratio: str = "9:16",
    out_dir: Optional[str] = None,
    transcript_segments: Optional[List[Dict]] = None,
) -> List[Dict]:
    stem = os.path.splitext(os.path.basename(source_path))[0]
    video_id = stem.replace("source_", "")
    out_dir = out_dir or os.path.join(LOCAL_OUTPUT_DIR, f"shorts_{video_id}")
    os.makedirs(out_dir, exist_ok=True)
    
    results: List[Dict] = []
    for i, h in enumerate(highlights, 1):
        slug = _slugify(h.get("title", f"short_{i:02d}"))
        out_path = os.path.join(out_dir, f"{slug}.mp4")
        print(f"[clip/local] {i}/{len(highlights)}: {h.get('title', '(untitled)')} → {out_path}", flush=True)
        try:
            crop_clip_local(
                source_path,
                float(h["start_time"]),
                float(h["end_time"]),
                aspect_ratio,
                out_path,
                transcript_segments=transcript_segments,
                title=h.get("title"),
            )
            results.append({**h, "clip_url": out_path})
        except Exception as e:
            print(f"[clip/local] {i} failed: {e}", flush=True)
            results.append({**h, "clip_url": None, "error": str(e)})
    return results
