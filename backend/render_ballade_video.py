import time
import sys
import os
from native_sheet_renderer import render_ethereal_score_video

def main():
    musicxml_path = "chopin_ballade_1.musicxml"
    audio_path = sys.argv[4] if len(sys.argv) > 4 else "backend/outputs/chopin_synced_audio.wav"
    max_dur = float(sys.argv[1]) if len(sys.argv) > 1 else 60.0
    fps = int(sys.argv[2]) if len(sys.argv) > 2 else 60
    output_video_path = sys.argv[3] if len(sys.argv) > 3 else "backend/outputs/chopin_ballade_master_60fps.mp4"

    print(f"--- Starting Chopin Ballade No. 1 Render ---", flush=True)
    print(f"MusicXML: {musicxml_path}", flush=True)
    print(f"Audio: {audio_path}", flush=True)
    print(f"Output Video: {output_video_path}", flush=True)
    print(f"Duration: {max_dur}s @ {fps} FPS", flush=True)

    last_pct = -1
    def on_progress(pct, frame_idx, total_frames):
        nonlocal last_pct
        if pct != last_pct and pct % 5 == 0:
            print(f"Rendering Progress: {pct}% ({frame_idx}/{total_frames} frames)", flush=True)
            last_pct = pct

    t0 = time.time()
    result = render_ethereal_score_video(
        musicxml_path=musicxml_path,
        audio_path=audio_path,
        output_video_path=output_video_path,
        fps=fps,
        width=1920,
        height=1080,
        zoom=1.85,
        progress_callback=on_progress,
        max_duration_sec=max_dur
    )
    t1 = time.time()

    print(f"Render completed in {t1 - t0:.2f}s!", flush=True)
    print(f"Result: {result}", flush=True)

if __name__ == "__main__":
    main()
