import "./index.css";
import { Composition } from "remotion";
import { ShortsComposition, shortsSchema } from "./ShortsComposition";

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="Shorts"
        component={ShortsComposition}
        durationInFrames={4500} // Up to 150 seconds, supports all custom clip durations
        fps={30}
        width={1080}
        height={1920}
        schema={shortsSchema}
        defaultProps={{
          videoUrl: "",
          subtitles: [],
          fps: 30,
        }}
      />
    </>
  );
};
