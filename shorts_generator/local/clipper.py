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


def _get_ffmpeg_path() -> str:
    """Detect fully-featured FFmpeg (e.g. Homebrew ffmpeg-full or system ffmpeg)."""
    full_path = "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
    if os.path.exists(full_path):
        return full_path
    return "ffmpeg"


def _cut_subclip(source_path: str, start: float, end: float, out_path: str) -> str:
    """ffmpeg -ss start -t duration → re-encoded mp4 with audio."""
    from shorts_generator.config import USE_GPU
    from shorts_generator.local.gpu_detector import GPUDetector
    
    gpu_det = GPUDetector()
    encoder_args = gpu_det.get_encoder_args(use_gpu=USE_GPU)
    
    cmd = [
        _get_ffmpeg_path(), "-y", "-loglevel", "error",
        "-ss", f"{start:.3f}",
        "-i", source_path,
        "-t", f"{end - start:.3f}",
        *encoder_args,
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


def _ema_with_motion_limits_hybrid(
    values: Sequence[float],
    focus_ids: Sequence[int],
    start_value: float,
    fps: float,
    max_pixels_per_second: float,
) -> List[float]:
    """Smooth within the same speaker but snap instantly when switching speakers."""
    if not values:
        return []

    max_step = max(1.0, max_pixels_per_second / max(1.0, fps))
    smoothed = []
    current = start_value
    last_id = focus_ids[0] if len(focus_ids) > 0 else 0
    for i, target in enumerate(values):
        curr_id = focus_ids[i] if i < len(focus_ids) else last_id
        if curr_id != last_id:
            current = target
            last_id = curr_id
        
        delta = target - current
        step = _clamp(delta * 0.12, -max_step, max_step)
        current += step
        smoothed.append(current)
    return smoothed


def _calculate_lip_activity(face_landmarks, prev_lip_distance=None) -> float:
    """Calculate lip movement activity score using MediaPipe Face Mesh landmarks."""
    upper_lip = face_landmarks.landmark[13]
    lower_lip = face_landmarks.landmark[14]
    
    mouth_left = face_landmarks.landmark[61]
    mouth_right = face_landmarks.landmark[291]
    
    mouth_height = abs(upper_lip.y - lower_lip.y)
    mouth_width = abs(mouth_left.x - mouth_right.x)
    
    if mouth_width > 0:
        aspect_ratio = mouth_height / mouth_width
    else:
        aspect_ratio = 0.0
        
    delta = 0.0
    if prev_lip_distance is not None:
        delta = abs(mouth_height - prev_lip_distance)
        
    # Combine openness and movement, weighting movement higher
    activity_score = (aspect_ratio * 0.4) + (delta * 0.6)
    return float(activity_score)


def _stabilize_positions_with_activity(positions: List[float], activities: List[float], min_shot_duration: int, switch_threshold: float) -> List[float]:
    """Stabilize crop positions based on lip activity scores (Shot-Based Locking)."""
    if not positions:
        return positions
    
    import numpy as np
    
    window_size = 30
    smoothed = []
    for i in range(len(positions)):
        start = max(0, i - window_size // 2)
        end = min(len(positions), i + window_size // 2)
        window = positions[start:end]
        smoothed.append(float(np.median(window)))
        
    final = []
    shot_start = 0
    current_position = smoothed[0] if smoothed else 0.0
    
    for i in range(len(smoothed)):
        frames_since_switch = i - shot_start
        
        # Only allow switch if min shot duration passed, position changed significantly, and speaker is talking
        if frames_since_switch >= min_shot_duration:
            position_diff = abs(smoothed[i] - current_position)
            activity = activities[i] if i < len(activities) else 0.0
            
            if position_diff > 150 and activity > switch_threshold:
                # Lock previous shot to its median position
                shot_positions = smoothed[shot_start:i]
                if shot_positions:
                    shot_median = float(np.median(shot_positions))
                    final.extend([shot_median] * len(shot_positions))
                shot_start = i
                current_position = smoothed[i]
                
    # Handle the remaining frames in the last shot
    shot_positions = smoothed[shot_start:]
    if shot_positions:
        shot_median = float(np.median(shot_positions))
        final.extend([shot_median] * len(shot_positions))
        
    return final if final else smoothed


def _stabilize_positions_by_speaker(positions: List[float], focus_ids: List[int], min_shot_duration: int, switch_threshold: float) -> List[float]:
    """Stabilize crop positions based on speaker focus IDs (Shot-Based Locking)."""
    if not positions:
        return positions
    
    import numpy as np
    
    smoothed = []
    window_size = 30
    for i in range(len(positions)):
        start = max(0, i - window_size // 2)
        end = min(len(positions), i + window_size // 2)
        window = positions[start:end]
        smoothed.append(float(np.median(window)))
        
    final = []
    shot_start = 0
    current_position = smoothed[0] if smoothed else 0.0
    current_focus_id = focus_ids[0] if focus_ids else 0
    
    for i in range(len(smoothed)):
        frames_since_switch = i - shot_start
        
        # Only allow switch if min shot duration passed, and speaker changed OR position changed significantly
        if frames_since_switch >= min_shot_duration:
            position_diff = abs(smoothed[i] - current_position)
            speaker_changed = (focus_ids[i] != current_focus_id) if i < len(focus_ids) else False
            
            if speaker_changed or position_diff > switch_threshold:
                # Lock previous shot to its median position
                shot_positions = smoothed[shot_start:i]
                if shot_positions:
                    shot_median = float(np.median(shot_positions))
                    final.extend([shot_median] * len(shot_positions))
                shot_start = i
                current_position = smoothed[i]
                current_focus_id = focus_ids[i] if i < len(focus_ids) else current_focus_id
                
    # Handle the remaining frames in the last shot
    shot_positions = smoothed[shot_start:]
    if shot_positions:
        shot_median = float(np.median(shot_positions))
        final.extend([shot_median] * len(shot_positions))
        
    return final if final else smoothed


def _format_time(seconds: float) -> str:
    """Convert seconds to ASS time format (H:MM:SS.CC)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centisecs = int((seconds % 1) * 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centisecs:02d}"


def _create_ass_subtitle_capcut(word_captions: List[Dict], output_path: str, offset: float = 0.0):
    """Create an ASS subtitle file with word-by-word active yellow highlighting."""
    ass_content = """[Script Info]
Title: Auto-generated captions
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial Black,65,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,2,2,50,50,400,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    chunk_size = 4
    
    for i in range(0, len(word_captions), chunk_size):
        chunk = word_captions[i:i + chunk_size]
        if not chunk:
            continue
            
        for j, current_word in enumerate(chunk):
            word_start = current_word["start"] - offset
            word_end = current_word["end"] - offset
            
            # Hide subtitle if it falls entirely within the first 0.8 seconds (thumbnail teaser period)
            if word_end <= 0.8:
                continue
            
            # Clamp starting time if it overlaps with the first 0.8 seconds
            if word_start < 0.8:
                word_start = 0.8
            
            text_parts = []
            for k, w in enumerate(chunk):
                word_text = w["word"].strip().upper()
                if k == j:
                    # Highlight active word in bright yellow (Hex: &H00FFFF& in ASS BBGGRR)
                    text_parts.append(f"{{\\c&H00FFFF&}}{word_text}{{\\c&HFFFFFF&}}")
                else:
                    text_parts.append(word_text)
                    
            text = " ".join(text_parts)
            events.append({
                'start': _format_time(word_start),
                'end': _format_time(word_end),
                'text': text
            })
            
    for event in events:
        ass_content += f"Dialogue: 0,{event['start']},{event['end']},Default,,0,0,0,,{event['text']}\n"
        
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(ass_content)


def _detect_faces(frame, cv2, face_cascade, detector_name="opencv-haar") -> List[Dict]:
    """Return face boxes using MediaPipe if specified, defaulting to Haar Cascade."""
    faces: List[Dict] = []
    if detector_name == "mediapipe-mesh":
        try:
            import mediapipe as mp
            import mediapipe.python.solutions.face_mesh as mp_face_mesh
            if not hasattr(_detect_faces, "mp_mesh_detector"):
                _detect_faces.mp_mesh_detector = mp_face_mesh.FaceMesh(
                    static_image_mode=False,
                    max_num_faces=3,
                    refine_landmarks=True,
                    min_detection_confidence=0.5,
                    min_tracking_confidence=0.5
                )
            
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = _detect_faces.mp_mesh_detector.process(rgb)
            if results.multi_face_landmarks:
                h_img, w_img, _ = frame.shape
                for landmarks in results.multi_face_landmarks:
                    # Calculate box around landmarks
                    xs = [l.x for l in landmarks.landmark]
                    ys = [l.y for l in landmarks.landmark]
                    x_min, x_max = min(xs), max(xs)
                    y_min, y_max = min(ys), max(ys)
                    
                    w = int((x_max - x_min) * w_img)
                    h = int((y_max - y_min) * h_img)
                    x = int(x_min * w_img)
                    y = int(y_min * h_img)
                    
                    # Extract key mouth landmarks
                    upper_lip = landmarks.landmark[13]
                    lower_lip = landmarks.landmark[14]
                    mouth_left = landmarks.landmark[61]
                    mouth_right = landmarks.landmark[291]
                    
                    lip_dist = abs(upper_lip.y - lower_lip.y)
                    mouth_w = abs(mouth_left.x - mouth_right.x)
                    
                    faces.append({
                        "x": x,
                        "y": y,
                        "w": w,
                        "h": h,
                        "score": 0.9,
                        "lip_dist": lip_dist,
                        "mouth_aspect_ratio": float(lip_dist / mouth_w) if mouth_w > 0 else 0.0
                    })
        except Exception:
            pass
            
    # Stage 2 Fallback: MediaPipe Basic (BlazeFace) - Extremely robust to tilt, profile, and illumination.
    if len(faces) == 0 and (detector_name in ["mediapipe", "mediapipe-mesh"]):
        try:
            import mediapipe as mp
            import mediapipe.python.solutions.face_detection as mp_face_detection
            if not hasattr(_detect_faces, "mp_detector"):
                _detect_faces.mp_detector = mp_face_detection.FaceDetection(
                    model_selection=1, min_detection_confidence=0.4 # slightly more permissive for profiles
                )
            
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            results = _detect_faces.mp_detector.process(rgb)
            if results.detections:
                h_img, w_img, _ = frame.shape
                for detection in results.detections:
                    bbox = detection.location_data.relative_bounding_box
                    x = int(bbox.xmin * w_img)
                    y = int(bbox.ymin * h_img)
                    w = int(bbox.width * w_img)
                    h = int(bbox.height * h_img)
                    score = float(detection.score[0]) if detection.score else 0.7
                    
                    keypoints = detection.location_data.relative_keypoints
                    nose_y = None
                    mouth_y = None
                    if keypoints and len(keypoints) >= 4:
                        nose_y = float(keypoints[2].y * h_img)
                        mouth_y = float(keypoints[3].y * h_img)
                    
                    faces.append({
                        "x": x,
                        "y": y,
                        "w": w,
                        "h": h,
                        "score": score,
                        "nose_y": nose_y,
                        "mouth_y": mouth_y
                    })
        except Exception:
            pass

    # Stage 3 Fallback: OpenCV Haar Frontal & Profile
    if len(faces) == 0:
        try:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            # First try Frontal
            detected = face_cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(60, 60)
            )
            for x, y, bw, bh in detected:
                faces.append({"x": int(x), "y": int(y), "w": int(bw), "h": int(bh), "score": 0.7})
            
            # If still nothing, try Profile
            if len(faces) == 0:
                if not hasattr(_detect_faces, "profile_cascade"):
                    _detect_faces.profile_cascade = cv2.CascadeClassifier(
                        cv2.data.haarcascades + "haarcascade_profileface.xml"
                    )
                # Try profile face to handle extreme side-facing orientations
                detected_prof = _detect_faces.profile_cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60)
                )
                for x, y, bw, bh in detected_prof:
                    faces.append({"x": int(x), "y": int(y), "w": int(bw), "h": int(bh), "score": 0.65})
                
                # Also try flipped image for profile in opposite direction
                if len(faces) == 0:
                    flipped = cv2.flip(gray, 1)
                    detected_flipped = _detect_faces.profile_cascade.detectMultiScale(
                        flipped, scaleFactor=1.1, minNeighbors=4, minSize=(60, 60)
                    )
                    h_img, w_img = gray.shape
                    for x, y, bw, bh in detected_flipped:
                        # Remap x coord back to original
                        orig_x = w_img - x - bw
                        faces.append({"x": int(orig_x), "y": int(y), "w": int(bw), "h": int(bh), "score": 0.65})
        except Exception:
            pass

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

    from shorts_generator.config import LOCAL_FACE_DETECTOR, USE_SHOT_LOCK, MIN_SHOT_DURATION
    detector_name = LOCAL_FACE_DETECTOR
    min_center = crop_w / 2.0
    max_center = src_w - crop_w / 2.0
    default_center = _clamp(src_w / 2.0, min_center, max_center)
    detect_every_n = max(2, int(round(fps / 6.0)))


    max_match_distance = max(crop_w * 0.45, src_w * 0.12)
    sample_frames = list(range(0, total_frames, detect_every_n))
    tracks: List[Dict] = []


    for frame_idx in sample_frames:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret:
            continue
        faces = _detect_faces(frame, cv2, face_cascade, detector_name=detector_name)
        detections = []
        for face in faces:
            area = float(face["w"] * face["h"])
            if area < (src_w * src_h) * 0.002:
                continue
            cx = float(face["x"] + face["w"] / 2.0)
            cy = float(face["y"] + face["h"] / 2.0)
            
            # Extract mouth sub-image from lower-center part of the face box
            h_f, w_f, _ = frame.shape
            my0 = max(0, min(h_f - 1, int(face["y"] + face["h"] * 0.65)))
            my1 = max(0, min(h_f - 1, int(face["y"] + face["h"] * 0.90)))
            mx0 = max(0, min(w_f - 1, int(face["x"] + face["w"] * 0.25)))
            mx1 = max(0, min(w_f - 1, int(face["x"] + face["w"] * 0.75)))
            
            mouth_norm = None
            if (my1 > my0) and (mx1 > mx0):
                try:
                    mouth_sub = frame[my0:my1, mx0:mx1]
                    mouth_gray = cv2.cvtColor(mouth_sub, cv2.COLOR_BGR2GRAY)
                    mouth_norm = cv2.resize(mouth_gray, (40, 20))
                except Exception:
                    pass
            
            detections.append({
                **face,
                "frame": frame_idx,
                "cx": cx,
                "cy": cy,
                "area": area,
                "mouth_norm": mouth_norm
            })

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
                det["mouth_diff"] = 0.0
                tracks.append({"detections": [det]})
                used_tracks.add(len(tracks) - 1)
            else:
                last_det = tracks[best_idx]["detections"][-1]
                mouth_diff = 0.0
                if "lip_dist" in det and "lip_dist" in last_det:
                    # Higher fidelity MediaPipe metric combining openness (aspect ratio) and dynamics (delta)
                    delta = abs(det["lip_dist"] - last_det["lip_dist"])
                    ratio = det.get("mouth_aspect_ratio", 0.0)
                    # Scale to achieve 1.0-5.0 range consistent with other methods
                    mouth_diff = (ratio * 5.0) + (delta * 600.0)
                elif det.get("mouth_norm") is not None and last_det.get("mouth_norm") is not None:
                    try:
                        import numpy as np
                        diff_img = cv2.absdiff(det["mouth_norm"], last_det["mouth_norm"])
                        mouth_diff = float(np.mean(diff_img))
                    except Exception:
                        pass
                det["mouth_diff"] = mouth_diff
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
    # Filter out fleeting noise tracks (at least 3 detections required)
    high_quality_tracks = [t for t in ranked_tracks if len(t["detections"]) >= 3]
    if high_quality_tracks:
        ranked_tracks = high_quality_tracks
        
    primary = ranked_tracks[0]
    primary_detections = primary["detections"]

    keyframes: Dict[int, float] = {}
    keyframes_focus_id: Dict[int, int] = {}
    mode_note = "single"

    # Active Speaker Detection if multiple faces are found
    if len(ranked_tracks) > 1:
        current_focus_idx = 0
        last_switch_frame = -100
        min_switch_interval = int(fps * 1.0)  # Hold focus for at least 1.0s (down from 2.0s for higher dialogue cadence)
        
        for frame_idx in sample_frames:
            active_tracks_at_frame = []
            for ti, track in enumerate(ranked_tracks[:3]):  # Check top 3 tracks
                has_det = any(abs(d["frame"] - frame_idx) <= detect_every_n * 2 for d in track["detections"])
                if has_det:
                    # Collect mouth temporal differences around this frame (+/- 1.5s)
                    window = [
                        d["mouth_diff"]
                        for d in track["detections"]
                        if abs(d["frame"] - frame_idx) <= int(fps * 0.75) and d.get("mouth_diff") is not None
                    ]
                    # The mean of mouth temporal differences represents the intensity of speaking activity
                    mean_val = sum(window) / len(window) if len(window) >= 1 else 0.0
                    # The standard deviation of the window represents dynamic fluctuation (dynamic lip movement!)
                    if len(window) >= 2:
                        import math
                        variance = sum((x - mean_val) ** 2 for x in window) / len(window)
                        std_val = math.sqrt(variance)
                    else:
                        std_val = 0.0
                    
                    # Combine mean intensity and dynamic fluctuation to isolate true speech/lip movement
                    score = mean_val * (1.0 + 1.8 * std_val)
                    active_tracks_at_frame.append((ti, score))
            
            if active_tracks_at_frame:
                best_ti, best_score = max(active_tracks_at_frame, key=lambda x: x[1])
                
                # Calculate current focus score for comparative thresholding
                current_score = 0.0
                for ti, sc in active_tracks_at_frame:
                    if ti == current_focus_idx:
                        current_score = sc
                        break
                
                # Ensure the prospective switch target has an actual face detection near this frame
                track_has_recent_face = any(abs(d["frame"] - frame_idx) <= int(fps * 0.75) for d in ranked_tracks[best_ti]["detections"])
                
                # Magnitude hysteresis: only switch if best is significantly better than current, or current is near silent
                is_significantly_better = best_score > (current_score * 1.25) or current_score < 0.7
                
                # If dynamic speaking activity is significant, different, AND clearly stronger than current speaker
                if track_has_recent_face and best_score > 1.3 and best_ti != current_focus_idx and is_significantly_better:
                    if frame_idx - last_switch_frame >= min_switch_interval:
                        current_focus_idx = best_ti
                        last_switch_frame = frame_idx
            
            # Use the focus coordinate of the active speaking track with a robust temporal fallback
            focus_track = ranked_tracks[current_focus_idx]
            valid_dets = [d for d in focus_track["detections"] if abs(d["frame"] - frame_idx) <= int(fps * 1.0)]
            if valid_dets:
                closest_det = min(valid_dets, key=lambda d: abs(d["frame"] - frame_idx))
                target_cx = closest_det["cx"]
            else:
                # CRITICAL FIX: If track is momentarily lost, STICK to its absolute closest detection EVER.
                # Do not jump to global center! Jumping to center yields blank walls in dual interviews.
                all_dets = focus_track["detections"]
                if all_dets:
                    closest_ever = min(all_dets, key=lambda d: abs(d["frame"] - frame_idx))
                    target_cx = closest_ever["cx"]
                else:
                    # Ultra-fallback: Look globally for ANYTHING else
                    local_dets = []
                    for track in ranked_tracks:
                        local_dets.extend([
                            d for d in track["detections"]
                            if abs(d["frame"] - frame_idx) <= int(fps * 0.5)
                        ])
                    if local_dets:
                        closest_local_det = min(local_dets, key=lambda d: abs(d["frame"] - frame_idx))
                        target_cx = closest_local_det["cx"]
                    else:
                        target_cx = default_center

            keyframes[frame_idx] = _clamp(target_cx, min_center, max_center)
            keyframes_focus_id[frame_idx] = current_focus_idx
            
        mode_note = "active-speaker"
    else:
        for det in primary_detections:
            keyframes[det["frame"]] = _clamp(det["cx"], min_center, max_center)
            keyframes_focus_id[det["frame"]] = 0
        mode_note = "single"

    sorted_keys = sorted(keyframes)
    raw = [default_center] * total_frames
    focus_ids = [0] * total_frames
    first_key = sorted_keys[0]
    for i in range(0, min(first_key, total_frames)):
        raw[i] = keyframes[first_key]
        focus_ids[i] = keyframes_focus_id.get(first_key, 0)
        
    for left, right in zip(sorted_keys, sorted_keys[1:]):
        left_v = keyframes[left]
        right_v = keyframes[right]
        left_id = keyframes_focus_id.get(left, 0)
        right_id = keyframes_focus_id.get(right, 0)
        span = max(1, right - left)
        for i in range(left, min(right + 1, total_frames)):
            t = (i - left) / span
            # Hybrid speaker-aware interpolation:
            # Smooth linearly within the SAME speaker; cut/snap instantly across different speakers
            if left_id == right_id:
                raw[i] = left_v + (right_v - left_v) * t
                focus_ids[i] = left_id
            else:
                raw[i] = left_v if i < right else right_v
                focus_ids[i] = left_id if i < right else right_id
    last_key = sorted_keys[-1]
    for i in range(last_key, total_frames):
        raw[i] = keyframes[last_key]
        focus_ids[i] = keyframes_focus_id.get(last_key, 0)

    # Smooth only within same speaker tracks to suppress micro-shakes
    smoothed = _ema_with_motion_limits_hybrid(
        raw,
        focus_ids,
        raw[0],
        fps=fps,
        max_pixels_per_second=max(80.0, crop_w * 0.85),
    )
    trajectory = [_clamp(v, min_center, max_center) for v in smoothed]

    if USE_SHOT_LOCK:
        trajectory = _stabilize_positions_by_speaker(
            trajectory,
            focus_ids,
            min_shot_duration=MIN_SHOT_DURATION,
            switch_threshold=150.0
        )

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
            "/System/Library/Fonts/Supplemental/Impact.ttf",
            "/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf",
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
    """Split input and alternately select high-impact keywords so colors stagger naturally."""
    words = text.strip().upper().replace(".", "").replace(",", "").replace("?", "").replace("!", "").split()
    highlights = []
    for idx, w in enumerate(words):
        if idx % 2 == 0: # Highlight every other word for dynamic creator feel
            highlights.append(w)
    return highlights


def draw_gradient_overlay(draw, out_w: int, out_h: int, pil_img):
    """Draw severe cinematic vignette at bottom to ensure ultimate text readability."""
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    # Dual-vignette Strategy
    # Top faint vignette
    for gy in range(0, int(out_h * 0.2)):
        alpha = int(140 * (1.0 - (gy / (out_h * 0.2))))
        overlay_draw.line([(0, gy), (out_w, gy)], fill=(0, 0, 0, alpha))
    # Heavy bottom vignette
    for gy in range(int(out_h * 0.45), out_h):
        factor = ((gy - out_h * 0.45) / (out_h * 0.55)) ** 1.6
        alpha = int(250 * factor)
        overlay_draw.line([(0, gy), (out_w, gy)], fill=(3, 3, 4, alpha))
    return Image.alpha_composite(pil_img.convert("RGBA"), overlay)


def draw_subject_outline(draw, out_w: int, out_h: int, center_x: float, center_y: float):
    """Placeholder disabled to maintain pure visual cinematic focus matching sample image."""
    pass


def draw_badge(draw, out_w: int, out_h: int, style: str = "modern_shorts"):
    """Draw elegant outline capsule with central yellow play icon and 'SHORTS PILIHAN' white text."""
    GOLD = (247, 182, 43)
    
    b_font = _get_font(int(out_w * 0.035), "bold")
    lbl = "SHORTS PILIHAN"
    
    b = draw.textbbox((0, 0), lbl, font=b_font)
    lw, lh = b[2] - b[0], b[3] - b[1]
    
    icon_sz = int(lh * 0.9)
    gap = int(out_w * 0.02)
    px, py = int(out_w * 0.035), int(out_h * 0.015)
    
    bw = icon_sz + gap + lw + (px * 2)
    bh = max(icon_sz, lh) + (py * 2)
    
    bx = int(out_w * 0.06)
    by = int(out_h * 0.04)
    
    # 1. Outline Rounded Box
    draw.rounded_rectangle([bx, by, bx + bw, by + bh], radius=10, fill=(0,0,0,120), outline=GOLD, width=2)
    
    # 2. Solid Play Triangle
    ix = bx + px
    iy = by + (bh - icon_sz) // 2
    points = [(ix, iy), (ix, iy + icon_sz), (ix + int(icon_sz * 0.86), iy + (icon_sz // 2))]
    draw.polygon(points, fill=GOLD)
    
    # 3. White Text
    draw.text((ix + icon_sz + gap, by + (bh - lh) // 2 - 1), lbl, fill=(255,255,255), font=b_font)


def draw_cta(draw, out_w: int, out_h: int, style: str = "modern_shorts"):
    """Draw minimal, right-aligned 'TONTON SAMPAI AKHIR' anchored slightly above lower boundary with a yellow bullet."""
    GOLD = (247, 182, 43)
    sub_font = _get_font(int(out_w * 0.028), "bold")
    
    lbl = "TONTON SAMPAI AKHIR"
    b = draw.textbbox((0, 0), lbl, font=sub_font)
    lw, lh = b[2] - b[0], b[3] - b[1]
    
    dot_rad = int(out_w * 0.006)
    gap_x = int(out_w * 0.018)
    
    # Right side anchor
    tx = out_w - int(out_w * 0.08) - lw
    ty = out_h - int(out_h * 0.08)
    
    # 1. Drop Shadow for legibility on white backgrounds
    draw.text((tx + 2, ty + 2), lbl, fill=(0,0,0,180), font=sub_font)
    # 2. Text
    draw.text((tx, ty), lbl, fill=(255, 255, 255, 220), font=sub_font)
    
    # 3. The distinctive yellow dot
    cx = tx - gap_x
    cy = ty + (lh // 2) + 1
    draw.ellipse([cx - dot_rad, cy - dot_rad, cx + dot_rad, cy + dot_rad], fill=GOLD)


def draw_dynamic_title(draw, out_w: int, out_h: int, title: str, style: str = "modern_shorts"):
    """Draw huge staggered-color typography anchored to the bottom with alternating colors and an accent underscore."""
    GOLD = (247, 182, 43)
    WHITE = (255, 255, 255)
    
    title_txt = title.upper().strip()
    words = title_txt.split()
    if not words: return
    
    # Start with bold massive sizes
    font_sz = int(out_w * 0.135)
    margin_left = int(out_w * 0.06)
    usable_width = out_w - (margin_left * 2)
    
    # 1. Auto-scale Loop: Decrease size until it reasonably fits in max 4 lines without overflow
    lines = []
    fnt_base = None
    fnt_bold = None
    
    # Safety counter to prevent infinite loops
    for _ in range(10): 
        fnt_base = _get_font(font_sz, "bold")
        fnt_bold = _get_font(int(font_sz * 1.05), "bold")
        
        lines = []
        cur = []
        
        for w in words:
            test = cur + [w]
            acc_w = 0
            for test_w in test:
                bb = draw.textbbox((0, 0), test_w, font=fnt_base)
                acc_w += (bb[2]-bb[0]) + int(out_w * 0.022)
            
            # If words are insanely wide even one per line, we MUST break anyway but force shrink
            if acc_w <= usable_width and len(cur) < 2:
                cur.append(w)
            else:
                if cur: lines.append(cur)
                cur = [w]
        if cur: lines.append(cur)
        
        # Check constraints: max 4 lines, and not running too tall. 
        # If title is long, shrink and retry.
        if len(lines) > 4 or font_sz > int(out_w * 0.14): 
            font_sz = int(font_sz * 0.85) # Shrink by 15%
        else:
            # Double check actual maximum line width one more time
            any_overflow = False
            for ln in lines:
                l_w = 0
                for twd in ln:
                    bb = draw.textbbox((0,0), twd, font=fnt_base)
                    l_w += (bb[2]-bb[0]) + int(out_w * 0.02)
                if l_w > usable_width:
                    any_overflow = True
                    break
            if any_overflow:
                font_sz = int(font_sz * 0.85)
            else:
                break # Fits perfectly!
                
    # Final fonts secured. Use EVERYTHING, no hard limit drops!
    display = lines 
    line_spc = int(font_sz * 1.05)
    
    # Anchor anchored low, scaling based on total number of rendered lines
    b_y = int(out_h * 0.82) - (len(display) * line_spc)
    
    word_idx = 0
    cy = b_y
    
    for ln_idx, ln in enumerate(display):
        cx = margin_left
        for wd in ln:
            # Color Alternation Strategy (Yellow / White)
            use_gold = (word_idx % 2 == 0)
            fnt = fnt_bold if use_gold else fnt_base
            fill = GOLD if use_gold else WHITE
            
            tb = draw.textbbox((0, 0), wd, font=fnt)
            ww, wh = tb[2]-tb[0], tb[3]-tb[1]
            
            # Heavy drop-shadow loops ensuring legibility 
            for off in [2, 3, 4]:
                draw.text((cx + off, cy + off), wd, fill=(0,0,0,190), font=fnt)
            
            # Final Foreground text
            draw.text((cx, cy), wd, fill=fill, font=fnt)
            
            cx += ww + int(out_w * 0.025)
            word_idx += 1
            
        cy += line_spc
        
    # 2. Heavy signature golden underline beneath the last actual line rendered
    bar_y = cy + int(out_h * 0.008)
    bar_w = int(out_w * 0.22)
    draw.line([(margin_left, bar_y), (margin_left + bar_w, bar_y)], fill=GOLD, width=8)


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


def _format_timestamp(seconds: float) -> str:
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hrs > 0:
        return f"{hrs:d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def draw_timestamp_pill(draw, out_w: int, out_h: int, current_time: float, alpha_mult: float = 1.0):
    """Draw bare stacked text in top-right corner, outline applied for high contrast, support fade alpha."""
    time_txt = _format_timestamp(current_time)
    
    line1 = "Full Video"
    line2 = time_txt
    
    font_l1 = _get_font(int(out_w * 0.030), "regular") 
    font_l2 = _get_font(int(out_w * 0.038), "bold")
    
    bb1 = draw.textbbox((0, 0), line1, font=font_l1)
    bb2 = draw.textbbox((0, 0), line2, font=font_l2)
    w1, h1 = bb1[2] - bb1[0], bb1[3] - bb1[1]
    w2, h2 = bb2[2] - bb2[0], bb2[3] - bb2[1]
    
    # Specific margin spacing off the edge ("jangan mentok")
    margin_right = int(out_w * 0.06)
    margin_top = int(out_w * 0.06)
    line_spacing = int(out_h * 0.003)
    
    # Right aligned edge x coordinate
    right_edge_x = out_w - margin_right
    y_pos = margin_top
    
    # Dynamic alpha calculations based on incoming multiplier
    text_a = int(255 * alpha_mult)
    stroke_a = int(220 * alpha_mult)
    
    if text_a <= 0:
        return # Invisible
    
    # Draw Line 1
    draw.text(
        (right_edge_x - w1, y_pos), 
        line1, font=font_l1, 
        fill=(255, 255, 255, text_a), 
        stroke_width=2, stroke_fill=(0, 0, 0, stroke_a)
    )
    
    # Draw Line 2
    draw.text(
        (right_edge_x - w2, y_pos + h1 + line_spacing), 
        line2, font=font_l2, 
        fill=(255, 255, 255, text_a), 
        stroke_width=2, stroke_fill=(0, 0, 0, stroke_a)
    )


def generate_end_frame_canvas(out_w: int, out_h: int, metadata: dict):
    """Synthesize an attribution frame replicating the reference dark/gold cinematic layout."""
    import os
    import cv2
    import numpy as np
    from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
    
    GOLD = (247, 182, 43)
    WHITE = (255, 255, 255)
    
    # 1. Base Canvas & Dynamic Background (Very Dark)
    canvas = Image.new("RGB", (out_w, out_h), (10, 10, 12))
    thumb_path = metadata.get("thumbnail_path")
    thumb_img = None
    
    if thumb_path and os.path.exists(thumb_path):
        try:
            thumb_img = Image.open(thumb_path).convert("RGB")
        except Exception:
            pass
            
    if thumb_img:
        # Center crop to fit vertically
        aspect_c = out_w / out_h
        tw, th = thumb_img.size
        if (tw / th) > aspect_c:
            cw = int(th * aspect_c)
            left = (tw - cw) // 2
            bg = thumb_img.crop((left, 0, left + cw, th))
        else:
            bg = thumb_img
            
        bg = bg.resize((out_w, out_h), Image.Resampling.BILINEAR)
        bg = bg.filter(ImageFilter.GaussianBlur(radius=40))
        # Extreme darkening to create contrast focus
        bg = ImageEnhance.Brightness(bg).enhance(0.18)
        canvas.paste(bg, (0, 0))
    
    draw = ImageDraw.Draw(canvas, "RGBA")
    
    # 2. "VIDEO LENGKAP DI" Header with Flanking Gold Lines
    tag_txt = "VIDEO LENGKAP DI"
    tag_font = _get_font(int(out_w * 0.045), "bold")
    tb = draw.textbbox((0, 0), tag_txt, font=tag_font)
    tw = tb[2] - tb[0]
    th = tb[3] - tb[1]
    
    y_tag = int(out_h * 0.13)
    center_x = out_w // 2
    draw.text((center_x - (tw // 2), y_tag), tag_txt, fill=GOLD, font=tag_font)
    
    # Lines flanking the text
    line_y = y_tag + (th // 2)
    line_pad = int(out_w * 0.05)
    margin_x = int(out_w * 0.12)
    
    draw.line([(margin_x, line_y), (center_x - (tw // 2) - line_pad, line_y)], fill=GOLD, width=2)
    draw.line([(center_x + (tw // 2) + line_pad, line_y), (out_w - margin_x, line_y)], fill=GOLD, width=2)
    
    # 3. Massive, Dynamic Stacking Channel Name
    chan_txt = str(metadata.get("channel", "")).upper().strip()
    if not chan_txt:
        chan_txt = "YOUTUBE"
        
    # Let's try splitting to two lines if it's long like the sample
    words = chan_txt.split()
    chan_lines = []
    if len(words) >= 2:
        # Group reasonably
        mid = len(words) // 2
        chan_lines.append(" ".join(words[:mid]))
        chan_lines.append(" ".join(words[mid:]))
    else:
        chan_lines.append(chan_txt)
        
    chan_font_sz = int(out_w * 0.13) # MASSIVE
    y_chan = y_tag + th + int(out_h * 0.03)
    
    for ln in chan_lines[:2]:
        # Auto scale down slightly if word is absolutely insane
        fnt = _get_font(chan_font_sz, "bold")
        t_b = draw.textbbox((0, 0), ln, font=fnt)
        w = t_b[2] - t_b[0]
        curr_sz = chan_font_sz
        while w > int(out_w * 0.92) and curr_sz > 40:
            curr_sz -= 5
            fnt = _get_font(curr_sz, "bold")
            t_b = draw.textbbox((0, 0), ln, font=fnt)
            w = t_b[2] - t_b[0]
            
        draw.text(((out_w - w) // 2, y_chan), ln, fill=WHITE, font=fnt)
        y_chan += (t_b[3] - t_b[1]) + int(out_h * 0.005)
        
    # 4. Elegant Thumbnail With Rounded Corners and Glowing Border
    y_thumb = int(out_h * 0.38)
    if thumb_img:
        t_w = int(out_w * 0.88)
        t_h = int(thumb_img.height * (t_w / thumb_img.width))
        r_thumb = thumb_img.resize((t_w, t_h), Image.Resampling.LANCZOS)
        
        t_x = (out_w - t_w) // 2
        radius = 30
        
        # Apply rounded mask corner technique
        mask = Image.new("L", (t_w, t_h), 0)
        m_draw = ImageDraw.Draw(mask)
        m_draw.rounded_rectangle([0, 0, t_w, t_h], radius=radius, fill=255)
        
        # Paste with mask to achieve clean rounded corners
        canvas.paste(r_thumb, (t_x, y_thumb), mask)
        
        # Add the precise thin golden stroke directly from reference image
        draw.rounded_rectangle([t_x, y_thumb, t_x + t_w, y_thumb + t_h], radius=radius, outline=GOLD, width=3)
        y_after_thumb = y_thumb + t_h
    else:
        y_after_thumb = y_thumb + int(out_h * 0.2)

    # 5. Video Title Placed Elegantly Below Box
    v_title = str(metadata.get("title", "")).upper().strip()
    title_font = _get_font(int(out_w * 0.052), "bold")
    
    words = v_title.split()
    lines = []
    cur = []
    for w in words:
        test = " ".join(cur + [w])
        b = draw.textbbox((0, 0), test, font=title_font)
        if (b[2] - b[0]) < int(out_w * 0.88):
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur: lines.append(" ".join(cur))
    
    y_cur = y_after_thumb + int(out_h * 0.06)
    # Limit to 2 readable lines
    for ln in lines[:2]:
        lb = draw.textbbox((0, 0), ln, font=title_font)
        w = lb[2] - lb[0]
        draw.text(((out_w - w) // 2, y_cur), ln, fill=WHITE, font=title_font)
        y_cur += (lb[3] - lb[1]) + int(out_h * 0.01)
        
    # 6. Clean YouTube Style CTA Button
    btn_txt = "LIKE & SUBSCRIBE"
    btn_font = _get_font(int(out_w * 0.048), "bold")
    bb = draw.textbbox((0, 0), btn_txt, font=btn_font)
    bw, bh = (bb[2] - bb[0]), (bb[3] - bb[1])
    
    # Slightly bigger padding
    p_x, p_y = 50, 25
    
    bx1 = (out_w - (bw + p_x * 2)) // 2
    bx2 = bx1 + bw + p_x * 2
    by1 = int(out_h * 0.83)
    by2 = by1 + bh + p_y * 2
    
    # Deep red from reference image
    btn_color = (198, 32, 32, 255) 
    
    draw.rounded_rectangle([bx1, by1, bx2, by2], radius=15, fill=btn_color)
    # Draw text perfectly centered
    t_x = bx1 + (bx2 - bx1 - bw) // 2
    t_y = by1 + (by2 - by1 - bh) // 2 - 2
    draw.text((t_x, t_y), btn_txt, fill=WHITE, font=btn_font)

    return cv2.cvtColor(np.array(canvas.convert("RGB")), cv2.COLOR_RGB2BGR)


def _reframe_vertical(
    in_path: str,
    out_path: str,
    aspect_ratio: str,
    start_time: float,
    word_captions: List[Dict],
    draw_subtitles: bool = True,
    title: Optional[str] = None,
    source_metadata: Optional[Dict] = None,
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

    from shorts_generator.config import USE_GPU
    from shorts_generator.local.gpu_detector import GPUDetector
    
    gpu_det = GPUDetector()
    encoder_args = gpu_det.get_encoder_args(use_gpu=USE_GPU)

    silent_path = out_path + ".silent.mp4"
    cmd_ffmpeg = [
        _get_ffmpeg_path(), "-y", "-loglevel", "error",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24",
        "-s", f"{out_w}x{out_h}",
        "-r", f"{fps:.3f}",
        "-i", "-",
        *encoder_args,
        "-pix_fmt", "yuv420p",
        silent_path
    ]
    writer_proc = subprocess.Popen(cmd_ffmpeg, stdin=subprocess.PIPE)
    y0 = int((src_h - crop_h) // 2)

    # Reset to beginning for the actual frame-writing pass
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    frame_idx = 0
    teaser_frames = int(round(0.8 * fps))
    total_main_frames = len(focus_trajectory) if focus_trajectory else 0
    
    # Pre-calculate total frames to read from capture if focus_trajectory somehow missing
    if not total_main_frames:
        total_main_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
    last_active_word = None

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        msec = cap.get(cv2.CAP_PROP_POS_MSEC)
        if msec > 0:
            current_time = start_time + (msec / 1000.0)
        else:
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

        # 1. First N frames (~0.8s): Draw Modern Creator Thumbnail Teaser
        if frame_idx - 1 < teaser_frames and title:
            face_canvas_x = int(((center_x - x0) / crop_w) * out_w)
            face_canvas_y = int(out_h * 0.32)
            cropped = render_thumbnail(cropped, title, out_w, out_h, face_canvas_x, face_canvas_y)

        elif draw_subtitles:
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

            # 2. Body frames (or active words): Draw Single Word Subtitles
            if display_word:
                # Convert BGR (OpenCV) to RGB (PIL)
                pil_img = Image.fromarray(cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB))
                draw = ImageDraw.Draw(pil_img, "RGBA")
                
                word_text = display_word["word"].upper()
                sub_font_size = int(out_w * 0.06)  # Standard default size
                max_allowed_width = int(out_w * 0.88)
                
                while sub_font_size > 28:
                    sub_font = _get_font(sub_font_size, "bold")
                    sub_bbox = draw.textbbox((0, 0), word_text, font=sub_font)
                    sub_w = sub_bbox[2] - sub_bbox[0]
                    if sub_w <= max_allowed_width:
                        break
                    sub_font_size -= 4
                    
                sub_font = _get_font(sub_font_size, "bold")
                sub_bbox = draw.textbbox((0, 0), word_text, font=sub_font)
                sub_w = sub_bbox[2] - sub_bbox[0]
                sub_h = sub_bbox[3] - sub_bbox[1]
                
                sub_x = (out_w - sub_w) // 2
                sub_y = int(out_h * 0.75)  # Centered horizontally, at 75% height
                
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

        # 3. Overlay: Final Active Source Timestamp Pill
        if source_metadata:
            # Handle dynamic visibility / fade animation transitions
            alpha_mult = 1.0
            fade_frames = int(round(0.5 * fps))
            
            # Condition 1: Fade-In (Triggered AFTER teaser thumbnail finishes)
            if frame_idx <= teaser_frames:
                alpha_mult = 0.0
            elif frame_idx <= (teaser_frames + fade_frames) and fade_frames > 0:
                # Ramping up from 0.0 to 1.0 over 0.5s
                alpha_mult = (frame_idx - teaser_frames) / float(fade_frames)
                
            # Condition 2: Fade-Out (Transitioning out before the 2s closing frame kicks in)
            # Since cap.release() occurs right after this loop, total_main_frames represents the end of the raw video.
            elif total_main_frames > 0 and frame_idx >= (total_main_frames - fade_frames) and fade_frames > 0:
                # Ramping down from 1.0 to 0.0 toward the final frame
                rem = total_main_frames - frame_idx
                alpha_mult = max(0.0, rem / float(fade_frames))
            
            # Only perform relatively expensive PIL rendering operations if object is actually visible
            if alpha_mult > 0.001:
                pil_ticker = Image.fromarray(cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB))
                draw_ticker = ImageDraw.Draw(pil_ticker, "RGBA")
                # Enhanced function now accepts alpha blending
                draw_timestamp_pill(draw_ticker, out_w, out_h, current_time, alpha_mult=alpha_mult)
                cropped = cv2.cvtColor(np.array(pil_ticker.convert("RGB")), cv2.COLOR_RGB2BGR)

        writer_proc.stdin.write(cropped.tobytes())

    cap.release()

    # Generate 2-second End Frame attribution loop
    has_end_frame = False
    if source_metadata:
        try:
            print(f"[clip/local] Generating attribution end-card for '{source_metadata.get('title', 'source')}'...", flush=True)
            end_frame_img = generate_end_frame_canvas(out_w, out_h, source_metadata)
            end_frames_count = int(2.0 * fps)
            for _ in range(end_frames_count):
                writer_proc.stdin.write(end_frame_img.tobytes())
            has_end_frame = True
        except Exception as ef_err:
            print(f"[clip/local] Warning: End frame generation failed ({ef_err})", flush=True)

    writer_proc.stdin.close()
    writer_proc.wait()

    # Mux audio back on, applying apad filter if the video duration was extended
    if has_end_frame:
        cmd = [
            _get_ffmpeg_path(), "-y", "-loglevel", "error",
            "-i", silent_path,
            "-i", in_path,
            "-c:v", "copy",
            "-filter_complex", "[1:a:0]apad=pad_dur=2.0[aout]", # Pad original audio with silence
            "-map", "0:v:0", "-map", "[aout]",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            out_path,
        ]
    else:
        cmd = [
            _get_ffmpeg_path(), "-y", "-loglevel", "error",
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
    source_metadata: Optional[Dict] = None,
) -> str:
    """Cut + reframe one highlight, returning the local mp4 path."""
    cut_path = out_path + ".cut.mp4"
    word_captions = _get_word_captions(transcript_segments or [], start_time, end_time)
    
    from ..config import USE_REMOTION
    
    try:
        _cut_subclip(source_path, start_time, end_time, cut_path)
        if USE_REMOTION and os.path.exists("shorts_renderer"):
            clean_cropped_path = out_path + ".clean.mp4"
            _reframe_vertical(cut_path, clean_cropped_path, aspect_ratio, start_time, word_captions, draw_subtitles=False, title=title, source_metadata=source_metadata)
            
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
            from shorts_generator.config import USE_ASS_SUBTITLES, USE_GPU
            from shorts_generator.local.gpu_detector import GPUDetector
            import tempfile
            
            if USE_ASS_SUBTITLES:
                reframed_clean_path = out_path + ".clean_reframed.mp4"
                ass_file = f"temp_subtitles_{int(start_time)}.ass"
                try:
                    # 1. Reframe vertical with subtitle drawing disabled (extremely fast!)
                    _reframe_vertical(cut_path, reframed_clean_path, aspect_ratio, start_time, word_captions, draw_subtitles=False, title=title, source_metadata=source_metadata)
                    
                    # 2. Build the ASS subtitle file with CapCut word-highlighting
                    _create_ass_subtitle_capcut(word_captions, ass_file, start_time)
                    
                    # 3. Burn captions via FFmpeg (with optional hardware encoder!)
                    gpu_det = GPUDetector()
                    encoder_args = gpu_det.get_encoder_args(use_gpu=USE_GPU)
                    
                    cmd = [
                        _get_ffmpeg_path(), "-y", "-loglevel", "error",
                        "-i", reframed_clean_path,
                        "-vf", f"ass=filename={ass_file}",
                        *encoder_args,
                        "-c:a", "copy",
                        out_path
                    ]
                    subprocess.run(cmd, check=True)
                    
                    # Cleanup
                    if os.path.exists(ass_file):
                        os.unlink(ass_file)
                    if os.path.exists(reframed_clean_path):
                        os.remove(reframed_clean_path)
                except Exception as e:
                    print(f"[clip/local] ASS burning failed ({e}); falling back to PIL-based drawing", flush=True)
                    if os.path.exists(ass_file):
                        try: os.unlink(ass_file)
                        except Exception: pass
                    if os.path.exists(reframed_clean_path):
                        try: os.remove(reframed_clean_path)
                        except Exception: pass
                    _reframe_vertical(cut_path, out_path, aspect_ratio, start_time, word_captions, draw_subtitles=True, title=title, source_metadata=source_metadata)
            else:
                _reframe_vertical(cut_path, out_path, aspect_ratio, start_time, word_captions, draw_subtitles=True, title=title, source_metadata=source_metadata)
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
    source_metadata: Optional[Dict] = None,
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
                source_metadata=source_metadata,
            )
            results.append({**h, "clip_url": out_path})
            
            # SIDE-CAR HELPER: Create a handy copy-paste friendly text file with metadata for posting!
            try:
                txt_path = os.path.splitext(out_path)[0] + ".txt"
                with open(txt_path, "w", encoding="utf-8") as f:
                    f.write("==================================================\n")
                    f.write("🎬  POST DETAILS / CAPTION HELPER\n")
                    f.write("==================================================\n\n")
                    f.write(f"📝 [JUDUL VIDEO]\n{h.get('title', 'N/A')}\n\n")
                    f.write(f"💬 [CAPTION / DESKRIPSI VIDEO]\n{h.get('social_caption', 'N/A')}\n\n")
                    f.write(f"🪝  [HOOK SENTENCE]\n\"{h.get('hook_sentence', 'N/A')}\"\n\n")
                    f.write("--- METADATA INTERNAL ---\n")
                    f.write(f"💡 [NOTE PEMBUAT / WHY VIRAL]: {h.get('virality_reason', 'N/A')}\n")
                    f.write(f"🔥 [VIRAL SCORE]: {h.get('score', 'N/A')}/100\n\n")
                    f.write("==================================================\n")
                    f.write("Ready to copy and paste for Shorts / Reels / TikTok!\n")
                print(f"[clip/local] Created companion metadata: {os.path.basename(txt_path)}", flush=True)
            except Exception as te:
                print(f"[clip/local] Info: Could not create metadata file: {te}", flush=True)
        except Exception as e:
            print(f"[clip/local] {i} failed: {e}", flush=True)
            results.append({**h, "clip_url": None, "error": str(e)})
    return results
