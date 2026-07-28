import os
import torch
import shutil
import music21
import copy
import numpy as np

# Automatically locate and add ffmpeg to PATH if missing
if not shutil.which("ffmpeg"):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    venv_scripts = os.path.join(script_dir, ".venv", "Scripts")
    if os.path.exists(venv_scripts):
        os.environ["PATH"] = venv_scripts + os.path.pathsep + os.environ["PATH"]

from piano_transcription_inference import PianoTranscription, sample_rate, load_audio
from piano_transcription_inference.utilities import write_events_to_midi, RegressionPostProcessor

def estimate_bpm_and_meter(note_events):
    """
    Estimates the tempo (BPM) and meter (3/4 vs 4/4) directly from note events list.
    """
    onsets = sorted([n['onset_time'] for n in note_events])
    if len(onsets) < 10:
        return 120.0, "4/4"
        
    # Calculate Inter-Onset Intervals (IOIs)
    iois = []
    for i in range(len(onsets) - 1):
        for j in range(i + 1, min(i + 5, len(onsets))):
            diff = onsets[j] - onsets[i]
            if 0.15 < diff < 2.5:
                iois.append(diff)
                
    if not iois:
        return 120.0, "4/4"
        
    # Calculate histogram peak
    bins = np.arange(0.15, 2.5, 0.05)
    counts, bin_edges = np.histogram(iois, bins=bins)
    best_bin = np.argmax(counts)
    peak_ioi = (bin_edges[best_bin] + bin_edges[best_bin+1]) / 2.0
    
    bpm = 60.0 / peak_ioi
    # Normalize to standard 75-150 range
    while bpm < 75.0:
        bpm *= 2.0
    while bpm > 150.0:
        bpm /= 2.0
        
    # Detect Time Signature using downbeat accent periodicity
    # Bass notes (MIDI < 57) usually represent beat 1 (downbeats)
    bass_notes = [n for n in note_events if n['midi_note'] < 57]
    if not bass_notes:
        bass_notes = note_events
        
    bass_onsets = sorted([n['onset_time'] for n in bass_notes])
    if len(bass_onsets) < 4:
        return bpm, "4/4"
        
    beat_len = 60.0 / bpm
    bass_beats = [t / beat_len for t in bass_onsets]
    
    diffs = []
    for i in range(len(bass_beats) - 1):
        diff = bass_beats[i+1] - bass_beats[i]
        diffs.append(diff)
        
    score_3 = 0
    score_4 = 0
    for d in diffs:
        nearest_beat = round(d)
        if nearest_beat == 0:
            continue
        if nearest_beat % 3 == 0:
            score_3 += 1
        if nearest_beat % 4 == 0:
            score_4 += 1
            
    # If 3/4 matches better, label as waltz
    time_sig = "3/4" if (score_3 > score_4 and score_3 >= 2) else "4/4"
    return bpm, time_sig

def calibrate_heuristics(est_note_events):
    """
    Analyzes raw note events and calculates optimal confidence (velocity-based)
    and minimum note duration thresholds to filter noise out.
    """
    if not est_note_events:
        return 0.40, 30.0 # defaults if no notes detected
        
    velocities = [note['velocity'] for note in est_note_events]
    durations = [note['offset_time'] - note['onset_time'] for note in est_note_events]
    
    # Calibrate minimum duration (ms)
    sorted_durations = sorted(durations)
    n_notes = len(sorted_durations)
    pct_15_dur = sorted_durations[int(n_notes * 0.15)]
    
    # Keep minimum duration low (20ms to 40ms) to protect staccato accompaniment notes in waltzes
    if pct_15_dur < 0.04:
        optimal_min_duration = 35.0
    elif pct_15_dur < 0.06:
        optimal_min_duration = 30.0
    else:
        optimal_min_duration = 20.0
        
    # Calibrate confidence threshold (onset threshold)
    avg_velocity = sum(velocities) / len(velocities)
    
    # If the average velocity is low (soft playing), lower the threshold to prevent pruning quiet notes
    if avg_velocity < 45:
        optimal_confidence = 0.38
    elif avg_velocity < 55:
        optimal_confidence = 0.40
    else:
        optimal_confidence = 0.42 # Clean, loud recording
        
    print(f"[Heuristics] Analyzed {len(est_note_events)} raw notes. Avg velocity: {avg_velocity:.1f}. 15th pct duration: {pct_15_dur*1000:.1f}ms.")
    print(f"[Heuristics] Calibrated optimal parameters -> Confidence: {optimal_confidence}, Min Duration: {optimal_min_duration}ms")
    return optimal_confidence, optimal_min_duration

def filter_finger_slips(note_events):
    """
    Filters out short transient notes (duration < 55ms) that overlap with
    another longer note (duration > 100ms) within 120ms, which usually
    represent accidental key grazes (appoggiaturas / slips).
    ONLY filters slips in the TREBLE register (pitch >= 60).
    """
    if len(note_events) < 2:
        return note_events
        
    sorted_notes = sorted(note_events, key=lambda x: x['onset_time'])
    kept = []
    for i, note_ev in enumerate(sorted_notes):
        onset = note_ev['onset_time']
        duration = note_ev['offset_time'] - onset
        pitch = note_ev['midi_note']
        
        # ONLY filter slips in the TREBLE register (pitch >= 60)
        # Left-hand chords or bass lines must never be pruned as slips!
        if pitch >= 60 and duration < 0.055: # < 55ms
            is_slip = False
            # Look at neighbor notes
            for j in range(max(0, i-3), min(len(sorted_notes), i+4)):
                if j == i:
                    continue
                other = sorted_notes[j]
                other_dur = other['offset_time'] - other['onset_time']
                
                # If neighbor is longer and starts within 120ms
                if other_dur > 0.100 and abs(other['onset_time'] - onset) < 0.120:
                    is_slip = True
                    break
            if is_slip:
                print(f"[Slip Filter] Pruned treble transient slip at pitch {pitch} ({duration*1000:.1f}ms)")
                continue
        kept.append(note_ev)
    return kept

def apply_pedal_to_durations(note_events, pedal_events):
    """
    Extends note offset_time if the sustain pedal is held down when the note ends.
    The note is sustained until the pedal is released or the exact same pitch is played again.
    """
    if not pedal_events or not note_events:
        return note_events
        
    notes = sorted(note_events, key=lambda x: x['onset_time'])
    pedals = sorted(pedal_events, key=lambda x: x['onset_time'])
    
    for i, note in enumerate(notes):
        offset = note['offset_time']
        pitch = note['midi_note']
        
        active_pedal_release = None
        for p in pedals:
            if p['onset_time'] <= offset + 0.1 and p['offset_time'] >= offset:
                active_pedal_release = p['offset_time']
                break
                
        if active_pedal_release:
            next_onset = active_pedal_release
            for j in range(i + 1, len(notes)):
                next_note = notes[j]
                if next_note['onset_time'] > active_pedal_release:
                    break
                if next_note['midi_note'] == pitch:
                    # Same pitch played again, cut the sustain here
                    next_onset = next_note['onset_time']
                    break
            
            # Extend duration without exceeding the pedal release time or next identical note
            note['offset_time'] = max(note['offset_time'], next_onset)
            
    return notes

def quantize_and_clean_events(note_events, bpm, allow_triplets=False, time_signature="4/4"):
    """
    Quantizes note onsets and durations in beat-space and fills small silence gaps
    to prevent random loose rests and cluttered voice layouts.
    Uses 1/12 beat resolution if triplets are enabled or time signature is 6/8.
    """
    if not note_events:
        return []
        
    beat_len = 60.0 / bpm
    
    # Sort notes by onset
    sorted_notes = sorted(note_events, key=lambda x: x['onset_time'])
    
    # Determine grid resolution (12.0 allows both binary 1/4 and ternary 1/3 beat snapping)
    grid = 12.0 if (allow_triplets or time_signature == "6/8") else 4.0
    min_dur_beat = 0.25 # minimum 1/16 note (0.25 beats)
    if grid == 12.0:
        min_dur_beat = 0.1667 # 1/6 beat or 1/12 beat
        
    # Convert to beat space
    beat_notes = []
    for n in sorted_notes:
        onset_beat = n['onset_time'] / beat_len
        offset_beat = n['offset_time'] / beat_len
        duration_beat = offset_beat - onset_beat
        
        # Snap onset to grid
        snap_onset = round(onset_beat * grid) / grid
        
        # Round duration to grid
        snap_dur = round(duration_beat * grid) / grid
        if snap_dur < min_dur_beat:
            snap_dur = min_dur_beat
            
        beat_notes.append({
            'onset_beat': snap_onset,
            'duration_beat': snap_dur,
            'offset_beat': snap_onset + snap_dur,
            'midi_note': n['midi_note'],
            'velocity': n['velocity']
        })
        
    # Group by hand (Treble vs Bass) to avoid merging across hands
    treble_notes = [n for n in beat_notes if n['midi_note'] >= 60]
    bass_notes = [n for n in beat_notes if n['midi_note'] < 60]
    
    def clean_hand_rests(notes):
        if not notes:
            return []
            
        # 1. First, resolve overlaps/gaps for the SAME pitch (prevent key double-strikes)
        pitch_groups = {}
        for n in notes:
            pitch_groups.setdefault(n['midi_note'], []).append(n)
            
        for pitch, group in pitch_groups.items():
            group.sort(key=lambda x: x['onset_beat'])
            for i in range(len(group) - 1):
                curr_note = group[i]
                next_note = group[i+1]
                gap = next_note['onset_beat'] - curr_note['offset_beat']
                if gap < 0:
                    # Overlap: trim previous note
                    curr_note['offset_beat'] = next_note['onset_beat']
                    curr_note['duration_beat'] = curr_note['offset_beat'] - curr_note['onset_beat']
                elif gap < 0.35:
                    # Small gap: bridge
                    curr_note['offset_beat'] = next_note['onset_beat']
                    curr_note['duration_beat'] = curr_note['offset_beat'] - curr_note['onset_beat']

        # 2. Bridge small silence gaps between consecutive notes of DIFFERENT pitches
        # We do NOT trim overlaps of different pitches (preserving polyphony / chords / sustained melody)
        notes = sorted(notes, key=lambda x: x['onset_beat'])
        for i in range(len(notes) - 1):
            curr_note = notes[i]
            for j in range(i + 1, min(i + 10, len(notes))):
                next_note = notes[j]
                # Find the next note that starts after the current note's onset
                if next_note['onset_beat'] > curr_note['onset_beat']:
                    gap = next_note['onset_beat'] - curr_note['offset_beat']
                    # If there's a tiny gap (e.g. 0 < gap < 0.25 beats), bridge it
                    if 0 < gap < 0.25:
                        curr_note['offset_beat'] = next_note['onset_beat']
                        curr_note['duration_beat'] = curr_note['offset_beat'] - curr_note['onset_beat']
                    break
        return notes

    cleaned_treble = clean_hand_rests(treble_notes)
    cleaned_bass = clean_hand_rests(bass_notes)
    
    all_cleaned = cleaned_treble + cleaned_bass
    
    # Convert back to seconds at 120 BPM base (which is what we write in the MIDI file for music21 to parse)
    # Since music21 parses the MIDI assuming 120 BPM, 1 beat = 0.5 seconds.
    final_events = []
    for n in all_cleaned:
        final_events.append({
            'onset_time': n['onset_beat'] * 0.5,
            'offset_time': (n['onset_beat'] + n['duration_beat']) * 0.5,
            'midi_note': n['midi_note'],
            'velocity': n['velocity']
        })
        
    return final_events

def split_piano_grand_staff(flat_stream, time_signature=None, bpm=120, split_point=60):
    """
    Splits a flat music21 stream of elements into a Grand Staff (Treble and Bass staves)
    strictly separating chords note-by-note at custom split point.
    Optionally overrides or injects a custom time signature and BPM tempo at offset 0.
    """
    from music21 import stream, note, chord, clef, instrument, key, meter, tempo
    
    score = stream.Score()
    
    # Create treble and bass parts
    right_hand = stream.Part()
    left_hand = stream.Part()
    
    right_hand.id = 'RightHand'
    left_hand.id = 'LeftHand'
    
    # Add piano instrument and appropriate clefs
    right_hand.insert(0, instrument.Piano())
    right_hand.insert(0, clef.TrebleClef())
    
    left_hand.insert(0, instrument.Piano())
    left_hand.insert(0, clef.BassClef())
    
    # Inject custom tempo and time signature at offset 0 (round BPM to avoid long decimals)
    rounded_bpm = round(bpm, 1)
    right_hand.insert(0, tempo.MetronomeMark(number=rounded_bpm))
    left_hand.insert(0, tempo.MetronomeMark(number=rounded_bpm))
    
    if time_signature:
        right_hand.insert(0, meter.TimeSignature(time_signature))
        left_hand.insert(0, meter.TimeSignature(time_signature))
        
    # Dynamic split state
    current_split = split_point

    # Group elements by offset to process chords/simultaneous notes together
    elements_by_offset = {}
    for element in flat_stream:
        if isinstance(element, (meter.TimeSignature, tempo.MetronomeMark)):
            continue
        if isinstance(element, (note.Note, chord.Chord, key.KeySignature)):
            elements_by_offset.setdefault(element.offset, []).append(element)
            
    for offset in sorted(elements_by_offset.keys()):
        group = elements_by_offset[offset]
        
        for element in group:
            if isinstance(element, key.KeySignature):
                right_hand.insert(offset, copy.deepcopy(element))
                left_hand.insert(offset, copy.deepcopy(element))
                continue
                
            # Extract all pitches at this offset for dynamic split evaluation
            pitches = []
            if isinstance(element, note.Note):
                pitches.append(element.pitch.midi)
            elif isinstance(element, chord.Chord):
                pitches.extend([p.midi for p in element.pitches])
                
            if pitches:
                # If we have multiple pitches, find a large gap to split
                pitches.sort()
                if len(pitches) >= 2:
                    max_gap = 0
                    best_split = current_split
                    for i in range(len(pitches) - 1):
                        gap = pitches[i+1] - pitches[i]
                        if gap > max_gap:
                            max_gap = gap
                            # Split exactly in the middle of the largest gap
                            best_split = pitches[i] + (gap / 2.0)
                            
                    # Smooth the split point (inertia)
                    if max_gap >= 7: # at least a fifth gap to justify moving split
                        current_split = (current_split * 0.7) + (best_split * 0.3)
                else:
                    # Single note: slightly pull split point towards center if it's very far
                    p = pitches[0]
                    if p > 72: # very high
                        current_split = (current_split * 0.9) + (65 * 0.1)
                    elif p < 48: # very low
                        current_split = (current_split * 0.9) + (55 * 0.1)
            
            # Now assign notes
            if isinstance(element, note.Note):
                if element.pitch.midi >= current_split:
                    right_hand.insert(offset, element)
                else:
                    left_hand.insert(offset, element)
            elif isinstance(element, chord.Chord):
                treble_pitches = [p for p in element.pitches if p.midi >= current_split]
                bass_pitches = [p for p in element.pitches if p.midi < current_split]
                
                if treble_pitches:
                    n_dur = copy.deepcopy(element.duration)
                    if len(treble_pitches) == 1:
                        n = note.Note(treble_pitches[0], duration=n_dur)
                    else:
                        n = chord.Chord(treble_pitches, duration=n_dur)
                    right_hand.insert(offset, n)
                if bass_pitches:
                    n_dur = copy.deepcopy(element.duration)
                    if len(bass_pitches) == 1:
                        n = note.Note(bass_pitches[0], duration=n_dur)
                    else:
                        n = chord.Chord(bass_pitches, duration=n_dur)
                    left_hand.insert(offset, n)

    # Post-process parts to remove polyphonic overlaps and excessive rests
    def remove_polyphonic_overlaps(part):
        from music21 import note, chord
        import copy
        
        elements_by_offset = {}
        for el in list(part.recurse()):
            if isinstance(el, (note.Note, chord.Chord)):
                elements_by_offset.setdefault(el.offset, []).append(el)
                part.remove(el)
                
        if not elements_by_offset:
            return
            
        sorted_offsets = sorted(elements_by_offset.keys())
        for idx, offset in enumerate(sorted_offsets):
            group = elements_by_offset[offset]
            pitches = []
            duration = None
            
            for el in group:
                if isinstance(el, note.Note):
                    pitches.append(el.pitch)
                    if duration is None or el.duration.quarterLength > duration.quarterLength:
                        duration = el.duration
                elif isinstance(el, chord.Chord):
                    pitches.extend(el.pitches)
                    if duration is None or el.duration.quarterLength > duration.quarterLength:
                        duration = el.duration
                        
            unique_pitches = list(set(pitches))
            if not unique_pitches:
                continue
                
            if len(unique_pitches) == 1:
                new_el = note.Note(unique_pitches[0])
            else:
                new_el = chord.Chord(unique_pitches)
                
            new_el.duration = copy.deepcopy(duration)
            
            if idx < len(sorted_offsets) - 1:
                next_offset = sorted_offsets[idx+1]
                gap = next_offset - offset
                if gap > 0 and new_el.quarterLength > gap:
                    new_el.quarterLength = gap
                    
            part.insert(offset, new_el)

    remove_polyphonic_overlaps(right_hand)
    remove_polyphonic_overlaps(left_hand)
    
    score.insert(0, right_hand)
    score.insert(0, left_hand)
    
    return score

def post_process_and_save_midi(
    output_dict_path: str,
    raw_midi_path: str,
    confidence_threshold: float,
    min_duration_ms: float,
    bpm: float,
    filter_slips: bool = True,
    allow_triplets: bool = False,
    time_signature: str = "4/4"
):
    """
    Instantiates the RegressionPostProcessor with the custom confidence threshold,
    extracts the notes, runs the filtering/quantization pipeline, and saves raw MIDI.
    Runs in under 15ms by loading already estimated neural network activations.
    """
    print(f"Loading neural activations from {output_dict_path}...")
    with np.load(output_dict_path) as data:
        output_dict = {key: data[key] for key in data.files}
        
    print(f"Running RegressionPostProcessor (onset threshold: {confidence_threshold})...")
    post_processor = RegressionPostProcessor(
        frames_per_second=100,
        classes_num=88,
        onset_threshold=confidence_threshold,
        offset_threshold=confidence_threshold,
        frame_threshold=max(0.05, confidence_threshold - 0.2),
        pedal_offset_threshold=0.2
    )
    
    (est_note_events, est_pedal_events) = post_processor.output_dict_to_midi_events(output_dict)
    
    min_duration_sec = min_duration_ms / 1000.0
    filtered_note_events = []
    for note in est_note_events:
        duration = note['offset_time'] - note['onset_time']
        if duration >= min_duration_sec:
            filtered_note_events.append(note)
            
    print(f"Filtered {len(est_note_events) - len(filtered_note_events)} notes. Kept {len(filtered_note_events)} notes.")
    
    # Filter out short finger slip transients (accidental grazes / appoggiaturas) if requested
    if filter_slips:
        clean_note_events = filter_finger_slips(filtered_note_events)
    else:
        clean_note_events = filtered_note_events
        
    # Apply pedal-to-duration to merge sustained notes
    clean_note_events = apply_pedal_to_durations(clean_note_events, est_pedal_events)
    
    # Pre-quantize and clean up legato overlaps and small silence gaps
    cleaned_note_events = quantize_and_clean_events(clean_note_events, bpm, allow_triplets=allow_triplets, time_signature=time_signature)
    
    # Pedal events scaling
    scale_ratio = bpm / 120.0
    scaled_pedal_events = []
    for pedal in est_pedal_events:
        scaled_pedal = copy.deepcopy(pedal)
        scaled_pedal['onset_time'] = pedal['onset_time'] * scale_ratio
        scaled_pedal['offset_time'] = pedal['offset_time'] * scale_ratio
        scaled_pedal_events.append(scaled_pedal)
        
    # Write raw MIDI
    write_events_to_midi(
        start_time=0.0,
        note_events=cleaned_note_events,
        pedal_events=scaled_pedal_events,
        midi_path=raw_midi_path
    )
    print("Raw MIDI updated successfully.")

def transcribe_audio_to_raw_midi(
    audio_path: str,
    raw_midi_path: str,
    output_dict_path: str,
    confidence_threshold: float = 0.5,
    min_duration_ms: float = 50.0,
    auto_calibrate: bool = False,
    time_signature: str = "auto",
    bpm: str = "auto",
    filter_slips: bool = True,
    allow_triplets: bool = False
):
    """
    Step 1: Runs the AI model and outputs a raw, duration-filtered, tempo-scaled MIDI file.
    """
    # Select hardware acceleration
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print(f"Initializing PianoTranscription on device: {device}")
    
    transcriptor = PianoTranscription(device=device)
    
    # Load audio
    print(f"Loading audio from {audio_path}...")
    audio, _ = load_audio(audio_path, sr=sample_rate, mono=True)
    audio_duration = float(len(audio)) / sample_rate
    
    # Run transcription
    print("Running ByteDance Onsets & Frames transcription...")
    transcribed_dict = transcriptor.transcribe(audio, None) # don't write midi from internal method
    output_dict = transcribed_dict['output_dict']
    
    # Save raw activations (sigmoids) to compressed NPZ file
    print(f"Saving neural activations to {output_dict_path}...")
    np.savez_compressed(output_dict_path, **output_dict)
    
    # Get estimated note and pedal lists (default model threshold)
    est_note_events = transcribed_dict.get('est_note_events', [])
    
    # Auto-detect BPM and Time Signature from raw note events
    detected_bpm, detected_meter = estimate_bpm_and_meter(est_note_events)
    print(f"[Auto-Detection] Estimated BPM: {detected_bpm:.1f}, Estimated Meter: {detected_meter}")
    
    # Resolve values (auto vs manual)
    final_bpm = detected_bpm if bpm == "auto" else float(bpm)
    final_meter = detected_meter if time_signature == "auto" else time_signature
    
    # Calibrate thresholds if auto-calibrate is True
    if auto_calibrate:
        opt_conf, opt_min_dur = calibrate_heuristics(est_note_events)
        confidence_threshold = opt_conf
        min_duration_ms = opt_min_dur
        
    # Execute the post-processing helper
    post_process_and_save_midi(
        output_dict_path=output_dict_path,
        raw_midi_path=raw_midi_path,
        confidence_threshold=confidence_threshold,
        min_duration_ms=min_duration_ms,
        bpm=final_bpm,
        filter_slips=filter_slips,
        allow_triplets=allow_triplets,
        time_signature=final_meter
    )
    
    return confidence_threshold, min_duration_ms, final_bpm, final_meter, audio_duration

def quantize_and_export(
    raw_midi_path: str,
    output_xml_path: str,
    output_midi_path: str,
    quantize_grid: float = 0.25,
    time_signature: str = "4/4",
    bpm: float = 120.0,
    split_point: int = 60,
    allow_triplets: bool = False
):
    """
    Step 2: Takes a raw MIDI file, quantizes it at import time, splits it, and exports formats.
    """
    # Map quantize grid to snap thresholds (divisors)
    divisors = []
    if quantize_grid <= 0.25:
        # snap to 1/16, 1/8, or 1/4 note (divisors 4, 2, 1)
        divisors = [4, 2, 1]
    elif quantize_grid <= 0.5:
        # snap to 1/8 or 1/4 note (divisors 2, 1)
        divisors = [2, 1]
    else:
        # snap to 1/4 note (divisor 1)
        divisors = [1]
        
    # Add triplet support (divisor 3) if allow_triplets is checked, or if time signature is 6/8
    if allow_triplets or time_signature == "6/8":
        divisors.append(3)
        
    divisors = tuple(sorted(list(set(divisors)), reverse=True))
    
    print(f"Loading and quantizing raw MIDI from {raw_midi_path} with divisors: {divisors}...")
    # Parse and quantize at the same time to strictly restrict time grid and remove tuplets
    score_stream = music21.converter.parse(
        raw_midi_path,
        quantizePost=True,
        quarterLengthDivisors=divisors
    )
    
    # Split into Treble and Bass staves (Grand Staff) using the custom split_point
    print(f"Splitting quantized stream with time signature {time_signature}, tempo {bpm} BPM, and split boundary {split_point}...")
    flat_stream = score_stream.flatten()
    split_score = split_piano_grand_staff(flat_stream, time_signature=time_signature, bpm=bpm, split_point=split_point)
    
    print("Making well-formed notation with measures and ties...")
    split_score.makeNotation(inPlace=True)
    
    # Export formats
    print(f"Exporting MusicXML to: {output_xml_path}")
    split_score.write('musicxml', fp=output_xml_path)
    
    print(f"Exporting quantized MIDI to: {output_midi_path}")
    split_score.write('midi', fp=output_midi_path)
    print("Quantization and export complete!")
