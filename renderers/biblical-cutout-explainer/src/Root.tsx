import React from "react";
import {Composition, staticFile} from "remotion";
import {BiblicalCutout, type RenderInput} from "./BiblicalCutout";

export const RemotionRoot: React.FC = () => (
  <Composition
    id="BiblicalCutout"
    component={BiblicalCutout}
    width={1920}
    height={1080}
    fps={30}
    durationInFrames={12600}
    calculateMetadata={async () => {
      const response = await fetch(staticFile("render-input.json"));
      if (!response.ok) {
        throw new Error(`render-input.json indisponível: ${response.status}`);
      }
      const input = (await response.json()) as RenderInput;
      return {
        width: input.width,
        height: input.height,
        fps: input.fps,
        durationInFrames: Math.max(1, Math.ceil(input.duration_seconds * input.fps)),
      };
    }}
  />
);
