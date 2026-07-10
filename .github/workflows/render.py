import os
import random
import subprocess
import threading
import time
import urllib.request
import re
from pathlib import Path
import gdown

# ── Config ────────────────────────────────────────────────────────────────────
TMP              = Path("/tmp/redsky")
LOVESONGS_FOLDER = "1T6ybcn1EXj9YhwsTurCPlVjUqjDH7IIP"
REDSKYIMG_FOLDER = "1BvFlR82cNTxZYuzSuGqPaWgYMMJH4gr2"
DURATION         = random.randint(18000, 36000)  # 5h – 10h

OUT_W, OUT_H = 1920, 1080

MIN_SIZE_BYTES    = int(1.50 * 1024 ** 3)
MAX_SIZE_BYTES    = int(1.99 * 1024 ** 3)
TARGET_SIZE_BYTES = random.randint(int(1.55 * 1024 ** 3), int(1.90 * 1024 ** 3))
AUDIO_BITRATE_K   = 128
VIDEO_KBPS        = int((TARGET_SIZE_BYTES * 8) / DURATION / 1000) - AUDIO_BITRATE_K
VIDEO_KBPS        = max(VIDEO_KBPS, 200)

TARGET_IMAGE_NAME = os.environ.get("TARGET_IMAGE_NAME")
if not TARGET_IMAGE_NAME:
    raise SystemExit("TARGET_IMAGE_NAME env var not set.")

TMP.mkdir(exist_ok=True)
(TMP / "lovesongs").mkdir(exist_ok=True)
(TMP / "redskyimg").mkdir(exist_ok=True)

print("Downloading songs...")
gdown.download_folder(id=LOVESONGS_FOLDER, output=str(TMP / "lovesongs"), quiet=False)

print("Downloading images...")
gdown.download_folder(id=REDSKYIMG_FOLDER, output=str(TMP / "redskyimg"), quiet=False)

matches = list((TMP / "redskyimg").rglob(TARGET_IMAGE_NAME))
if not matches:
    raise SystemExit(f"Target image {TARGET_IMAGE_NAME} not found after download.")

image_path = matches[0]
output_path = TMP / f"OUT_{image_path.stem}.mp4"

print(f"\n>>> IMAGE        : {image_path.name}")
print(f">>> OUTPUT FRAME : {OUT_W}x{OUT_H} (16:9, crop-to-fill)")
print(f">>> DURATION     : {DURATION}s ({DURATION//3600}h {(DURATION%3600)//60}m)")
print(f">>> TARGET SIZE  : {TARGET_SIZE_BYTES / 1e9:.2f} GB (range 1.50–1.99 GB)")
print(f">>> VIDEO BITRATE: {VIDEO_KBPS}k\n")

try:
    req = urllib.request.Request(
        f"https://drive.google.com/drive/folders/{REDSKYIMG_FOLDER}",
        headers={"User-Agent": "Mozilla/5.0"}
    )
    html = urllib.request.urlopen(req).read().decode("utf-8")
    name_id_matches = re.findall(r'"(1[a-zA-Z0-9_-]{25,})"[^}]*?"([^"]+\.(?:jpg|jpeg|png))"', html, re.IGNORECASE)
    file_id = None
    for fid, fname in name_id_matches:
        if fname.lower() == image_path.name.lower():
            file_id = fid
            break
    if file_id:
        (TMP / f"image_id_{image_path.stem}.txt").write_text(file_id)
        print(f">>> Drive file ID: {file_id}")
    else:
        print(">>> Could not extract Drive file ID — summary will show filename only")
except Exception as e:
    print(f">>> Drive ID lookup failed: {e}")

songs = list((TMP / "lovesongs").glob("*.mp3"))
if not songs:
    raise SystemExit("No songs found!")
random.shuffle(songs)
print("Song order:")
for i, s in enumerate(songs):
    print(f"  {i+1}. {s.name}")

concat_path = TMP / f"concat_{image_path.stem}.txt"
estimated_song_len = 200
repeats_needed = max(1, (DURATION // (len(songs) * estimated_song_len)) + 2)
with open(concat_path, "w") as f:
    for _ in range(repeats_needed):
        for s in songs:
            f.write(f"file '{s}'\n")

filter_complex = (
    f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
    f"crop={OUT_W}:{OUT_H},format=yuv420p[outv]"
)

cmd = [
    "ffmpeg", "-y",
    "-loop", "1", "-i", str(image_path),
    "-f", "concat", "-safe", "0", "-i", str(concat_path),
    "-t", str(DURATION),
    "-filter_complex", filter_complex,
    "-map", "[outv]",
    "-map", "1:a",
    "-c:v", "libx264", "-preset", "ultrafast",
    "-b:v", f"{VIDEO_KBPS}k", "-maxrate", f"{VIDEO_KBPS}k", "-bufsize", f"{VIDEO_KBPS * 2}k",
    "-profile:v", "high", "-level", "4.1", "-r", "24", "-g", "48",
    "-c:a", "aac", "-b:a", f"{AUDIO_BITRATE_K}k", "-ar", "44100",
    "-movflags", "+faststart",
    "-shortest",
    str(output_path),
]

print("\nRunning FFmpeg...")
proc = subprocess.Popen(cmd)

stopped_by_watcher = False

def size_watcher():
    global stopped_by_watcher
    while proc.poll() is None:
        time.sleep(10)
        if output_path.exists():
            size = output_path.stat().st_size
            mb   = size / (1024 * 1024)
            gb   = size / (1024 * 1024 * 1024)
            print(f"[SIZE] {output_path.name} → {mb:.1f} MB ({gb:.3f} GB)", flush=True)
            if size >= MAX_SIZE_BYTES:
                print(f"[SIZE] ⚠️  Hit 1.99 GB cap — stopping FFmpeg cleanly.", flush=True)
                stopped_by_watcher = True
                proc.terminate()
                break

watcher = threading.Thread(target=size_watcher, daemon=True)
watcher.start()
proc.wait()
watcher.join()

if proc.returncode not in (0, -15):
    raise SystemExit("FFmpeg failed — check output above.")

final_size    = output_path.stat().st_size
final_size_mb = final_size / (1024 * 1024)
final_size_gb = final_size / (1024 * 1024 * 1024)
stop_reason   = "capped at 1.99 GB by size watcher" if stopped_by_watcher else "duration reached"

if final_size < MIN_SIZE_BYTES:
    print(f"[WARN] Output is only {final_size_gb:.3f} GB — below the 1.50 GB minimum target.")

print(f"\nDONE — {output_path}")
print(f"Stop reason  : {stop_reason}")
print(f"Bitrate used : {VIDEO_KBPS}k")
print(f"Frame        : {OUT_W}x{OUT_H}")
print(f"Size         : {final_size_mb:.1f} MB ({final_size_gb:.3f} GB)")
print(f"Image        : {image_path.name}")

github_output = os.environ.get("GITHUB_OUTPUT")
if github_output:
    with open(github_output, "a") as f:
        f.write(f"output_path={output_path}\n")
        f.write(f"image_name={image_path.name}\n")
        f.write(f"duration_seconds={DURATION}\n")
        f.write(f"final_size_mb={final_size_mb:.1f}\n")
        f.write(f"video_kbps={VIDEO_KBPS}\n")
        
