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
    zoom: float = 1.38,
    progress_callback = None
) -> dict:
    """
    Renders an ethereal, cinematic 2D sheet music video directly using:
    - Verovio (C++ engraving for classical notation typography)
    - resvg-py (ultra-fast Rust SVG rasterizer)
    - OpenCV (camera tracking, progressive note reveal, additive bloom shaders, and particles)
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
    for entry in timemap:
        t = entry.get("tstamp", 0)
        if "on" in entry:
            for nid in entry["on"]:
                if nid not in note_intervals:
                    note_intervals[nid] = [t, t + 450] # default 450ms sustain
        if "off" in entry:
            for nid in entry["off"]:
                if nid in note_intervals:
                    note_intervals[nid][1] = t

    # 3. Typography and Two-Layer Styles
    # Full score style (notes, text, clefs, dynamics with luminous palette)
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

    # Base staves style (hiding notes, chords, stems, beams, accidentals for progressive reveal)
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

    # 4. Rasterize each page in dual layers and extract systems
    pages_data = []
    for p_num in range(1, total_pages + 1):
        raw_svg = tk.renderToSVG(p_num)
        # Sanitize SMuFL missing notehead glyph in tempo text
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

    # 5. Generate Pre-rendered Bloom Glow Sprite
    glow_r = int(36 * zoom)
    glow_sprite = np.zeros((glow_r * 2, glow_r * 2, 3), dtype=np.float32)
    for dy in range(-glow_r, glow_r):
        for dx in range(-glow_r, glow_r):
            dist = np.sqrt(dx*dx + dy*dy)
            if dist < glow_r:
                decay = (1.0 - (dist / glow_r)) ** 2.2
                # Golden amber glow aura: B=65, G=185, R=255
                glow_sprite[dy + glow_r, dx + glow_r] = [65.0 * decay, 185.0 * decay, 255.0 * decay]

    # 6. Base Ethereal Twilight Background (Deep indigo to purple wine gradient)
    base_bg = np.zeros((height, width, 3), dtype=np.uint8)
    for r in range(height):
        t = r / float(height)
        b_val = int(24 + t * 26)
        g_val = int(8 + t * 14)
        r_val = int(12 + t * 46)
        base_bg[r, :] = (b_val, g_val, r_val)

    # 7. Ethereal Particle System (Magical floating stardust)
    np.random.seed(42)
    num_particles = 90
    particles_x = np.random.uniform(0, width, num_particles)
    particles_y = np.random.uniform(0, height, num_particles)
    particles_speed = np.random.uniform(0.3, 1.1, num_particles)
    particles_size = np.random.randint(1, 3, num_particles)
    particles_phase = np.random.uniform(0, 2 * np.pi, num_particles)

    # 8. Setup FFmpeg Process
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
    screen_target_x = width * 0.38
    screen_target_y = height * 0.50
    cam_x = 0.0
    cam_y = 0.0
    cam_inited = False
    current_page_idx = -1

    # 9. Frame Rendering Loop
    for frame_idx in range(total_frames):
        current_time_ms = (frame_idx / float(fps)) * 1000.0

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

        # Progressive note reveal compositing
        page_comp = current_page["img_staves"].copy()
        feather = 14
        for s in current_page["systems"]:
            top = s["top_y"]
            bot = s["bottom_y"]
            if current_time_ms >= s["end_ms"]:
                # System completely finished: full reveal
                page_comp[top:bot, :] = current_page["img_full"][top:bot, :]
            elif current_time_ms >= s["start_ms"]:
                # Active system: reveal up to playhead_x with soft edge
                px = int(playhead_x + 8)
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
            if abs(target_cam_x - cam_x) > 600:
                cam_x = target_cam_x - 100.0
            else:
                cam_x += (target_cam_x - cam_x) * 0.08
            cam_y += (target_cam_y - cam_y) * 0.06

        # Affine transform for camera viewport onto 1080p
        M = np.array([
            [zoom, 0, screen_target_x - cam_x * zoom],
            [0, zoom, screen_target_y - cam_y * zoom]
        ], dtype=np.float32)

        warped = cv2.warpAffine(
            page_comp, M, (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0)
        )

        # Base frame setup with floating stardust particles
        frame = base_bg.copy()
        particles_y -= particles_speed
        particles_y = np.where(particles_y < 0, height, particles_y)
        for p_i in range(num_particles):
            px = int(particles_x[p_i] + np.sin(frame_idx * 0.04 + particles_phase[p_i]) * 8)
            py = int(particles_y[p_i])
            if 0 <= px < width and 0 <= py < height:
                sz = int(particles_size[p_i])
                cv2.circle(frame, (px, py), sz, (180, 200, 255), -1, lineType=cv2.LINE_AA)

        # Composite warped score over cosmic background
        s_rgb = warped[:, :, :3]
        s_alpha = (warped[:, :, 3] / 255.0)[:, :, np.newaxis]
        frame = (s_rgb * s_alpha + frame * (1.0 - s_alpha)).astype(np.uint8)

        # Draw glowing bloom aura for each active note
        for n in active_notes:
            scr_nx = int((n["x"] - cam_x) * zoom + screen_target_x)
            scr_ny = int((n["y"] - cam_y) * zoom + screen_target_y)

            x1 = max(0, scr_nx - glow_r)
            x2 = min(width, scr_nx + glow_r)
            y1 = max(0, scr_ny - glow_r)
            y2 = min(height, scr_ny + glow_r)

            sp_x1 = x1 - (scr_nx - glow_r)
            sp_x2 = sp_x1 + (x2 - x1)
            sp_y1 = y1 - (scr_ny - glow_r)
            sp_y2 = sp_y1 + (y2 - y1)

            glow_crop = glow_sprite[sp_y1:sp_y2, sp_x1:sp_x2]
            sub_frame = frame[y1:y2, x1:x2].astype(np.float32)
            frame[y1:y2, x1:x2] = np.clip(sub_frame + glow_crop * 1.8, 0, 255).astype(np.uint8)

            # Hot-white glowing core on notehead
            cv2.circle(frame, (scr_nx, scr_ny), 5, (255, 255, 255), -1, lineType=cv2.LINE_AA)

        # Dynamic glowing playhead beam
        scr_ph_x = int((playhead_x - cam_x) * zoom + screen_target_x)
        scr_min_y = int((active_sys["min_y"] - 35 - cam_y) * zoom + screen_target_y)
        scr_max_y = int((active_sys["max_y"] + 35 - cam_y) * zoom + screen_target_y)

        if 0 <= scr_ph_x < width:
            # Outer subtle glow line
            cv2.line(
                frame,
                (scr_ph_x, max(0, scr_min_y)),
                (scr_ph_x, min(height, scr_max_y)),
                (40, 180, 255), 3,
                lineType=cv2.LINE_AA
            )
            # Inner core laser beam
            cv2.line(
                frame,
                (scr_ph_x, max(0, scr_min_y)),
                (scr_ph_x, min(height, scr_max_y)),
                (220, 250, 255), 1,
                lineType=cv2.LINE_AA
            )

        pipe.stdin.write(frame.tobytes())

        # Progress reporting
        now = time.time()
        if progress_callback and (now - last_report_time >= 0.5 or frame_idx == total_frames - 1):
            pct = int((frame_idx + 1) / total_frames * 100)
            progress_callback(pct, frame_idx + 1, total_frames)
            last_report_time = now

    pipe.stdin.close()
    pipe.wait()

    # 10. Mux with Audio if provided
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
