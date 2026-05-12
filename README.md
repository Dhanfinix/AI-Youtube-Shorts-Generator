# AI YouTube Shorts Generator (Gemini Edition)

**The ultimate open-source alternative for automated viral clipping.** Drop in any long-form YouTube URL and get back high-quality, ranked 9:16 vertical shorts directly on your machine—powered by the state-of-the-art **Google Gemini AI** infrastructure.

Built for creators, agencies, and developers seeking unlimited clips without monthly usage caps or per-minute fees. Utilizes industry-leading Gemini multimodal analysis for precise highlight detection and professional facial-recognition vertical reframing.

![longshorts](https://github.com/user-attachments/assets/3f5d1abf-bf3b-475f-8abf-5e253003453a)

## ✨ Features

- **⚡ Powered by Google Gemini**: Utilizes official Google GenAI SDK for blazing-fast, hyper-accurate virality intelligence.
- **🎬 Auto-Cascading Reliability**: Automatically survives API rate-limiting by smoothly falling back across redundant Gemini endpoints (e.g., Flash, Pro).
- **🎤 Hybrid Intelligent Transcription**: Prioritizes free & instant YouTube caption ingest; automatically defaults to rich Gemini Cloud Audio parsing only if native tracks fail.
- **🤖 Virality-Aware Analytics**: Ranks clips based on a proven content matrix: hook moments, opinion bombs, revelation triggers, conflict, quotables, and practical value.
- **🧩 Long-Video Scalability**: Automated overlapping chunk parsing handles hours-long podcasts without exhausting context windows.
- **🎯 Cinematic Vertical Crop**: Smooth, stabilized motion tracking (using Mediapipe) keeps the active speaker in center frame.
- **✨ CapCut-Style Word Highlights**: Generates Gorgeous dynamic word-by-word highlighting overlays burned directly into your video tracks.
- **📱 Total Flexibility**: Easily adjust targeting ratios (9:16 for TikTok/Reels/Shorts, 1:1 for square, etc.).

---

## Installation

### Prerequisites
- Python 3.10+
- `ffmpeg` installed and accessible on your system `PATH`.
- A Google Gemini API Key (obtain a free tier key from [Google AI Studio](https://aistudio.google.com/)).

### Steps

1. **Clone the repository:**
   ```bash
   git clone https://github.com/SamurAIGPT/AI-Youtube-Shorts-Generator.git
   cd AI-Youtube-Shorts-Generator
   ```

2. **Setup Virtual Environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-local.txt
   ```

4. **Configure Environment:**
   Create a `.env` file in the project root and insert your Gemini authentication key:
   ```env
   GEMINI_API_KEY=your_google_gemini_api_key_here
   ```
   *(Check out `.env.example` for advanced customization switches)*

---

## Usage

### Single Video Run

Fire off the pipeline targeting any valid YouTube address:

```bash
python main.py "https://www.youtube.com/watch?v=VIDEO_ID"
```

The pipeline performs download, transcription validation, highlight curation, face-tracking, and rendering automatically. Results land neatly in the `./output/` repository.

### Options and Overrides

Customize target quantity and final file output formats:

```bash
python main.py "https://www.youtube.com/watch?v=VIDEO_ID" \
    --num-clips 5 \
    --aspect-ratio 9:16 \
    --output-json result.json
```

### Batch Processing

Process large link dumps via text aggregate:
```bash
xargs -a urls.txt -I{} python main.py "{}"
```

---

## 🧰 CLI Flag Registry

| Flag | Default | Purpose |
|------|---------|---------|
| `--num-clips` | `3` | Total number of curated viral moments to actually render into short files. |
| `--aspect-ratio` | `9:16` | Target frame container ratio. Use `1:1` for generic square feeds. |
| `--format` | `1080` | Desired raw download ceiling (360 / 480 / 720 / 1080). |
| `--language` | `auto` | Overrides automatic script detection if targeting translation. |
| `--output-json` | — | Exports raw transcript & raw highlight metrics maps for advanced scripting. |

---

## 💡 Project Structure

The repository employs a fully flattened, lightweight package topography:

```
AI-Youtube-Shorts-Generator/
├── main.py                       # Console Entry-Point
├── requirements-local.txt        # Modern Architecture Dependencies
├── .env.example                  # Fully Documented Variables Template
└── shorts_generator/             # Unified Domain Kernel
    ├── config.py                 # Global Variable Hydration
    ├── downloader.py             # Streamlined Vanilla yt-dlp Engine
    ├── transcriber.py            # Dual-mode (YT API + Gemini Cloud) Transcription
    ├── highlights.py             # Virality Ranking Heuristics
    ├── llm.py                    # Native Official Google GenAI Hub
    ├── clipper.py                # FFmpeg Composition + Face-Tracker
    ├── gpu_detector.py           # Acceleration Evaluator
    └── pipeline.py               # Unified Core Orchestrator
```

## License

This project is licensed under the MIT License.
