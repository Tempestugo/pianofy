import os
import re
import time
import subprocess
import xml.etree.ElementTree as ET
import numpy as np
import cv2
import verovio
import resvg_py
from scipy.interpolate import PchipInterpolator

def render_ethereal_score_video(
    musicxml_path: str,
    audio_path: str,
    output_video_path: str,
    fps: int = 60,
    width: int = 1920,
    height: int = 1080,
    zoom: float = 1.85,
    progress_callback = None,
    max_duration_sec: float = None,
    max_measures: int = None,
    align_to_audio_midi: str = None,
    align_to_audio_file: str = None,
    custom_note_intervals: dict = None
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
        "pageWidth": 3000,
        "pageHeight": 1600,
        "scale": 70,
        "adjustPageHeight": False
    }
    tk.setOptions(verovio_opts)
    tk.loadFile(musicxml_path)

    total_pages = tk.getPageCount()
    timemap = tk.renderToTimemap()
    if not timemap:
        return {"status": "error", "error": "Verovio could not extract timemap from MusicXML"}

    # Extract tied note ends from MEI so continuation notes don't produce phantom onsets
    mei_xml = tk.getMEI()
    root_mei = ET.fromstring(mei_xml)
    tied_end_ids = {
        t.get("endid").lstrip("#")
        for t in root_mei.iter()
        if "tie" in t.tag and t.get("endid")
    }

    # 2. Extract total audio duration and note intervals (with optional live performance alignment)
    align_target = align_to_audio_file or (audio_path if audio_path and os.path.exists(audio_path) else align_to_audio_midi)
    if custom_note_intervals is not None:
        note_intervals = custom_note_intervals
        all_onsets = sorted([v[0] for v in note_intervals.values() if v[2]])
        max_tstamp_ms = max((v[1] for v in note_intervals.values()), default=10000)
    elif align_target and os.path.exists(align_target):
        try:
            from audio_score_aligner import AudioScoreAligner
            aligner = AudioScoreAligner(musicxml_path, audio_path=align_target)
            align_res = aligner.align(
                max_measures=max_measures,
                max_duration_sec=float(max_duration_sec) if max_duration_sec is not None else None
            )
            print(f"[AudioScoreAligner] Direct alignment success: {align_res}")
            note_intervals = aligner.generate_verovio_aligned_timemap(tk)
            all_onsets = sorted([v[0] for v in note_intervals.values() if v[2]])
            max_tstamp_ms = max((v[1] for v in note_intervals.values()), default=10000)
        except Exception as e:
            print(f"[Warning] AudioScoreAligner failed: {e}. Falling back to nominal timemap.")
            align_target = None

    if custom_note_intervals is None and not (align_target and os.path.exists(align_target)):
        max_tstamp_ms = max((entry.get("tstamp", 0) for entry in timemap), default=10000)
        note_intervals = {}
        all_onsets = []
        for entry in timemap:
            t = entry.get("tstamp", 0)
            if "on" in entry:
                for nid in entry["on"]:
                    is_real_onset = (nid not in tied_end_ids)
                    if is_real_onset:
                        all_onsets.append(t)
                    if nid not in note_intervals:
                        note_intervals[nid] = [t, t + 450, is_real_onset]
            if "off" in entry:
                for nid in entry["off"]:
                    if nid in note_intervals:
                        note_intervals[nid][1] = max(note_intervals[nid][1], t)

    all_onsets.sort()

    total_duration_sec = (max_tstamp_ms / 1000.0) + 2.5
    if max_duration_sec is not None:
        total_duration_sec = min(total_duration_sec, float(max_duration_sec))
    total_frames = max(60, int(fps * total_duration_sec))

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

    # Smooth with a temporal Gaussian-like kernel (approx 2.0s) for steady, non-flickering atmosphere
    k_size = int(fps * 2.0) | 1
    kernel = np.ones(k_size, dtype=np.float32) / k_size
    tension_curve = np.convolve(norm_density, kernel, mode="same")
    tension_curve = np.clip(tension_curve, 0.0, 1.0)

    # 4. Typography and Two-Layer Styles
    style_full = """
    <style>
      path, polygon, rect, use { fill: #f0eae1 !important; stroke: #f0eae1 !important; }
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
      path, polygon, rect, use { fill: #f0eae1 !important; stroke: #f0eae1 !important; }
      .staff path { stroke: #d0c4b4 !important; }
      .barLine path { stroke: #998a78 !important; }
      text, tspan { fill: #f0eae1 !important; stroke: none !important; }
      .pgHead, .pgFoot, .footer { display: none !important; }
      .note, .chord, .beam, .stem, .accid, .flag, .rest, .ledgerLines, .slur, .tie, .dynam, .dir, .ornam, .trill, .turn, .hairpin, .tempo, .mNum { display: none !important; }
    </style>
    """

    style_markings = """
    <style>
      path, polygon, rect, use { fill: #f0eae1 !important; stroke: #f0eae1 !important; }
      text, tspan { fill: #f0eae1 !important; stroke: none !important; }
      .tempo text, .tempo tspan { fill: #ffd67a !important; font-weight: bold; }
      .pgHead, .pgFoot, .footer { display: none !important; }
      .staff, .barLine, .clef, .keySig, .meterSig, .note, .chord, .beam, .stem, .accid, .flag, .rest, .ledgerLines { display: none !important; }
    </style>
    """

    # 5. Rasterize each page in triple layers (staves, full, markings) and extract systems
    pages_data = []
    for p_num in range(1, total_pages + 1):
        raw_svg = tk.renderToSVG(p_num)
        raw_svg = raw_svg.replace("\ueca5", chr(0x2669))
        raw_svg = raw_svg.replace('visibility="hidden"', 'visibility="visible"')

        svg_full = raw_svg.replace("</svg>", f"{style_full}</svg>")
        png_full = resvg_py.svg_to_bytes(svg_full)
        img_full = cv2.imdecode(np.frombuffer(png_full, np.uint8), cv2.IMREAD_UNCHANGED)

        svg_staves = raw_svg.replace("</svg>", f"{style_staves}</svg>")
        png_staves = resvg_py.svg_to_bytes(svg_staves)
        img_staves = cv2.imdecode(np.frombuffer(png_staves, np.uint8), cv2.IMREAD_UNCHANGED)

        svg_markings = raw_svg.replace("</svg>", f"{style_markings}</svg>")
        png_markings = resvg_py.svg_to_bytes(svg_markings)
        img_markings = cv2.imdecode(np.frombuffer(png_markings, np.uint8), cv2.IMREAD_UNCHANGED)

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
                measures = [m for m in g.iter("{http://www.w3.org/2000/svg}g") if "measure" in (m.get("class") or "")]
                staves_data = {}
                sys_notes = []

                for m in measures:
                    m_staves = [s for s in m if "staff" in (s.get("class") or "")]
                    for s_idx, st_elem in enumerate(m_staves):
                        staff_key = s_idx
                        if staff_key not in staves_data:
                            staves_data[staff_key] = []
                        for n in st_elem.iter("{http://www.w3.org/2000/svg}g"):
                            if (n.get("class") or "").strip() == "note" or " note " in f" {n.get('class')} ":
                                nid = n.get("id")
                                if not nid or nid not in note_intervals:
                                    continue
                                
                                nh_list = [h for h in n.iter("{http://www.w3.org/2000/svg}g") if "notehead" in (h.get("class") or "")]
                                if not nh_list:
                                    continue
                                nh_u = list(nh_list[0].iter("{http://www.w3.org/2000/svg}use"))
                                if not nh_u:
                                    continue
                                tr_m = re.search(r"translate\((\d+),\s*(\d+)\)", nh_u[0].get("transform", ""))
                                if not tr_m:
                                    continue
                                
                                nx = (margin_x + float(tr_m.group(1))) * scale_x
                                ny = (margin_y + float(tr_m.group(2))) * scale_y

                                acc_x = None
                                acc_list = [a for a in n.iter("{http://www.w3.org/2000/svg}g") if "accid" in (a.get("class") or "")]
                                if acc_list:
                                    acc_u = list(acc_list[0].iter("{http://www.w3.org/2000/svg}use"))
                                    if acc_u:
                                        acc_tr = re.search(r"translate\((\d+),\s*(\d+)\)", acc_u[0].get("transform", ""))
                                        if acc_tr:
                                            acc_x = (margin_x + float(acc_tr.group(1))) * scale_x

                                stem_x = None
                                stem_list = [st for st in n.iter("{http://www.w3.org/2000/svg}g") if "stem" in (st.get("class") or "")]
                                if stem_list:
                                    stem_p = list(stem_list[0].iter("{http://www.w3.org/2000/svg}path"))
                                    if stem_p:
                                        stem_d = stem_p[0].get("d", "")
                                        sm = re.search(r"M(\d+)", stem_d)
                                        if sm:
                                            stem_x = (margin_x + float(sm.group(1))) * scale_x

                                dot_list = [d for d in n.iter("{http://www.w3.org/2000/svg}g") if "dot" in (d.get("class") or "")]
                                dot_max_x = None
                                for d in dot_list:
                                    for el in d.iter():
                                        if "cx" in el.attrib:
                                            dx_val = (margin_x + float(el.attrib["cx"])) * scale_x
                                            rx_val = float(el.attrib.get("rx", 36)) * scale_x
                                            dot_max_x = max(dot_max_x or 0.0, dx_val + rx_val)

                                flag_list = [fl for fl in n.iter("{http://www.w3.org/2000/svg}g") if "flag" in (fl.get("class") or "")]
                                flag_max_x = None
                                if flag_list:
                                    flag_u = list(flag_list[0].iter("{http://www.w3.org/2000/svg}use"))
                                    if flag_u:
                                        tr_f = re.search(r"translate\((\d+)", flag_u[0].get("transform", ""))
                                        if tr_f:
                                            flag_max_x = (margin_x + float(tr_f.group(1))) * scale_x + 12.0

                                is_half = False
                                if nh_u:
                                    xlink = nh_u[0].get('{http://www.w3.org/1999/xlink}href', '')
                                    if 'E0A3' in xlink or 'E0A2' in xlink:
                                        is_half = True

                                nh_width = 14.0 if is_half else 11.2
                                left_bound = acc_x if acc_x is not None else nx
                                bounds_candidates = [nx + nh_width]
                                if stem_x is not None:
                                    bounds_candidates.append(stem_x + 1.0)
                                if dot_max_x is not None:
                                    bounds_candidates.append(dot_max_x + 0.5)
                                if flag_max_x is not None:
                                    bounds_candidates.append(flag_max_x)
                                right_bound = max(bounds_candidates)

                                note_obj = {
                                    "id": nid,
                                    "x": nx,
                                    "y": ny,
                                    "acc_x": acc_x,
                                    "stem_x": stem_x,
                                    "left_x": left_bound,
                                    "right_x": right_bound,
                                    "on_ms": note_intervals[nid][0],
                                    "off_ms": note_intervals[nid][1],
                                    "is_onset": note_intervals[nid][2] if len(note_intervals[nid]) > 2 else True,
                                    "staff": staff_key
                                }
                                staves_data[staff_key].append(note_obj)
                                sys_notes.append(note_obj)
                                page_notes.append(note_obj)

                if sys_notes:
                    min_y = min(n["y"] for n in sys_notes)
                    max_y = max(n["y"] for n in sys_notes)
                    min_x = min(n["x"] for n in sys_notes)
                    max_x = max(n["x"] for n in sys_notes)
                    start_ms = min(n["on_ms"] for n in sys_notes)
                    end_ms = max(n["off_ms"] for n in sys_notes)
                    # Group notes into discrete sequential events for the entire system (preserves stems across staves)
                    # Strict vertical column clustering: notes must share the exact same vertical X alignment (|dx| <= 3.0 px)
                    # (such as rolled chords/arpeggios like Measure 7 or chords spanning staves)
                    # Sequential melodic notes (dx >= 15px) are strictly kept separate to prevent false pairing!
                    col_evs = []
                    for n in sorted(sys_notes, key=lambda n: (n["on_ms"], n["x"])):
                        matched = False
                        for ev in col_evs:
                            is_time_match = abs(n["on_ms"] - ev["on_ms"]) <= 15
                            is_col_match = abs(n["x"] - ev["x_center"]) <= 3.0 and abs(n["on_ms"] - ev["on_ms"]) < 1200.0
                            if is_time_match or is_col_match:
                                ev["notes"].append(n)
                                ev["on_ms"] = min(ev["on_ms"], n["on_ms"])
                                ev["is_onset"] = ev["is_onset"] or n.get("is_onset", True)
                                ev["min_left"] = min(ev["min_left"], n["left_x"])
                                ev["max_right"] = max(ev["max_right"], n["right_x"])
                                ev["x_center"] = float(np.mean([note["x"] for note in ev["notes"]]))
                                matched = True
                                break
                        if not matched:
                            col_evs.append({
                                "on_ms": n["on_ms"],
                                "notes": [n],
                                "is_onset": n.get("is_onset", True),
                                "min_left": n["left_x"],
                                "max_right": n["right_x"],
                                "x_center": n["x"]
                            })
                    col_evs.sort(key=lambda e: (e["on_ms"], e["x_center"]))
                    sys_events = col_evs

                    page_systems.append({
                        "id": sys_id,
                        "notes": sys_notes,
                        "min_x": min_x,
                        "max_x": max_x,
                        "min_y": min_y,
                        "max_y": max_y,
                        "top_y": 0,
                        "bottom_y": img_h,
                        "center_y": (min_y + max_y) / 2.0,
                        "start_ms": start_ms,
                        "end_ms": end_ms,
                        "events": sys_events
                    })

        # Compute non-clipping system vertical boundaries so slurs, ties, and markings are never cut
        num_sys = len(page_systems)
        for i in range(num_sys):
            top_y = 0 if i == 0 else int((page_systems[i-1]["max_y"] + page_systems[i]["min_y"]) / 2.0)
            bot_y = img_h if i == num_sys - 1 else int((page_systems[i]["max_y"] + page_systems[i+1]["min_y"]) / 2.0)
            page_systems[i]["top_y"] = top_y
            page_systems[i]["bottom_y"] = bot_y

        # Extract markings (slurs, hairpins, dynamics, directions, tempo) with onset times & bounds
        slur_map = {}
        for s in root_mei.iter("{http://www.music-encoding.org/ns/mei}slur"):
            sid = s.get("{http://www.w3.org/XML/1998/namespace}id")
            startid = s.get("startid", "").lstrip("#")
            endid = s.get("endid", "").lstrip("#")
            slur_map[sid] = (startid, endid)

        parents = {}
        for p in root.iter("{http://www.w3.org/2000/svg}g"):
            for child in p:
                parents[child] = p

        page_markings = []
        for g in root.iter("{http://www.w3.org/2000/svg}g"):
            cls = (g.get("class") or "").split()
            if any(k in cls for k in ("slur", "hairpin", "dynam", "dir", "tempo")):
                gid = g.get("id")
                start_ms = None
                if "slur" in cls:
                    st_id, en_id = slur_map.get(gid, (None, None))
                    if st_id and st_id in note_intervals:
                        start_ms = note_intervals[st_id][0]
                if start_ms is None:
                    curr = g
                    while curr is not None:
                        if "measure" in (curr.get("class") or "").split():
                            m_notes = [n for n in curr.iter("{http://www.w3.org/2000/svg}g") if "note" in (n.get("class") or "").split()]
                            n_times = [note_intervals[n.get("id")][0] for n in m_notes if n.get("id") in note_intervals]
                            if n_times:
                                start_ms = min(n_times)
                            break
                        curr = parents.get(curr)

                if start_ms is None:
                    start_ms = 0.0

                xs, ys = [], []
                for elem in g.iter():
                    tr = elem.get("transform", "")
                    match = re.search(r"translate\((\d+),\s*(\d+)\)", tr)
                    if match:
                        xs.append(float(match.group(1)))
                        ys.append(float(match.group(2)))
                    d = elem.get("d", "")
                    if d:
                        c = [float(val) for val in re.findall(r"[-+]?\d*\.\d+|\d+", d)]
                        if c:
                            xs.extend(c[0::2])
                            ys.extend(c[1::2])

                if xs and ys:
                    x1 = int(max(0, (margin_x + min(xs)) * scale_x - 4))
                    x2 = int(min(img_w, (margin_x + max(xs)) * scale_x + 4))
                    y1 = int(max(0, (margin_y + min(ys)) * scale_y - 4))
                    y2 = int(min(img_h, (margin_y + max(ys)) * scale_y + 4))
                    if x2 > x1 and y2 > y1:
                        page_markings.append({
                            "id": gid,
                            "class": cls,
                            "start_ms": start_ms,
                            "x1": x1, "x2": x2, "y1": y1, "y2": y2
                        })

        p_start_ms = min((n["on_ms"] for n in page_notes), default=0.0)
        p_end_ms = max((n["off_ms"] for n in page_notes), default=999999.0)

        pages_data.append({
            "page_num": p_num,
            "img_full": img_full,
            "img_staves": img_staves,
            "img_markings": img_markings,
            "markings": page_markings,
            "img_w": img_w,
            "img_h": img_h,
            "notes": page_notes,
            "systems": page_systems,
            "start_ms": p_start_ms,
            "end_ms": p_end_ms
        })

        if p_start_ms > (total_duration_sec * 1000.0 + 2000.0):
            break

    # Adjust page transition boundaries
    for i in range(len(pages_data)):
        pages_data[i]["active_start_ms"] = 0.0 if i == 0 else pages_data[i]["start_ms"] - 400.0
        pages_data[i]["active_end_ms"] = (
            pages_data[i+1]["start_ms"] - 400.0 if i < len(pages_data) - 1 else total_duration_sec * 1000.0
        )

    # 6. Precompute 100% Continuous, Gaussian-Smoothed Musical Camera Trajectory (Never Stumbles, Never Stops)
    cam_x_frames = np.zeros(total_frames, dtype=np.float32)
    cam_y_frames = np.zeros(total_frames, dtype=np.float32)

    for p_idx, page in enumerate(pages_data):
        if page.get("active_start_ms", 0.0) >= total_duration_sec * 1000.0:
            continue
        systems = page["systems"]
        if not systems:
            continue

        # Calculate dynamic inter-system transitions within this page
        sys_transitions = []
        for s_idx in range(len(systems) - 1):
            s_curr = systems[s_idx]
            s_next = systems[s_idx + 1]
            last_on = s_curr["events"][-1]["on_ms"]
            next_on = s_next["events"][0]["on_ms"]
            gap = next_on - last_on
            mid_ms = (last_on + next_on) / 2.0
            trans_dur_ms = min(2800.0, max(1400.0, gap * 0.45))
            t_start = mid_ms - trans_dur_ms / 2.0
            t_end = mid_ms + trans_dur_ms / 2.0
            sys_transitions.append({
                "from_idx": s_idx,
                "to_idx": s_idx + 1,
                "t_start": t_start,
                "t_end": t_end,
                "x_from": float(np.mean([n["x"] for n in s_curr["events"][-1]["notes"]])),
                "x_to": float(np.mean([n["x"] for n in s_next["events"][0]["notes"]])),
                "y_from": float(s_curr["center_y"]),
                "y_to": float(s_next["center_y"])
            })

        for s_idx, sys_obj in enumerate(systems):
            evs = sys_obj["events"]
            if not evs:
                continue

            if s_idx == 0:
                s_t_start = page["active_start_ms"]
                s_t_end = sys_transitions[0]["t_start"] if sys_transitions else page["active_end_ms"]
            elif s_idx == len(systems) - 1:
                s_t_start = sys_transitions[-1]["t_end"]
                s_t_end = page["active_end_ms"]
            else:
                s_t_start = sys_transitions[s_idx-1]["t_end"]
                s_t_end = sys_transitions[s_idx]["t_start"]

            f_start = max(0, int((s_t_start / 1000.0) * fps))
            f_end = min(total_frames, int((s_t_end / 1000.0) * fps))
            seg_len = f_end - f_start
            if seg_len <= 0:
                continue

            t_axis = np.linspace(s_t_start, s_t_end, seg_len)

            ev_times = np.array([e["on_ms"] for e in evs], dtype=np.float64)
            ev_xs = np.array([np.mean([n["x"] for n in e["notes"]]) for e in evs], dtype=np.float64)

            # Build strictly monotonic, non-stumbling camera keyframes with guaranteed forward drift
            adj_times = [ev_times[0]]
            adj_xs = [ev_xs[0]]
            min_drift_rate = 0.025  # px/ms = 25 px/sec minimum continuous forward movement

            for i_e in range(1, len(ev_times)):
                t_curr = ev_times[i_e]
                x_curr = ev_xs[i_e]
                dt = t_curr - adj_times[-1]
                if dt < 10.0:
                    adj_xs[-1] = max(adj_xs[-1], x_curr)
                    continue
                min_x = adj_xs[-1] + max(3.0, dt * min_drift_rate)
                adj_times.append(t_curr)
                adj_xs.append(max(x_curr, min_x))

            # Build strictly increasing knot sequences for PCHIP
            knots_t = []
            knots_x = []

            # Pre-roll knot strictly before adj_times[0]
            if s_t_start < adj_times[0] - 15.0:
                pre_x = max(50.0, adj_xs[0] - (adj_times[0] - s_t_start) * min_drift_rate)
                knots_t.append(s_t_start)
                knots_x.append(pre_x)

            for t_val, x_val in zip(adj_times, adj_xs):
                if not knots_t or t_val > knots_t[-1] + 5.0:
                    knots_t.append(t_val)
                    knots_x.append(max(x_val, knots_x[-1] + 3.0 if knots_x else x_val))

            # Post-roll knot strictly after knots_t[-1]
            if s_t_end > knots_t[-1] + 15.0:
                post_x = knots_x[-1] + (s_t_end - knots_t[-1]) * min_drift_rate
                knots_t.append(s_t_end)
                knots_x.append(post_x)
            elif s_t_end > knots_t[-1]:
                knots_t[-1] = s_t_end

            if len(knots_t) >= 2:
                pchip = PchipInterpolator(knots_t, knots_x)
                smooth_x = pchip(t_axis)
            else:
                smooth_x = np.full(seg_len, adj_xs[0], dtype=np.float32)

            # Additional Gaussian smoothing for cinematic organic feel
            sigma_f = int(0.10 * fps)
            if sigma_f > 1:
                k_half = int(sigma_f * 3)
                kx = np.arange(-k_half, k_half + 1)
                kernel = np.exp(-0.5 * (kx / sigma_f)**2)
                kernel /= np.sum(kernel)
                padded = np.pad(smooth_x, k_half, mode="edge")
                smooth_x = np.convolve(padded, kernel, mode="valid")

            cam_x_frames[f_start:f_end] = smooth_x[:seg_len]
            cam_y_frames[f_start:f_end] = sys_obj["center_y"]

        # Inter-system smooth transitions in S-curve (guaranteeing exact positional continuity)
        for tr in sys_transitions:
            f_t_start = max(0, int((tr["t_start"] / 1000.0) * fps))
            f_t_end = min(total_frames, int((tr["t_end"] / 1000.0) * fps))
            tr_len = f_t_end - f_t_start
            if tr_len > 0:
                p = np.linspace(0.0, 1.0, tr_len)
                ease = 0.5 * (1.0 - np.cos(np.pi * p))
                x_from = float(cam_x_frames[max(0, f_t_start - 1)])
                x_to = float(cam_x_frames[min(total_frames - 1, f_t_end)])
                cam_x_frames[f_t_start:f_t_end] = x_from + (x_to - x_from) * ease
                cam_y_frames[f_t_start:f_t_end] = tr["y_from"] + (tr["y_to"] - tr["y_from"]) * ease

    # Subtle boundary smoothing across frame boundaries
    sigma_global = int(0.08 * fps)
    if sigma_global > 1:
        k_h = sigma_global * 2
        kx = np.arange(-k_h, k_h + 1)
        kern = np.exp(-0.5 * (kx / sigma_global)**2)
        kern /= np.sum(kern)
        cam_x_frames = np.convolve(np.pad(cam_x_frames, k_h, mode="edge"), kern, mode="valid")
        cam_y_frames = np.convolve(np.pad(cam_y_frames, k_h, mode="edge"), kern, mode="valid")

    # 7. Precompute Noise Mesh for Red Clouds
    gw, gh = 160, 90
    gx = np.linspace(0, 4.0, gw, dtype=np.float32)
    gy = np.linspace(0, 2.25, gh, dtype=np.float32)
    grid_X, grid_Y = np.meshgrid(gx, gy)

    # 7. Generate Pre-rendered Pure White Luminous Flash Sprite
    glow_r = int(18 * (zoom / 1.38))
    glow_sprite = np.zeros((glow_r * 2, glow_r * 2, 3), dtype=np.float32)
    for dy in range(-glow_r, glow_r):
        for dx in range(-glow_r, glow_r):
            dist = np.sqrt(dx*dx + dy*dy)
            if dist < glow_r:
                decay = (1.0 - (dist / glow_r)) ** 2.2
                # Pure white luminous flash (soft, non-blinding intensity)
                glow_sprite[dy + glow_r, dx + glow_r] = [175.0 * decay, 175.0 * decay, 175.0 * decay]

    # 8. 2D Elliptical Spotlight Mask & Vignette
    screen_target_x = width * 0.38
    screen_target_y = height * 0.50
    y_coords, x_coords = np.mgrid[0:height, 0:width].astype(np.float32)
    dx = (x_coords - screen_target_x) / (width * 0.48)
    dy = (y_coords - screen_target_y) / (height * 0.52)
    dist_sq = dx * dx + dy * dy
    spotlight_raw = np.clip(1.0 - (dist_sq ** 1.15), 0.0, 1.0)
    spotlight_score = np.clip(spotlight_raw * 1.55, 0.0, 1.0)[:, :, np.newaxis]
    spotlight_u8 = np.clip(spotlight_raw * 1.55 * 255.0, 0.0, 255.0).astype(np.uint8)
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
        "-crf", "16",
        temp_raw_video
    ]

    pipe = subprocess.Popen(ffmpeg_video_cmd, stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)

    t_start = time.time()
    last_report_time = 0.0

    # Camera tracking state
    cam_x = 0.0
    cam_y = 0.0
    cam_vx = 0.0
    cam_inited = False
    current_page_idx = -1
    last_sys_id = None
    last_event_idx = -1

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
        # Notes played so far on current page (to keep softly illuminated within viewport)
        illuminated_notes = [
            n for n in current_page["notes"]
            if n["on_ms"] <= current_time_ms
        ]

        # Calculate playhead_x for active system
        if active_notes:
            playhead_x = float(np.mean([n["x"] for n in active_notes]))
        else:
            past_notes = [n for n in active_sys["notes"] if n["on_ms"] <= current_time_ms]
            future_notes = [n for n in active_sys["notes"] if n["on_ms"] > current_time_ms]
            if past_notes and future_notes:
                p_prev, p_next = past_notes[-1], future_notes[0]
                interp_factor = (current_time_ms - p_prev["on_ms"]) / max(1.0, (p_next["on_ms"] - p_prev["on_ms"]))
                playhead_x = float(p_prev["x"] + (p_next["x"] - p_prev["x"]) * np.clip(interp_factor, 0.0, 1.0))
            elif past_notes:
                playhead_x = float(past_notes[-1]["x"])
            else:
                playhead_x = float(active_sys["min_x"])

        # Progressive discrete note reveal ("picotada nota por nota", full system height preserving all stems)
        page_comp = current_page["img_staves"].copy()

        # 1. Reveal all active markings in full as soon as their start note / measure arrives
        for m in current_page.get("markings", []):
            if current_time_ms >= m["start_ms"] - 30.0:
                x1, x2, y1, y2 = m["x1"], m["x2"], m["y1"], m["y2"]
                patch = current_page["img_markings"][y1:y2, x1:x2]
                mask = (patch[:, :, 3] > 0)
                page_comp[y1:y2, x1:x2][mask] = patch[mask]

        # 2. Progressive discrete note reveal ("picotada nota por nota", full system height preserving all stems)
        for s in current_page["systems"]:
            top = s["top_y"]
            bot = s["bottom_y"]
            if current_time_ms >= s["end_ms"]:
                page_comp[top:bot, :] = current_page["img_full"][top:bot, :]
            elif current_time_ms >= s["start_ms"]:
                evs = s.get("events", [])
                if not evs:
                    continue
                # Reveal synchronized with acoustic hammer strike (optimal 60ms perceptual anticipation)
                reveal_t = current_time_ms + 60.0
                if reveal_t < evs[0]["on_ms"]:
                    px = max(0, int(evs[0]["min_left"] - 4))
                elif reveal_t >= evs[-1]["on_ms"]:
                    px = current_page["img_w"]
                else:
                    last_k = 0
                    for k in range(len(evs) - 1):
                        if evs[k]["on_ms"] <= reveal_t < evs[k+1]["on_ms"]:
                            last_k = k
                            break
                    curr_right = evs[last_k]["max_right"]
                    next_left = evs[last_k+1]["min_left"]
                    if curr_right < next_left:
                        px = int((curr_right + next_left) / 2.0)
                    else:
                        px = int(next_left - 1) if next_left > evs[last_k]["min_left"] + 5 else int(curr_right)
                    # Guaranteed protection: px must never slice into note k+1's accidental or notehead
                    if px >= next_left and next_left > evs[last_k]["min_left"] + 5:
                        px = int(next_left - 1)
                page_comp[top:bot, :px] = current_page["img_full"][top:bot, :px]

        # 3. Musical Camera Kinematics: 100% Fluid Continuous Traveling (Never Freezes, Never Stumbles)
        cam_x = float(cam_x_frames[frame_idx])
        cam_y = float(cam_y_frames[frame_idx])


        # Camera shake driven by tension (strictly 0.0 when calm, subtle micro-tremor in tension)
        shake_x, shake_y = 0.0, 0.0
        if cur_tension > 0.45:
            shake_mag = ((cur_tension - 0.45) / 0.55) ** 2.0 * 2.5
            shake_x = np.sin(frame_idx * 1.6) * shake_mag + np.cos(frame_idx * 2.8) * (shake_mag * 0.3)
            shake_y = np.cos(frame_idx * 1.9) * shake_mag + np.sin(frame_idx * 3.7) * (shake_mag * 0.25)

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

        # Calm passages stay deep, elegant black ("deixar no preto por um tempo")
        # Colors emerge gradually and stably as note density / tension builds
        color_intensity = float(np.clip((cur_tension - 0.28) / 0.50, 0.0, 1.0) ** 1.6)

        # Composite score with 2D spotlight vignette
        alpha_eff = cv2.multiply(warped[:, :, 3], spotlight_u8, scale=1.0/255.0)

        if color_intensity < 0.001:
            b_s = cv2.multiply(warped[:, :, 0], alpha_eff, scale=1.0/255.0)
            g_s = cv2.multiply(warped[:, :, 1], alpha_eff, scale=1.0/255.0)
            r_s = cv2.multiply(warped[:, :, 2], alpha_eff, scale=1.0/255.0)
            frame = cv2.merge([b_s, g_s, r_s])
        else:
            # Procedural Nebula background: Calm Celestial Sapphire Blue -> Radiant Wine Crimson
            n1 = np.sin(grid_X * 1.5 + time_sec * 0.35) * np.cos(grid_Y * 1.8 - time_sec * 0.25)
            n2 = np.sin(grid_X * 3.2 - time_sec * 0.55 + n1 * 0.8) * np.cos(grid_Y * 3.5 + time_sec * 0.45)
            cloud_raw = np.clip((n1 * 0.6 + n2 * 0.4 + 1.0) * 0.5, 0.0, 1.0)
            cloud_hd = cv2.resize(cloud_raw.astype(np.float32), (width, height), interpolation=cv2.INTER_LINEAR)

            w_calm = float((1.0 - cur_tension) ** 1.4)
            w_tense = float(cur_tension ** 1.6)
            w_sum = max(0.001, w_calm + w_tense)
            w_calm /= w_sum
            w_tense /= w_sum

            neb_r = (cloud_hd * 20.0 + 10.0) * w_calm + (cloud_hd * 130.0 + 40.0) * w_tense
            neb_g = (cloud_hd * 35.0 + 14.0) * w_calm + (cloud_hd * 24.0 + 2.0) * w_tense
            neb_b = (cloud_hd * 105.0 + 40.0) * w_calm + (cloud_hd * 42.0 + 5.0) * w_tense

            cloud_r = np.clip(neb_r * color_intensity, 0, 255)
            cloud_g = np.clip(neb_g * color_intensity, 0, 255)
            cloud_b = np.clip(neb_b * color_intensity, 0, 255)

            cloud_r = (cloud_r * bg_vignette).astype(np.uint8)
            cloud_g = (cloud_g * bg_vignette).astype(np.uint8)
            cloud_b = (cloud_b * bg_vignette).astype(np.uint8)
            bg_frame = cv2.merge([cloud_b, cloud_g, cloud_r])

            inv_a = 255 - alpha_eff
            b_s = cv2.multiply(warped[:, :, 0], alpha_eff, scale=1.0/255.0)
            g_s = cv2.multiply(warped[:, :, 1], alpha_eff, scale=1.0/255.0)
            r_s = cv2.multiply(warped[:, :, 2], alpha_eff, scale=1.0/255.0)

            b_bg = cv2.multiply(bg_frame[:, :, 0], inv_a, scale=1.0/255.0)
            g_bg = cv2.multiply(bg_frame[:, :, 1], inv_a, scale=1.0/255.0)
            r_bg = cv2.multiply(bg_frame[:, :, 2], inv_a, scale=1.0/255.0)

            frame = cv2.merge([cv2.add(b_s, b_bg), cv2.add(g_s, g_bg), cv2.add(r_s, r_bg)])


        # Draw pure white luminous flash on onset & persistent semi-illumination on played notes
        for n in illuminated_notes:
            dt = current_time_ms - n["on_ms"]
            is_sustaining = (current_time_ms <= n["off_ms"])

            # Subtle initial onset flash (lasts ~110ms, only for real struck onsets, not tied continuation notes)
            is_real_onset = n.get("is_onset", True)
            onset_flash = (np.exp(-dt / 35.0) if dt < 120.0 else 0.0) if is_real_onset else 0.0

            # Persistent gentle illumination on played notes ("meio iluminadas")
            if is_sustaining:
                base_glow = 0.28 + (cur_tension * 0.15)
            else:
                base_glow = 0.16 + (cur_tension * 0.08)

            bloom_boost = base_glow + onset_flash * 0.45

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

            # Crisp white core on notehead
            if 0 <= scr_nx < width and 0 <= scr_ny < height:
                if onset_flash > 0.05:
                    cv2.circle(frame, (scr_nx, scr_ny), 3, (255, 255, 255), -1, lineType=cv2.LINE_AA)
                elif is_sustaining:
                    cv2.circle(frame, (scr_nx, scr_ny), 2, (245, 245, 250), -1, lineType=cv2.LINE_AA)

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
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
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
