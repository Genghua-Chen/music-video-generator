"""Generate animated music videos from local audio and artwork."""
import argparse
import base64
from bisect import bisect_left, bisect_right
from functools import lru_cache
from io import BytesIO
import json
import os
from pathlib import Path
import random
import re
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance, ImageFont, ImageOps
from pydub import AudioSegment
from pydub.utils import make_chunks


@lru_cache(maxsize=1)
def load_fonts():
    paths = (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    )
    for path in paths:
        try:
            return tuple(ImageFont.truetype(path, size) for size in (58, 28, 24))
        except OSError:
            continue
    return tuple(ImageFont.load_default() for _ in range(3))


class MinimalRecordVideoGenerator:
    def __init__(self, audio_path, cover_path, background_path, output_path="output.mp4", song_name="", artist_name=""):
        self.audio_path = audio_path
        self.cover_path = cover_path
        self.background_path = background_path
        self.output_path = output_path
        self.song_name = song_name
        self.artist_name = artist_name
        self.fps = 30
        self.resolution = (1920, 1080)
        
        # Pre-load and cache images for faster rendering
        self.bg_image = None
        self.cover_image = None
        
    def analyze_audio(self):
        """Analyze audio for amplitude and basic beat detection"""
        print("Analyzing audio...")
        print(f"Loading audio from: {self.audio_path}")
        
        try:
            # Load audio with pydub
            audio = AudioSegment.from_file(self.audio_path)
            self.duration = len(audio) / 1000.0  # Convert to seconds
            print(f"✓ Audio loaded successfully! Duration: {self.duration:.2f} seconds")
        except Exception as e:
            print(f"✗ Error loading audio: {e}")
            raise
        
        # Calculate RMS amplitude for each chunk
        chunk_length_ms = 50  # 50ms chunks
        chunks = make_chunks(audio, chunk_length_ms)
        
        self.amplitudes = []
        self.times = []
        
        for i, chunk in enumerate(chunks):
            # Calculate RMS amplitude
            amplitude = chunk.rms / 10000.0  # Normalize
            self.amplitudes.append(amplitude)
            self.times.append(i * chunk_length_ms / 1000.0)
        
        # Normalize amplitudes
        max_amp = max(self.amplitudes) if self.amplitudes else 0
        self.amplitudes = [a / (max_amp or 1) for a in self.amplitudes]
        
        print("✓ Audio analysis complete!")
        print(f"  - Total chunks analyzed: {len(self.amplitudes)}")
        print(f"  - Average intensity: {np.mean(self.amplitudes):.2f}")
        
    def preload_images(self):
        """Pre-load and resize images once to avoid repeated loading"""
        print("\n📸 Pre-loading images...")
        w, h = self.resolution
        
        # Load and resize background
        self.bg_image = Image.open(self.background_path).convert('RGBA')
        self.bg_image = ImageOps.fit(self.bg_image, (w, h), Image.Resampling.LANCZOS)
        
        # Load cover and make it square by cropping to center
        self.cover_image = Image.open(self.cover_path).convert('RGBA')
        
        # Make cover square by cropping to center
        cover_w, cover_h = self.cover_image.size
        if cover_w != cover_h:
            # Crop to square (center crop)
            size = min(cover_w, cover_h)
            left = (cover_w - size) // 2
            top = (cover_h - size) // 2
            right = left + size
            bottom = top + size
            self.cover_image = self.cover_image.crop((left, top, right, bottom))
            print(f"  - Cover cropped from {cover_w}x{cover_h} to {size}x{size} (center crop)")
        
        self.blurred_backgrounds = [
            ImageEnhance.Brightness(self.bg_image.filter(ImageFilter.GaussianBlur(12 + i / 2))).enhance(0.48)
            for i in range(9)
        ]
        self.cover_layers = lru_cache(maxsize=64)(self.create_cover_layers)
        print("✓ Images pre-loaded!")
        
    def get_audio_intensity(self, t):
        """Get audio intensity at time t"""
        if not self.times:
            return 0.5
        
        idx = bisect_left(self.times, t)
        if idx == len(self.times) or (
            idx and t - self.times[idx - 1] <= self.times[idx] - t
        ):
            idx -= 1
        return self.amplitudes[idx]

    def create_waveform_circle(self, t, w, h):
        """Create circular waveform visualization"""
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        intensity = self.get_audio_intensity(t)
        
        n_points = 160
        base_radius = 260
        
        points = []
        for i in range(n_points + 1):
            angle = (i / n_points) * 360
            rad = np.radians(angle)
            wave = np.sin(rad * 6 + t * 2) * (3 + intensity * 7)
            r = base_radius + wave + intensity * 8
            
            x = w // 2 + int(np.cos(rad) * r)
            y = h // 2 + int(np.sin(rad) * r)
            points.append((x, y))
        
        color = (245, 242, 234, 86)
        draw.line(points, fill=color, width=2)
        
        return img
    
    def create_text_overlay(self, t, w, h):
        """Create text overlay with song name, artist, and time"""
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        font_large, font_medium, font_small = load_fonts()
        if self.song_name:
            draw.text((w // 2, 100), self.song_name, font=font_large,
                      fill=(248, 245, 239, 255), anchor="mm")
        if self.artist_name:
            y = 160 if self.song_name else 100
            draw.text((w // 2, y), self.artist_name, font=font_medium,
                      fill=(235, 231, 222, 195), anchor="mm")

        elapsed = int(t)
        duration = int(self.duration)
        time_str = f"{elapsed // 60:02d}:{elapsed % 60:02d}"
        total_str = f"{duration // 60:02d}:{duration % 60:02d}"

        left, right = 64, w - 64
        baseline = h - 62
        for x, label, anchor in ((left, time_str, "ls"), (right, total_str, "rs")):
            draw.text((x, baseline), label, font=font_small, anchor=anchor,
                      fill=(245, 245, 245, 255), stroke_width=1,
                      stroke_fill=(0, 0, 0, 150))

        y = h - 44
        draw.line((left, y, right, y), fill=(255, 255, 255, 90), width=2)
        progress = min(1, t / self.duration)
        if progress > 0:
            draw.line((left, y, left + int((right - left) * progress), y),
                      fill=(255, 255, 255, 245), width=2)

        return img

    def create_cover_layers(self, cover_size):
        cover = self.cover_image.resize((cover_size, cover_size), Image.Resampling.LANCZOS)
        
        # Create circular mask for the cover
        mask = Image.new('L', (cover_size, cover_size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse([0, 0, cover_size, cover_size], fill=255)
        
        # Apply circular mask to cover
        cover_circular = Image.new('RGBA', (cover_size, cover_size), (0, 0, 0, 0))
        cover_circular.paste(cover, (0, 0))
        cover_circular.putalpha(mask)
        
        return cover_circular

    def create_frame(self, t):
        """Create a single video frame at time t"""
        w, h = self.resolution
        
        intensity = self.get_audio_intensity(t)
        bg = self.blurred_backgrounds[round(intensity * 8)]
        bg = Image.alpha_composite(bg, self.create_waveform_circle(t, w, h))

        cover_size = int(450 + intensity * 12)
        cover = self.cover_layers(cover_size).rotate(
            (t * 9) % 360, resample=Image.Resampling.BICUBIC
        )
        cover_x = (w - cover_size) // 2
        cover_y = (h - cover_size) // 2
        bg.alpha_composite(cover, (cover_x, cover_y))

        # Add text overlay (song name, artist, time)
        text_overlay = self.create_text_overlay(t, w, h)
        bg = Image.alpha_composite(bg, text_overlay)
        
        return np.array(bg.convert('RGB'))
    
    def generate_video(self):
        """Stream rendered frames to FFmpeg and encode the source audio directly."""
        w, h = self.resolution
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-f", "rawvideo", "-pixel_format", "rgb24",
            "-video_size", f"{w}x{h}", "-framerate", str(self.fps), "-i", "pipe:0",
            "-i", str(self.audio_path), "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "ultrafast", "-b:v", "5000k",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(self.output_path),
        ]
        total_frames = int(np.ceil(self.duration * self.fps))
        with subprocess.Popen(command, stdin=subprocess.PIPE) as encoder:
            try:
                for frame in range(total_frames):
                    if frame % (self.fps * 5) == 0:
                        print(f"Rendering {frame / total_frames:.0%}", flush=True)
                    encoder.stdin.write(self.create_frame(frame / self.fps))
                encoder.stdin.close()
                if encoder.wait():
                    raise RuntimeError("FFmpeg video encoding failed")
            except BaseException:
                if encoder.poll() is None:
                    encoder.kill()
                raise
        if hasattr(self, "cover_layers"):
            self.cover_layers.cache_clear()


class VinylSleeveVideoGenerator(MinimalRecordVideoGenerator):
    """Render a fixed album sleeve with an exposed, rotating record label."""

    lyrics = ()

    def preload_images(self):
        w, h = self.resolution
        with Image.open(self.background_path) as background:
            self.scene = ImageOps.fit(background.convert("RGBA"), (w, h), Image.Resampling.LANCZOS)
        self.scene = self.scene.filter(ImageFilter.GaussianBlur(14))
        self.scene = ImageEnhance.Brightness(self.scene).enhance(0.68)
        with Image.open(self.cover_path) as cover:
            self.sleeve = ImageOps.fit(cover.convert("RGBA"), (438, 438), Image.Resampling.LANCZOS)

        disc_size = 418
        disc = Image.new("RGBA", (disc_size, disc_size))
        draw = ImageDraw.Draw(disc)
        draw.ellipse((0, 0, disc_size - 1, disc_size - 1), fill=(13, 13, 15), outline=(48, 48, 51), width=2)
        for inset in (10, 23, 46, 72, 99, 125):
            end = disc_size - 1 - inset
            draw.ellipse((inset, inset, end, end), outline=(40, 40, 43), width=2)

        self.sleeve_position = (124, 352)
        self.disc_position = (296, 362)
        self.disc = disc
        label_size = 168
        self.label = self.sleeve.resize((label_size, label_size), Image.Resampling.LANCZOS)
        mask = Image.new("L", self.label.size)
        ImageDraw.Draw(mask).ellipse((0, 0, label_size - 1, label_size - 1), fill=255)
        self.label.putalpha(mask)
        self.label_position = tuple(position + (disc_size - label_size) // 2 for position in self.disc_position)

        _, artist_font, _ = load_fonts()
        title_font = artist_font.font_variant(size=46) if hasattr(artist_font, "font_variant") else artist_font
        self.clock_font = title_font
        self.artist_font = artist_font

    def load_lyrics(self, source):
        self.lyrics = []
        stem = normalize_output_stem(source.stem).casefold()
        sidecar = next(
            (file for file in sorted(source.parent.iterdir())
             if file.is_file() and file.suffix.lower() == ".lrc"
             and normalize_output_stem(file.stem).casefold() == stem),
            None,
        )
        if sidecar is None:
            return
        data = sidecar.read_bytes()
        encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        try:
            content = data.decode(encoding)
        except UnicodeDecodeError:
            content = data.decode("gb18030", errors="replace")
        match = re.search(r"\[offset:\s*([+-]?\d+)\]", content, re.IGNORECASE)
        offset = int(match[1]) / 1000 if match else 0
        timestamp = re.compile(r"\[(\d+):(\d{2})(?:[.:](\d{1,3}))?\]")
        lines = {}
        for line in content.splitlines():
            tags = list(timestamp.finditer(line))
            if not tags:
                continue
            lyric = line[tags[-1].end():].strip()
            for tag in tags:
                minutes, seconds, fraction = tag.groups()
                time = int(minutes) * 60 + int(seconds) + float(f"0.{fraction or '0'}")
                lines[round(max(0, time + offset), 3)] = lyric
        self.lyric_times = sorted(lines)
        self.lyrics = [lines[time] for time in self.lyric_times]
        if any(self.lyrics):
            print(f"Loaded lyrics: {sidecar.name}")
        else:
            self.lyrics = []

    def draw_lyrics(self, frame, t):
        index = bisect_right(self.lyric_times, t) - 1
        if index < 0:
            return

        width, height, row_height = 1040, 250, 58
        overlay = Image.new("RGBA", (width, height))
        draw = ImageDraw.Draw(overlay)
        elapsed = t - self.lyric_times[index]
        slide = round(row_height * (1 - min(1, elapsed / 0.28)) ** 3) if index else 0
        for row in range(max(0, index - 3), min(len(self.lyrics), index + 3)):
            y = height // 2 + (row - index) * row_height + slide
            alpha = (255 if row == index else 115) * min(1, max(0, min(y, height - y) / 32))
            if alpha:
                draw.text((width // 2, y), self.lyrics[row], font=self.clock_font if row == index else self.artist_font,
                          anchor="mm", fill=(255, 255, 255, round(alpha)),
                          stroke_width=1, stroke_fill=(0, 0, 0, round(alpha / 2)))
        frame.alpha_composite(overlay, (740, 555))

    def create_frame(self, t):
        frame = self.scene.copy()
        # The sleeve enters first, then the record slides out from behind it.
        sleeve_progress = min(1, t / 0.72)
        sleeve_slide = (sleeve_progress - 1) ** 3 + 1
        sleeve_x, sleeve_y = self.sleeve_position
        sleeve_offset = round((sleeve_x + self.sleeve.width) * (sleeve_slide - 1))

        disc_progress = min(1, max(0, (t - 0.36) / 0.64))
        disc_slide = 1 - (1 - disc_progress) ** 3
        disc_offset = sleeve_offset - round(
            (self.disc_position[0] - sleeve_x) * (1 - disc_slide)
        )
        disc_x, disc_y = self.disc_position
        frame.alpha_composite(self.disc, (disc_x + disc_offset, disc_y))
        label = self.label.rotate((-t * 36) % 360, resample=Image.Resampling.BICUBIC)
        label_x, label_y = self.label_position
        frame.alpha_composite(label, (label_x + disc_offset, label_y))
        frame.alpha_composite(self.sleeve, (sleeve_x + sleeve_offset, sleeve_y))

        draw = ImageDraw.Draw(frame)
        if t >= 0.8:
            if self.lyrics:
                self.draw_lyrics(frame, t)
            else:
                draw.text((1260, 680), "No lyrics", font=self.artist_font,
                          anchor="mm", fill=(220, 220, 220))
        text_progress = min(1, max(0, (t - 0.12) / 0.68))
        offset = round((frame.height - 827 + 8) * (1 - text_progress) ** 3)
        left, right, y = 740, 1780, 827 + offset
        draw.line((left, y, right, y), fill=(148, 148, 148), width=3)
        progress_x = left + round((right - left) * min(1, t / self.duration))
        if progress_x > left:
            draw.line((left, y, progress_x, y), fill=(255, 255, 255), width=3)

        draw.text((left, 892 + offset), self.song_name, font=self.clock_font,
                  anchor="ls", fill=(250, 248, 244))
        draw.text((left, 936 + offset), self.artist_name, font=self.artist_font,
                  anchor="ls", fill=(226, 223, 217))
        elapsed = int(t)
        draw.text(
            (right, 909 + offset), f"{elapsed // 60:02d}:{elapsed % 60:02d}",
            font=self.clock_font, anchor="rs", fill=(250, 248, 244),
        )
        return np.array(frame.convert("RGB"))


# Preserve the existing import name.
MusicVideoGenerator = MinimalRecordVideoGenerator

def find_audio_file(folder_path):
    """Find audio file (mp3, flac, wav, m4a) in folder"""
    audio_extensions = ('.ncm', '.mp3', '.flac', '.wav', '.m4a', '.aac', '.ogg')
    
    if os.path.isfile(folder_path):
        return folder_path
    
    if not os.path.isdir(folder_path):
        raise FileNotFoundError(f"Audio path not found: {folder_path}")
    
    files = [f for f in sorted(os.listdir(folder_path)) 
             if f.lower().endswith(audio_extensions)]
    
    if not files:
        raise FileNotFoundError(f"No audio files found in: {folder_path}")
    
    # Return first audio file found
    audio_file = os.path.join(folder_path, files[0])
    print(f"📀 Found audio: {files[0]}")
    return audio_file

def find_cover_file(folder_path, *, randomize=False):
    """Find cover image (jpg, jpeg, png) in folder"""
    image_extensions = ('.jpg', '.jpeg', '.png', '.webp')
    
    if os.path.isfile(folder_path):
        return folder_path
    
    if not os.path.isdir(folder_path):
        raise FileNotFoundError(f"Cover path not found: {folder_path}")
    
    files = sorted(path for path in Path(folder_path).iterdir()
                   if path.is_file() and path.suffix.lower() in image_extensions)
    if not files:
        raise FileNotFoundError(f"No image files found in: {folder_path}")

    image = random.choice(files) if randomize else files[0]
    print(f"🖼️  Selected image: {image.name}")
    return image

def decrypt_ncm_block(data, mask, key):
    # OpenSSL handles AES-128-ECB and validates the PKCS#7 padding.
    return subprocess.run(
        ["openssl", "enc", "-d", "-aes-128-ecb", "-K", key.hex()],
        input=bytes(value ^ mask for value in data),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
    ).stdout


def unpack_ncm(source, temp_dir):
    """Extract metadata, artwork and original audio using the README's layout."""
    with source.open("rb") as stream:
        def read_exact(size):
            data = stream.read(size)
            if len(data) != size:
                raise ValueError("Truncated NCM file")
            return data

        def read_size():
            return struct.unpack("<I", read_exact(4))[0]

        if read_exact(10)[:8] != b"CTENFDAM":
            raise ValueError("Invalid NCM header")
        key = decrypt_ncm_block(read_exact(read_size()), 0x64, b"hzHRAmso5kInbaxW")
        prefix = b"neteasecloudmusic"
        if not key.startswith(prefix) or len(key) == len(prefix):
            raise ValueError("Invalid NCM audio key")
        key = key[len(prefix):]

        raw_meta = read_exact(read_size())
        meta = {}
        if raw_meta:
            encoded = bytes(value ^ 0x63 for value in raw_meta)
            encrypted = base64.b64decode(encoded[22:], validate=True)
            # The metadata payload is base64-decoded before AES decryption.
            decoded = decrypt_ncm_block(encrypted, 0, b"#14ljk_!\\]&0U<'(")
            kind, _, payload = decoded.partition(b":")
            meta = json.loads(payload)
            if kind == b"dj":
                meta = meta.get("mainMusic") or meta

        read_exact(5)
        frame_size, image_size = read_size(), read_size()
        art = read_exact(image_size)
        stream.seek(max(frame_size - image_size, 0), os.SEEK_CUR)

        box = list(range(256))
        j = 0
        for i in range(256):
            j = (box[i] + j + key[i % len(key)]) & 255
            box[i], box[j] = box[j], box[i]
        keystream = bytes(box[(box[k] + box[(box[k] + k) & 255]) & 255]
                          for k in ((i + 1) & 255 for i in range(256)))

        def decode(chunk):
            repeated = (keystream * ((len(chunk) + 255) // 256))[:len(chunk)]
            return (int.from_bytes(chunk, "big") ^ int.from_bytes(repeated, "big")).to_bytes(
                len(chunk), "big"
            )

        # Each full chunk is a multiple of the 256-byte keystream period.
        chunk_size = 1024 * 1024
        first = decode(stream.read(chunk_size))
        if not first:
            raise ValueError("NCM file has no audio payload")
        extension = ".flac" if first.startswith(b"fLaC") else ".mp3"
        audio = temp_dir / ("audio" + extension)
        with audio.open("wb") as output:
            output.write(first)
            while chunk := stream.read(chunk_size):
                output.write(decode(chunk))

    artist = "、".join(entry[0] for entry in (meta.get("artist") or []) if entry and entry[0])
    return audio, meta.get("musicName"), artist, art


def read_audio_tags(source):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags:stream=index,codec_type:stream_disposition=attached_pic",
         "-of", "json", str(source)],
        capture_output=True, check=True,
    )
    info = json.loads(result.stdout)
    tags = {key.lower(): value for key, value in info.get("format", {}).get("tags", {}).items()}
    picture = next((stream["index"] for stream in info.get("streams", [])
                    if stream.get("disposition", {}).get("attached_pic")), None)
    art = b""
    if picture is not None:
        art = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(source), "-map", f"0:{picture}",
             "-c:v", "copy", "-frames:v", "1", "-f", "image2pipe", "pipe:1"],
            capture_output=True, check=True,
        ).stdout
    return source, tags.get("title"), tags.get("artist"), art


def render_track(source, output, args, project):
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".music-video-", dir=output.parent) as directory:
        temp_dir = Path(directory)
        if source.suffix.lower() == ".ncm":
            audio, title, artist, art = unpack_ncm(source, temp_dir)
        else:
            audio, title, artist, art = read_audio_tags(source)

        fallback_artist, separator, fallback_title = source.stem.partition(" - ")
        title = args.song or title or (fallback_title if separator else source.stem)
        artist = args.artist or artist or (fallback_artist if separator else "")
        if args.cover:
            cover = find_cover_file(args.cover)
        elif art:
            try:
                with Image.open(BytesIO(art)) as image:
                    cover = temp_dir / "cover.png"
                    image.convert("RGB").save(cover)
            except OSError:
                cover = None
        else:
            cover = None

        if not cover:
            cover = next((source.with_suffix(ext) for ext in (".jpg", ".png", ".jpeg", ".webp")
                          if source.with_suffix(ext).is_file()), None)
        if args.background:
            background = find_cover_file(args.background, randomize=True)
        elif args.background_mode == "random" or not cover:
            try:
                background = find_cover_file(project / "Background", randomize=True)
            except FileNotFoundError:
                if cover:
                    background = cover
                else:
                    background = temp_dir / "default.png"
                    Image.new("RGB", (512, 512), "#20253c").save(background)
        else:
            background = cover

        staged_output = temp_dir / "video.mp4"
        generator_type = VinylSleeveVideoGenerator if args.style == "vinyl" else MinimalRecordVideoGenerator
        generator = generator_type(audio, cover or background, background, staged_output, title, artist)
        if args.style == "vinyl":
            generator.load_lyrics(source)
        generator.analyze_audio()
        generator.preload_images()
        generator.generate_video()
        if args.audio:
            staged_output.replace(output)
        else:
            try:
                os.link(staged_output, output)
            except FileExistsError:
                print(f"Skipped {source.name}, {output.name} was created by another process")
                return
        print(f"Saved {output}")


def normalize_output_stem(stem):
    return re.sub(r"[‐‑‒–—―−﹘﹣－]+", "-", stem)


def main():
    project = Path(__file__).resolve().parent
    unpublished = project / "unpublished_video"
    parser = argparse.ArgumentParser(description="Generate animated music videos")
    parser.add_argument("--audio", help="Render one audio file (or the first audio file in a folder)")
    parser.add_argument("--input-dir", type=Path, default=project / "raw_music",
                        help="Batch input folder (default: raw_music next to the script)")
    parser.add_argument("--style", choices=("minimal", "vinyl"), default="vinyl",
                        help="Select the vinyl sleeve (default) or centered cover layout")
    parser.add_argument("--cover", help="Override cover artwork with an image or image folder")
    parser.add_argument("--background-mode", choices=("cover", "random"), default="cover",
                        help="Use cover artwork or pick a random Background image for each track")
    parser.add_argument("--background", help="Override with a fixed image or pick randomly from a folder")
    parser.add_argument("--output", type=Path, help="Output path for single-file mode")
    parser.add_argument("--song", help="Override the title")
    parser.add_argument("--artist", help="Override the artist")
    args = parser.parse_args()
    if args.output and not args.audio:
        parser.error("--output requires --audio")
    if args.audio:
        sources = [Path(find_audio_file(args.audio))]
    else:
        if not args.input_dir.is_dir():
            parser.error(f"Input folder not found: {args.input_dir}")
        sources = sorted(
            (path for path in args.input_dir.iterdir()
             if path.is_file() and path.suffix.lower() in {".ncm", ".mp3"}),
            key=lambda path: (path.stem.casefold(), path.suffix.lower() != ".ncm", path.name),
        )

    failed = 0
    for source in sources:
        if args.audio:
            output = args.output or unpublished / "music_video.mp4"
        else:
            stem = normalize_output_stem(source.stem)
            output = unpublished / f"{stem}.mp4"
            folders = (unpublished, project / "processed_videos", project / "processed_video",
                       source.parent, project)
            if any(
                video.is_file()
                and video.suffix.lower() == ".mp4"
                and normalize_output_stem(video.stem).casefold() == stem.casefold()
                for folder in folders if folder.is_dir()
                for video in folder.iterdir()
            ):
                print(f"Skipped {source.name}, {output.name} already exists")
                continue
        try:
            render_track(source, output, args, project)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            failed += 1
            details = getattr(error, "stderr", None)
            print(f"Failed {source.name}: {error}", file=sys.stderr)
            if isinstance(details, bytes) and details:
                print(details.decode(errors="replace").strip(), file=sys.stderr)
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
