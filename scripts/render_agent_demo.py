"""Render a short video replay from a saved live-model evaluation report."""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import tempfile
import textwrap

from PIL import Image, ImageDraw, ImageFont


WIDTH, HEIGHT = 1280, 720
BACKGROUND = "#101820"
PANEL = "#192630"
TEXT = "#f1f5f5"
MUTED = "#a9b9bd"
ACCENT = "#4bd8bd"


def font(size: int):
    filename = "/System/Library/Fonts/Menlo.ttc"
    try:
        return ImageFont.truetype(filename, size)
    except OSError:
        return ImageFont.load_default(size=size)


def draw_lines(draw: ImageDraw.ImageDraw, value: str, y: int,
               *, size: int = 31, color: str = TEXT,
               width: int = 55, spacing: int = 14) -> int:
    for paragraph in value.splitlines() or [""]:
        for line in textwrap.wrap(paragraph, width=width) or [""]:
            draw.text((96, y), line, font=font(size), fill=color)
            y += size + spacing
    return y


def frame(section: str, title: str, body: str, footer: str) -> Image.Image:
    canvas = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(canvas)
    draw.rectangle((0, 0, WIDTH, 12), fill=ACCENT)
    draw.text((80, 55), "AUTOOPS / LIVE TASK REPLAY", font=font(24), fill=ACCENT)
    draw.text((80, 140), section.upper(), font=font(22), fill=MUTED)
    draw.text((80, 190), title, font=font(42), fill=TEXT)
    draw.rounded_rectangle((76, 285, 1204, 570), radius=8, fill=PANEL)
    draw_lines(draw, body, 325)
    draw.text((80, 635), footer, font=font(21), fill=MUTED)
    return canvas


def render(report_path: pathlib.Path, output_path: pathlib.Path) -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    case = next(item for item in report["cases"] if item["name"] == "read_service_note")
    if not case["passed"] or not case["executed_tools"]:
        raise ValueError("demo case must be a successful, tool-using live run")
    footer = f"{report['model']}  |  {case['duration_seconds']:.2f}s  |  {report['recorded_at'][:10]} UTC"
    scenes = [
        frame("01 / request", "User asks AutoOps", case["request"], footer),
        frame("02 / agent action", "Tool selected and executed",
              " -> ".join(case["executed_tools"]), footer),
        frame("03 / final result", "Answer returned to user",
              case["final_answer"], footer),
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="autoops-demo-") as directory:
        frames = []
        for index, scene in enumerate(scenes):
            path = pathlib.Path(directory) / f"scene-{index}.png"
            scene.save(path)
            frames.append(path)
        playlist = pathlib.Path(directory) / "scenes.ffconcat"
        playlist.write_text(
            "ffconcat version 1.0\n"
            + "".join(f"file '{path}'\nduration 4\n" for path in frames)
            + f"file '{frames[-1]}'\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-safe", "0", "-f", "concat", "-i", str(playlist),
             "-t", "12", "-vf", "fps=24,format=yuv420p", "-c:v", "libx264",
             "-movflags", "+faststart", str(output_path)],
            check=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=pathlib.Path)
    parser.add_argument("output", type=pathlib.Path)
    arguments = parser.parse_args()
    render(arguments.report, arguments.output)
