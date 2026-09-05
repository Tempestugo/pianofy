import os
import json
import music21

midi_path = "backend/outputs/user_audio_test.mid"
if not os.path.exists(midi_path):
    midi_path = "backend/outputs/96803c2b-f9df-4de9-9846-e041a9f276d3.mid"

score = music21.converter.parse(midi_path)

notes = []
bpm_val = 85.714
beat_sec = 60.0 / bpm_val

for el in score.flatten().notes:
    onset_beat = float(el.offset)
    duration_beat = float(el.duration.quarterLength)
    if hasattr(el, 'pitch'):
        notes.append({
            'pitch': int(el.pitch.midi),
            'velocity': 80,
            'onset_time': float(onset_beat * beat_sec),
            'offset_time': float((onset_beat + duration_beat) * beat_sec)
        })
    elif hasattr(el, 'pitches'):
        for p in el.pitches:
            notes.append({
                'pitch': int(p.midi),
                'velocity': 80,
                'onset_time': float(onset_beat * beat_sec),
                'offset_time': float((onset_beat + duration_beat) * beat_sec)
            })

notes_json = json.dumps(notes[:150])

blender_gui_script = f'''import bpy
import math
import json

notes = json.loads({repr(notes_json)})

# Helper function to build pure Emission Shaders
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

# Render & EEVEE Post-Processing Settings
scene = bpy.context.scene
scene.frame_start = 1
scene.frame_end = 360
scene.render.fps = 30
scene.render.resolution_x = 1920
scene.render.resolution_y = 1080

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

# Clean existing objects
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)

# World Nebula / Sunset Sky Shader
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
ramp.color_ramp.elements[0].position = 0.0
ramp.color_ramp.elements[0].color = (0.015, 0.008, 0.035, 1.0) # Deep Indigo

e1 = ramp.color_ramp.elements.new(0.5)
e1.position = 0.55
e1.color = (0.09, 0.02, 0.18, 1.0) # Deep Sunset Purple

ramp.color_ramp.elements[1].position = 1.0
ramp.color_ramp.elements[1].color = (0.28, 0.08, 0.14, 1.0) # Warm Golden Magenta

mapping = w_nodes.new(type='ShaderNodeMapping')
coord = w_nodes.new(type='ShaderNodeTexCoord')

w_links.new(coord.outputs['Generated'], mapping.inputs['Vector'])
w_links.new(mapping.outputs['Vector'], grad.inputs['Vector'])
w_links.new(grad.outputs['Fac'], ramp.inputs['Fac'])
w_links.new(ramp.outputs['Color'], w_bg.inputs['Color'])
w_links.new(w_bg.outputs['Background'], w_output.inputs['Surface'])

# Animated Background Rotation
mapping.inputs['Rotation'].default_value = (0, 0, 0)
mapping.inputs['Rotation'].keyframe_insert(data_path="default_value", frame=1)
mapping.inputs['Rotation'].default_value = (0, 0, math.radians(45))
mapping.inputs['Rotation'].keyframe_insert(data_path="default_value", frame=360)

# Diatonic Pitch Y conversion
def pitch_to_y(pitch):
    semitone_map = {{0:(0,False), 1:(0,True), 2:(1,False), 3:(1,True), 4:(2,False), 5:(3,False), 6:(3,True), 7:(4,False), 8:(4,True), 9:(5,False), 10:(5,True), 11:(6,False)}}
    octave = (pitch // 12) - 1
    note_in_oct = pitch % 12
    step_in_oct, is_sharp = semitone_map[note_in_oct]
    step_index = octave * 7 + step_in_oct
    
    if pitch >= 60:
        delta = step_index - 30
        return 0.9 + delta * 0.15
    else:
        delta = step_index - 18
        return -1.5 + delta * 0.15

# Staff Lines & Barlines (Ultra-Thin Glowing Emission)
mat_staff, _ = create_emission_material("StaffLineMat", (0.85, 0.75, 0.45, 1.0), strength=4.5)
mat_barline, _ = create_emission_material("BarlineMat", (0.5, 0.4, 0.6, 1.0), strength=2.5)

line_spacing = 0.3
y_treble = 0.9
y_bass = -1.5

for i in range(5):
    # Treble Lines
    y_pos = y_treble + i * line_spacing
    bpy.ops.mesh.primitive_cylinder_add(radius=0.008, depth=300.0, location=(150.0, y_pos, 0.0), rotation=(0, 1.5708, 0))
    line = bpy.context.active_object
    line.active_material = mat_staff

    # Bass Lines
    y_pos_b = y_bass + i * line_spacing
    bpy.ops.mesh.primitive_cylinder_add(radius=0.008, depth=300.0, location=(150.0, y_pos_b, 0.0), rotation=(0, 1.5708, 0))
    line_b = bpy.context.active_object
    line_b.active_material = mat_staff

# 2D Glowing Emission Noteheads and Stems
for idx, note in enumerate(notes):
    pitch = note["pitch"]
    onset = note["onset_time"]
    offset = note["offset_time"]
    x_pos = onset * 2.5
    y_pos = pitch_to_y(pitch)

    import colorsys
    t = (pitch - 21) / 87.0
    hue = (240.0 - t * 240.0) / 360.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.8, 0.95)
    
    mat_note, em_node = create_emission_material(f"NoteEmMat_{{idx}}", (r, g, b, 1.0), strength=2.0)

    # Notehead Disc
    bpy.ops.mesh.primitive_cylinder_add(radius=0.13, depth=0.01, location=(x_pos, y_pos, 0.02), rotation=(1.5708, 0, 0))
    head = bpy.context.active_object
    head.scale = (1.3, 0.5, 0.9)
    head.rotation_euler = (0, 0, math.radians(25))
    head.active_material = mat_note

    # Stem
    stem_dir = 1.0 if pitch < 60 else -1.0
    stem_len = 0.75
    stem_x = x_pos + (0.12 if stem_dir > 0 else -0.12)
    stem_y = y_pos + (stem_len / 2.0) * stem_dir
    bpy.ops.mesh.primitive_cylinder_add(radius=0.012, depth=stem_len, location=(stem_x, stem_y, 0.02))
    stem = bpy.context.active_object
    stem.active_material = mat_note

    # Emission Strength Keyframes (Light Aura Burst)
    if em_node:
        em_prop = em_node.inputs['Strength']
        f_onset = int(onset * 30.0)
        f_offset = int(offset * 30.0)
        
        if f_onset > 1:
            em_prop.default_value = 2.0
            em_prop.keyframe_insert(data_path="default_value", frame=f_onset - 1)
        em_prop.default_value = 16.0
        em_prop.keyframe_insert(data_path="default_value", frame=max(1, f_onset))
        em_prop.default_value = 14.0
        em_prop.keyframe_insert(data_path="default_value", frame=max(2, f_offset))
        em_prop.default_value = 2.0
        em_prop.keyframe_insert(data_path="default_value", frame=f_offset + 1)

# Floating Magic Dust Particle System
mat_part, _ = create_emission_material("PartMat", (0.9, 0.85, 1.0, 1.0), strength=8.0)
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.02, location=(0, 0, -100))
part_shape = bpy.context.active_object
part_shape.active_material = mat_part
part_shape.hide_render = True

bpy.ops.mesh.primitive_plane_add(size=1.0, location=(150.0, 0, -0.3))
emitter = bpy.context.active_object
emitter.scale = (350.0, 10.0, 1.0)
emitter.hide_render = True

psys = emitter.modifiers.new(name="MagicDust", type='PARTICLE_SYSTEM')
p_settings = psys.particle_system.settings
p_settings.count = 300
p_settings.frame_start = 1
p_settings.frame_end = 360
p_settings.lifetime = 150
p_settings.lifetime_random = 0.5
p_settings.render_type = 'OBJECT'
p_settings.instance_object = part_shape
p_settings.particle_size = 0.7
p_settings.size_random = 0.8
p_settings.brownian_factor = 0.6
p_settings.effector_weights.gravity = 0.05

# Cyan Glowing Sweeping Playhead Beam
mat_playhead, _ = create_emission_material("Playhead2D", (0.0, 0.95, 1.0, 1.0), strength=18.0)
bpy.ops.mesh.primitive_cylinder_add(radius=0.02, depth=8.0, location=(0, 0, 0.05))
ph = bpy.context.active_object
ph.active_material = mat_playhead
ph.location.x = 0
ph.keyframe_insert(data_path="location", index=0, frame=1)
ph.location.x = 12.0 * 2.5
ph.keyframe_insert(data_path="location", index=0, frame=360)

# 2D Orthographic Camera tracking score
cam_data = bpy.data.cameras.new("ScoreCamera2D")
cam_data.type = 'ORTHO'
cam_data.ortho_scale = 6.6
cam_obj = bpy.data.objects.new("ScoreCamera2D", cam_data)
bpy.context.collection.objects.link(cam_obj)
scene.camera = cam_obj

cam_obj.location = (1.8, 0.0, 10.0)
cam_obj.rotation_euler = (0, 0, 0)

for frame in range(1, 361):
    t_sec = (frame - 1) / 30.0
    cam_obj.location.x = (t_sec * 2.5) + 1.8
    cam_obj.keyframe_insert(data_path="location", frame=frame)

print("Partitura Cinemática Etérea gerada com sucesso na interface do Blender!")
'''

with open("open_in_blender.py", "w", encoding="utf-8") as f:
    f.write(blender_gui_script)

print("Atualizado open_in_blender.py com Sucesso para Partitura Cinemática Etérea!")
