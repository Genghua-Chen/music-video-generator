"""
Music Video Generator (Simplified Dependencies)
Generates fancy animated music videos from MP3, cover art, and background images.

Requirements:
pip install moviepy numpy pillow pydub

Usage:
python music_video_generator.py --audio song.mp3 --cover cover.jpg --background bg.jpg --output video.mp4
"""

import numpy as np
from moviepy.editor import *
from PIL import Image, ImageDraw, ImageFilter, ImageEnhance
from pydub import AudioSegment
from pydub.utils import make_chunks
import argparse
from pathlib import Path

class MusicVideoGenerator:
    def __init__(self, audio_path, cover_path, background_path, output_path="output.mp4"):
        self.audio_path = audio_path
        self.cover_path = cover_path
        self.background_path = background_path
        self.output_path = output_path
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
        max_amp = max(self.amplitudes) if self.amplitudes else 1
        self.amplitudes = [a / max_amp for a in self.amplitudes]
        
        # Simple beat detection - look for peaks in amplitude
        self.beat_times = []
        threshold = 0.7
        min_beat_gap = 0.2  # Minimum 200ms between beats
        
        for i in range(1, len(self.amplitudes) - 1):
            if (self.amplitudes[i] > threshold and 
                self.amplitudes[i] > self.amplitudes[i-1] and 
                self.amplitudes[i] > self.amplitudes[i+1]):
                
                # Check if it's far enough from the last beat
                if not self.beat_times or (self.times[i] - self.beat_times[-1]) > min_beat_gap:
                    self.beat_times.append(self.times[i])
        
        print(f"Duration: {self.duration:.2f}s, Detected beats: {len(self.beat_times)}")
        print("✓ Audio analysis complete!")
        print(f"  - Total chunks analyzed: {len(self.amplitudes)}")
        print(f"  - Average intensity: {np.mean(self.amplitudes):.2f}")
        
    def preload_images(self):
        """Pre-load and resize images once to avoid repeated loading"""
        print("\n📸 Pre-loading images...")
        w, h = self.resolution
        
        # Load and resize background
        self.bg_image = Image.open(self.background_path).convert('RGBA')
        self.bg_image = self.bg_image.resize((w, h), Image.Resampling.LANCZOS)
        
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
        
        print("✓ Images pre-loaded!")
        
    def get_audio_intensity(self, t):
        """Get audio intensity at time t"""
        if not self.times:
            return 0.5
        
        # Find closest time index
        idx = min(range(len(self.times)), key=lambda i: abs(self.times[i] - t))
        return self.amplitudes[idx] if idx < len(self.amplitudes) else 0.5
    
    def get_spectral_value(self, t):
        """Generate pseudo-spectral value based on time and intensity"""
        intensity = self.get_audio_intensity(t)
        # Create a wave pattern that varies with intensity
        return (np.sin(t * 2 + intensity * 5) + 1) / 2
    
    def is_near_beat(self, t, threshold=0.15):
        """Check if time t is near a beat"""
        return any(abs(t - beat) < threshold for beat in self.beat_times)
    
    def create_particle_system(self, t, w, h):
        """Create animated particles based on audio"""
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        intensity = self.get_audio_intensity(t)
        spectral = self.get_spectral_value(t)
        
        # Reduced particles for speed
        n_particles = int(20 + intensity * 40)  # Reduced from 30-100
        
        for i in range(n_particles):
            # Simplified to 2 layers instead of 3
            layer = i % 2
            angle = (t * (20 + layer * 20) + i * 360 / n_particles) % 360
            rad = np.radians(angle)
            
            base_radius = 200 + layer * 100
            radius = base_radius + intensity * 120 + np.sin(t * 3 + i) * 40
            
            x = w // 2 + int(np.cos(rad) * radius)
            y = h // 2 + int(np.sin(rad) * radius)
            
            # Simplified color calculation
            hue = int((spectral * 360 + t * 50 + layer * 120) % 360)
            r = int(128 + 127 * np.sin(np.radians(hue)))
            g = int(128 + 127 * np.sin(np.radians(hue + 120)))
            b = int(128 + 127 * np.sin(np.radians(hue + 240)))
            
            size = int(4 + intensity * 10 + layer * 2)
            alpha = int(100 + intensity * 155)
            
            # Single glow instead of multiple
            draw.ellipse([x-size-2, y-size-2, x+size+2, y+size+2], 
                        fill=(r, g, b, alpha // 3))
            draw.ellipse([x-size, y-size, x+size, y+size], fill=(r, g, b, alpha))
        
        return img
    
    def create_visualizer_bars(self, t, w, h):
        """Create audio visualizer bars"""
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        intensity = self.get_audio_intensity(t)
        spectral = self.get_spectral_value(t)
        
        n_bars = 60  # Reduced from 80
        bar_width = w // n_bars
        
        for i in range(n_bars):
            phase = t * 3 + i * 0.15
            base_wave = 0.5 + 0.3 * np.sin(phase)
            height_mult = base_wave + intensity * 0.7
            bar_height = int(height_mult * h * 0.35)
            
            hue_offset = (i / n_bars) * 360 + t * 80
            r = int(128 + 127 * np.sin(np.radians(hue_offset)))
            g = int(128 + 127 * np.sin(np.radians(hue_offset + 120)))
            b = int(128 + 127 * np.sin(np.radians(hue_offset + 240)))
            
            x = i * bar_width
            y_bottom = h - bar_height
            
            # Only draw bottom bars
            draw.rectangle([x, y_bottom, x + bar_width - 2, h], 
                         fill=(r, g, b, 200))
        
        return img
    
    def create_waveform_circle(self, t, w, h):
        """Create circular waveform visualization"""
        img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        intensity = self.get_audio_intensity(t)
        
        n_points = 80  # Reduced from 120
        base_radius = 500
        
        points = []
        for i in range(n_points + 1):
            angle = (i / n_points) * 360
            rad = np.radians(angle)
            wave = np.sin(rad * 8 + t * 4) * 30
            r = base_radius + wave + intensity * 80
            
            x = w // 2 + int(np.cos(rad) * r)
            y = h // 2 + int(np.sin(rad) * r)
            points.append((x, y))
        
        spectral = self.get_spectral_value(t)
        color = (
            int(255 * spectral),
            int(255 * (1 - spectral)),
            int(255 * intensity),
            100
        )
        
        draw.line(points, fill=color, width=3)
        
        return img
    
    def create_frame(self, t):
        """Create a single video frame at time t"""
        w, h = self.resolution
        
        # Use pre-loaded background
        bg = self.bg_image.copy()
        
        # Apply dynamic blur based on intensity
        intensity = self.get_audio_intensity(t)
        blur_amount = 2 + intensity * 4  # Reduced blur range
        bg = bg.filter(ImageFilter.GaussianBlur(blur_amount))
        
        # Adjust brightness with beat flashes
        enhancer = ImageEnhance.Brightness(bg)
        if self.is_near_beat(t):
            bg = enhancer.enhance(1.4)
        else:
            bg = enhancer.enhance(0.8 + intensity * 0.3)
        
        # Simplified vignette
        vignette = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        vignette_draw = ImageDraw.Draw(vignette)
        max_offset = min(w, h) // 2 - 10
        num_rings = min(100, max_offset // 5)  # Reduced rings
        for i in range(num_rings):
            alpha = int(i * 1.5)
            offset = i * 5
            if offset < max_offset:
                vignette_draw.rectangle([offset, offset, w-offset, h-offset], 
                                       outline=(0, 0, 0, alpha))
        
        bg = Image.alpha_composite(bg, vignette)
        
        # Add waveform (lighter weight than particles)
        waveform = self.create_waveform_circle(t, w, h)
        bg = Image.alpha_composite(bg, waveform)
        
        # Add particles
        particles = self.create_particle_system(t, w, h)
        bg = Image.alpha_composite(bg, particles)
        
        # Add visualizer bars
        bars = self.create_visualizer_bars(t, w, h)
        bg = Image.alpha_composite(bg, bars)
        
        # Use pre-loaded cover
        base_size = 420
        if self.is_near_beat(t):
            pulse = 1.15
        else:
            pulse = 1.0 + intensity * 0.12
        
        cover_size = int(base_size * pulse)
        cover = self.cover_image.resize((cover_size, cover_size), Image.Resampling.LANCZOS)
        
        # Create circular mask for the cover
        mask = Image.new('L', (cover_size, cover_size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse([0, 0, cover_size, cover_size], fill=255)
        
        # Apply circular mask to cover
        cover_circular = Image.new('RGBA', (cover_size, cover_size), (0, 0, 0, 0))
        cover_circular.paste(cover, (0, 0))
        cover_circular.putalpha(mask)
        
        # Simplified glow - single layer, also circular
        glow = cover_circular.copy()
        glow = glow.filter(ImageFilter.GaussianBlur(20))
        enhancer = ImageEnhance.Brightness(glow)
        glow = enhancer.enhance(1.5)
        
        # Rotating cover (rotate the circular version)
        rotation = (t * 15) % 360
        cover_rotated = cover_circular.rotate(rotation, expand=False)
        glow_rotated = glow.rotate(rotation, expand=False)
        
        cover_x = (w - cover_size) // 2
        cover_y = (h - cover_size) // 2
        
        bg.paste(glow_rotated, (cover_x, cover_y), glow_rotated)
        bg.paste(cover_rotated, (cover_x, cover_y), cover_rotated)
        
        # Simplified frame - 2 rings instead of 3
        frame_overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        frame_draw = ImageDraw.Draw(frame_overlay)
        
        spectral = self.get_spectral_value(t)
        frame_color = (
            int(255 * spectral),
            int(255 * (1 - spectral)),
            int(255 * intensity),
            255
        )
        
        for ring in range(2):
            offset = ring * 8
            thickness = 4
            for i in range(thickness):
                frame_draw.ellipse([
                    cover_x - 15 - offset - i, 
                    cover_y - 15 - offset - i,
                    cover_x + cover_size + 15 + offset + i, 
                    cover_y + cover_size + 15 + offset + i
                ], outline=frame_color)
        
        bg = Image.alpha_composite(bg, frame_overlay)
        
        return np.array(bg.convert('RGB'))
    
    def generate_video(self):
        """Generate the complete music video"""
        print("\n" + "="*50)
        print("STARTING VIDEO GENERATION")
        print("="*50)
        print(f"Output: {self.output_path}")
        print(f"Resolution: {self.resolution[0]}x{self.resolution[1]}")
        print(f"FPS: {self.fps}")
        print(f"Duration: {self.duration:.2f}s")
        print("="*50 + "\n")
        
        # Create video clip
        frame_count = 0
        def make_frame(t):
            nonlocal frame_count
            frame_count += 1
            if frame_count % (self.fps * 5) == 0:  # Every 5 seconds
                progress = (t / self.duration) * 100
                print(f"⏳ Progress: {progress:.1f}% ({t:.1f}s / {self.duration:.1f}s)")
            return self.create_frame(t)
        
        video = VideoClip(make_frame, duration=self.duration)
        video = video.set_fps(self.fps)
        
        # Load audio
        audio = AudioFileClip(self.audio_path)
        video = video.set_audio(audio)
        
        # Write output
        print(f"\n💾 Writing video to {self.output_path}...")
        print("This may take a while depending on video length...")
        
        try:
            video.write_videofile(
                self.output_path,
                fps=self.fps,
                codec='libx264',
                audio_codec='aac',
                preset='ultrafast',  # Changed from 'medium' for speed
                threads=8,  # Increased threads
                bitrate='5000k',  # Reduced bitrate slightly
                verbose=True,
                logger='bar'
            )
            
            print("\n" + "="*50)
            print("✓ VIDEO GENERATION COMPLETE!")
            print("="*50)
            print(f"Output saved to: {self.output_path}")
            
            # Check file size
            file_size = Path(self.output_path).stat().st_size / (1024 * 1024)
            print(f"File size: {file_size:.2f} MB")
            print("="*50)
            
        except Exception as e:
            print(f"\n✗ Error during video generation: {e}")
            raise

def main():
    parser = argparse.ArgumentParser(description='Generate fancy music videos')
    parser.add_argument('--audio', required=True, help='Path to MP3 audio file')
    parser.add_argument('--cover', required=True, help='Path to cover image')
    parser.add_argument('--background', required=True, help='Path to background image')
    parser.add_argument('--output', default='music_video.mp4', help='Output video path')
    
    args = parser.parse_args()
    
    print("\n" + "="*50)
    print("MUSIC VIDEO GENERATOR")
    print("="*50)
    
    # Verify files exist
    print("\n📁 Checking input files...")
    for label, file_path in [("Audio", args.audio), ("Cover", args.cover), ("Background", args.background)]:
        if not Path(file_path).exists():
            print(f"✗ Error: {label} file not found: {file_path}")
            return
        else:
            file_size = Path(file_path).stat().st_size / 1024
            print(f"✓ {label}: {file_path} ({file_size:.1f} KB)")
    
    print("\n✓ All input files found!")
    
    try:
        # Generate video
        generator = MusicVideoGenerator(args.audio, args.cover, args.background, args.output)
        generator.analyze_audio()
        generator.preload_images()  # Pre-load images before generating
        generator.generate_video()
    except KeyboardInterrupt:
        print("\n\n⚠ Generation cancelled by user")
    except Exception as e:
        print(f"\n\n✗ Fatal error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
    
"""
python music_video_generator.py \
  --audio testaudio.mp3 \
  --cover 小胡同/track-1926623288.jpg \
  --background Background/wallpaper.jpg \
  --output my_video.mp4
"""