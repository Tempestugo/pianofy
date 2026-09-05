import os
import sys
import time
import requests
import json

BASE_URL = "http://127.0.0.1:8000"
AUDIO_FILE = os.path.abspath("test_sample_15s.mp3")

def run_test():
    print("=== PIANOFY E2E VALIDATION: MP3 INJECTION & NATIVE VIDEO RENDER ===")
    if not os.path.exists(AUDIO_FILE):
        print(f"Error: audio file {AUDIO_FILE} not found!")
        sys.exit(1)
    
    print(f"1. Injecting MP3: {AUDIO_FILE} (size: {os.path.getsize(AUDIO_FILE)} bytes)...")
    with open(AUDIO_FILE, "rb") as f:
        files = {"file": ("test_sample_15s.mp3", f, "audio/mpeg")}
        data = {
            "confidence_threshold": 0.45,
            "min_duration_ms": 30.0,
            "quantize_grid": 0.25,
            "auto_calibrate": "true",
            "time_signature": "auto",
            "bpm": "auto",
            "split_point": 60,
            "filter_slips": "true",
            "allow_triplets": "false"
        }
        res = requests.post(f"{BASE_URL}/api/transcribe", files=files, data=data)
    
    if res.status_code != 200:
        print(f"Failed to submit transcribe request: {res.status_code} {res.text}")
        sys.exit(1)
        
    task_info = res.json()
    task_id = task_info["task_id"]
    print(f"   Task created successfully: {task_id}")
    
    print("\n2. Polling transcription progress...")
    start_time = time.time()
    while True:
        status_res = requests.get(f"{BASE_URL}/api/tasks/{task_id}")
        if status_res.status_code != 200:
            print(f"Error fetching task status: {status_res.status_code}")
            time.sleep(1)
            continue
            
        task = status_res.json()
        st = task.get("status")
        pct = task.get("progress", 0)
        msg = task.get("message", "")
        print(f"   [{int(time.time() - start_time)}s] Status: {st} | Progress: {pct}% | {msg}")
        
        if st == "SUCCESS":
            print("   Transcription SUCCESS!")
            break
        elif st == "FAILED":
            print(f"   Transcription FAILED: {msg}")
            sys.exit(1)
        time.sleep(2)
        
    print(f"\n3. Triggering Native 2D Ethereal Video Rendering for task {task_id}...")
    render_res = requests.post(f"{BASE_URL}/api/render-native-video/{task_id}")
    print(f"   Trigger response: {render_res.status_code} {render_res.text}")
    
    print("\n4. Polling Native Video Render status...")
    v_start_time = time.time()
    while True:
        v_status_res = requests.get(f"{BASE_URL}/api/tasks/{task_id}/native-video-status")
        if v_status_res.status_code != 200:
            print(f"Error fetching video render status: {v_status_res.status_code}")
            time.sleep(1)
            continue
            
        v_status = v_status_res.json()
        v_st = v_status.get("status")
        v_pct = v_status.get("progress", 0)
        v_msg = v_status.get("message", "")
        print(f"   [{int(time.time() - v_start_time)}s] Video Status: {v_st} | Progress: {v_pct}% | {v_msg}")
        
        if v_st == "SUCCESS":
            print(f"\n   >>> VIDEO RENDER SUCCESS! Video URL: {v_status.get('video_url')}")
            break
        elif v_st == "FAILED":
            print(f"\n   >>> VIDEO RENDER FAILED: {v_msg}")
            sys.exit(1)
            
        time.sleep(1.5)
        
    expected_video_path = os.path.join("backend", "outputs", f"{task_id}_native_2d.mp4")
    if os.path.exists(expected_video_path):
        size_mb = os.path.getsize(expected_video_path) / (1024 * 1024)
        print(f"\n5. Video file verified on disk: {expected_video_path} ({size_mb:.2f} MB)")
    else:
        print(f"\n5. Video file missing at {expected_video_path}!")
        sys.exit(1)
        
    print("\n=== E2E PIPELINE PASSED WITH FLYING COLORS ===")

if __name__ == "__main__":
    run_test()
