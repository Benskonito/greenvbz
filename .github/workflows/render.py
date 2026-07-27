import os
import random
import signal
import subprocess
import threading
import time
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
REPO_ROOT   = Path(__file__).resolve().parent
IMAGES_DIR  = REPO_ROOT / "images"
AUDIO_DIR   = REPO_ROOT / "audio"
TMP         = Path("/tmp/redsky")

OUT_W, OUT_H = 1920, 1080  # 1080p — highest resolution target

# ── Duration logic ───────────────────────────────────────────────────────────
# Normal range: 1h20m (4800s) to 3h (10800s)
# Rare extended range: 3h to 4h (10800s - 14400s), low probability
# Hard cap: never exceeds 4h
MIN_DURATION      = 80 * 60          # 1h20m = 4800s
NORMAL_MAX        = 3 * 60 * 60      # 3h    = 10800s
RARE_MAX          = 4 * 60 * 60      # 4h    = 14400s
RARE_CHANCE       = 0.12             # ~12% of runs land in the 3h-4h "rare" zone


def pick_duration():
    if random.random() < RARE_CHANCE:
        return random.randint(NORMAL_MAX, RARE_MAX)
    return random.randint(MIN_DURATION, NORMAL_MAX)


DURATION = pick_duration()

# File size budget (GitHub release asset limit is 2GB, so we stay under that
# with margin). Because duration is now much shorter than before, this same
# size budget naturally translates into a much higher, better-looking bitrate.
MIN_SIZE_BYTES    = int(1.50 * 1024 ** 3)
MAX_SIZE_BYTES    = int(1.99 * 1024 ** 3)
TARGET_SIZE_BYTES = random.randint(int(1.55 * 1024 ** 3), int(1.90 * 1024 ** 3))
AUDIO_BITRATE_K   = 128
VIDEO_KBPS        = int((TARGET_SIZE_BYTES * 8) / DURATION / 1000) - AUDIO_BITRATE_K
VIDEO_KBPS        = max(VIDEO_KBPS, 800)  # floor raised — short duration means quality shouldn't tank

TARGET_IMAGE_NAME = os.environ.get("TARGET_IMAGE_NAME")
if not TARGET_IMAGE_NAME:
    raise SystemExit("TARGET_IMAGE_NAME env var not set.")

TMP.mkdir(parents=True, exist_ok=True)

matches = list(IMAGES_DIR.rglob(TARGET_IMAGE_NAME))
if not matches:
    raise SystemExit(f"Target image {TARGET_IMAGE_NAME} not found in {IMAGES_DIR}.")
image_path = matches[0]
output_path = TMP / f"OUT_{image_path.stem}.mp4"

print(f"\n>>> IMAGE        : {image_path.name}")
print(f">>> OUTPUT FRAME : {OUT_W}x{OUT_H} (16:9, crop-to-fill, 1080p)")
print(f">>> DURATION     : {DURATION}s ({DURATION // 3600}h {(DURATION % 3600) // 60}m)")
print(f">>> TARGET SIZE  : {TARGET_SIZE_BYTES / 1e9:.2f} GB (range 1.50-1.99 GB)")
print(f">>> VIDEO BITRATE: {VIDEO_KBPS}k\n")

songs = sorted(AUDIO_DIR.glob("*.mp3"))
if not songs:
    raise SystemExit(f"No songs found in {AUDIO_DIR}!")
random.shuffle(songs)
print("Song order:")
for i, s in enumerate(songs):
    print(f"  {i + 1}. {s.name}")


def probe_duration(path: Path) -> float:
    """Get real duration of an audio file via ffprobe, instead of guessing."""
    try:
        out = subprocess.check_output(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            text=True,
        ).strip()
        return float(out)
    except Exception as e:
        print(f"[WARN] Could not probe {path.name} ({e}) — assuming 200s.")
        return 200.0


song_durations = [probe_duration(s) for s in songs]
total_playlist_len = sum(song_durations)
print(f"\nTotal single-pass playlist length: {total_playlist_len:.1f}s")

# Repeat the shuffled playlist enough times to cover DURATION with a safety
# margin, so -shortest never truncates the video early due to running out
# of audio.
SAFETY_MARGIN = 1.15  # 15% extra buffer
repeats_needed = max(1, int((DURATION * SAFETY_MARGIN) // total_playlist_len) + 1)
print(f"Playlist repeats: {repeats_needed}")

concat_path = TMP / f"concat_{image_path.stem}.txt"
with open(concat_path, "w") as f:
    for _ in range(repeats_needed):
        for s in songs:
            f.write(f"file '{s.resolve()}'\n")

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
            mb = size / (1024 * 1024)
            gb = size / (1024 * 1024 * 1024)
            print(f"[SIZE] {output_path.name} -> {mb:.1f} MB ({gb:.3f} GB)", flush=True)
            if size >= MAX_SIZE_BYTES:
                print("[SIZE] Hit 1.99 GB cap - stopping FFmpeg cleanly (SIGINT).", flush=True)
                stopped_by_watcher = True
                # SIGINT lets ffmpeg finish writing the moov atom / trailer
                # cleanly, unlike SIGTERM which can leave the mp4 corrupt.
                proc.send_signal(signal.SIGINT)
                try:
                    proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    print("[SIZE] FFmpeg didn't exit after SIGINT - forcing terminate.", flush=True)
                    proc.terminate()
                break


watcher = threading.Thread(target=size_watcher, daemon=True)
watcher.start()
proc.wait()
watcher.join()

if proc.returncode not in (0, -2, -15):  # -2 = SIGINT, -15 = SIGTERM
    raise SystemExit("FFmpeg failed - check output above.")

final_size = output_path.stat().st_size
final_size_mb = final_size / (1024 * 1024)
final_size_gb = final_size / (1024 * 1024 * 1024)
stop_reason = "capped at 1.99 GB by size watcher" if stopped_by_watcher else "duration reached"

if final_size < MIN_SIZE_BYTES:
    print(f"[WARN] Output is only {final_size_gb:.3f} GB - below the 1.50 GB minimum target.")

print(f"\nDONE - {output_path}")
print(f"Stop reason  : {stop_reason}")
print(f"Bitrate used : {VIDEO_KBPS}k")
print(f"Frame        : {OUT_W}x{OUT_H}")
print(f"Duration     : {DURATION}s ({DURATION // 3600}h {(DURATION % 3600) // 60}m)")
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
