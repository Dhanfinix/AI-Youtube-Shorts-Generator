import { useCurrentFrame, useVideoConfig, Video, spring, AbsoluteFill, staticFile } from "remotion";
import React from "react";
import { z } from "zod";

export const shortsSchema = z.object({
  videoUrl: z.string(),
  title: z.string().optional(),
  subtitles: z.array(
    z.object({
      word: z.string(),
      start: z.number(), // in seconds
      end: z.number(),   // in seconds
    })
  ),
  fps: z.number().default(30),
});

export type ShortsProps = z.infer<typeof shortsSchema>;

export const ShortsComposition: React.FC<ShortsProps> = ({
  videoUrl,
  title = "",
  subtitles = [],
}) => {
  const frame = useCurrentFrame();
  const config = useVideoConfig();
  const { height } = config;
  // CRITICAL: use Remotion's composition fps for time calculation,
  // not the video's native fps. Remotion's frame counter ticks at
  // composition fps (30), so currentTime must use that same rate.
  const currentTime = frame / config.fps;

  // Show teaser thumbnail card on first 24 frames (~0.8s)
  const showTeaser = frame < 24;

  // ─── ONE-WORD-AT-A-TIME subtitle logic ───────────────────────────────────
  // Find the word actively being spoken right now
  const activeWordIdx = subtitles.findIndex(
    (w) => currentTime >= w.start && currentTime <= w.end
  );

  // If between words, find the last word that was spoken (persist it)
  let displayWordIdx = activeWordIdx;
  if (displayWordIdx === -1) {
    // Find the most recently finished word
    let lastFinishedIdx = -1;
    for (let i = 0; i < subtitles.length; i++) {
      if (subtitles[i].end < currentTime) lastFinishedIdx = i;
      else break;
    }
    displayWordIdx = lastFinishedIdx;
  }

  // The single word to display
  const displayWord = displayWordIdx >= 0 ? subtitles[displayWordIdx] : null;
  const isCurrentlySpoken = displayWordIdx === activeWordIdx && activeWordIdx !== -1;

  // Spring animation: punch in when word first appears
  const spr = displayWord
    ? spring({
        frame: frame - Math.round(displayWord.start * config.fps),
        fps: config.fps,
        config: { damping: 14, mass: 0.6, stiffness: 240 },
      })
    : 0;

  const wordScale = isCurrentlySpoken ? 1.0 + spr * 0.12 : 1.0;
  const wordColor = isCurrentlySpoken ? "#FFDE00" : "rgba(255,255,255,0.85)";
  const titleText = title.toLocaleUpperCase("id-ID");
  const titleFontSize = titleText.length > 52 ? 52 : titleText.length > 34 ? 60 : 70;

  // Resolve video source
  const resolvedSrc = videoUrl.startsWith("http") ? videoUrl : staticFile(videoUrl);

  return (
    <AbsoluteFill
      style={{
        backgroundColor: "#000",
        fontFamily: "'Plus Jakarta Sans', 'Outfit', 'Inter', sans-serif",
        overflow: "hidden",
      }}
    >
      {/* 1. Main Vertical Video */}
      <Video
        src={resolvedSrc}
        className="main-video"
        style={{ width: "100%", height: "100%" }}
      />

      {/* 2. Cinematic Vignette */}
      <div
        style={{
          position: "absolute",
          inset: 0,
          background: "linear-gradient(to bottom, rgba(0,0,0,0) 50%, rgba(0,0,0,0.65) 100%)",
          pointerEvents: "none",
        }}
      />

      {/* 3. ONE-WORD subtitle — centered, clean, at 2/5 from bottom */}
      {!showTeaser && displayWord && (
        <div
          style={{
            position: "absolute",
            bottom: `${height * 0.4}px`, /* 2/5 from bottom */
            left: 0,
            right: 0,
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            padding: "0 40px",
            pointerEvents: "none",
          }}
        >
          <span
            key={displayWord.word + displayWordIdx}
            style={{
              fontSize: 72,
              fontWeight: 900,
              color: wordColor,
              transform: `scale(${wordScale})`,
              display: "inline-block",
              textAlign: "center",
              textTransform: "uppercase",
              letterSpacing: 0,
              lineHeight: 1,
              textShadow: [
                "3px 3px 0px #000",
                "-3px -3px 0px #000",
                "3px -3px 0px #000",
                "-3px 3px 0px #000",
                "0px 6px 20px rgba(0,0,0,0.9)",
              ].join(", "),
            }}
          >
            {displayWord.word.toUpperCase()}
          </span>
        </div>
      )}

      {/* 4. Modern Indonesian teaser thumbnail (first 24 frames) */}
      {showTeaser && title && (
        <div
          style={{
            position: "absolute",
            inset: 0,
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            alignItems: "center",
            zIndex: 100,
            pointerEvents: "none",
            background:
              "linear-gradient(to bottom, rgba(0,0,0,0.08) 0%, rgba(0,0,0,0.3) 42%, rgba(0,0,0,0.86) 100%)",
          }}
        >
          <div
            style={{
              position: "absolute",
              top: "9%",
              left: "7%",
              padding: "12px 22px",
              backgroundColor: "#FFDE00",
              color: "#080808",
              fontSize: 30,
              fontWeight: 900,
              letterSpacing: 0,
              borderRadius: 999,
              boxShadow: "0 12px 30px rgba(0,0,0,0.35)",
            }}
          >
            SHORTS PILIHAN
          </div>
          <div
            style={{
              position: "absolute",
              left: "6%",
              right: "6%",
              bottom: "11%",
              minHeight: "27%",
              padding: "34px 38px 38px",
              backgroundColor: "rgba(8, 8, 8, 0.82)",
              borderRadius: 26,
              border: "3px solid rgba(255,255,255,0.92)",
              boxShadow: "0 28px 80px rgba(0, 0, 0, 0.72)",
              display: "flex",
              gap: 22,
              alignItems: "stretch",
            }}
          >
            <div
              style={{
                width: "28%",
                height: 10,
                backgroundColor: "#2EE6A6",
                borderRadius: 999,
              }}
            />
            <h1
              style={{
                fontSize: titleFontSize,
                fontWeight: 900,
                lineHeight: 1.02,
                letterSpacing: 0,
                color: "#FFFFFF",
                margin: 0,
                textTransform: "uppercase",
                textShadow: "0 8px 28px rgba(0,0,0,0.85)",
              }}
            >
              {titleText}
            </h1>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 16,
                color: "rgba(255,255,255,0.82)",
                fontSize: 28,
                fontWeight: 800,
              }}
            >
              <span
                style={{
                  width: 18,
                  height: 18,
                  borderRadius: 999,
                  backgroundColor: "#FF3B30",
                  display: "inline-block",
                }}
              />
              TONTON SAMPAI AKHIR
            </div>
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
};
