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


def _reframe_vertical(
    in_path: str,
    out_path: str,
    aspect_ratio: str,
    start_time: float,
    word_captions: List[Dict],
    draw_subtitles: bool = True,
) -> str:
    """Crop video, apply smooth horizontal face tracking, and burn karaoke captions."""
    try:
        import cv2  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "opencv-python is required for --mode local. Install it with:\n"
            "    pip install -r requirements-local.txt"
        ) from e

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
        "-s", f"{crop_w}x{crop_h}",
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
    last_phrase = []
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

        if draw_subtitles:
            # Active word-level highlighting (Karaoke) logic
            active_word_obj = None
            current_phrase = []

            for i, w in enumerate(word_captions):
                if w["start"] <= current_time <= w["end"]:
                    active_word_obj = w
                    chunk_idx = i // 3  # Group into 3-word phrases
                    start_idx = chunk_idx * 3
                    end_idx = min(len(word_captions), start_idx + 3)
                    current_phrase = word_captions[start_idx:end_idx]
                    break

            # Retain last phrase during small gaps so text doesn't flicker out
            if not current_phrase and active_word_obj is None:
                current_phrase = last_phrase
                active_word_obj = last_active_word
            else:
                last_phrase = current_phrase
                last_active_word = active_word_obj

            if current_phrase:
                font = cv2.FONT_HERSHEY_DUPLEX
                scale = crop_w / 320.0
                border_thickness = max(3, int(scale * 6.5))
                fill_thickness = max(1, int(scale * 1.8))
                
                space_w, _ = cv2.getTextSize(" ", font, scale, fill_thickness)
                space_w = space_w[0]

                # Calculate phrase width
                sizes = []
                total_w = 0
                for w in current_phrase:
                    (w_w, w_h), _ = cv2.getTextSize(w["word"], font, scale, border_thickness)
                    sizes.append((w_w, w_h))
                    total_w += w_w
                total_w += space_w * (len(current_phrase) - 1)

                # Dynamic scale-down loop: Ensure text stays within 85% of the screen width
                max_allowed_w = int(crop_w * 0.85)
                while total_w > max_allowed_w and scale > 0.3:
                    scale -= 0.05
                    border_thickness = max(3, int(scale * 6.5))
                    fill_thickness = max(1, int(scale * 1.8))
                    space_w, _ = cv2.getTextSize(" ", font, scale, fill_thickness)
                    space_w = space_w[0]
                    sizes = []
                    total_w = 0
                    for w in current_phrase:
                        (w_w, w_h), _ = cv2.getTextSize(w["word"], font, scale, border_thickness)
                        sizes.append((w_w, w_h))
                        total_w += w_w
                    total_w += space_w * (len(current_phrase) - 1)

                # Center position
                pos_x = (crop_w - total_w) // 2
                pos_y = int(crop_h * 0.78)

                # Find the active word index for word-by-word reveal
                active_idx = -1
                if active_word_obj:
                    for idx, w in enumerate(current_phrase):
                        if w == active_word_obj:
                            active_idx = idx
                            break
                # Fallback: if active_word_obj is None, show all words in the phrase
                if active_idx == -1:
                    active_idx = len(current_phrase) - 1

                for idx, w in enumerate(current_phrase):
                    if idx > active_idx:
                        break  # Word-by-word reveal: don't draw future words yet
                    
                    w_w, w_h = sizes[idx]
                    is_active = (w == active_word_obj)
                    fill_color = (0, 215, 255) if is_active else (255, 255, 255)  # Premium Vibrant Gold vs Pure White
                    
                    # Draw black outline
                    cv2.putText(cropped, w["word"], (pos_x, pos_y), font, scale, (0, 0, 0), border_thickness, cv2.LINE_AA)
                    # Draw fill
                    cv2.putText(cropped, w["word"], (pos_x, pos_y), font, scale, fill_color, fill_thickness, cv2.LINE_AA)
                    
                    pos_x += w_w + space_w

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
    try:
        _cut_subclip(source_path, start_time, end_time, cut_path)
        if os.path.exists("shorts_renderer"):
            clean_cropped_path = out_path + ".clean.mp4"
            _reframe_vertical(cut_path, clean_cropped_path, aspect_ratio, start_time, word_captions, draw_subtitles=False)
            
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
            _reframe_vertical(cut_path, out_path, aspect_ratio, start_time, word_captions)
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
