"""
Driftstation - Prompt Factory
================================
Standalone channel script. Connects to a local Ollama model to brainstorm
one on-brand text-to-image prompt at a time for the "Driftstation" niche,
then automates sending it into your local image generator.

REQUIREMENTS
------------
1. Ollama running locally, with a model pulled, e.g.:
     ollama pull gemma2:2b
2. Python packages:
     pip install requests pyautogui pyperclip

USAGE
-----
  python driftstation.py --rounds 20 --cooldown 15 --x 530 --y 180

CONTROLS WHILE RUNNING
-----------------------
- Drag mouse to any screen corner -> pyautogui failsafe, aborts instantly.
- Ctrl+C in the terminal          -> stops cleanly after the current round.
"""

import sys
import time
import random
import argparse
import requests

try:
    import pyautogui
    import pyperclip
except ImportError:
    sys.exit("Missing packages. Run:\n  pip install requests pyautogui pyperclip")

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

OLLAMA_URL = "http://localhost:11434/api/generate"
DEFAULT_MODEL = "gemma2:2b"

CHANNEL_NAME = "Driftstation"

STYLE = """A calm sci-fi space station interior, or a viewport looking onto nebulas and distant stars. Soft blue/purple ambient lighting. Cozy-futuristic stillness, never sterile, cold, or tense."""

STRUCTURE = """Frame a viewport showing stars/nebula in the background, with a console, seating area, or plants in the foreground. Soft ambient glow throughout."""

PATTERNS = """Recurring motifs: abstract glowing control panels (no readable text), soft drifting light particles, a distant planet or nebula, quiet stillness."""

CONSISTENCY = """Locked palette: blue, purple, and teal, with rare warm accent lighting. Mood is always calm - never alarms, danger, or damage."""

ALTERNATIVES = """Rotate location across: observation deck, sleeping quarters, greenhouse module, quiet corridor, cockpit. Rotate the view outside between nebula, ringed planet, asteroid field, dense star field."""

SAFETY_RULES = """Hard rules, never break these:
- No real people, celebrities, or identifiable faces.
- No copyrighted characters, logos, or brands.
- No violence, gore, weapons, or disturbing imagery.
- No sexual or suggestive content.
- No text or words rendered in the image.
- Purely atmospheric, cozy, dreamlike, or scenic. Fun and safe, always.
- Output ONLY the final image prompt itself. No preamble, no quotes,
  no explanations, no "Here's a prompt:" - just the prompt text."""


def build_system_prompt() -> str:
    return (
        f"You are the visual art director for a YouTube ambience channel called "
        f'"{CHANNEL_NAME}".\n\n'
        f"STYLE (the channel's core identity):\n{STYLE}\n\n"
        f"STRUCTURE (how every shot should be composed):\n{STRUCTURE}\n\n"
        f"RECURRING PATTERNS (motifs to draw from, not all at once):\n{PATTERNS}\n\n"
        f"CONSISTENCY RULES (never break these, they define the channel's brand):\n{CONSISTENCY}\n\n"
        f"ALTERNATIVES (rotate through these for variety across prompts):\n{ALTERNATIVES}\n\n"
        f"{SAFETY_RULES}"
    )


def ask_ollama(history: list, model: str) -> str:
    system_prompt = build_system_prompt()

    avoid_note = ""
    if history:
        recent = history[-8:]
        avoid_note = (
            "\n\nDo not repeat or closely resemble any of these recent prompts:\n"
            + "\n".join(f"- {p}" for p in recent)
        )

    user_msg = (
        "Generate one single new text-to-image prompt for this channel. "
        "Pick ONE combination from the structure/patterns/alternatives above - "
        "do not try to include everything at once. Be visually specific about "
        "lighting, composition, and color. One to three sentences max."
        + avoid_note
    )

    payload = {
        "model": model,
        "prompt": user_msg,
        "system": system_prompt,
        "stream": False,
        "options": {"temperature": 0.9},
    }

    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        sys.exit(f"[FATAL] Could not reach Ollama at {OLLAMA_URL}. Run: ollama serve")

    text = resp.json().get("response", "").strip()
    return text.strip('"').strip()


def send_prompt(prompt: str, x: int, y: int, fast_type: bool):
    pyautogui.click(x, y)
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)

    if fast_type:
        pyautogui.write(prompt, interval=0.005)
    else:
        pyperclip.copy(prompt)
        time.sleep(0.05)
        pyautogui.hotkey("ctrl", "v")

    time.sleep(0.1)
    pyautogui.press("tab")
    pyautogui.press("tab")
    pyautogui.press("tab")
    pyautogui.press("enter")


def main():
    parser = argparse.ArgumentParser(description=f"{CHANNEL_NAME} prompt factory")
    parser.add_argument("--x", type=int, default=602)
    parser.add_argument("--y", type=int, default=177)
    parser.add_argument("--rounds", type=int, default=10)
    parser.add_argument("--min-wait", type=float, default=3.0,
                         help="Minimum random wait (seconds) between rounds")
    parser.add_argument("--max-wait", type=float, default=8.0,
                         help="Maximum random wait (seconds) between rounds")
    parser.add_argument("--fast-type", action="store_true")
    parser.add_argument("--start-delay", type=float, default=5.0)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()

    print(f"[INFO] Channel: {CHANNEL_NAME}")
    print(f"[INFO] Model: {args.model}")
    print(f"[INFO] Click target: ({args.x}, {args.y})")
    print(f"[INFO] Mode: {'fast typing' if args.fast_type else 'clipboard paste'}")
    print(f"[INFO] Wait between rounds: random {args.min_wait}-{args.max_wait}s")
    print(f"[INFO] Starting in {args.start_delay}s - switch to your generator window now.")
    time.sleep(args.start_delay)

    history = []
    for i in range(1, args.rounds + 1):
        print(f"\n=== Round {i}/{args.rounds} ===")
        prompt = ask_ollama(history, args.model)
        if not prompt:
            print("[WARN] Empty response, skipping.")
            continue

        print(f"[PROMPT] {prompt}")
        history.append(prompt)

        try:
            send_prompt(prompt, args.x, args.y, args.fast_type)
        except pyautogui.FailSafeException:
            print("[STOPPED] Failsafe triggered.")
            break

        if i < args.rounds:
            wait = round(random.uniform(args.min_wait, args.max_wait), 1)
            print(f"[WAIT] Cooling down {wait}s...")
            time.sleep(wait)

    print("\n[DONE] Factory run complete.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[STOPPED] Interrupted by user (Ctrl+C).")
