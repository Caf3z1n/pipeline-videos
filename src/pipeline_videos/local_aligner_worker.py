from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    parser.add_argument("response", type=Path)
    args = parser.parse_args()

    import whisperx

    request = json.loads(args.request.read_text(encoding="utf-8"))
    audio_path = str(request["audio_path"])
    transcript = str(request["transcript"])
    duration = float(request["duration"])
    model_dir = request.get("model_dir")

    audio = whisperx.load_audio(audio_path)
    model, metadata = whisperx.load_align_model(
        language_code="pt",
        device="cpu",
        model_dir=model_dir,
    )
    result = whisperx.align(
        [{"text": transcript, "start": 0.0, "end": duration}],
        model,
        metadata,
        audio,
        "cpu",
        return_char_alignments=False,
    )
    words = []
    for item in result.get("word_segments", []):
        if item.get("start") is None or item.get("end") is None:
            continue
        words.append(
            {
                "word": str(item["word"]),
                "start": float(item["start"]),
                "end": float(item["end"]),
                "score": float(item["score"]) if item.get("score") is not None else None,
            }
        )
    args.response.parent.mkdir(parents=True, exist_ok=True)
    args.response.write_text(
        json.dumps({"engine": "whisperx-3.8.6", "words": words}, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
