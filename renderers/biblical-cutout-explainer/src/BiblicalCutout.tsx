import React, {useEffect, useMemo, useState} from "react";
import {Audio} from "@remotion/media";
import {
  AbsoluteFill,
  Img,
  Sequence,
  cancelRender,
  continueRender,
  delayRender,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

type Box = {x: number; y: number; width: number; height: number};
type Word = {index: number; word: string; start: number; end: number};
type TextPage = {
  page_id: string;
  start: number;
  end: number;
  text: string;
  first_word_index: number;
  last_word_index_exclusive: number;
  quote_open: boolean;
  quote_close: boolean;
  emphasis_word_indexes: number[];
};
type AssetEvent = {
  event_id: string;
  asset_id: string;
  src: string;
  start: number;
  end: number;
  zone: Box;
};
type Block = {
  block_id: string;
  block_type:
    | "assets_with_full_text_v1"
    | "center_statement_v1"
    | "fullscreen_image_sequence_v1";
  start: number;
  end: number;
  layout_variant:
    | "text_left_visual_right"
    | "visual_left_text_right"
    | "centered"
    | "fullscreen"
    | null;
  reference_label: string | null;
  text_zone: Box | null;
  text_pages: TextPage[];
  asset_events: AssetEvent[];
};
type SfxEvent = {kind: "whoosh" | "click"; at: number; src: string};

export type RenderInput = {
  width: number;
  height: number;
  fps: number;
  duration_seconds: number;
  background: string;
  font: string;
  narration: string;
  music: string | null;
  music_volume: number;
  words: Word[];
  blocks: Block[];
  sfx_events: SfxEvent[];
};

const frameAt = (seconds: number, fps: number) => Math.round(seconds * fps);
const quoteFree = (value: string) => value.replace(/[“”„‟"]/g, "");

const fittedFontSize = ({
  pageWords,
  emphasizedIndexes,
  zone,
  frameWidth,
  frameHeight,
}: {
  pageWords: Word[];
  emphasizedIndexes: number[];
  zone: Box;
  frameWidth: number;
  frameHeight: number;
}) => {
  if (typeof document === "undefined") return 64;
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) return 64;
  const maximumWidth = zone.width * frameWidth - 12;
  const maximumHeight = zone.height * frameHeight - 12;
  const fits = (base: number) => {
    let lineWidth = 0;
    let lineHeight = 0;
    let totalHeight = 0;
    for (const word of pageWords) {
      const scale = emphasizedIndexes.includes(word.index) ? 1.35 : 1;
      const size = base * scale;
      context.font = `${size}px Knewave`;
      const width = context.measureText(quoteFree(word.word)).width;
      if (width > maximumWidth) return false;
      const gap = lineWidth === 0 ? 0 : 16;
      if (lineWidth + gap + width > maximumWidth) {
        totalHeight += lineHeight * 0.98 + 10;
        lineWidth = width;
        lineHeight = size;
      } else {
        lineWidth += gap + width;
        lineHeight = Math.max(lineHeight, size);
      }
    }
    totalHeight += lineHeight * 0.98;
    return totalHeight <= maximumHeight;
  };
  let low = 36;
  let high = 118;
  while (low < high) {
    const candidate = Math.ceil((low + high) / 2);
    if (fits(candidate)) low = candidate;
    else high = candidate - 1;
  }
  return low;
};

const useRenderInput = () => {
  const [input, setInput] = useState<RenderInput | null>(null);
  const [handle] = useState(() => delayRender("Carregando contrato cutout"));
  useEffect(() => {
    fetch(staticFile("render-input.json"))
      .then(async (response) => {
        if (!response.ok) {
          throw new Error(`render-input.json indisponível: ${response.status}`);
        }
        const loaded = (await response.json()) as RenderInput;
        const font = new FontFace(
          "Knewave",
          `url(${staticFile(loaded.font)}) format("truetype")`,
        );
        const loadedFont = await font.load();
        (document.fonts as unknown as {add: (item: FontFace) => void}).add(loadedFont);
        setInput(loaded);
        continueRender(handle);
      })
      .catch((error) => cancelRender(error));
  }, [handle]);
  return input;
};

const boxStyle = (box: Box): React.CSSProperties => ({
  left: `${box.x * 100}%`,
  top: `${box.y * 100}%`,
  width: `${box.width * 100}%`,
  height: `${box.height * 100}%`,
});

const Background: React.FC<{src: string}> = ({src}) => (
  <AbsoluteFill>
    <Img src={staticFile(src)} style={{width: "100%", height: "100%", objectFit: "cover"}} />
  </AbsoluteFill>
);

const AssetLayer: React.FC<{events: AssetEvent[]}> = ({events}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <>
      {events.map((event) => {
        const start = frameAt(event.start, fps);
        const end = frameAt(event.end, fps);
        if (frame < start || frame >= end) return null;
        const enter = interpolate(frame, [start, start + 6], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        const exit = interpolate(frame, [end - 5, end], [1, 0], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        return (
          <Img
            key={event.event_id}
            src={staticFile(event.src)}
            style={{
              position: "absolute",
              ...boxStyle(event.zone),
              objectFit: "contain",
              opacity: enter * exit,
              transform: `scale(${interpolate(enter, [0, 1], [0.92, 1])})`,
              filter: "drop-shadow(0 18px 14px rgba(70,38,15,.22))",
              zIndex: 4,
            }}
          />
        );
      })}
    </>
  );
};

const FullscreenLayer: React.FC<{events: AssetEvent[]}> = ({events}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  return (
    <>
      {events.map((event, index) => {
        const start = frameAt(event.start, fps);
        const end = frameAt(event.end, fps);
        const first = index === 0;
        const last = index === events.length - 1;
        const visibleStart = first ? start : start - 5;
        const visibleEnd = last ? end : end + 5;
        if (frame < visibleStart || frame >= visibleEnd) return null;
        const enter = interpolate(
          frame,
          first ? [start, start + 5] : [start - 5, start + 5],
          [0, 1],
          {extrapolateLeft: "clamp", extrapolateRight: "clamp"},
        );
        const exit = interpolate(
          frame,
          last ? [end - 5, end] : [end - 5, end + 5],
          [1, 0],
          {extrapolateLeft: "clamp", extrapolateRight: "clamp"},
        );
        return (
          <Img
            key={event.event_id}
            src={staticFile(event.src)}
            style={{
              position: "absolute",
              inset: 0,
              width: "100%",
              height: "100%",
              objectFit: "cover",
              opacity: Math.min(enter, exit),
              zIndex: index,
            }}
          />
        );
      })}
    </>
  );
};

const TextPageView: React.FC<{
  page: TextPage;
  zone: Box;
  words: Word[];
  centered: boolean;
}> = ({page, zone, words, centered}) => {
  const frame = useCurrentFrame();
  const {fps, width, height} = useVideoConfig();
  const pageWords = words.filter(
    (word) =>
      word.index >= page.first_word_index &&
      word.index < page.last_word_index_exclusive,
  );
  const base = useMemo(
    () =>
      fittedFontSize({
        pageWords,
        emphasizedIndexes: page.emphasis_word_indexes,
        zone,
        frameWidth: width,
        frameHeight: height,
      }),
    [height, page.emphasis_word_indexes, pageWords, width, zone],
  );
  const visible =
    frame >= frameAt(page.start, fps) &&
    frame < frameAt(page.end, fps);
  if (!visible) return null;
  const firstWord = pageWords[0];
  const lastWord = pageWords.at(-1);
  const showOpen = Boolean(
    page.quote_open && firstWord && frame >= frameAt(firstWord.start, fps),
  );
  const showClose = Boolean(
    page.quote_close && lastWord && frame >= frameAt(lastWord.end, fps),
  );
  return (
    <div
      style={{
        position: "absolute",
        ...boxStyle(zone),
        display: "flex",
        flexWrap: "wrap",
        justifyContent: centered ? "center" : "flex-start",
        alignContent: "center",
        alignItems: "baseline",
        columnGap: 16,
        rowGap: 10,
        color: "#37251c",
        fontFamily: "Knewave, sans-serif",
        fontSize: base,
        lineHeight: 0.98,
        textAlign: centered ? "center" : "left",
        textTransform: "uppercase",
        textShadow: "0 3px 0 rgba(255,248,214,.52)",
        overflow: "hidden",
        padding: 6,
        boxSizing: "border-box",
        zIndex: 10,
      }}
    >
      {showOpen ? <span style={{fontSize: base * 1.3, color: "#9b4932"}}>“</span> : null}
      {pageWords.map((word) => {
        const wordFrame = frameAt(word.start, fps);
        if (frame < wordFrame) return null;
        const reveal = interpolate(frame, [wordFrame, wordFrame + 4], [0, 1], {
          extrapolateLeft: "clamp",
          extrapolateRight: "clamp",
        });
        const emphasized = page.emphasis_word_indexes.includes(word.index);
        return (
          <span
            key={word.index}
            style={{
              whiteSpace: "nowrap",
              fontSize: emphasized ? base * 1.35 : base,
              color: emphasized ? "#9b4932" : "#37251c",
              opacity: reveal,
              transform: `translateY(${interpolate(reveal, [0, 1], [12, 0])}px)`,
            }}
          >
            {quoteFree(word.word)}
          </span>
        );
      })}
      {showClose ? <span style={{fontSize: base * 1.3, color: "#9b4932"}}>”</span> : null}
    </div>
  );
};

const ReferenceLabelView: React.FC<{label: string; zone: Box}> = ({label, zone}) => {
  const {width, height} = useVideoConfig();
  const size = useMemo(() => {
    if (typeof document === "undefined") return 96;
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d");
    if (!context) return 96;
    const maximumWidth = zone.width * width - 28;
    const maximumHeight = zone.height * height - 28;
    for (let candidate = 116; candidate >= 52; candidate -= 2) {
      context.font = `${candidate}px Knewave`;
      if (
        context.measureText(label.toUpperCase()).width <= maximumWidth &&
        candidate <= maximumHeight
      ) {
        return candidate;
      }
    }
    return 52;
  }, [height, label, width, zone]);
  return (
    <div
      style={{
        position: "absolute",
        ...boxStyle(zone),
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        color: "#37251c",
        fontFamily: "Knewave, sans-serif",
        fontSize: size,
        lineHeight: 1,
        textAlign: "center",
        textTransform: "uppercase",
        textShadow: "0 4px 0 rgba(255,248,214,.62)",
        zIndex: 10,
      }}
    >
      {label}
    </div>
  );
};

const BlockView: React.FC<{block: Block; input: RenderInput}> = ({block, input}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  if (frame < frameAt(block.start, fps) || frame >= frameAt(block.end, fps)) return null;
  if (block.block_type === "fullscreen_image_sequence_v1") {
    return <FullscreenLayer events={block.asset_events} />;
  }
  const centered = block.block_type === "center_statement_v1";
  return (
    <>
      <Background src={input.background} />
      {block.block_type === "assets_with_full_text_v1" ? (
        <AssetLayer events={block.asset_events} />
      ) : null}
      {block.reference_label && block.text_zone ? (
        <ReferenceLabelView label={block.reference_label} zone={block.text_zone} />
      ) : null}
      {block.text_zone
        ? block.text_pages.map((page) => (
            <TextPageView
              key={page.page_id}
              page={page}
              zone={block.text_zone!}
              words={input.words}
              centered={centered}
            />
          ))
        : null}
    </>
  );
};

export const BiblicalCutout: React.FC = () => {
  const input = useRenderInput();
  const frame = useCurrentFrame();
  const {fps, durationInFrames} = useVideoConfig();
  const activeBlock = useMemo(
    () =>
      input?.blocks.find(
        (block) =>
          frame >= frameAt(block.start, fps) &&
          frame < frameAt(block.end, fps),
      ),
    [fps, frame, input],
  );
  if (!input) return null;
  return (
    <AbsoluteFill style={{backgroundColor: "#ead29b"}}>
      {activeBlock ? <BlockView block={activeBlock} input={input} /> : null}
      <Audio src={staticFile(input.narration)} volume={1} />
      {input.music ? (
        <Audio
          src={staticFile(input.music)}
          loop
          volume={(audioFrame) => {
            const fadeIn = interpolate(
              audioFrame,
              [0, frameAt(1.6, fps)],
              [0, input.music_volume],
              {extrapolateLeft: "clamp", extrapolateRight: "clamp"},
            );
            const fadeOut = interpolate(
              audioFrame,
              [durationInFrames - frameAt(2.5, fps), durationInFrames],
              [input.music_volume, 0],
              {extrapolateLeft: "clamp", extrapolateRight: "clamp"},
            );
            return Math.min(fadeIn, fadeOut);
          }}
        />
      ) : null}
      {input.sfx_events.map((event, index) => (
        <Sequence key={`${event.kind}-${index}`} from={frameAt(event.at, fps)} layout="none">
          <Audio src={staticFile(event.src)} volume={0.5} />
        </Sequence>
      ))}
      <AbsoluteFill
        style={{
          opacity: interpolate(
            frame,
            [durationInFrames - frameAt(0.35, fps), durationInFrames],
            [0, 1],
            {extrapolateLeft: "clamp", extrapolateRight: "clamp"},
          ),
          backgroundColor: "#000",
          pointerEvents: "none",
        }}
      />
    </AbsoluteFill>
  );
};
