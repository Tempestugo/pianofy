import os
import sys
import time
import numpy as np
import soundfile as sf
import tinysoundfont

def synthesize_midi(
    midi_path: str,
    output_wav_path: str,
    sf2_path: str = "backend/TimGM6mb.sf2",
    duration_sec: float = None,
    sr: int = 44100,
    block_size: int = 1024
):
    print(f"Initializing tinysoundfont with {sf2_path}...", flush=True)
    synth = tinysoundfont.Synth()
    sfid = synth.sfload(sf2_path)
    synth.program_select(0, sfid, 0, 0) # Bank 0, preset 0 (Acoustic Grand Piano)

    seq = tinysoundfont.Sequencer(synth)
    seq.midi_load(midi_path)
    print(f"Loaded MIDI file: {midi_path}", flush=True)

    if duration_sec is None:
        import mido
        mid = mido.MidiFile(midi_path)
        duration_sec = mid.length + 3.0
        print(f"Detected MIDI length: {mid.length:.2f}s, rendering total: {duration_sec:.2f}s", flush=True)

    total_samples = int(duration_sec * sr)
    num_blocks = (total_samples + block_size - 1) // block_size

    print(f"Rendering {duration_sec:.1f}s of audio ({num_blocks} blocks of {block_size})...", flush=True)
    t0 = time.time()

    blocks = []
    dt = block_size / float(sr)
    for b in range(num_blocks):
        seq.process(dt)
        buf = synth.generate(block_size)
        arr = np.frombuffer(buf, dtype=np.float32).reshape(-1, 2)
        blocks.append(arr)
        if b % 2000 == 0 and b > 0:
            print(f"  Progress: {b}/{num_blocks} blocks ({(b/num_blocks)*100:.0f}%)", flush=True)

    t1 = time.time()
    audio = np.concatenate(blocks, axis=0)
    print(f"Rendered in {t1 - t0:.2f}s ({duration_sec / max(0.01, t1 - t0):.1f}x real-time)", flush=True)

    max_peak = np.max(np.abs(audio))
    print(f"Raw peak amplitude: {max_peak:.4f}", flush=True)
    if max_peak > 0:
        audio = audio * (0.89 / max_peak)

    os.makedirs(os.path.dirname(os.path.abspath(output_wav_path)), exist_ok=True)
    sf.write(output_wav_path, audio, sr)
    print(f"Successfully saved {output_wav_path} ({len(audio)/sr:.1f}s)", flush=True)
    return output_wav_path

if __name__ == "__main__":
    midi_file = sys.argv[1] if len(sys.argv) > 1 else "chopin_ballade_1.mid"
    out_file = sys.argv[2] if len(sys.argv) > 2 else "backend/outputs/chopin_ballade_audio.wav"
    dur = float(sys.argv[3]) if len(sys.argv) > 3 else None
    synthesize_midi(midi_file, out_file, duration_sec=dur)
