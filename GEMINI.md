# Pianofy Architecture & Development Rules

## 1. MusicXML Generation & Engraving
- **DO NOT USE Partitura for MusicXML Engraving**: Partitura (`partitura`) must never be used to emit MusicXML for Verovio rendering. It lacks robust beaming, chord unification, and valid note-type resolution for rubato or real-world audio timings, resulting in missing stems, missing beams, and open ties.
- **Use the Consolidator Pipeline (`backend/transcriber.py`)**: All MusicXML generation from neural audio transcription must pass through the proven pipeline:
  1. RegressionPostProcessor & heuristic filters (finger slip, harmonic, pedal merge).
  2. Dorico-style quantizer snapping to musical grids (divisors 4, 3, 2, 1).
  3. Krumhansl-Schmuckler key analysis & diatonic enharmonic spelling.
  4. Grand Staff hand separation with chord clustering.
  5. Music21 `makeNotation(inPlace=True)` + `clean_redundant_voices()` to produce standard `<type>`, `<beam>`, and measure barlines.
- **Neural Downbeat Tracking (BeatNetLite)**: Always track dynamic measure barlines ($B1$) using BeatNetLite instead of static periodic beat-tracking (e.g. `librosa.beat_track`). Downbeats must anchor the start of measures with a 150ms snap window to prevent artificial ties, preserve whole notes, and handle expressive rubato.
- **Grand Staff Continuous Clef Invariant**: Both staves (`RightHand` and `LeftHand`) must always have equal measure lengths. Any measure lacking active notes/chords in either staff must be explicitly padded with full-measure rests (`note.Rest(quarterLength=bar_dur)`). Empty staves must never be omitted, as Verovio drops unmeasured staves mid-score.
- **Safe Audio Loading**: Always load audio via `librosa.load(audio_path, sr=sample_rate, mono=True)` directly. Never call deprecated legacy wrappers (e.g. `piano_transcription_inference.load_audio`) that trigger deleted `librosa.core.audio` attributes.
- **No Hardcoded Ad-hoc Notation Patches**: Do not attempt to fix unmusical XML exports with iterative regex or ad-hoc duration hacking. Notation must be syntactically valid and standard.

## 2. Synchronization Architecture
- **Separation of Notation and Time**:
  - The MusicXML represents the **ideal symbolic score** (well-proportioned measures, proper noteheads, stems, beams).
  - The Renderer (`backend/native_sheet_renderer.py`) represents the **live temporal reality**, mapping audio milliseconds directly to note IDs via Verovio's timemap.
- **Camera Tracking & Reveal**:
  - Camera tracking knots are anchored strictly to real audio onset times.
  - Reveal masks must reveal complete discrete notes/chords without slicing noteheads, stems, accidentals, or beams.
- **Discrete Sequential Note Reveal vs Block Popping**:
  - Chords/rolled chords clustering must be strictly acoustic: $\le 35\text{ms}$ for simultaneous attacks, or $\le 65\text{ms}$ for rolled chords within $12\text{px}$ horizontally. Never cluster melodic notes separated in time into the same column.
  - Bounding box merging must ONLY occur when bounding boxes strictly overlap horizontally (`prev["max_right"] > ev["min_left"]`). If there is ANY whitespace ($\ge 0\text{px}$), notes MUST remain discrete sequential reveal events anchored to their individual acoustic strike.
- **Vertical System Isolation**:
  - Each system must be rendered and clipped in strict isolation. Bounding boxes must cover all system annotations (pedals, dynamics, tempo headers like *Moderato*) while strictly excluding glyphs from adjacent staves/systems (zero vertical bleed).

## 3. Visual Verification Protocol
- **Mandatory Keyframe Inspection**: Never confirm a rendering or synchronization fix based solely on console logs or numerical bounding box outputs. Always render and visually inspect keyframe images (`view_file`) at critical musical transitions (chords, fast runs, fermatas, system shifts).
