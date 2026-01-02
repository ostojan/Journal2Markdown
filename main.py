#!/usr/bin/env python3
"""Journal HTML -> Markdown converter.

This module parses exported HTML Apple Journal entries and writes one Markdown
file per entry into the specified output directory. Media found inside an
entry's `div.assetGrid` are copied into per-output `Media/Images` and
`Media/Videos` subdirectories. Images that are not in a supported format
are converted to JPEG/PNG as appropriate.

Usage:
    python3 main.py <input_dir> <output_dir>

Notes:
 - Expects HTML files in `input_dir` and will create `output_dir` if
   missing.
 - Requires Pillow and pillow-heif to handle HEIC/HEIF images.

"""

import argparse
import sys
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from bs4 import BeautifulSoup, Tag
from dateutil.parser import parse as parse_date
from PIL import Image
from pillow_heif import register_heif_opener  # type: ignore

SUPPORTED_IMAGE_FORMATS = {
    "JPEG",
    "PNG",
}


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert exported HTML Apple Journal entries to Markdown."
    )
    parser.add_argument("input_dir", help="Directory containing .html entry files")
    parser.add_argument(
        "output_dir", help="Directory where Markdown files will be written"
    )
    return parser.parse_args()


def validate_input_path(input_path: Path) -> None:
    if not input_path.exists() or not input_path.is_dir():
        raise FileNotFoundError(
            f"Input directory '{input_path}' does not exist or is not a directory."
        )


def create_output_path(output_path: Path) -> None:
    output_path.mkdir(parents=True, exist_ok=True)


def extract_date(soup: BeautifulSoup) -> Optional[datetime]:
    date_str = " ".join(
        t
        for div in soup.find_all("div", class_="pageHeader")
        if (t := div.get_text(" ", strip=True))
    )
    return parse_date(date_str) if date_str else None


def extract_title(soup: BeautifulSoup) -> Optional[str]:
    title = " ".join(
        t
        for div in soup.find_all("div", class_="title")
        if (t := div.get_text(strip=True))
    )
    return title or None


def find_asset_divs(soup: BeautifulSoup) -> List[Tag]:
    return [div for div in soup.find_all("div", class_="assetGrid")]


def collect_image_paths(asset_divs: List[Tag], input_path: Path) -> List[Path]:
    return [
        (input_path / str(img["src"])).resolve(strict=False)
        for asset_div in asset_divs
        for img in asset_div.find_all("img")
    ]


def collect_video_paths(asset_divs: List[Tag], input_path: Path) -> List[Path]:
    return [
        (input_path / str(source["src"])).resolve(strict=False)
        for asset_div in asset_divs
        for source in asset_div.find_all("source")
    ]


def process_image(image_path: Path, images_dir: Path) -> Optional[str]:
    try:
        images_dir.mkdir(parents=True, exist_ok=True)
        with Image.open(image_path) as img:
            base = images_dir / image_path.stem
            if img.format not in SUPPORTED_IMAGE_FORMATS:
                target_format = "PNG"
                target_mode = "RGBA"
                if not img.mode in ("RGBA", "LA") and not (
                    img.mode == "P" and "transparency" in img.info
                ):
                    target_format = "JPEG"
                    target_mode = "RGB"
                target_path = base.with_suffix(f".{target_format.lower()}")
                if not target_path.exists():
                    converted = img.convert(target_mode)
                    converted.save(target_path, format=target_format)
            else:
                target_path = base.with_suffix(f".{img.format.lower()}")
                if not target_path.exists():
                    try:
                        target_path.write_bytes(image_path.read_bytes())
                    except Exception:
                        # fallback: save via Pillow
                        img.save(target_path, format=img.format)
        return f"Media/Images/{target_path.name}"
    except Exception as e:
        print(f"Warning: Failed to process image '{image_path}': {e}", file=sys.stderr)
        return None


def process_video(video_path: Path, videos_dir: Path) -> Optional[str]:
    try:
        videos_dir.mkdir(parents=True, exist_ok=True)
        target = videos_dir / video_path.name
        if not target.exists():
            target.write_bytes(video_path.read_bytes())
        return f"Media/Videos/{target.name}"
    except Exception as e:
        print(f"Warning: Failed to copy video '{video_path}': {e}", file=sys.stderr)
        return None


def extract_paragraphs(soup: BeautifulSoup) -> List[str]:
    paragraphs: List[str] = []
    for body in soup.find_all("div", class_="bodyText"):
        for p in body.find_all("p"):
            text = re.sub(r"\s+", " ", p.get_text(strip=True))
            if text:
                paragraphs.append(text)
    return paragraphs


def process_entry(
    entry: Path, input_path: Path, output_path: Path, media_root: Path
) -> None:
    with entry.open("r", encoding="utf-8") as f:
        soup = BeautifulSoup(f.read(), "lxml")

    date = extract_date(soup)
    title = extract_title(soup)
    asset_divs = find_asset_divs(soup)

    images_paths = collect_image_paths(asset_divs, input_path)
    videos_paths = collect_video_paths(asset_divs, input_path)

    images_dir = media_root / "Images"
    videos_dir = media_root / "Videos"

    images: List[str] = []
    for img_path in images_paths:
        rel = process_image(img_path, images_dir)
        if rel:
            images.append(rel)

    videos: List[str] = []
    for vid_path in videos_paths:
        rel = process_video(vid_path, videos_dir)
        if rel:
            videos.append(rel)

    paragraphs = extract_paragraphs(soup)

    out_file_name = (
        f"{date.year}-{date.month:02d}-{date.day:02d}.md"
        if date
        else f"{entry.stem}.md"
    )
    entry_output_path = output_path / out_file_name
    out_file_content: List[str] = []
    if title:
        out_file_content.append(f"# {title}")
    for img in images:
        out_file_content.append(f"![Image]({img})")
    for vid in videos:
        out_file_content.append(f"![Video]({vid})")
    out_file_content.extend(paragraphs)

    content = "\n\n".join(out_file_content) if out_file_content else ""
    if content:
        content += "\n"

    with entry_output_path.open("w", encoding="utf-8") as out_file:
        out_file.write(content)


def process_files(input_path: Path, output_path: Path) -> int:
    media_root = output_path / "Media"
    create_output_path(media_root)

    for entry in input_path.iterdir():
        if not entry.is_file():
            continue
        try:
            process_entry(entry, input_path, output_path, media_root)
        except Exception as e:
            print(f"Warning: Failed to process entry '{entry}': {e}", file=sys.stderr)
            continue
    return 0


def main() -> int:
    try:
        register_heif_opener()
        args = parse_arguments()
        input_path = Path(args.input_dir)
        validate_input_path(input_path)
        output_path = Path(args.output_dir)
        create_output_path(output_path)
        return process_files(input_path, output_path)
    except:
        print("An error occurred during processing.", file=sys.stderr)
        return -1


if __name__ == "__main__":
    sys.exit(main())
