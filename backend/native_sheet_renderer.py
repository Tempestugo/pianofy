import os
import re
import time
import subprocess
import xml.etree.ElementTree as ET
import numpy as np
import cv2
import verovio
import resvg_py

def render_ethereal_score_video(
    musicxml_path: str,
    audio_path: str,
    output_video_path: str,
    fps: int = 60,
    width: int = 1920,
    height: int = 1080,
    zoom: float = 1.85,
    progress_callback = None
) -> dict:
    """
    Renders an ethereal, cinematic 2D sheet music video directly using:
    - Verovio (C++ engraving for classical notation typography)
    - resvg-py (ultra-fast Rust SVG rasterizer)
    - OpenCV (agogic tension curves, red cloud nebula, camera tracking, progressive note reveal, and additive bloom)
    - FFmpeg (high-performance deterministic rawvideo pipe with synchronized audio)
    """
    if not os.path.exists(musicxml_path):
        return {"status": "error", "error": f"MusicXML not found at {musicxml_path}"}

    # 1. Initialize Verovio Toolkit
    tk = verovio.toolkit()
    res_path = os.path.join(os.path.dirname(verovio.__file__), "data")
    if os.path.exists(res_path):
        tk.setResourcePath(res_path)

    verovio_opts = {
        "pageWidth": 2200,
        "pageHeight": 1200,
        "scale": 55,
        "adjustPageHeight": False
    }
    tk.setOptions(verovio_opts)
    tk.loadFile(musicxml_path)

    total_pages = tk.getPageCount()
    timemap = tk.renderToTimemap()
    if not timemap:
        return {"status": "error", "error": "Verovio could not extract timemap from MusicXML"}

    # 2. Extract total audio duration and note intervals
    max_tstamp_ms = max((entry.get("tstamp", 0) for entry in timemap), default=10000)
    total_duration_sec = (max_tstamp_ms / 1000.0) + 2.5
    total_frames = max(60, int(fps * total_duration_sec))

    note_intervals = {}
    all_onsets = []
    for entry in timemap:
        t = entry.get("tstamp", 0)
        if "on" in entry:
            for nid in entry["on"]:
                all_onsets.append(t)
                if nid not in note_intervals:
                    note_intervals[nid] = [t, t + 450] # default 450ms sustain
        if "off" in entry:
            for nid in entry["off"]:
                if nid in note_intervals:
                    note_intervals[nid][1] = t

    all_onsets.sort()

    # 3. Calculate Adaptive Musical Agogics & Tension Curve
    densities = np.zeros(total_frames, dtype=np.float32)
    for f in range(total_frames):
        t_ms = (f / float(fps)) * 1000.0
        # Onset density in a 400ms window centered on current time
        densities[f] = sum(1 for o in all_onsets if abs(o - t_ms) <= 400)

    # Adaptive dynamic range normalization using 10th and 90th percentiles
    p10 = float(np.percentile(densities, 10))
    p90 = float(np.percentile(densities, 90))
    span = max(1.0, p90 - p10)

    # Power curve so calm passages stay deep/dark, and high density flares into tension
    norm_density = np.clip((densities - p10) / span, 0.0, 1.0) ** 1.6

    # Smooth with a temporal Gaussian-like kernel (approx 0.5s)
    k_size = int(fps * 0.5) | 1
    kernel = np.ones(k_size, dtype=np.float32) / k_size
    tension_curve = np.convolve(norm_density, kernel, mode="same")
    tension_curve = np.clip(tension_curve, 0.0, 1.0)

    # 4. Typography and Two-Layer Styles
    style_full = """
    <style>
      path, polygon, rect { fill: #f0eae1 !important; stroke: #f0eae1 !important; }
      .notehead path, .notehead use { fill: #ffffff !important; }
      .staff path { stroke: #d0c4b4 !important; }
      .barLine path { stroke: #998a78 !important; }
      text, tspan { fill: #f0eae1 !important; stroke: none !important; }
      .tempo text, .tempo tspan { fill: #ffd67a !important; font-weight: bold; }
      .mNum text, .mNum tspan { fill: #b8a898 !important; }
      .pgHead, .pgFoot, .footer { display: none !important; }
    </style>
    """

    style_staves = """
    <style>
      path, polygon, rect { fill: #f0eae1 !important; stroke: #f0eae1 !important; }
      .staff path { stroke: #d0c4b4 !important; }
      .barLine path { stroke: #998a78 !important; }
      text, tspan { fill: #f0eae1 !important; stroke: none !important; }
      .tempo text, .tempo tspan { fill: #ffd67a !important; font-weight: bold; }
      .mNum text, .mNum tspan { fill: #b8a898 !important; }
      .pgHead, .pgFoot, .footer { display: none !important; }
      .note, .chord, .beam, .stem, .accid, .flag, .rest, .ledgerLines { display: none !important; }
    </style>
    """

    # 5. Rasterize each page in dual layers and extract systems
    pages_data = []
    for p_num in range(1, total_pages + 1):
        raw_svg = tk.renderToSVG(p_num)
        raw_svg = raw_svg.replace("\ueca5", chr(0x2669))

        svg_full = raw_svg.replace("</svg>", f"{style_full}</svg>")
        png_full = resvg_py.svg_to_bytes(svg_full)
        img_full = cv2.imdecode(np.frombuffer(png_full, np.uint8), cv2.IMREAD_UNCHANGED)

        svg_staves = raw_svg.replace("</svg>", f"{style_staves}</svg>")
        png_staves = resvg_py.svg_to_bytes(svg_staves)
        img_staves = cv2.imdecode(np.frombuffer(png_staves, np.uint8), cv2.IMREAD_UNCHANGED)

        img_h, img_w, _ = img_full.shape
        vb_m = re.search(r'viewBox="([^"]+)"', raw_svg)
        if vb_m:
            vb_parts = vb_m.group(1).split()
            vb_w, vb_h = float(vb_parts[2]), float(vb_parts[3])
        else:
            vb_w, vb_h = img_w, img_h

        scale_x = img_w / vb_w
        scale_y = img_h / vb_h

        root = ET.fromstring(raw_svg)
        margin_x, margin_y = 0.0, 0.0
        for g in root.iter("{http://www.w3.org/2000/svg}g"):
            if g.get("class") == "page-margin":
                m = re.search(r"translate\((\d+),\s*(\d+)\)", g.get("transform", ""))
                if m:
                    margin_x, margin_y = float(m.group(1)), float(m.group(2))
                    break

        page_notes = []
        page_systems = []
        for g in root.iter("{http://www.w3.org/2000/svg}g"):
            if g.get("class") == "system":
                sys_id = g.get("id")
                sys_notes = []
                for n in g.iter("{http://www.w3.org/2000/svg}g"):
                    if n.get("class") == "note":
                        nid = n.get("id")
                        for u in n.iter("{http://www.w3.org/2000/svg}use"):
                            m = re.search(r"translate\((\d+),\s*(\d+)\)", u.get("transform", ""))
                            if m and nid in note_intervals:
                                on_ms, off_ms = note_intervals[nid]
                                nx = (margin_x + float(m.group(1))) * scale_x
                                ny = (margin_y + float(m.group(2))) * scale_y
                                note_obj = {
                                    "id": nid,
                                    "x": nx,
                                    "y": ny,
                                    "on_ms": on_ms,
                                    "off_ms": off_ms
                                }
                                sys_notes.append(note_obj)
                                page_notes.append(note_obj)

                if sys_notes:
                    min_y = min(n["y"] for n in sys_notes)
                    max_y = max(n["y"] for n in sys_notes)
                    min_x = min(n["x"] for n in sys_notes)
                    max_x = max(n["x"] for n in sys_notes)
                    start_ms = min(n["on_ms"] for n in sys_notes)
                    end_ms = max(n["off_ms"] for n in sys_notes)
                    page_systems.append({
                        "id": sys_id,
                        "notes": sys_notes,
                        "min_x": min_x,
                        "max_x": max_x,
                        "min_y": min_y,
                        "max_y": max_y,
                        "top_y": max(0, int(min_y - 45)),
                        "bottom_y": min(img_h, int(max_y + 45)),
                        "center_y": (min_y + max_y) / 2.0,
                        "start_ms": start_ms,
                        "end_ms": end_ms
                    })

        p_start_ms = min((n["on_ms"] for n in page_notes), default=0.0)
        p_end_ms = max((n["off_ms"] for n in page_notes), default=999999.0)

        pages_data.append({
            "page_num": p_num,
            "img_full": img_full,
            "img_staves": img_staves,
            "img_w": img_w,
            "img_h": img_h,
            "notes": page_notes,
            "systems": page_systems,
            "start_ms": p_start_ms,
            "end_ms": p_end_ms
        })

    # Adjust page transition boundaries
    for i in range(len(pages_data)):
        pages_data[i]["active_start_ms"] = 0.0 if i == 0 else pages_data[i]["start_ms"] - 400.0
        pages_data[i]["active_end_ms"] = (
            pages_data[i+1]["start_ms"] - 400.0 if i < len(pages_data) - 1 else total_duration_sec * 1000.0
        )

    # 6. Precompute Noise Mesh for Red Clouds
    gw, gh = 160, 90
    gx = np.linspace(0, 4.0, gw, dtype=np.float32)
    gy = np.linspace(0, 2.25, gh, dtype=np.float32)
    grid_X, grid_Y = np.meshgrid(gx, gy)

    # 7. Generate Pre-rendered Bloom Glow Sprite
    glow_r = int(38 * (zoom / 1.38))
    glow_sprite = np.zeros((glow_r * 2, glow_r * 2, 3), dtype=np.float32)
    for dy in range(-glow_r, glow_r):
        for dx in range(-glow_r, glow_r):
            dist = np.sqrt(dx*dx + dy*dy)
            if dist < glow_r:
                decay = (1.0 - (dist / glow_r)) ** 2.2
                # Golden amber/ruby glow aura
                glow_sprite[dy + glow_r, dx + glow_r] = [45.0 * decay, 140.0 * decay, 255.0 * decay]

    # 8. 2D Elliptical Spotlight Mask & Vignette
    screen_target_x = width * 0.38
    screen_target_y = height * 0.50
    y_coords, x_coords = np.mgrid[0:height, 0:width].astype(np.float32)
    dx = (x_coords - screen_target_x) / (width * 0.48)
    dy = (y_coords - screen_target_y) / (height * 0.52)
    dist_sq = dx * dx + dy * dy
    spotlight_raw = np.clip(1.0 - (dist_sq ** 1.15), 0.0, 1.0)
    spotlight_score = np.clip(spotlight_raw * 1.55, 0.0, 1.0)[:, :, np.newaxis]
    bg_vignette = np.clip(1.0 - (dist_sq ** 1.1) * 1.15, 0.0, 1.0)

    # 9. Setup FFmpeg Process
    os.makedirs(os.path.dirname(os.path.abspath(output_video_path)), exist_ok=True)
    temp_raw_video = output_video_path + ".temp_raw.mp4"

    ffmpeg_video_cmd = [
        "ffmpeg", "-y",
        "-f", "rawvideo",
        "-vcodec", "rawvideo",
        "-s", f"{width}x{height}",
        "-pix_fmt", "bgr24",
        "-r", str(fps),
        "-i", "-",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "veryfast",
        "-crf", "18",
        temp_raw_video
    ]

    pipe = subprocess.Popen(ffmpeg_video_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    t_start = time.time()
    last_report_time = 0.0

    # Camera tracking state
    cam_x = 0.0
    cam_y = 0.0
    cam_inited = False
    current_page_idx = -1

    # 10. Frame Rendering Loop
    for frame_idx in range(total_frames):
        current_time_ms = (frame_idx / float(fps)) * 1000.0
        time_sec = frame_idx / float(fps)
        cur_tension = float(tension_curve[frame_idx])

        # Determine current active page
        current_page = pages_data[0]
        p_idx = 0
        for idx, p_data in enumerate(pages_data):
            if p_data["active_start_ms"] <= current_time_ms <= p_data["active_end_ms"]:
                current_page = p_data
                p_idx = idx
                break

        # Handle page transitions for camera
        if p_idx != current_page_idx:
            current_page_idx = p_idx
            if current_page["systems"]:
                cam_x = current_page["systems"][0]["min_x"] - 50.0
                cam_y = current_page["systems"][0]["center_y"]
                cam_inited = True

        # Find active system on current page
        active_sys = current_page["systems"][0] if current_page["systems"] else None
        for s in current_page["systems"]:
            if s["start_ms"] <= current_time_ms <= s["end_ms"]:
                active_sys = s
                break
            elif current_time_ms > s["end_ms"]:
                active_sys = s

        if not active_sys:
            continue

        # Active notes on current system
        active_notes = [
            n for n in active_sys["notes"]
            if n["on_ms"] <= current_time_ms <= n["off_ms"]
        ]

        # Calculate playhead_x for active system
        if active_notes:
            playhead_x = float(np.mean([n["x"] for n in active_notes]))
        else:
            past_notes = [n for n in active_sys["notes"] if n["on_ms"] <= current_time_ms]
            future_notes = [n for n in active_sys["notes"] if n["on_ms"] > current_time_ms]
            if past_notes and future_notes:
                p_prev, p_next = past_notes[-1], future_notes[0]
                interp = (current_time_ms - p_prev["on_ms"]) / max(1.0, (p_next["on_ms"] - p_prev["on_ms"]))
                playhead_x = float(p_prev["x"] + (p_next["x"] - p_prev["x"]) * np.clip(interp, 0.0, 1.0))
            elif past_notes:
                playhead_x = float(past_notes[-1]["x"])
            else:
                playhead_x = float(active_sys["min_x"])

        # Progressive note reveal (invisible line, notes appear at playhead)
        page_comp = current_page["img_staves"].copy()
        feather = 16
        for s in current_page["systems"]:
            top = s["top_y"]
            bot = s["bottom_y"]
            if current_time_ms >= s["end_ms"]:
                # System completely finished: full reveal
                page_comp[top:bot, :] = current_page["img_full"][top:bot, :]
            elif current_time_ms >= s["start_ms"]:
                # Active system: reveal up to playhead_x with soft organic edge
                px = int(playhead_x + 6)
                page_comp[top:bot, :max(0, px - feather)] = current_page["img_full"][top:bot, :max(0, px - feather)]
                for f_i in range(feather):
                    col = px - feather + f_i
                    if 0 <= col < current_page["img_w"]:
                        a = f_i / float(feather)
                        page_comp[top:bot, col] = (
                            current_page["img_full"][top:bot, col] * a +
                            current_page["img_staves"][top:bot, col] * (1.0 - a)
                        ).astype(np.uint8)

        # Smooth camera tracking
        target_cam_x = playhead_x
        target_cam_y = active_sys["center_y"]

        if not cam_inited:
            cam_x = target_cam_x
            cam_y = target_cam_y
            cam_inited = True
        else:
            if abs(target_cam_x - cam_x) > 700:
                cam_x = target_cam_x - 100.0
            else:
                cam_x += (target_cam_x - cam_x) * 0.08
            cam_y += (target_cam_y - cam_y) * 0.06

        # Camera shake driven by tension
        shake_x, shake_y = 0.0, 0.0
        if cur_tension > 0.22:
            shake_mag = (cur_tension ** 1.8) * 8.0
            shake_x = np.sin(frame_idx * 1.7) * shake_mag + np.cos(frame_idx * 3.1) * (shake_mag * 0.4)
            shake_y = np.cos(frame_idx * 2.1) * shake_mag + np.sin(frame_idx * 4.3) * (shake_mag * 0.3)

        effective_cam_x = cam_x + shake_x
        effective_cam_y = cam_y + shake_y

        # Affine transform for camera viewport onto 1080p
        M = np.array([
            [zoom, 0, screen_target_x - effective_cam_x * zoom],
            [0, zoom, screen_target_y - effective_cam_y * zoom]
        ], dtype=np.float32)

        warped = cv2.warpAffine(
            page_comp, M, (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0)
        )

        # High tension motion blur / optical bloom
        if cur_tension > 0.60:
            blur_amount = 1 + int((cur_tension - 0.60) * 8)
            if blur_amount > 1:
                blurred_score = cv2.blur(warped, (blur_amount, 1))
                warped = cv2.addWeighted(warped, 0.65, blurred_score, 0.35, 0)

        # Procedural Red Clouds (Crimson Nebula) background driven by agogics
        # Drifting multi-octave noise
        n1 = np.sin(grid_X * 1.5 + time_sec * 0.45) * np.cos(grid_Y * 1.8 - time_sec * 0.35)
        n2 = np.sin(grid_X * 3.2 - time_sec * 0.75 + n1 * 0.8) * np.cos(grid_Y * 3.5 + time_sec * 0.55)
        n3 = np.sin(grid_X * 6.1 + time_sec * 1.1) * np.cos(grid_Y * 6.2 - time_sec * 0.9)
        cloud_raw = np.clip((n1 * 0.5 + n2 * 0.35 + n3 * 0.15 + 1.0) * 0.5, 0.0, 1.0)
        cloud_hd = cv2.resize(cloud_raw.astype(np.float32), (width, height), interpolation=cv2.INTER_CUBIC)

        # Modulate color by tension: pure dark when calm -> deep wine -> radiant crimson when tense
        cloud_power = float(np.power(cur_tension, 1.45))
        if cloud_power < 0.02:
            # Pure dark stage background when calm
            bg_frame = np.zeros((height, width, 3), dtype=np.uint8)
        else:
            # Cinematic wine-crimson nebula clouds (calibrated to reference video)
            cloud_r = np.clip((cloud_hd * 85.0 + 35.0) * cloud_power, 0, 255)
            cloud_g = np.clip((cloud_hd * 22.0 + 2.0) * (cloud_power ** 1.2), 0, 255)
            cloud_b = np.clip((cloud_hd * 36.0 + 4.0) * (cloud_power ** 1.2), 0, 255)

            # Peripheral vignette keeping screen edges deep, cinematic and focused
            cloud_r = (cloud_r * bg_vignette).astype(np.uint8)
            cloud_g = (cloud_g * bg_vignette).astype(np.uint8)
            cloud_b = (cloud_b * bg_vignette).astype(np.uint8)
            bg_frame = cv2.merge([cloud_b, cloud_g, cloud_r])

        # Composite score with 2D spotlight vignette
        s_rgb = warped[:, :, :3].astype(np.float32)
        s_alpha = (warped[:, :, 3] / 255.0)[:, :, np.newaxis]
        # Apply 2D elliptical spotlight mask
        s_alpha = s_alpha * spotlight_score

        frame = np.clip(s_rgb * s_alpha + bg_frame.astype(np.float32) * (1.0 - s_alpha), 0, 255).astype(np.uint8)

        # Draw glowing bloom aura on active notes (luminosity boosted by agogics)
        bloom_boost = 1.3 + (cur_tension * 1.7)
        for n in active_notes:
            scr_nx = int((n["x"] - effective_cam_x) * zoom + screen_target_x)
            scr_ny = int((n["y"] - effective_cam_y) * zoom + screen_target_y)

            if scr_nx < -glow_r or scr_nx > width + glow_r or scr_ny < -glow_r or scr_ny > height + glow_r:
                continue

            x1 = max(0, scr_nx - glow_r)
            x2 = min(width, scr_nx + glow_r)
            y1 = max(0, scr_ny - glow_r)
            y2 = min(height, scr_ny + glow_r)

            if x1 >= x2 or y1 >= y2:
                continue

            sp_x1 = x1 - (scr_nx - glow_r)
            sp_x2 = sp_x1 + (x2 - x1)
            sp_y1 = y1 - (scr_ny - glow_r)
            sp_y2 = sp_y1 + (y2 - y1)

            glow_crop = glow_sprite[sp_y1:sp_y2, sp_x1:sp_x2]
            sub_frame = frame[y1:y2, x1:x2].astype(np.float32)
            frame[y1:y2, x1:x2] = np.clip(sub_frame + glow_crop * bloom_boost, 0, 255).astype(np.uint8)

            # Hot-white glowing core on notehead
            if 0 <= scr_nx < width and 0 <= scr_ny < height:
                core_r = int(5 + cur_tension * 3)
                cv2.circle(frame, (scr_nx, scr_ny), core_r, (255, 255, 255), -1, lineType=cv2.LINE_AA)

        pipe.stdin.write(frame.tobytes())

        # Progress reporting
        now = time.time()
        if progress_callback and (now - last_report_time >= 0.5 or frame_idx == total_frames - 1):
            pct = int((frame_idx + 1) / total_frames * 100)
            progress_callback(pct, frame_idx + 1, total_frames)
            last_report_time = now

    pipe.stdin.close()
    pipe.wait()

    # 11. Mux with Audio if provided
    has_audio = audio_path and os.path.exists(audio_path)
    if has_audio:
        ffmpeg_mux_cmd = [
            "ffmpeg", "-y",
            "-i", temp_raw_video,
            "-i", audio_path,
            "-c:v", "copy",
            "-c:a", "aac",
            "-shortest",
            output_video_path
        ]
        mux_proc = subprocess.run(ffmpeg_mux_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if mux_proc.returncode == 0 and os.path.exists(output_video_path):
            try: os.remove(temp_raw_video)
            except OSError: pass
        else:
            os.replace(temp_raw_video, output_video_path)
    else:
        os.replace(temp_raw_video, output_video_path)

    total_time = time.time() - t_start
    fps_achieved = round(total_frames / max(1.0, total_time), 1)

    return {
        "status": "ok",
        "output_video": output_video_path,
        "total_frames": total_frames,
        "total_time_sec": round(total_time, 2),
        "fps_achieved": fps_achieved
    }
