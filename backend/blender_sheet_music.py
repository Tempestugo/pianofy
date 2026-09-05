import os
import json
import socket
import logging
import traceback
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

BLENDER_HOST = os.environ.get("BLENDER_MCP_HOST", "127.0.0.1")
BLENDER_PORT = int(os.environ.get("BLENDER_MCP_PORT", "8081"))

def midi_to_diatonic_step(pitch: int) -> tuple[int, bool]:
    """
    Converts MIDI pitch (0-127) to diatonic staff step index and determines if it is an accidental (#).
    C0 is step 0.
    Returns (step_index, is_accidental).
    """
    octave = (pitch // 12) - 1
    note_in_octave = pitch % 12
    
    # Map semitone to (diatonic_step_in_octave, is_sharp)
    semitone_map = {
        0: (0, False),  # C
        1: (0, True),   # C#
        2: (1, False),  # D
        3: (1, True),   # D#
        4: (2, False),  # E
        5: (3, False),  # F
        6: (3, True),   # F#
        7: (4, False),  # G
        8: (4, True),   # G#
        9: (5, False),  # A
        10: (5, True),  # A#
        11: (6, False),  # B
    }
    
    step_in_oct, is_sharp = semitone_map[note_in_octave]
    step_index = octave * 7 + step_in_oct
    return step_index, is_sharp

def generate_blender_sheet_music_script(
    notes: List[Dict[str, Any]],
    audio_duration: float,
    audio_path: str,
    output_video_path: str,
    output_thumb_path: str,
    fps: int = 30,
    time_signature: str = "4/4",
    bpm: float = 120.0
) -> str:
    """
    Generates a Python script for Blender to construct and render a 3D Sheet Music (Partitura 3D) scene.
    """
    cleaned_notes = []
    for n in notes:
        cleaned_notes.append({
            "pitch": int(n.get("pitch", 60)),
            "onset_time": float(n.get("onset_time", 0.0)),
            "offset_time": float(n.get("offset_time", 1.0)),
            "velocity": int(n.get("velocity", 80))
        })
    
    notes_json = json.dumps(cleaned_notes)
    frames_dir = os.path.abspath(output_video_path + "_frames")
    os.makedirs(frames_dir, exist_ok=True)
    frames_dir_esc = os.path.join(frames_dir, "frame_").replace("\\", "/")
    audio_path_esc = audio_path.replace("\\", "/") if audio_path else ""
    output_thumb_esc = os.path.abspath(output_thumb_path).replace("\\", "/")


    script_template = '''import bpy
import math
import json
import os

def create_emission_material(name, color_rgba, strength=5.0):
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    
    output = nodes.new(type='ShaderNodeOutputMaterial')
    emission = nodes.new(type='ShaderNodeEmission')
    emission.inputs['Color'].default_value = color_rgba
    emission.inputs['Strength'].default_value = strength
    links.new(emission.outputs['Emission'], output.inputs['Surface'])
    return mat, emission

def main():
    send_status("Iniciando montagem da Partitura Cinemática Etérea no Blender...")
    
    fps = __FPS__
    audio_duration = __AUDIO_DURATION__
    total_frames = max(30, int(audio_duration * fps))
    
    scene = bpy.context.scene
    scene.render.fps = fps
    scene.frame_start = 1
    scene.frame_end = total_frames
    
    scene.render.resolution_x = 1920
    scene.render.resolution_y = 1080
    scene.render.resolution_percentage = 100
    
    # 1. Render Engine & Post-Processing (Eevee Bloom & Motion Blur)
    scene.render.engine = 'BLENDER_EEVEE'
    if hasattr(scene, "eevee"):
        if hasattr(scene.eevee, "use_bloom"):
            scene.eevee.use_bloom = True
            scene.eevee.bloom_intensity = 0.22
            scene.eevee.bloom_radius = 6.5
            scene.eevee.bloom_threshold = 0.4
        if hasattr(scene.eevee, "use_motion_blur"):
            scene.eevee.use_motion_blur = True
            scene.eevee.motion_blur_shutter = 0.4
            
    send_status("Limpando cena anterior...")
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    
    for block in bpy.data.meshes: bpy.data.meshes.remove(block)
    for block in bpy.data.materials: bpy.data.materials.remove(block)
    for block in bpy.data.cameras: bpy.data.cameras.remove(block)
    for block in bpy.data.lights: bpy.data.lights.remove(block)
    
    # 2. Dynamic Ethereal Sunset/Nebula Background
    world = bpy.data.worlds.new("EtherealWorld")
    scene.world = world
    world.use_nodes = True
    w_nodes = world.node_tree.nodes
    w_links = world.node_tree.links
    w_nodes.clear()
    
    w_output = w_nodes.new(type='ShaderNodeOutputWorld')
    w_bg = w_nodes.new(type='ShaderNodeBackground')
    w_bg.inputs['Strength'].default_value = 0.8
    
    grad = w_nodes.new(type='ShaderNodeTexGradient')
    grad.gradient_type = 'SPHERICAL'
    
    ramp = w_nodes.new(type='ShaderNodeValToRGB')
    # Color stops: Deep Space Indigo -> Sunset Purple -> Warm Golden Magenta
    ramp.color_ramp.elements[0].position = 0.0
    ramp.color_ramp.elements[0].color = (0.015, 0.008, 0.035, 1.0)
    
    e1 = ramp.color_ramp.elements.new(0.5)
    e1.position = 0.55
    e1.color = (0.09, 0.02, 0.18, 1.0)
    
    ramp.color_ramp.elements[1].position = 1.0
    ramp.color_ramp.elements[1].color = (0.28, 0.08, 0.14, 1.0)
    
    mapping = w_nodes.new(type='ShaderNodeMapping')
    coord = w_nodes.new(type='ShaderNodeTexCoord')
    
    w_links.new(coord.outputs['Generated'], mapping.inputs['Vector'])
    w_links.new(mapping.outputs['Vector'], grad.inputs['Vector'])
    w_links.new(grad.outputs['Fac'], ramp.inputs['Fac'])
    w_links.new(ramp.outputs['Color'], w_bg.inputs['Color'])
    w_links.new(w_bg.outputs['Background'], w_output.inputs['Surface'])
    
    # Slowly animate World Mapping rotation for passage of time effect
    mapping.inputs['Rotation'].default_value = (0, 0, 0)
    mapping.inputs['Rotation'].keyframe_insert(data_path="default_value", frame=1)
    mapping.inputs['Rotation'].default_value = (0, 0, math.radians(45))
    mapping.inputs['Rotation'].keyframe_insert(data_path="default_value", frame=total_frames)
    
    send_status("Construindo Pauta Dupla 2D Etérea (Clave de Sol e Clave de Fá)...")
    
    scale_x = 2.5
    line_spacing = 0.3
    staff_gap = 1.8
    
    y_treble_base = staff_gap / 2.0
    y_bass_base = -staff_gap / 2.0 - (4 * line_spacing)
    
    STEP_E4 = 30
    STEP_G2 = 18
    
    total_length = max(10.0, audio_duration * scale_x + 5.0)
    
    # Ultra-thin glowing staff lines
    mat_staff, _ = create_emission_material("StaffLineMat", (0.85, 0.75, 0.45, 1.0), strength=4.5)
    mat_barline, _ = create_emission_material("BarlineMat", (0.5, 0.4, 0.6, 1.0), strength=2.5)
    
    def draw_staff_line(y_pos, start_x=-2.0, end_x=total_length):
        bpy.ops.mesh.primitive_cylinder_add(radius=0.008, depth=end_x - start_x, location=((start_x + end_x)/2.0, y_pos, 0))
        line_obj = bpy.context.active_object
        line_obj.rotation_euler = (0, math.pi / 2.0, 0)
        line_obj.data.materials.append(mat_staff)
        return line_obj
        
    for i in range(5):
        draw_staff_line(y_treble_base + i * line_spacing)
        draw_staff_line(y_bass_base + i * line_spacing)
        
    bpm = __BPM__
    sec_per_beat = 60.0 / bpm
    sec_per_measure = sec_per_beat * 4.0
    num_measures = int(audio_duration / sec_per_measure) + 2
    
    for m in range(num_measures + 1):
        x_m = m * sec_per_measure * scale_x
        bpy.ops.mesh.primitive_cylinder_add(radius=0.012, depth=(y_treble_base + 4*line_spacing) - y_bass_base + 0.4, location=(x_m, (y_treble_base + 4*line_spacing + y_bass_base)/2.0, 0.01))
        bar_obj = bpy.context.active_object
        bar_obj.data.materials.append(mat_barline)
    
    send_status("Criando notas musicais 2D com Shaders de Emissão de Luz...")
    
    notes_data_str = __NOTES_JSON_STR__
    notes = json.loads(notes_data_str)
    
    def get_pitch_y(pitch):
        semitone_map = {0:(0,False), 1:(0,True), 2:(1,False), 3:(1,True), 4:(2,False), 5:(3,False), 6:(3,True), 7:(4,False), 8:(4,True), 9:(5,False), 10:(5,True), 11:(6,False)}
        octave = (pitch // 12) - 1
        note_in_oct = pitch % 12
        step_in_oct, is_sharp = semitone_map[note_in_oct]
        step_index = octave * 7 + step_in_oct
        
        if pitch >= 60:
            delta = step_index - STEP_E4
            y = y_treble_base + delta * (line_spacing / 2.0)
            clef = "treble"
        else:
            delta = step_index - STEP_G2
            y = y_bass_base + delta * (line_spacing / 2.0)
            clef = "bass"
        return y, clef, is_sharp
        
    note_materials = {}
    
    for note_data in notes:
        pitch = note_data['pitch']
        onset = note_data['onset_time']
        offset = note_data['offset_time']
        
        x_pos = onset * scale_x
        y_pos, clef_type, is_sharp = get_pitch_y(pitch)
        
        if pitch not in note_materials:
            mat_name = f"NoteEmMat_{pitch}"
            import colorsys
            t = (pitch - 21) / 87.0
            hue = (240.0 - t * 240.0) / 360.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.8, 0.95)
            
            mat, em_node = create_emission_material(mat_name, (r, g, b, 1.0), strength=2.0)
            note_materials[pitch] = (mat, em_node)
            
        mat, em_node = note_materials[pitch]
        
        # 2D Disc Notehead
        bpy.ops.mesh.primitive_cylinder_add(radius=0.13, depth=0.01, location=(x_pos, y_pos, 0.02), rotation=(math.pi/2.0, 0, 0))
        notehead = bpy.context.active_object
        notehead.scale = (1.3, 0.5, 0.9)
        notehead.rotation_euler = (0, 0, math.radians(25))
        notehead.data.materials.append(mat)
        
        stem_dir = 1.0 if clef_type == "treble" else -1.0
        stem_length = 0.75
        stem_x = x_pos + (0.12 if stem_dir > 0 else -0.12)
        stem_y = y_pos + (stem_length / 2.0) * stem_dir
        
        bpy.ops.mesh.primitive_cylinder_add(radius=0.012, depth=stem_length, location=(stem_x, stem_y, 0.02))
        stem_obj = bpy.context.active_object
        stem_obj.data.materials.append(mat)
        
        # Keyframe Emission Strength boost (Light Aura Pulse on play)
        f_onset = max(1, int(onset * fps))
        f_offset = max(f_onset + 1, int(offset * fps))
        
        if em_node:
            em_prop = em_node.inputs['Strength']
            
            if f_onset > 1:
                em_prop.default_value = 2.0
                em_prop.keyframe_insert(data_path='default_value', frame=f_onset - 1)
                
            em_prop.default_value = 16.0  # Intense light burst triggering EEVEE Bloom aura
            em_prop.keyframe_insert(data_path='default_value', frame=f_onset)
            
            em_prop.default_value = 14.0
            em_prop.keyframe_insert(data_path='default_value', frame=f_offset)
            
            em_prop.default_value = 2.0
            em_prop.keyframe_insert(data_path='default_value', frame=f_offset + 1)

    # 3. VFX Particle System (Floating Magic Dust / Sparks)
    send_status("Adicionando Sistema de Partículas (Poeira Mágica / Vagalumes)...")
    mat_part, _ = create_emission_material("PartMat", (0.9, 0.85, 1.0, 1.0), strength=8.0)
    
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.02, location=(0, 0, -100))
    part_shape = bpy.context.active_object
    part_shape.data.materials.append(mat_part)
    part_shape.hide_render = True
    
    bpy.ops.mesh.primitive_plane_add(size=1.0, location=(total_length/2.0, 0, -0.3))
    emitter = bpy.context.active_object
    emitter.scale = (total_length + 20.0, 10.0, 1.0)
    emitter.hide_render = True
    
    psys = emitter.modifiers.new(name="MagicDust", type='PARTICLE_SYSTEM')
    p_settings = psys.particle_system.settings
    p_settings.count = 250
    p_settings.frame_start = 1
    p_settings.frame_end = total_frames
    p_settings.lifetime = 150
    p_settings.lifetime_random = 0.5
    p_settings.render_type = 'OBJECT'
    p_settings.instance_object = part_shape
    p_settings.particle_size = 0.7
    p_settings.size_random = 0.8
    p_settings.brownian_factor = 0.6
    p_settings.effector_weights.gravity = 0.05
    
    # 4. Cyan Glowing Sweeping Playhead Beam
    mat_playhead, _ = create_emission_material("PlayheadMat", (0.0, 0.95, 1.0, 1.0), strength=18.0)
    bpy.ops.mesh.primitive_cylinder_add(radius=0.02, depth=10.0, location=(0, 0, 0.2))
    playhead_beam = bpy.context.active_object
    playhead_beam.data.materials.append(mat_playhead)
    
    playhead_beam.location.x = 0
    playhead_beam.keyframe_insert(data_path="location", index=0, frame=1)
    playhead_beam.location.x = audio_duration * scale_x
    playhead_beam.keyframe_insert(data_path="location", index=0, frame=total_frames)
    
    # 5. 2D Orthographic Camera tracking the score
    cam_data = bpy.data.cameras.new("ScoreCamera2D")
    cam_data.type = 'ORTHO'
    cam_data.ortho_scale = 6.6
    
    cam_obj = bpy.data.objects.new("ScoreCamera2D", cam_data)
    bpy.context.collection.objects.link(cam_obj)
    scene.camera = cam_obj
    
    cam_obj.location = (1.8, 0.0, 10.0)
    cam_obj.rotation_euler = (0, 0, 0)
    
    for f in range(1, total_frames + 1):
        t_sec = (f - 1) / float(fps)
        x_target = t_sec * scale_x
        cam_obj.location = (x_target + 1.8, 0.0, 10.0)
        cam_obj.keyframe_insert(data_path="location", frame=f)
        
    send_status("Renderizando quadros PNG da Partitura Cinemática Etérea em 1080p...")
    
    scene.render.image_settings.file_format = 'PNG'
    scene.render.filepath = __FRAMES_DIR_ESC__
    bpy.ops.render.render(animation=True)
    
    scene.frame_set(min(15, total_frames))
    scene.render.image_settings.file_format = 'JPEG'
    scene.render.filepath = __OUTPUT_THUMB_PATH__
    bpy.ops.render.render(write_still=True)
    
    send_status("Quadros 3D renderizados com sucesso pelo Blender!")

main()
'''

    script = script_template.replace("__FPS__", str(fps))\
                            .replace("__AUDIO_DURATION__", str(audio_duration))\
                            .replace("__BPM__", str(bpm))\
                            .replace("__NOTES_JSON_STR__", repr(notes_json))\
                            .replace("__FRAMES_DIR_ESC__", repr(frames_dir_esc))\
                            .replace("__OUTPUT_THUMB_PATH__", repr(output_thumb_esc))\
                            .replace("__AUDIO_PATH__", repr(audio_path_esc))

    return script

def render_blender_sheet_music_task(
    notes: List[Dict[str, Any]],
    audio_duration: float,
    audio_path: str,
    output_video_path: str,
    output_thumb_path: str,
    fps: int = 30,
    bpm: float = 120.0,
    time_signature: str = "4/4"
) -> Dict[str, Any]:
    """
    Constructs the script and sends it to the running Blender MCP socket server.
    Converts rendered frame sequence to MP4 using ffmpeg.
    """
    script = generate_blender_sheet_music_script(
        notes=notes,
        audio_duration=audio_duration,
        audio_path=audio_path,
        output_video_path=output_video_path,
        output_thumb_path=output_thumb_path,
        fps=fps,
        bpm=bpm,
        time_signature=time_signature
    )
    
    results = {
        "status": "unknown",
        "messages": [],
        "error": None
    }
    
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(300)
        s.connect((BLENDER_HOST, BLENDER_PORT))
        
        payload = json.dumps({"script": script})
        s.sendall(payload.encode())
        
        buffer = ""
        while True:
            try:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buffer += chunk.decode()
                
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    if line.strip():
                        try:
                            msg = json.loads(line)
                            if msg.get("status") == "progress":
                                results["messages"].append(msg.get("message", ""))
                            elif msg.get("status") == "ok":
                                results["status"] = "ok"
                                results["messages"].append(msg.get("message", "Complete"))
                            elif msg.get("status") == "error":
                                results["status"] = "error"
                                results["error"] = msg.get("error", "Unknown error")
                        except json.JSONDecodeError:
                            pass
                if results["status"] in ("ok", "error"):
                    break
            except socket.timeout:
                results["status"] = "error"
                results["error"] = "Tempo limite excedido na renderização do Blender."
                break
        s.close()
        
        # If Blender finished rendering frames, compile MP4 video with ffmpeg
        if results["status"] == "ok":
            import subprocess
            ffmpeg_bin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "node_modules", "ffmpeg-static", "ffmpeg.exe")
            if not os.path.exists(ffmpeg_bin):
                import shutil
                ffmpeg_bin = shutil.which("ffmpeg") or "ffmpeg"
                
            frames_dir = os.path.abspath(output_video_path + "_frames")
            frame_pattern = os.path.join(frames_dir, "frame_%04d.png")
            out_mp4 = os.path.abspath(output_video_path)
            
            cmd = [
                ffmpeg_bin, "-y",
                "-r", str(fps),
                "-start_number", "1",
                "-i", frame_pattern
            ]
            if audio_path and os.path.exists(audio_path):
                cmd.extend(["-i", os.path.abspath(audio_path), "-c:a", "aac", "-shortest"])
            cmd.extend([
                "-c:v", "libx264",
                "-pix_fmt", "yuv420p",
                out_mp4
            ])
            
            logger.info(f"Compilando vídeo MP4 com FFmpeg: {' '.join(cmd)}")
            subprocess.run(cmd, check=True)
            results["messages"].append("Vídeo MP4 gerado com sucesso!")

            
    except Exception as e:
        results["status"] = "error"
        results["error"] = f"Erro na compilação do vídeo: {str(e)}"
        logger.error(traceback.format_exc())
        
    return results

