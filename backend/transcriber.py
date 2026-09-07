import os
import torch
import shutil
import music21
import copy
import numpy as np

import scipy.signal
if not hasattr(scipy.signal, 'hann') and hasattr(scipy.signal, 'windows'):
    scipy.signal.hann = scipy.signal.windows.hann

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
    
    # Check note density
    total_dur = max(1.0, onsets[-1] - onsets[0])
    note_density = len(note_events) / total_dur
    
    # If density is low (< 3.2 notes/s) and bpm > 110, the peak IOI was likely eighth notes,
    # not the quarter note beat unit (e.g. Chopin Ballade Largo/Moderato accompaniment)
    if note_density < 3.2 and bpm > 110.0:
        bpm /= 2.0
        
    # Allow slow tempos down to 45 BPM if density is low (< 3.5 notes/s)
    min_bpm = 45.0 if note_density < 3.5 else 70.0
    while bpm < min_bpm:
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

def filter_acoustic_overtones_and_duplicates(note_events):
    """
    Filters out ghost notes caused by acoustic piano harmonic overtones and duplicate note triggers:
    1. Merges duplicate note events on the exact same pitch within 45ms.
    2. Prunes acoustic overtone ghosts (+12, +19, +24 semitones) that have weak velocity
       compared to the fundamental note strike.
    """
    if len(note_events) < 2:
        return note_events
        
    # 1. Merge exact duplicate note events on same pitch within 45ms
    sorted_notes = sorted(note_events, key=lambda x: (x['onset_time'], x['midi_note']))
    deduped = []
    skip_indices = set()
    for i in range(len(sorted_notes)):
        if i in skip_indices:
            continue
        n1 = sorted_notes[i]
        merged_n = dict(n1)
        for j in range(i + 1, min(i + 8, len(sorted_notes))):
            n2 = sorted_notes[j]
            if n2['onset_time'] - n1['onset_time'] > 0.045:
                break
            if n2['midi_note'] == n1['midi_note']:
                # Duplicate trigger: merge duration and take max velocity
                merged_n['offset_time'] = max(merged_n['offset_time'], n2['offset_time'])
                merged_n['velocity'] = max(merged_n['velocity'], n2['velocity'])
                skip_indices.add(j)
        deduped.append(merged_n)

    # 2. Filter sympathetic resonance & acoustic overtone ghosts (+12, +19, +24 st)
    deduped.sort(key=lambda x: x['onset_time'])
    pruned_indices = set()
    for i, fund in enumerate(deduped):
        f_onset = fund['onset_time']
        f_pitch = fund['midi_note']
        f_vel = fund['velocity']
        
        for j in range(i + 1, min(i + 20, len(deduped))):
            cand = deduped[j]
            if cand['onset_time'] - f_onset > 0.045:
                break
            interval = cand['midi_note'] - f_pitch
            c_vel = cand['velocity']
            
            # Twelfth (+19 st): 3rd harmonic
            if interval == 19 and (c_vel < 0.65 * f_vel or c_vel < 45):
                pruned_indices.add(j)
            # Double octave (+24 st): 4th harmonic
            elif interval == 24 and (c_vel < 0.55 * f_vel or c_vel < 40):
                pruned_indices.add(j)
            # Octave (+12 st): 2nd harmonic
            # Real played octaves have comparable velocity (c_vel >= 0.6 * f_vel).
            elif interval == 12:
                if f_pitch < 48 and c_vel < 45 and (f_vel - c_vel) >= 18:
                    pruned_indices.add(j)
                elif c_vel < 0.48 * f_vel and c_vel < 38:
                    pruned_indices.add(j)
                    
    clean = [n for idx, n in enumerate(deduped) if idx not in pruned_indices]
    if len(clean) != len(note_events):
        print(f"[Acoustic Filter] Processed {len(note_events)} -> {len(clean)} notes ({len(note_events) - len(clean)} duplicates/ghosts removed).")
    return clean

filter_sympathetic_harmonics = filter_acoustic_overtones_and_duplicates

def merge_decay_reattacks(notes, min_reattack_vel_ratio=0.75, min_reattack_abs_vel=38):
    """
    Merges false re-attacks caused by sustain pedal resonance and amplitude decay fluctuations.
    CRITICAL:
    1. Distance is measured from onset to onset (<= 0.32s), NOT from previous offset.
    2. Multi-note chord attacks (>= 2 simultaneous onsets) are genuine musical chords and are NEVER merged.
    """
    if len(notes) < 2:
        return notes
        
    sorted_by_onset = sorted(notes, key=lambda x: x['onset_time'])
    chord_onsets = set()
    for i in range(len(sorted_by_onset) - 1):
        if sorted_by_onset[i+1]['onset_time'] - sorted_by_onset[i]['onset_time'] < 0.045:
            chord_onsets.add(round(sorted_by_onset[i]['onset_time'], 2))
            chord_onsets.add(round(sorted_by_onset[i+1]['onset_time'], 2))

    sorted_notes = sorted(notes, key=lambda x: (x['midi_note'], x['onset_time']))
    merged = []
    skip = set()
    for i in range(len(sorted_notes)):
        if i in skip:
            continue
        n1 = dict(sorted_notes[i])
        for j in range(i + 1, min(i + 6, len(sorted_notes))):
            n2 = sorted_notes[j]
            if n2['midi_note'] != n1['midi_note']:
                break
                
            attack_gap = n2['onset_time'] - n1['onset_time']
            if attack_gap > 0.32:
                break
                
            n2_round_t = round(n2['onset_time'], 2)
            if n2_round_t in chord_onsets and len([x for x in sorted_by_onset if abs(x['onset_time'] - n2['onset_time']) < 0.045]) >= 2:
                break
                
            if n2['velocity'] < min_reattack_abs_vel or n2['velocity'] <= n1['velocity'] * min_reattack_vel_ratio:
                n1['offset_time'] = max(n1['offset_time'], n2['offset_time'])
                skip.add(j)
            else:
                break
        merged.append(n1)
    return sorted(merged, key=lambda x: x['onset_time'])

def trim_melodic_step_overlaps(notes):
    """
    Enforces voice-leading monophonic release between consecutive melodic steps.
    If a note in the melodic register (pitch >= 58) is followed by a stepwise note
    (+/- 1 or 2 semitones, or octave-displaced stepwise intervals 11, 13 semitones),
    the previous note's sustain is released when the new step is struck, preventing
    accidental minor-second cluster collisions in sheet music chords.
    """
    sorted_notes = sorted(notes, key=lambda x: x['onset_time'])
    for i in range(len(sorted_notes) - 1):
        n1 = sorted_notes[i]
        for j in range(i + 1, min(i + 8, len(sorted_notes))):
            n2 = sorted_notes[j]
            if n2['onset_time'] - n1['onset_time'] > 1.5:
                break
            diff = abs(n2['midi_note'] - n1['midi_note'])
            if n1['midi_note'] >= 58 and diff in (1, 2):
                if n1['offset_time'] > n2['onset_time']:
                    n1['offset_time'] = n2['onset_time']
            elif n1['midi_note'] >= 58 and diff in (11, 13):
                if n1['offset_time'] > n2['onset_time']:
                    n1['offset_time'] = n2['onset_time']
    return sorted_notes

def apply_pedal_to_durations(note_events, pedal_events, max_pedal_extension: float = 0.35):
    """
    Extends note offset_time if the sustain pedal is held down when the note ends.
    Capped at max_pedal_extension (default 0.35s) to avoid unreadable cross-measure tied notes,
    while still smoothing legato lines and acoustic resonance.
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
            next_onset = min(active_pedal_release, offset + max_pedal_extension)
            for j in range(i + 1, len(notes)):
                next_note = notes[j]
                if next_note['onset_time'] > active_pedal_release:
                    break
                if next_note['midi_note'] == pitch:
                    # Same pitch played again, cut the sustain here
                    next_onset = min(next_onset, next_note['onset_time'])
                    break
            
            # Extend duration without exceeding the pedal release time or next identical note
            note['offset_time'] = max(note['offset_time'], next_onset)
            
    return notes

def quantize_and_clean_events(note_events, bpm, allow_triplets=False, time_signature="4/4", beat_times_sec=None):
    """
    Quantizes note onsets and durations in beat-space and fills small silence gaps
    to prevent random loose rests and cluttered voice layouts.
    Uses 1/12 beat resolution if triplets are enabled or time signature is 6/8.
    """
    if not note_events:
        return []
        
    if beat_times_sec is not None and len(beat_times_sec) >= 2:
        avg_ioi = np.mean(np.diff(beat_times_sec))
        librosa_bpm = 60.0 / max(0.01, avg_ioi)
        # If detected bpm is slow (< 85 BPM) but librosa tracked eighth notes (> 115 BPM), downsample to beat unit
        if bpm < 85.0 and librosa_bpm > 115.0:
            beat_times_sec = beat_times_sec[::2]
        beat_indices = np.arange(len(beat_times_sec))
        def get_beat(t):
            if t <= beat_times_sec[0]:
                bpm_first = 60.0 / max(0.1, (beat_times_sec[1] - beat_times_sec[0]))
                return (t - beat_times_sec[0]) * (bpm_first / 60.0)
            elif t >= beat_times_sec[-1]:
                bpm_last = 60.0 / max(0.1, (beat_times_sec[-1] - beat_times_sec[-2]))
                return beat_indices[-1] + (t - beat_times_sec[-1]) * (bpm_last / 60.0)
            else:
                return np.interp(t, beat_times_sec, beat_indices)
    else:
        beat_len = 60.0 / max(1.0, bpm)
        def get_beat(t):
            return t / beat_len
    
    # Sort notes by onset
    sorted_notes = sorted(note_events, key=lambda x: x['onset_time'])
    
    # Convert notes to beat space and analyze beat-level triplet density (Dorico-style)
    notes_with_beats = []
    for n in sorted_notes:
        onset_beat = float(get_beat(n['onset_time']))
        offset_beat = float(get_beat(n['offset_time']))
        notes_with_beats.append({
            'raw_onset_beat': onset_beat,
            'raw_offset_beat': offset_beat,
            'midi_note': n['midi_note'],
            'velocity': n['velocity']
        })
        
    triplet_beats = set()
    if allow_triplets or time_signature == "6/8":
        beats_map = {}
        for n in notes_with_beats:
            b_floor = int(np.floor(n['raw_onset_beat']))
            beats_map.setdefault(b_floor, []).append(n['raw_onset_beat'])
            
        for b_floor, onsets in beats_map.items():
            if len(onsets) >= 3:
                unique_onsets = sorted(list(set([round(o, 2) for o in onsets])))
                if len(unique_onsets) >= 3:
                    diffs = np.diff(unique_onsets)
                    triplet_matches = sum(1 for d in diffs if 0.23 <= d <= 0.42)
                    if triplet_matches >= 2:
                        triplet_beats.add(b_floor)
                        print(f"[Dorico Quantizer] Detected genuine triplet group in beat {b_floor}")

    beat_notes = []
    for n in notes_with_beats:
        b_floor = int(np.floor(n['raw_onset_beat']))
        grid = 12.0 if b_floor in triplet_beats else 4.0
        min_dur_beat = 0.1667 if grid == 12.0 else 0.25
        
        onset_beat = n['raw_onset_beat']
        duration_beat = max(0.01, n['raw_offset_beat'] - onset_beat)
        
        snap_onset = round(onset_beat * grid) / grid
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
        
    def unify_chord_onsets(notes, window_beats=0.10):
        if not notes:
            return notes
        notes = sorted(notes, key=lambda x: x['onset_beat'])
        clusters = []
        curr_cluster = [notes[0]]
        for i in range(1, len(notes)):
            n = notes[i]
            if n['onset_beat'] - curr_cluster[0]['onset_beat'] <= window_beats:
                curr_cluster.append(n)
            else:
                clusters.append(curr_cluster)
                curr_cluster = [n]
        if curr_cluster:
            clusters.append(curr_cluster)
            
        unified = []
        for c in clusters:
            target_onset = c[0]['onset_beat']
            for n in c:
                diff = n['onset_beat'] - target_onset
                n['onset_beat'] = target_onset
                n['offset_beat'] -= diff
                unified.append(n)
        return unified

    # Group by hand (Treble vs Bass) to avoid merging across hands and unify micro-arpeggios into chords
    treble_notes = unify_chord_onsets([n for n in beat_notes if n['midi_note'] >= 60])
    bass_notes = unify_chord_onsets([n for n in beat_notes if n['midi_note'] < 60])
    
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
                # If same pitch struck within 0.15 beats (< 120ms), merge stutter/double-trigger
                if next_note['onset_beat'] - curr_note['onset_beat'] < 0.15:
                    next_note['onset_beat'] = min(curr_note['onset_beat'], next_note['onset_beat'])
                    next_note['offset_beat'] = max(curr_note['offset_beat'], next_note['offset_beat'])
                    next_note['duration_beat'] = next_note['offset_beat'] - next_note['onset_beat']
                    curr_note['duration_beat'] = 0.0 # mark as merged
                    continue
                gap = next_note['onset_beat'] - curr_note['offset_beat']
                if gap < 0:
                    # Overlap: trim previous note
                    curr_note['offset_beat'] = next_note['onset_beat']
                    curr_note['duration_beat'] = curr_note['offset_beat'] - curr_note['onset_beat']
                elif gap < 0.35:
                    # Small gap: bridge
                    curr_note['offset_beat'] = next_note['onset_beat']
                    curr_note['duration_beat'] = curr_note['offset_beat'] - curr_note['onset_beat']
                    
        notes = [n for n in notes if n['duration_beat'] >= 0.05]

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

    def compress_global_fermata_silences(notes, max_gap_beats=1.5):
        """
        Compresses long global pauses (> 1.5 beats across all voices) caused by performer
        fermatas or phrase-end breaths so they don't spawn completely empty ghost measures.
        """
        if not notes:
            return notes
        sorted_notes = sorted(notes, key=lambda x: x['onset_beat'])
        compressed = []
        cumulative_shift = 0.0
        current_max_offset = 0.0
        for i, n in enumerate(sorted_notes):
            n_copy = dict(n)
            n_copy['onset_beat'] -= cumulative_shift
            n_copy['offset_beat'] -= cumulative_shift
            if i > 0:
                silence = n_copy['onset_beat'] - current_max_offset
                if silence > max_gap_beats:
                    excess = silence - max_gap_beats
                    excess_snapped = round(excess)
                    if excess_snapped >= 1.0:
                        cumulative_shift += excess_snapped
                        n_copy['onset_beat'] -= excess_snapped
                        n_copy['offset_beat'] -= excess_snapped
                        print(f"[Fermata Compressor] Compressed pause of {silence:.2f} beats by {excess_snapped} beats at beat {current_max_offset:.2f}")
            compressed.append(n_copy)
            current_max_offset = max(current_max_offset, n_copy['offset_beat'])
        return compressed

    cleaned_treble = clean_hand_rests(treble_notes)
    cleaned_bass = clean_hand_rests(bass_notes)
    
    all_cleaned = compress_global_fermata_silences(cleaned_treble + cleaned_bass, max_gap_beats=1.5)
    
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
        
    # Find offsets where true bass notes (<= 48 / C3) sound to track active LH accompaniment
    bass_offsets = set()
    for element in flat_stream:
        if isinstance(element, (note.Note, chord.Chord)):
            for p in element.pitches:
                if p.midi <= 48:
                    bass_offsets.add(element.offset)
                    
    def has_nearby_bass(off, window=3.0):
        return any(abs(off - b_off) <= window for b_off in bass_offsets)
        
    current_split = float(split_point)

    # Group elements by offset to process chords/simultaneous notes together
    elements_by_offset = {}
    for element in flat_stream:
        if isinstance(element, (meter.TimeSignature, tempo.MetronomeMark)):
            continue
        if isinstance(element, (note.Note, chord.Chord, key.KeySignature)):
            elements_by_offset.setdefault(element.offset, []).append(element)
            
    for offset in sorted(elements_by_offset.keys()):
        group = elements_by_offset[offset]
        
        # Check for KeySignatures
        for element in group:
            if isinstance(element, key.KeySignature):
                right_hand.insert(offset, copy.deepcopy(element))
                left_hand.insert(offset, copy.deepcopy(element))
                
        # Gather all pitches and max duration at this offset
        all_pitches = []
        max_dur = None
        for el in group:
            if isinstance(el, (note.Note, chord.Chord)):
                all_pitches.extend([p.midi for p in el.pitches])
                if max_dur is None or el.duration.quarterLength > max_dur.quarterLength:
                    max_dur = el.duration
                    
        if not all_pitches:
            continue
            
        all_pitches = sorted(list(set(all_pitches)))
        
        # Eliminate any minor-second semitone clashes (notehead collisions in chords)
        if len(all_pitches) >= 2:
            no_clashes = [all_pitches[0]]
            for p in all_pitches[1:]:
                if p - no_clashes[-1] == 1:
                    # In melodic treble, prefer higher pitch; in bass, prefer lower fundamental root
                    if p >= 60:
                        no_clashes[-1] = p
                    continue
                no_clashes.append(p)
            all_pitches = no_clashes
            
        span = all_pitches[-1] - all_pitches[0]
        
        # 1. Octave Binding: 2 pitches exactly 12 or 24 semitones apart
        if len(all_pitches) == 2 and (all_pitches[1] - all_pitches[0] in (12, 24)):
            min_p = all_pitches[0]
            if min_p >= 48 or not has_nearby_bass(offset):
                n = chord.Chord(all_pitches, duration=copy.deepcopy(max_dur))
                right_hand.insert(offset, n)
            else:
                n = chord.Chord(all_pitches, duration=copy.deepcopy(max_dur))
                left_hand.insert(offset, n)
            continue
            
        # 2. Compact Hand Span (span <= 14 semitones - played by one hand)
        if span <= 14:
            min_p = all_pitches[0]
            max_p = all_pitches[-1]
            n = note.Note(all_pitches[0], duration=copy.deepcopy(max_dur)) if len(all_pitches) == 1 else chord.Chord(all_pitches, duration=copy.deepcopy(max_dur))
            if min_p >= 53 or not has_nearby_bass(offset):
                right_hand.insert(offset, n)
            elif max_p <= 55:
                left_hand.insert(offset, n)
            elif min_p >= 48:
                right_hand.insert(offset, n)
            else:
                left_hand.insert(offset, n)
            continue
            
        # 3. Wide Multi-Voice (span > 14 semitones - split across hands)
        treble_pitches = [p for p in all_pitches if p >= current_split]
        bass_pitches = [p for p in all_pitches if p < current_split]
        
        # Grand Staff Hand Balancing
        if not bass_pitches and len(all_pitches) >= 3:
            if all_pitches[0] <= 65:
                bass_pitches = [all_pitches[0]]
                treble_pitches = all_pitches[1:]
                
        if treble_pitches:
            n = note.Note(treble_pitches[0], duration=copy.deepcopy(max_dur)) if len(treble_pitches) == 1 else chord.Chord(treble_pitches, duration=copy.deepcopy(max_dur))
            right_hand.insert(offset, n)
        if bass_pitches:
            n = note.Note(bass_pitches[0], duration=copy.deepcopy(max_dur)) if len(bass_pitches) == 1 else chord.Chord(bass_pitches, duration=copy.deepcopy(max_dur))
            left_hand.insert(offset, n)

    # Post-process parts to remove polyphonic overlaps and excessive rests
    def remove_polyphonic_overlaps(part):
        from music21 import note, chord, duration
        from fractions import Fraction
        import copy
        
        elements_by_offset = {}
        for el in list(part.recurse()):
            if isinstance(el, (note.Note, chord.Chord)):
                # Quantize offset to 12th note fraction (preserves 16ths, 8ths, triplets without irrational float decimals)
                rounded_offset = Fraction(round(float(el.offset) * 12), 12)
                elements_by_offset.setdefault(rounded_offset, []).append(el)
                part.remove(el)
                
        if not elements_by_offset:
            return
            
        sorted_offsets = sorted(elements_by_offset.keys())
        for idx, offset in enumerate(sorted_offsets):
            group = elements_by_offset[offset]
            pitches = []
            dur = None
            
            for el in group:
                if isinstance(el, note.Note):
                    pitches.append(el.pitch)
                    if dur is None or el.duration.quarterLength > dur.quarterLength:
                        dur = el.duration
                elif isinstance(el, chord.Chord):
                    pitches.extend(el.pitches)
                    if dur is None or el.duration.quarterLength > dur.quarterLength:
                        dur = el.duration
                        
            unique_pitches = list(set(pitches))
            if not unique_pitches:
                continue
                
            if len(unique_pitches) == 1:
                new_el = note.Note(unique_pitches[0])
            else:
                new_el = chord.Chord(unique_pitches)
                
            new_el.duration = copy.deepcopy(dur)
            
            if idx < len(sorted_offsets) - 1:
                next_offset = sorted_offsets[idx+1]
                gap = next_offset - offset
                if gap > 0 and new_el.quarterLength > gap:
                    standard_lengths = [
                        Fraction(1, 6), Fraction(1, 4), Fraction(1, 3), Fraction(3, 8),
                        Fraction(1, 2), Fraction(2, 3), Fraction(3, 4), Fraction(1, 1),
                        Fraction(3, 2), Fraction(2, 1), Fraction(3, 1), Fraction(4, 1)
                    ]
                    valid = [s for s in standard_lengths if s <= gap + 0.02]
                    chosen_dur = max(valid) if valid else Fraction(1, 4)
                    new_el.duration = duration.Duration(chosen_dur)
                    
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
        
    # Filter sympathetic resonance ghost harmonics from acoustic piano bass strikes
    clean_note_events = filter_sympathetic_harmonics(clean_note_events)
    
    # Merge false re-attacks from sustain pedal decay / string amplitude beating
    clean_note_events = merge_decay_reattacks(clean_note_events)
    
    # Trim voice-leading step overlaps (monophonic release in melodic register: eliminates semitone clashes)
    clean_note_events = trim_melodic_step_overlaps(clean_note_events)
        
    # Apply pedal-to-duration to merge sustained notes (capped at 0.35s to prevent excessive ties)
    clean_note_events = apply_pedal_to_durations(clean_note_events, est_pedal_events, max_pedal_extension=0.35)
    
    # Re-apply trim after pedal extension to guarantee zero chord collisions
    clean_note_events = trim_melodic_step_overlaps(clean_note_events)
    
    # Fetch beat_times_sec if available in output_dict
    beat_times_sec = output_dict.get('beat_times_sec', None)
    
    # Pre-quantize and clean up legato overlaps and small silence gaps
    cleaned_note_events = quantize_and_clean_events(
        clean_note_events, bpm, allow_triplets=allow_triplets, 
        time_signature=time_signature, beat_times_sec=beat_times_sec
    )
    
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
    
    print("Extracting tempo map (beat track)...")
    import librosa
    _, beat_frames = librosa.beat.beat_track(y=audio, sr=sample_rate)
    beat_times_sec = librosa.frames_to_time(beat_frames, sr=sample_rate)
    if len(beat_times_sec) >= 2:
        output_dict['beat_times_sec'] = beat_times_sec
    
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

def respell_pitch(p, target_step_acc):
    """Respells pitch while preserving exact MIDI value and octave."""
    orig_midi = p.midi
    for oct_cand in [p.octave, p.octave + 1, p.octave - 1]:
        try:
            test_p = music21.pitch.Pitch(f'{target_step_acc}{oct_cand}')
            if test_p.midi == orig_midi:
                return test_p
        except Exception:
            pass
    return p

def apply_diatonic_enharmonics(score_or_stream, key_obj):
    """
    Applies contextual diatonic enharmonic spelling to all notes and chords
    based on the analyzed key signature (e.g. Ab and Bb in G minor / Eb major).
    """
    if key_obj is None:
        return
        
    scale_pitch_classes = {p.pitchClass: p.name for p in key_obj.pitches}
    
    if key_obj.sharps < 0: # Flat keys (F, Bb, Eb, Ab, Db, Gm, Cm, Fm, etc.)
        accidentals = {
            1: 'C#' if key_obj.mode == 'minor' and key_obj.tonic.name == 'G' else 'D-', # C# in G minor (leading tone to D)
            3: 'E-', # Eb
            4: 'E',
            6: 'F#' if key_obj.mode == 'minor' and key_obj.tonic.name in ('G', 'C') else 'G-',
            8: 'A-', # Ab
            10: 'B-', # Bb
            11: 'B' if key_obj.mode == 'minor' else 'C-'
        }
    else: # Sharp keys or C major
        accidentals = {
            1: 'C#',
            3: 'D#',
            6: 'F#',
            8: 'G#',
            10: 'B-' if key_obj.sharps == 0 else 'A#'
        }
        
    preferred_map = dict(accidentals)
    preferred_map.update(scale_pitch_classes)
    
    for el in score_or_stream.recurse().notes:
        if isinstance(el, music21.note.Note):
            target = preferred_map.get(el.pitch.pitchClass)
            if target and el.pitch.name != target:
                orig_tie = el.tie
                el.pitch = respell_pitch(el.pitch, target)
                el.tie = orig_tie
        elif isinstance(el, music21.chord.Chord):
            orig_chord_tie = el.tie
            orig_pitch_ties = [el.getTie(p) for p in el.pitches]
            new_pitches = []
            chord_pcs = {p.pitchClass for p in el.pitches}
            for p in el.pitches:
                target = preferred_map.get(p.pitchClass)
                # If pitch class 1 (C#/Db) is part of an A chord or diminished 7th chord with E (4), G (7), Bb (10)
                if p.pitchClass == 1 and (9 in chord_pcs or 10 in chord_pcs or 4 in chord_pcs):
                    target = 'C#'
                if target and p.name != target:
                    new_pitches.append(respell_pitch(p, target))
                else:
                    new_pitches.append(p)
            el.pitches = tuple(new_pitches)
            if orig_chord_tie:
                el.tie = orig_chord_tie
            for idx, ptie in enumerate(orig_pitch_ties):
                if ptie and idx < len(el.pitches):
                    el.setTie(ptie, el.pitches[idx])

def sanitize_tuplets(score_or_stream, allow_triplets=True):
    """
    Sanitizes all tuplets in the score (Dorico-style):
    - Strips irrational micro-tuplets (e.g. 24:23, 24:13, 19:22, 10:7, 5:4)
    - Enforces closed tuplet group validation: a tuplet must have both 'start' and 'stop'.
    - Eliminates isolated 'startStop' single-note tuplets caused by rubato.
    - Snaps note quarterLength to the nearest standard musical fraction.
    """
    from fractions import Fraction
    standard_lengths = [
        Fraction(1, 6), Fraction(1, 4), Fraction(1, 3), Fraction(3, 8),
        Fraction(1, 2), Fraction(2, 3), Fraction(3, 4), Fraction(1, 1),
        Fraction(3, 2), Fraction(2, 1), Fraction(3, 1), Fraction(4, 1)
    ]
    
    parts = getattr(score_or_stream, 'parts', [score_or_stream])
    for p in parts:
        measures = p.getElementsByClass('Measure')
        if not measures:
            measures = [p]
        for m in measures:
            tuplet_notes = [n for n in m.recurse().notes if n.duration.tuplets]
            if not tuplet_notes:
                continue
                
            types = [t.type for n in tuplet_notes for t in n.duration.tuplets]
            ratios = [(t.numberNotesActual, t.numberNotesNormal) for n in tuplet_notes for t in n.duration.tuplets]
            
            # Non-standard tuplets
            if not allow_triplets or any(r not in [(3, 2), (6, 4)] for r in ratios):
                for n in tuplet_notes:
                    n.duration.tuplets = ()
                    curr_ql = Fraction(str(round(float(n.duration.quarterLength), 4)))
                    closest = min(standard_lengths, key=lambda x: abs(x - curr_ql))
                    n.duration = music21.duration.Duration(closest)
                continue
                
            # Strict Dorico Group Coherence: must have both start and stop, and no isolated startStop
            if 'startStop' in types or len(tuplet_notes) < 2 or not ('start' in types and 'stop' in types):
                for n in tuplet_notes:
                    n.duration.tuplets = ()
                    curr_ql = Fraction(str(round(float(n.duration.quarterLength), 4)))
                    closest = min(standard_lengths, key=lambda x: abs(x - curr_ql))
                    n.duration = music21.duration.Duration(closest)
            else:
                # Clean up any orphaned None notes not bounded between start and stop
                in_group = False
                for n in tuplet_notes:
                    for d in list(n.duration.tuplets):
                        if d.type == 'start':
                            in_group = True
                        elif d.type == 'stop':
                            in_group = False
                        elif d.type is None and not in_group:
                            n.duration.tuplets = ()
                            curr_ql = Fraction(str(round(float(n.duration.quarterLength), 4)))
                            closest = min(standard_lengths, key=lambda x: abs(x - curr_ql))
                            n.duration = music21.duration.Duration(closest)

def merge_all_ties_in_stream(s):
    """
    Merges any notes/chords that were prematurely split with ties by the MIDI parser,
    re-combining them into continuous notes before measure allocation and staff splitting.
    Prevents severed ties from becoming accidental repeated notes.
    """
    elements = sorted(list(s.recurse().notes), key=lambda x: x.offset)
    by_pitch = {}
    for el in elements:
        pitches = tuple(sorted([p.nameWithOctave for p in el.pitches])) if el.isChord else (el.pitch.nameWithOctave,)
        by_pitch.setdefault(pitches, []).append(el)
        
    to_remove = set()
    for pitches, group in by_pitch.items():
        for i in range(len(group) - 1):
            el1 = group[i]
            el2 = group[i+1]
            if el1 in to_remove:
                continue
            gap = float(el2.offset - (el1.offset + el1.duration.quarterLength))
            if abs(gap) < 0.05 and (el1.tie and el1.tie.type in ('start', 'continue')):
                combined_ql = el1.duration.quarterLength + el2.duration.quarterLength
                el1.duration = music21.duration.Duration(combined_ql)
                el1.tie = el2.tie if el2.tie and el2.tie.type == 'continue' else None
                to_remove.add(el2)
                group[i+1] = el1
                
    for dead in to_remove:
        s.remove(dead, recurse=True)
    if to_remove:
        print(f"[Tie Consolidator] Merged {len(to_remove)} prematurely fragmented tied notes.")

def merge_intra_measure_ties(score):
    """
    1. Merges notes tied across internal beats in the same measure on identical pitch.
    2. Consolidates untied adjacent same-pitch/chord stutters caused by acoustic decay
       where a single held note was split into consecutive slices (e.g. dur2 <= 0.5).
    """
    for p in score.parts:
        for m in p.getElementsByClass('Measure'):
            streams = [m] + list(m.voices)
            for st in streams:
                elements = list(st.notes)
                to_remove = []
                for i in range(len(elements) - 1):
                    el1 = elements[i]
                    el2 = elements[i+1]
                    if el1 in to_remove:
                        continue
                    p1 = tuple(sorted([p.nameWithOctave for p in el1.pitches])) if el1.isChord else (el1.pitch.nameWithOctave,)
                    p2 = tuple(sorted([p.nameWithOctave for p in el2.pitches])) if el2.isChord else (el2.pitch.nameWithOctave,)
                    if p1 == p2:
                        gap = float(el2.offset - (el1.offset + el1.duration.quarterLength))
                        if abs(gap) < 0.05:
                            is_tied = (el1.tie and el1.tie.type in ('start', 'continue'))
                            # If tied OR if second note is a short stutter slice (dur <= 0.5)
                            if is_tied or (float(el2.duration.quarterLength) <= 0.5):
                                combined_ql = float(el1.duration.quarterLength + el2.duration.quarterLength)
                                max_allowed = float(m.barDuration.quarterLength - el1.offset)
                                combined_ql = min(combined_ql, max_allowed)
                                el1.duration = music21.duration.Duration(combined_ql)
                                el1.tie = el2.tie if (el2.tie and el2.tie.type == 'continue') else None
                                to_remove.append(el2)
                                elements[i+1] = el1
                for dead_el in to_remove:
                    if dead_el in st:
                        st.remove(dead_el)

def link_cross_measure_ties(score):
    """
    Connects notes/chords across measure barlines that share the exact same pitch
    and have no silence between them (i.e. note 1 ends at measure barline, note 2 starts at beat 0),
    turning unintended repeated noteheads into proper musical ties (ligaduras).
    """
    for part in score.parts:
        measures = list(part.getElementsByClass('Measure'))
        for i in range(len(measures) - 1):
            m1 = measures[i]
            m2 = measures[i+1]
            n1_list = list(m1.recurse().notes)
            n2_list = list(m2.recurse().notes)
            if not n1_list or not n2_list:
                continue
            n1 = n1_list[-1]
            n2 = n2_list[0]
            m1_dur = float(m1.barDuration.quarterLength)
            if abs(float(n1.offset + n1.duration.quarterLength) - m1_dur) < 0.05 and abs(float(n2.offset)) < 0.05:
                p1 = tuple(sorted([p.nameWithOctave for p in n1.pitches])) if n1.isChord else (n1.pitch.nameWithOctave,)
                p2 = tuple(sorted([p.nameWithOctave for p in n2.pitches])) if n2.isChord else (n2.pitch.nameWithOctave,)
                if p1 == p2:
                    if not n1.tie:
                        n1.tie = music21.tie.Tie('start')
                    elif n1.tie.type == 'stop':
                        n1.tie.type = 'continue'
                    if not n2.tie:
                        n2.tie = music21.tie.Tie('stop')
                    elif n2.tie.type == 'start':
                        n2.tie.type = 'continue'

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
    
    print("Running Krumhansl-Schmuckler Key Analysis...")
    flat_stream = score_stream.flatten()
    merge_all_ties_in_stream(flat_stream)
    best_key = flat_stream.analyze('key')
    print(f"Estimated Key: {best_key}")
    
    # We insert the key signature at offset 0 so it correctly spells the notes
    flat_stream.insert(0, best_key)
    
    # Sanitize any irrational tuplets at source before splitting
    sanitize_tuplets(flat_stream, allow_triplets=allow_triplets)
    
    # Split into Treble and Bass staves (Grand Staff) using the custom split_point
    print(f"Splitting quantized stream with time signature {time_signature}, tempo {bpm} BPM, and split boundary {split_point}...")
    split_score = split_piano_grand_staff(flat_stream, time_signature=time_signature, bpm=bpm, split_point=split_point)
    
    # Apply contextual enharmonics before making notation
    print("Applying contextual diatonic enharmonic spelling...")
    apply_diatonic_enharmonics(split_score, best_key)
    
    # Sanitize any irrational tuplets before notation
    sanitize_tuplets(split_score, allow_triplets=allow_triplets)
    
    print("Making well-formed notation with measures and ties...")
    split_score.makeNotation(inPlace=True)
    
    # Merge redundant intra-measure ties
    print("Merging redundant intra-measure ties...")
    merge_intra_measure_ties(split_score)
    
    # Clean up any residual tuplets created by makeNotation
    sanitize_tuplets(split_score, allow_triplets=allow_triplets)
    
    # Final enharmonic pass on split score
    apply_diatonic_enharmonics(split_score, best_key)
    
    # Connect notes crossing measure boundaries with legitimate musical ties (ligaduras)
    print("Linking cross-measure ties...")
    link_cross_measure_ties(split_score)
    
    # Export formats
    print(f"Exporting MusicXML to: {output_xml_path}")
    split_score.write('musicxml', fp=output_xml_path)
    
    print(f"Exporting quantized MIDI to: {output_midi_path}")
    split_score.write('midi', fp=output_midi_path)
    print("Quantization and export complete!")
