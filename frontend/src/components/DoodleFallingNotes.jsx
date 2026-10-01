import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import rough from 'roughjs';
import { 
  Play, 
  Pause, 
  RotateCcw, 
  Volume2, 
  VolumeX, 
  Maximize2, 
  Minimize2, 
  Smartphone, 
  Monitor, 
  Sparkles, 
  Palette,
  Sliders,
  Download
} from 'lucide-react';

const NOTE_NAMES = ['C', 'C#', 'D', 'Eb', 'E', 'F', 'F#', 'G', 'Ab', 'A', 'Bb', 'B'];
const BLACK_SEMITONES = new Set([1, 3, 6, 8, 10]);

function isBlackPitch(pitch) {
  return BLACK_SEMITONES.has(pitch % 12);
}

function pitchToName(pitch) {
  return NOTE_NAMES[pitch % 12];
}

function pitchToOctave(pitch) {
  return Math.floor(pitch / 12) - 1;
}

// Simple heuristic chord recognizer
function detectChordName(pitches) {
  if (!pitches || pitches.length < 2) return null;
  const uniquePcs = Array.from(new Set(pitches.map(p => p % 12)));
  if (uniquePcs.length < 2) return null;
  const bassPitch = Math.min(...pitches);
  const bassPc = bassPitch % 12;
  const rootName = NOTE_NAMES[bassPc];
  const intervals = uniquePcs.map(p => (p - bassPc + 12) % 12).sort((a, b) => a - b);
  
  if (intervals.includes(3) && intervals.includes(7)) return rootName + 'm';
  if (intervals.includes(4) && intervals.includes(7)) return rootName;
  if (intervals.includes(3) && intervals.includes(6)) return rootName + 'dim';
  if (intervals.includes(4) && intervals.includes(8)) return rootName + 'aug';
  if (intervals.includes(2) && intervals.includes(7)) return rootName + 'sus2';
  if (intervals.includes(5) && intervals.includes(7)) return rootName + 'sus4';
  if (intervals.includes(4) && intervals.includes(7) && intervals.includes(10)) return rootName + '7';
  if (intervals.includes(3) && intervals.includes(7) && intervals.includes(10)) return rootName + 'm7';
  if (intervals.includes(4) && intervals.includes(7) && intervals.includes(11)) return rootName + 'maj7';
  return rootName;
}

export default function DoodleFallingNotes({ 
  notes = [], 
  bpm = 120, 
  audioDuration = null 
}) {
  const containerRef = useRef(null);
  const canvasRef = useRef(null);
  
  // Playback states
  const [isPlaying, setIsPlaying] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [playbackSpeed, setPlaybackSpeed] = useState(1.0);
  const [isMuted, setIsMuted] = useState(false);
  const [aspectMode, setAspectMode] = useState('phone'); // 'phone' (9:16) or 'wide' (16:9 / responsive)
  const [noteSpeed, setNoteSpeed] = useState(260); // px per second
  const [showLetters, setShowLetters] = useState(true);
  const [showChords, setShowChords] = useState(true);
  
  // Audio synthesis
  const audioCtxRef = useRef(null);
  const scheduledIndicesRef = useRef(new Set());
  const startTimeRef = useRef(0);
  const pausedAtRef = useRef(0);
  
  // Particle hits & splashes
  const splashesRef = useRef([]);

  const DEFAULT_DEMO_NOTES = useMemo(() => [
    // Bass arpeggio (Left hand)
    { pitch: 46, onset_time: 0.0, duration: 1.8, velocity: 80 }, // Bb2
    { pitch: 53, onset_time: 0.25, duration: 1.6, velocity: 75 }, // F3
    { pitch: 58, onset_time: 0.5, duration: 1.4, velocity: 75 }, // Bb3
    { pitch: 61, onset_time: 0.75, duration: 1.2, velocity: 75 }, // Db4
    // Melody cascade (Right hand - Great Fairy Fountain style!)
    { pitch: 65, onset_time: 0.5, duration: 0.35, velocity: 85 }, // F4
    { pitch: 68, onset_time: 0.75, duration: 0.35, velocity: 88 }, // Ab4
    { pitch: 72, onset_time: 1.0, duration: 0.35, velocity: 90 }, // C5
    { pitch: 73, onset_time: 1.25, duration: 0.35, velocity: 92 }, // Db5
    { pitch: 77, onset_time: 1.5, duration: 0.4, velocity: 95 },  // F5
    { pitch: 80, onset_time: 1.75, duration: 0.4, velocity: 95 },  // Ab5
    { pitch: 84, onset_time: 2.0, duration: 0.5, velocity: 98 },  // C6
    // Measure 2 (Gb major chord)
    { pitch: 42, onset_time: 2.2, duration: 1.8, velocity: 80 }, // Gb2
    { pitch: 49, onset_time: 2.45, duration: 1.6, velocity: 75 }, // Db3
    { pitch: 54, onset_time: 2.7, duration: 1.4, velocity: 75 }, // Gb3
    { pitch: 58, onset_time: 2.95, duration: 1.2, velocity: 75 }, // Bb3
    { pitch: 66, onset_time: 2.7, duration: 0.35, velocity: 85 }, // Gb4
    { pitch: 70, onset_time: 2.95, duration: 0.35, velocity: 88 }, // Bb4
    { pitch: 73, onset_time: 3.2, duration: 0.35, velocity: 90 }, // Db5
    { pitch: 78, onset_time: 3.45, duration: 0.35, velocity: 92 }, // Gb5
    { pitch: 82, onset_time: 3.7, duration: 0.4, velocity: 95 },  // Bb5
    { pitch: 85, onset_time: 3.95, duration: 0.5, velocity: 98 },  // Db6
  ], []);

  // Calculate normalized notes list with onset_time and offset_time in seconds
  const formattedNotes = useMemo(() => {
    const rawList = (notes && notes.length > 0) ? notes : DEFAULT_DEMO_NOTES;
    const beatDuration = 60.0 / (bpm || 120.0);
    return rawList.map((n, idx) => {
      const onset = n.onset_time !== undefined ? n.onset_time : (n.onset_beat * beatDuration);
      const duration = n.duration !== undefined 
        ? n.duration 
        : (n.duration_time !== undefined 
            ? n.duration_time 
            : (n.duration_beat ? n.duration_beat * beatDuration : 0.5));
      return {
        id: idx,
        pitch: n.pitch,
        velocity: n.velocity || 80,
        onset_time: onset,
        offset_time: onset + duration,
        duration: duration
      };
    }).sort((a, b) => a.onset_time - b.onset_time);
  }, [notes, bpm, DEFAULT_DEMO_NOTES]);

  // Compute total duration
  const totalDuration = useMemo(() => {
    if (audioDuration && audioDuration > 0) return audioDuration;
    if (formattedNotes.length === 0) return 6;
    const last = formattedNotes[formattedNotes.length - 1];
    return Math.max(last.offset_time + 1.5, 6);
  }, [audioDuration, formattedNotes]);

  // Compute keyboard bounds (min and max pitch)
  const keyRange = useMemo(() => {
    if (formattedNotes.length === 0) return { minPitch: 36, maxPitch: 84 };
    let minP = 127;
    let maxP = 0;
    for (const n of formattedNotes) {
      if (n.pitch < minP) minP = n.pitch;
      if (n.pitch > maxP) maxP = n.pitch;
    }
    // Snap to nearest C boundaries with 3 notes padding
    const lowerC = Math.max(21, Math.floor((minP - 2) / 12) * 12);
    const upperC = Math.min(108, Math.ceil((maxP + 3) / 12) * 12);
    return {
      minPitch: Math.max(21, lowerC),
      maxPitch: Math.min(108, upperC)
    };
  }, [formattedNotes]);

  // Pre-calculate chord markers along the timeline
  const chordMarkers = useMemo(() => {
    if (!showChords || formattedNotes.length === 0) return [];
    const markers = [];
    const windowSec = 0.08;
    let i = 0;
    while (i < formattedNotes.length) {
      const currentOnset = formattedNotes[i].onset_time;
      const group = [formattedNotes[i].pitch];
      let j = i + 1;
      while (j < formattedNotes.length && formattedNotes[j].onset_time - currentOnset < windowSec) {
        group.push(formattedNotes[j].pitch);
        j++;
      }
      if (group.length >= 2) {
        const chordStr = detectChordName(group);
        if (chordStr && (!markers.length || currentOnset - markers[markers.length - 1].time > 0.4)) {
          markers.push({ time: currentOnset, chord: chordStr });
        }
      }
      i = j;
    }
    return markers;
  }, [formattedNotes, showChords]);

  // Web Audio Synth
  const playSynth = useCallback((pitch, velocity, startDelay = 0, dur = 0.6) => {
    if (isMuted) return;
    try {
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || window.webkitAudioContext)();
      }
      const ctx = audioCtxRef.current;
      if (ctx.state === 'suspended') ctx.resume();

      const now = ctx.currentTime + Math.max(0, startDelay);
      const freq = 440 * Math.pow(2, (pitch - 69) / 12);
      const vol = (velocity / 127) * 0.28;

      // Master gain
      const gain = ctx.createGain();
      gain.gain.setValueAtTime(0.001, now);
      gain.gain.linearRampToValueAtTime(vol, now + 0.008);
      gain.gain.exponentialRampToValueAtTime(vol * 0.5, now + 0.12);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + Math.max(0.3, dur * 1.2));
      gain.connect(ctx.destination);

      // Lowpass filter
      const filter = ctx.createBiquadFilter();
      filter.type = 'lowpass';
      filter.frequency.setValueAtTime(Math.min(3200, freq * 4), now);
      filter.frequency.exponentialRampToValueAtTime(Math.max(200, freq * 1.5), now + 0.6);
      filter.connect(gain);

      // Triangle body
      const osc1 = ctx.createOscillator();
      osc1.type = 'triangle';
      osc1.frequency.setValueAtTime(freq, now);
      osc1.connect(filter);

      // Sine fundamental
      const osc2 = ctx.createOscillator();
      osc2.type = 'sine';
      osc2.frequency.setValueAtTime(freq, now);
      osc2.connect(filter);

      // Hammer transient
      const hammer = ctx.createOscillator();
      hammer.type = 'sine';
      hammer.frequency.setValueAtTime(freq * 3.5, now);
      const hammerGain = ctx.createGain();
      hammerGain.gain.setValueAtTime(vol * 0.35, now);
      hammerGain.gain.exponentialRampToValueAtTime(0.0001, now + 0.04);
      hammer.connect(hammerGain);
      hammerGain.connect(gain);

      osc1.start(now);
      osc2.start(now);
      hammer.start(now);

      const stopTime = now + Math.max(0.4, dur * 1.3);
      osc1.stop(stopTime);
      osc2.stop(stopTime);
      hammer.stop(stopTime);
    } catch (e) {
      // AudioContext policy
    }
  }, [isMuted]);

  // Handle Play/Pause
  const togglePlay = () => {
    if (isPlaying) {
      pausedAtRef.current = currentTime;
      setIsPlaying(false);
    } else {
      startTimeRef.current = performance.now() - (pausedAtRef.current * 1000) / playbackSpeed;
      scheduledIndicesRef.current.clear();
      setIsPlaying(true);
    }
  };

  const resetPlay = () => {
    pausedAtRef.current = 0;
    setCurrentTime(0);
    scheduledIndicesRef.current.clear();
    splashesRef.current = [];
    if (isPlaying) {
      startTimeRef.current = performance.now();
    }
  };

  const handleSeek = (e) => {
    const newT = parseFloat(e.target.value);
    pausedAtRef.current = newT;
    setCurrentTime(newT);
    scheduledIndicesRef.current.clear();
    splashesRef.current = [];
    if (isPlaying) {
      startTimeRef.current = performance.now() - (newT * 1000) / playbackSpeed;
    }
  };

  // Main Canvas Render Loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const rc = rough.canvas(canvas);

    let animationId;

    const renderFrame = (nowTime) => {
      // 1. Advance playback time if playing
      let t = currentTime;
      if (isPlaying) {
        const elapsedSec = ((nowTime - startTimeRef.current) / 1000) * playbackSpeed;
        t = elapsedSec;
        if (t >= totalDuration) {
          setIsPlaying(false);
          t = totalDuration;
          pausedAtRef.current = totalDuration;
        }
        setCurrentTime(t);
      }

      // Check new notes to schedule audio
      if (isPlaying) {
        const lookahead = 0.04;
        formattedNotes.forEach((note) => {
          if (!scheduledIndicesRef.current.has(note.id)) {
            if (note.onset_time <= t + lookahead && note.onset_time >= t - 0.1) {
              scheduledIndicesRef.current.add(note.id);
              const delay = Math.max(0, note.onset_time - t);
              playSynth(note.pitch, note.velocity, delay, note.duration);
            }
          }
        });
      }

      // 2. Setup Dimensions
      const width = canvas.width;
      const height = canvas.height;

      // Clear with warm vintage paper color (#fcf7eb)
      ctx.fillStyle = '#fcf7eb';
      ctx.fillRect(0, 0, width, height);

      // Paper grain / texture dots (subtle cozy notebook background)
      ctx.fillStyle = 'rgba(215, 203, 185, 0.25)';
      const dotSpacing = 36;
      for (let dx = 18; dx < width; dx += dotSpacing) {
        for (let dy = 18; dy < height - 140; dy += dotSpacing) {
          ctx.beginPath();
          ctx.arc(dx, dy, 1.2, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      // 3. Layout Keyboard geometry
      const { minPitch, maxPitch } = keyRange;
      const whitePitches = [];
      for (let p = minPitch; p <= maxPitch; p++) {
        if (!isBlackPitch(p)) whitePitches.push(p);
      }
      const numWhite = Math.max(1, whitePitches.length);
      const whiteKeyWidth = width / numWhite;
      const keyboardHeight = Math.min(140, Math.max(90, height * 0.17));
      const keyboardY = height - keyboardHeight;
      const hitLineY = keyboardY;

      // Coordinate mapping for all pitches
      const pitchCoords = {};
      let wIdx = 0;
      for (let p = minPitch; p <= maxPitch; p++) {
        if (!isBlackPitch(p)) {
          pitchCoords[p] = {
            isBlack: false,
            x: wIdx * whiteKeyWidth,
            width: whiteKeyWidth,
            centerX: wIdx * whiteKeyWidth + whiteKeyWidth / 2
          };
          wIdx++;
        }
      }
      // Position black keys between white keys
      for (let p = minPitch; p <= maxPitch; p++) {
        if (isBlackPitch(p)) {
          const prevWhite = pitchCoords[p - 1];
          const nextWhite = pitchCoords[p + 1];
          let bX = 0;
          if (prevWhite && nextWhite) {
            bX = (prevWhite.x + whiteKeyWidth) - (whiteKeyWidth * 0.32);
          } else if (prevWhite) {
            bX = prevWhite.x + whiteKeyWidth * 0.7;
          } else if (nextWhite) {
            bX = nextWhite.x - whiteKeyWidth * 0.3;
          }
          const bW = whiteKeyWidth * 0.64;
          pitchCoords[p] = {
            isBlack: true,
            x: bX,
            width: bW,
            centerX: bX + bW / 2
          };
        }
      }

      // Active notes at time t
      const activePitches = new Set();
      const currentActiveNotes = [];
      formattedNotes.forEach((n) => {
        if (t >= n.onset_time && t <= n.offset_time) {
          activePitches.add(n.pitch);
          currentActiveNotes.push(n);
        }
      });

      // 4. Draw Beat / Measure Guidelines & Floating Chords
      const beatInterval = 60.0 / (bpm || 120);
      const lookaheadSec = (keyboardY) / noteSpeed;
      const firstBeatIdx = Math.max(0, Math.floor((t - 1) / beatInterval));
      const lastBeatIdx = Math.ceil((t + lookaheadSec + 1) / beatInterval);

      ctx.save();
      for (let b = firstBeatIdx; b <= lastBeatIdx; b++) {
        const beatTime = b * beatInterval;
        const beatY = hitLineY - (beatTime - t) * noteSpeed;
        if (beatY >= -20 && beatY <= hitLineY) {
          const isMeasure = (b % 4 === 0);
          ctx.strokeStyle = isMeasure ? 'rgba(180, 160, 135, 0.45)' : 'rgba(200, 185, 165, 0.25)';
          ctx.lineWidth = isMeasure ? 1.5 : 1;
          ctx.setLineDash(isMeasure ? [8, 6] : [3, 5]);
          ctx.beginPath();
          ctx.moveTo(10, beatY);
          ctx.lineTo(width - 10, beatY);
          ctx.stroke();

          if (isMeasure) {
            const measureNum = Math.floor(b / 4) + 1;
            ctx.setLineDash([]);
            ctx.font = 'bold 13px "Patrick Hand", "Caveat", cursive';
            ctx.fillStyle = 'rgba(140, 120, 95, 0.75)';
            ctx.fillText(`m.${measureNum}`, 14, beatY - 4);
          }
        }
      }
      ctx.restore();

      // Draw Floating Chords (like "Bbm" in falling_notes.mp4)
      if (showChords) {
        ctx.save();
        chordMarkers.forEach(cm => {
          const cmY = hitLineY - (cm.time - t) * noteSpeed;
          if (cmY >= 20 && cmY <= hitLineY + 20) {
            ctx.font = 'bold 22px "Patrick Hand", "Caveat", cursive';
            ctx.fillStyle = '#2c2520';
            ctx.textAlign = 'left';
            
            // Cute sketchy circle/blob behind chord
            ctx.fillStyle = 'rgba(255, 237, 213, 0.85)';
            ctx.beginPath();
            ctx.roundRect(14, cmY - 24, 60, 28, 8);
            ctx.fill();
            ctx.strokeStyle = '#2c2520';
            ctx.lineWidth = 1.5;
            ctx.stroke();
            
            ctx.fillStyle = '#2c2520';
            ctx.fillText(cm.chord, 24, cmY - 4);
          }
        });
        ctx.restore();
      }

      // 5. Draw Falling Note Bars (Rough.js + Doodle aesthetic)
      formattedNotes.forEach((n) => {
        const bottomY = hitLineY - (n.onset_time - t) * noteSpeed;
        const topY = hitLineY - (n.offset_time - t) * noteSpeed;
        const noteHeight = Math.max(16, bottomY - topY);

        // Clip visibility to canvas
        if (bottomY >= -50 && topY <= height + 50) {
          const coord = pitchCoords[n.pitch];
          if (!coord) return;

          const barWidth = Math.max(14, coord.width * 0.82);
          const barX = coord.centerX - barWidth / 2;

          // Color palette inspired by falling_notes.mp4
          // Left hand / Bass (< 60): Muted purple / lavender (#8875b4 / #a594cf)
          // Right hand / Melody (>= 60): Warm coral / rose (#ea7a72 / #f8968e)
          const isBass = n.pitch < 60;
          const fillColor = isBass ? '#9d8bc7' : '#f08077';
          const strokeColor = '#2b2420';

          // Hand-drawn rounded bar
          ctx.save();
          // Draw subtle soft shadow
          ctx.fillStyle = 'rgba(40, 30, 20, 0.08)';
          ctx.beginPath();
          ctx.roundRect(barX + 3, topY + 3, barWidth, noteHeight, 8);
          ctx.fill();

          // Bar Body with Rough.js generator or canvas roundRect with sketchy outline
          ctx.fillStyle = fillColor;
          ctx.beginPath();
          ctx.roundRect(barX, topY, barWidth, noteHeight, 8);
          ctx.fill();

          // Doodle outline
          ctx.strokeStyle = strokeColor;
          ctx.lineWidth = 2.2;
          ctx.lineJoin = 'round';
          ctx.stroke();

          // Inside letter label (e.g. "C", "F", "Eb")
          if (showLetters && noteHeight >= 20) {
            const letter = pitchToName(n.pitch);
            ctx.font = 'bold 14px "Patrick Hand", "Caveat", cursive';
            ctx.fillStyle = '#ffffff';
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            // Position letter near the bottom hit point of the bar
            const textY = Math.min(bottomY - 12, topY + noteHeight / 2);
            ctx.fillText(letter, coord.centerX, textY);
          }

          ctx.restore();
        }
      });

      // 6. Spawn Crayon Splashes on Note Hit
      currentActiveNotes.forEach(an => {
        if (Math.abs(t - an.onset_time) < 0.04) {
          const coord = pitchCoords[an.pitch];
          if (coord) {
            splashesRef.current.push({
              x: coord.centerX,
              y: hitLineY,
              color: an.pitch < 60 ? '#8b78ba' : '#f08077',
              life: 1.0,
              maxLife: 1.0,
              radius: 12 + Math.random() * 8
            });
          }
        }
      });

      // Update & Draw Splashes
      ctx.save();
      splashesRef.current.forEach((sp) => {
        sp.life -= 0.05;
        if (sp.life > 0) {
          ctx.strokeStyle = sp.color;
          ctx.lineWidth = 2.5 * sp.life;
          ctx.beginPath();
          ctx.arc(sp.x, sp.y, sp.radius * (2 - sp.life), 0, Math.PI * 2);
          ctx.stroke();

          // Little impact sparks
          const numRays = 4;
          for (let r = 0; r < numRays; r++) {
            const angle = (r * Math.PI) / 2 + 0.3;
            const dist1 = sp.radius * (1.2 - sp.life * 0.2);
            const dist2 = dist1 + 8 * sp.life;
            ctx.beginPath();
            ctx.moveTo(sp.x + Math.cos(angle) * dist1, sp.y + Math.sin(angle) * dist1);
            ctx.lineTo(sp.x + Math.cos(angle) * dist2, sp.y + Math.sin(angle) * dist2);
            ctx.stroke();
          }
        }
      });
      splashesRef.current = splashesRef.current.filter(sp => sp.life > 0);
      ctx.restore();

      // 7. Draw Hand-Drawn Hit Line (just above keyboard)
      ctx.save();
      rc.line(4, hitLineY, width - 4, hitLineY, {
        roughness: 1.5,
        bowing: 1.2,
        stroke: '#2c2520',
        strokeWidth: 2.5
      });
      ctx.restore();

      // 8. Draw Doodle Piano Keyboard (Rough.js)
      // White keys pass
      whitePitches.forEach((p) => {
        const coord = pitchCoords[p];
        if (!coord) return;

        const isActive = activePitches.has(p);
        const activeColor = p < 60 ? '#e0d5fa' : '#fed7d7'; // Soft pastel highlight
        const keyFill = isActive ? activeColor : '#fffdf9';

        ctx.save();
        // White key body with rough sketchy border
        rc.rectangle(coord.x, keyboardY, coord.width, keyboardHeight, {
          roughness: 1.2,
          bowing: 1.1,
          stroke: '#2c2520',
          strokeWidth: 1.8,
          fill: keyFill,
          fillStyle: 'solid'
        });

        // Handwritten C label (e.g. C3, C4, C5)
        if (p % 12 === 0) {
          ctx.font = 'bold 14px "Patrick Hand", "Caveat", cursive';
          ctx.fillStyle = isActive ? '#4c1d95' : 'rgba(60, 50, 40, 0.7)';
          ctx.textAlign = 'center';
          ctx.fillText(`C${pitchToOctave(p)}`, coord.centerX, keyboardY + keyboardHeight - 10);
        }
        ctx.restore();
      });

      // Black keys pass
      for (let p = minPitch; p <= maxPitch; p++) {
        if (isBlackPitch(p)) {
          const coord = pitchCoords[p];
          if (!coord) return;

          const isActive = activePitches.has(p);
          const blackKeyHeight = keyboardHeight * 0.62;
          const activeColor = p < 60 ? '#7c65ad' : '#dc6860';
          const keyFill = isActive ? activeColor : '#2c2520';

          ctx.save();
          rc.rectangle(coord.x, keyboardY, coord.width, blackKeyHeight, {
            roughness: 1.4,
            bowing: 1.2,
            stroke: '#1a1614',
            strokeWidth: 2,
            fill: keyFill,
            fillStyle: 'solid'
          });
          ctx.restore();
        }
      }

      // 9. Crayon Scribbles / Doodles (The iconic blue wax crayon squiggles!)
      ctx.save();
      const doodleBlue = '#2563eb';
      // Left bottom doodle
      rc.curve([
        [18, height - 16],
        [28, height - 28],
        [36, height - 12],
        [46, height - 24],
        [54, height - 10]
      ], {
        roughness: 2.2,
        stroke: doodleBlue,
        strokeWidth: 3.2
      });

      // Right bottom doodle swirl
      rc.curve([
        [width - 60, height - 12],
        [width - 45, height - 26],
        [width - 32, height - 10],
        [width - 18, height - 22]
      ], {
        roughness: 2.2,
        stroke: doodleBlue,
        strokeWidth: 3.2
      });
      ctx.restore();

      // Loop
      animationId = requestAnimationFrame(renderFrame);
    };

    animationId = requestAnimationFrame(renderFrame);
    return () => cancelAnimationFrame(animationId);
  }, [
    isPlaying, 
    currentTime, 
    playbackSpeed, 
    noteSpeed, 
    keyRange, 
    formattedNotes, 
    chordMarkers, 
    showLetters, 
    showChords, 
    bpm, 
    totalDuration, 
    playSynth
  ]);

  // Adjust canvas resolution dynamically based on aspect ratio
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    if (aspectMode === 'phone') {
      canvas.width = 540;
      canvas.height = 960; // 9:16
    } else {
      canvas.width = 960;
      canvas.height = 540; // 16:9
    }
  }, [aspectMode]);

  const formatTime = (sec) => {
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${m}:${s < 10 ? '0' : ''}${s}`;
  };

  return (
    <div 
      ref={containerRef}
      style={{
        width: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        background: 'linear-gradient(145deg, #120e18, #1c1524)',
        borderRadius: '20px',
        border: '1px solid rgba(197,160,89,0.3)',
        padding: '24px',
        boxSizing: 'border-box',
        gap: '20px',
        boxShadow: '0 16px 48px rgba(0,0,0,0.5)',
        color: '#eae0ce'
      }}
    >
      {/* Top Header / Mode Switchers */}
      <div style={{
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        flexWrap: 'wrap',
        gap: '12px',
        borderBottom: '1px solid rgba(197,160,89,0.15)',
        paddingBottom: '16px'
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div style={{
            width: '38px',
            height: '38px',
            borderRadius: '10px',
            background: 'linear-gradient(135deg, #f08077, #9d8bc7)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            boxShadow: '0 4px 16px rgba(240,128,119,0.3)'
          }}>
            <Palette size={20} color="#ffffff" />
          </div>
          <div>
            <h3 style={{ margin: 0, fontSize: '1.15rem', fontWeight: 700, color: '#fcf7eb' }}>
              Doodle Falling Notes (Estilo Desenho Animado)
            </h3>
            <span style={{ fontSize: '0.8rem', color: '#b8a998' }}>
              Inspirado no visual sketchbook artesanal com Rough.js
            </span>
          </div>
        </div>

        {/* Aspect Ratio & Display Toggles */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            onClick={() => setAspectMode(aspectMode === 'phone' ? 'wide' : 'phone')}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              padding: '6px 12px',
              borderRadius: '8px',
              background: 'rgba(255,255,255,0.06)',
              border: '1px solid rgba(197,160,89,0.25)',
              color: '#fcf7eb',
              cursor: 'pointer',
              fontSize: '0.82rem',
              fontWeight: 600
            }}
          >
            {aspectMode === 'phone' ? <Smartphone size={15} color="#f08077" /> : <Monitor size={15} color="#9d8bc7" />}
            {aspectMode === 'phone' ? 'Formato 9:16 (Celular)' : 'Formato Panorâmico'}
          </button>

          <button
            onClick={() => setShowLetters(!showLetters)}
            style={{
              padding: '6px 12px',
              borderRadius: '8px',
              background: showLetters ? 'rgba(240,128,119,0.2)' : 'rgba(255,255,255,0.05)',
              border: `1px solid ${showLetters ? '#f08077' : 'rgba(255,255,255,0.1)'}`,
              color: showLetters ? '#fca5a5' : '#888',
              cursor: 'pointer',
              fontSize: '0.82rem',
              fontWeight: 600
            }}
          >
            Letras: {showLetters ? 'ON' : 'OFF'}
          </button>

          <button
            onClick={() => setShowChords(!showChords)}
            style={{
              padding: '6px 12px',
              borderRadius: '8px',
              background: showChords ? 'rgba(157,139,199,0.2)' : 'rgba(255,255,255,0.05)',
              border: `1px solid ${showChords ? '#9d8bc7' : 'rgba(255,255,255,0.1)'}`,
              color: showChords ? '#c4b5fd' : '#888',
              cursor: 'pointer',
              fontSize: '0.82rem',
              fontWeight: 600
            }}
          >
            Cifras: {showChords ? 'ON' : 'OFF'}
          </button>
        </div>
      </div>

      {/* Canvas Viewport Frame */}
      <div style={{
        position: 'relative',
        display: 'flex',
        justifyContent: 'center',
        alignItems: 'center',
        background: '#09070c',
        borderRadius: '16px',
        padding: '12px',
        boxShadow: 'inset 0 0 24px rgba(0,0,0,0.8)',
        border: '1px solid rgba(255,255,255,0.08)',
        maxHeight: '680px',
        overflow: 'hidden'
      }}>
        <canvas
          ref={canvasRef}
          style={{
            maxWidth: '100%',
            maxHeight: '640px',
            borderRadius: '12px',
            boxShadow: '0 8px 32px rgba(0,0,0,0.4)',
            cursor: 'pointer'
          }}
          onClick={togglePlay}
        />

        {/* Large Play Overlay if paused at start */}
        {!isPlaying && currentTime < 0.1 && (
          <div 
            onClick={togglePlay}
            style={{
              position: 'absolute',
              top: '50%',
              left: '50%',
              transform: 'translate(-50%, -50%)',
              width: '80px',
              height: '80px',
              borderRadius: '50%',
              background: 'rgba(240, 128, 119, 0.9)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              cursor: 'pointer',
              boxShadow: '0 8px 32px rgba(240,128,119,0.5)',
              backdropFilter: 'blur(4px)',
              transition: 'transform 0.2s ease'
            }}
          >
            <Play size={38} color="#ffffff" style={{ marginLeft: '4px' }} />
          </div>
        )}
      </div>

      {/* Bottom Playback Controls */}
      <div style={{
        width: '100%',
        maxWidth: aspectMode === 'phone' ? '540px' : '960px',
        display: 'flex',
        flexDirection: 'column',
        gap: '12px'
      }}>
        {/* Progress Timeline */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span style={{ fontSize: '0.85rem', color: '#b8a998', fontVariantNumeric: 'tabular-nums' }}>
            {formatTime(currentTime)}
          </span>
          <input 
            type="range"
            min={0}
            max={totalDuration}
            step={0.05}
            value={currentTime}
            onChange={handleSeek}
            style={{
              flex: 1,
              accentColor: '#f08077',
              height: '6px',
              cursor: 'pointer'
            }}
          />
          <span style={{ fontSize: '0.85rem', color: '#b8a998', fontVariantNumeric: 'tabular-nums' }}>
            {formatTime(totalDuration)}
          </span>
        </div>

        {/* Buttons Bar */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          flexWrap: 'wrap',
          gap: '12px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button
              onClick={togglePlay}
              style={{
                width: '42px',
                height: '42px',
                borderRadius: '12px',
                background: isPlaying ? '#9d8bc7' : '#f08077',
                border: 'none',
                color: '#fff',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer',
                boxShadow: isPlaying ? '0 4px 14px rgba(157,139,199,0.4)' : '0 4px 14px rgba(240,128,119,0.4)'
              }}
            >
              {isPlaying ? <Pause size={20} /> : <Play size={20} style={{ marginLeft: '2px' }} />}
            </button>

            <button
              onClick={resetPlay}
              style={{
                width: '42px',
                height: '42px',
                borderRadius: '12px',
                background: 'rgba(255,255,255,0.08)',
                border: '1px solid rgba(255,255,255,0.1)',
                color: '#eae0ce',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer'
              }}
            >
              <RotateCcw size={18} />
            </button>

            <button
              onClick={() => setIsMuted(!isMuted)}
              style={{
                width: '42px',
                height: '42px',
                borderRadius: '12px',
                background: isMuted ? 'rgba(239, 68, 68, 0.2)' : 'rgba(255,255,255,0.08)',
                border: `1px solid ${isMuted ? '#ef4444' : 'rgba(255,255,255,0.1)'}`,
                color: isMuted ? '#f87171' : '#eae0ce',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                cursor: 'pointer'
              }}
            >
              {isMuted ? <VolumeX size={18} /> : <Volume2 size={18} />}
            </button>
          </div>

          {/* Speed & Fall Velocity Controls */}
          <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontSize: '0.8rem', color: '#b8a998' }}>Velocidade Queda:</span>
              <select
                value={noteSpeed}
                onChange={(e) => setNoteSpeed(Number(e.target.value))}
                style={{
                  background: 'rgba(255,255,255,0.08)',
                  border: '1px solid rgba(255,255,255,0.15)',
                  color: '#fcf7eb',
                  borderRadius: '6px',
                  padding: '4px 8px',
                  fontSize: '0.8rem',
                  cursor: 'pointer'
                }}
              >
                <option value={180} style={{ background: '#1c1524' }}>Suave (180px/s)</option>
                <option value={260} style={{ background: '#1c1524' }}>Normal (260px/s)</option>
                <option value={340} style={{ background: '#1c1524' }}>Rápida (340px/s)</option>
              </select>
            </div>

            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span style={{ fontSize: '0.8rem', color: '#b8a998' }}>Playback:</span>
              <select
                value={playbackSpeed}
                onChange={(e) => {
                  const s = parseFloat(e.target.value);
                  setPlaybackSpeed(s);
                  if (isPlaying) {
                    startTimeRef.current = performance.now() - (currentTime * 1000) / s;
                  }
                }}
                style={{
                  background: 'rgba(255,255,255,0.08)',
                  border: '1px solid rgba(255,255,255,0.15)',
                  color: '#fcf7eb',
                  borderRadius: '6px',
                  padding: '4px 8px',
                  fontSize: '0.8rem',
                  cursor: 'pointer'
                }}
              >
                <option value={0.5} style={{ background: '#1c1524' }}>0.5x</option>
                <option value={0.75} style={{ background: '#1c1524' }}>0.75x</option>
                <option value={1.0} style={{ background: '#1c1524' }}>1.0x</option>
                <option value={1.25} style={{ background: '#1c1524' }}>1.25x</option>
              </select>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
