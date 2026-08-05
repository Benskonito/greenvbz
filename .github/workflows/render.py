import os
import random
import signal
import subprocess
import threading
import time
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
REPO_ROOT      = Path(__file__).resolve().parent
IMAGES_DIR     = REPO_ROOT / "images"
AUDIO_DIR      = REPO_ROOT / "audio"
ASSETS_DIR     = REPO_ROOT / "assets"
SUBSCRIBE_PATH = ASSETS_DIR / "subscribe.mp4"   # <-- put your subscribe-button clip here
TMP            = Path("/tmp/redsky")

OUT_W, OUT_H = 1920, 1080  # always 1080p, no matter the source image size/ratio

# ── Duration: flat random 2h - 4h ────────────────────────────────────────────
MIN_DURATION = 2 * 60 * 60   # 2h  = 7200s
MAX_DURATION = 4 * 60 * 60   # 4h  = 14400s


def pick_duration():
    return random.randint(MIN_DURATION, MAX_DURATION)


DURATION = pick_duration()

# ── File size budget: random 1.20GB - 1.90GB ─────────────────────────────────
MIN_SIZE_BYTES    = int(1.20 * 1024 ** 3)
MAX_SIZE_BYTES    = int(1.90 * 1024 ** 3)
TARGET_SIZE_BYTES = random.randint(int(1.25 * 1024 ** 3), int(1.85 * 1024 ** 3))
AUDIO_BITRATE_K   = 128
VIDEO_KBPS        = int((TARGET_SIZE_BYTES * 8) / DURATION / 1000) - AUDIO_BITRATE_K
VIDEO_KBPS        = max(VIDEO_KBPS, 800)  # floor so quality never tanks

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
print(f">>> TARGET SIZE  : {TARGET_SIZE_BYTES / 1e9:.2f} GB (range 1.20-1.90 GB)")
print(f">>> VIDEO BITRATE: {VIDEO_KBPS}k\n")

songs = sorted(AUDIO_DIR.glob("*.mp3"))
if not songs:
    raise SystemExit(f"No songs found in {AUDIO_DIR}!")
random.shuffle(songs)
print("Song order:")
for i, s in enumerate(songs):
    print(f"  {i + 1}. {s.name}")


def probe_duration(path: Path) -> float:
    """Get real duration of a media file via ffprobe, instead of guessing."""
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

SAFETY_MARGIN = 1.20
repeats_needed = max(1, int((DURATION * SAFETY_MARGIN) // total_playlist_len) + 1)
print(f"Playlist repeats: {repeats_needed}")

AUDIO_FADE_SECONDS = 5
fade_start = max(0, DURATION - AUDIO_FADE_SECONDS)

concat_path = TMP / f"concat_{image_path.stem}.txt"
with open(concat_path, "w") as f:
    for _ in range(repeats_needed):
        for s in songs:
            f.write(f"file '{s.resolve()}'\n")

# ── Subscribe-button overlay scheduling ──────────────────────────────────────
SUB_MIN_GAP   = 4 * 60
SUB_MAX_GAP   = 7 * 60
SUB_WIDTH     = 340

have_subscribe = SUBSCRIBE_PATH.exists()
sub_duration = probe_duration(SUBSCRIBE_PATH) if have_subscribe else 0.0

windows = []
if have_subscribe and sub_duration > 0:
    t = random.randint(SUB_MIN_GAP, SUB_MAX_GAP)
    while t + sub_duration < DURATION - 5:
        windows.append({
            "start": t,
            "end": t + sub_duration,
            "corner": random.choice(["left", "right"]),
            "margin": random.randint(30, 70),
        })
        t += sub_duration + random.randint(SUB_MIN_GAP, SUB_MAX_GAP)
    print(f"\n>>> SUBSCRIBE OVERLAY: {len(windows)} appearances scheduled")
    for w in windows:
        mins = w["start"] // 60
        secs = w["start"] % 60
        print(f"    - at {mins}m{secs:02d}s ({w['corner']}, margin {w['margin']}px)")
else:
    print(f"\n>>> SUBSCRIBE OVERLAY: skipped (no file at {SUBSCRIBE_PATH})")


def build_overlay_exprs(windows):
    if not windows:
        return None, None, None
    x_chain = "0"
    y_chain = "0"
    enable_terms = []
    for w in reversed(windows):
        cond = f"between(t,{w['start']},{w['end']})"
        if w["corner"] == "right":
            xw = f"(W-w-{w['margin']}+8*sin(2*PI*t/2.3))"
        else:
            xw = f"({w['margin']}+8*sin(2*PI*t/2.3))"
        yw = f"(H-h-{w['margin']}+6*cos(2*PI*t/1.9))"
        x_chain = f"if({cond},{xw},{x_chain})"
        y_chain = f"if({cond},{yw},{y_chain})"
        enable_terms.append(cond)
    enable_expr = f"gte({'+'.join(enable_terms)},1)"
    return x_chain, y_chain, enable_expr


x_expr, y_expr, enable_expr = build_overlay_exprs(windows)

# ── Build ffmpeg filter graph ────────────────────────────────────────────────
bg_chain = (
    f"[0:v]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
    f"crop={OUT_W}:{OUT_H},format=yuv420p[bg]"
)
audio_chain = f"[1:a]afade=t=out:st={fade_start}:d={AUDIO_FADE_SECONDS}[outa]"

cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(image_path)]
cmd += ["-f", "concat", "-safe", "0", "-i", str(concat_path)]

if windows:
    cmd += ["-stream_loop", "-1", "-i", str(SUBSCRIBE_PATH)]
    filter_complex = (
        f"{bg_chain};"
        f"[2:v]scale={SUB_WIDTH}:-2,format=rgba,setsar=1[sub];"
        f"[bg][sub]overlay=x='{x_expr}':y='{y_expr}':enable='{enable_expr}':eval=frame[outv];"
        f"{audio_chain}"
    )
else:
    filter_complex = f"{bg_chain};[bg]copy[outv];{audio_chain}"

cmd += [
    "-t", str(DURATION),
    "-filter_complex", filter_complex,
    "-map", "[outv]",
    "-map", "[outa]",
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
                print("[SIZE] Hit 1.90 GB cap - stopping FFmpeg cleanly (SIGINT).", flush=True)
                stopped_by_watcher = True
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

if proc.returncode not in (0, -2, -15):
    raise SystemExit("FFmpeg failed - check output above.")

final_size = output_path.stat().st_size
final_size_mb = final_size / (1024 * 1024)
final_size_gb = final_size / (1024 * 1024 * 1024)
stop_reason = "capped at 1.90 GB by size watcher" if stopped_by_watcher else "duration reached"

if final_size < MIN_SIZE_BYTES:
    print(f"[WARN] Output is only {final_size_gb:.3f} GB - below the 1.20 GB minimum target.")

print(f"\nDONE - {output_path}")
print(f"Stop reason  : {stop_reason}")
print(f"Bitrate used : {VIDEO_KBPS}k")
print(f"Frame        : {OUT_W}x{OUT_H}")
print(f"Duration     : {DURATION}s ({DURATION // 3600}h {(DURATION % 3600) // 60}m)")
print(f"Size         : {final_size_mb:.1f} MB ({final_size_gb:.3f} GB)")
print(f"Image        : {image_path.name}")
print(f"Subscribe overlay appearances: {len(windows)}")

github_output = os.environ.get("GITHUB_OUTPUT")
if github_output:
    with open(github_output, "a") as f:
        f.write(f"output_path={output_path}\n")
        f.write(f"image_name={image_path.name}\n")
        f.write(f"duration_seconds={DURATION}\n")
        f.write(f"final_size_mb={final_size_mb:.1f}\n")
        f.write(f"video_kbps={VIDEO_KBPS}\n")
        f.write(f"subscribe_appearances={len(windows)}\n")
