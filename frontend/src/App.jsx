import React, { useState, useEffect, useRef } from 'react';
import { 
  UploadCloud, 
  Settings, 
  Music, 
  Download, 
  RefreshCw, 
  CheckCircle, 
  AlertTriangle, 
  Printer, 
  Loader2,
  Sliders,
  FileMusic,
  Play,
  Square,
  Eye,
  Video
} from 'lucide-react';
import confetti from 'canvas-confetti';
import { OpenSheetMusicDisplay } from 'opensheetmusicdisplay';

const API_BASE = 'http://localhost:8000'; // FastAPI default port

export default function App() {
  // File upload state
  const [file, setFile] = useState(null);
  const [dragOver, setDragOver] = useState(false);
  
  // Pipeline settings state
  const [confidence, setConfidence] = useState(0.45);
  const [minDuration, setMinDuration] = useState(30); // ms
  const [quantizeGrid, setQuantizeGrid] = useState(0.25); // 0.25 = semicolcheia, 0.5 = colcheia, 1.0 = seminima
  const [autoCalibrate, setAutoCalibrate] = useState(true);
  const [timeSignature, setTimeSignature] = useState('auto');
  const [bpmMode, setBpmMode] = useState('auto');
  const [bpm, setBpm] = useState(120);
  const [splitPoint, setSplitPoint] = useState(60);
  const [filterSlips, setFilterSlips] = useState(true);
  const [allowTriplets, setAllowTriplets] = useState(false);
  
  // Task tracking state
  const [taskId, setTaskId] = useState(null);
  const [taskStatus, setTaskStatus] = useState(null); // 'PENDING', 'PROCESSING', 'SUCCESS', 'FAILED'
  const [taskProgress, setTaskProgress] = useState(0);
  const [statusMessage, setStatusMessage] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const [isTranscribing, setIsTranscribing] = useState(false);
  const [isRequantizing, setIsRequantizing] = useState(false);
  
  // Renders
  const osmdRef = useRef(null);
  const osmdContainerRef = useRef(null);
  const [hasScoreRendered, setHasScoreRendered] = useState(false);

  // Playback & Animation states
  const [isPlaying, setIsPlaying] = useState(false);
  const [viewMode, setViewMode] = useState('animation'); // 'animation' (OneLine) vs 'pages' (PageFormat)
  const [speedFactor, setSpeedFactor] = useState(1.0);
  
  const audioCtxRef = useRef(null);
  const scheduledNoteIndicesRef = useRef(new Set());
  const playbackStartTimeRef = useRef(0);
  const playbackAnimFrameRef = useRef(null);
  const notesDataRef = useRef(null);
  const playheadMapRef = useRef([]);
  const vfElementsRef = useRef([]);
  
  // Playhead style state for exact height sizing
  const [playheadStyle, setPlayheadStyle] = useState({ top: '0px', height: '100%' });

  // Video recording states
  const [isRecording, setIsRecording] = useState(false);
  const mediaRecorderRef = useRef(null);
  const recordedChunksRef = useRef([]);
  const streamRef = useRef(null);
  const audioStreamDestRef = useRef(null);
  const renderCanvasRef = useRef(null);
  const cameraXRef = useRef(0);
  const cameraYRef = useRef(0);
  const bgImageRef = useRef(null);
  const fgImageRef = useRef(null);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (playbackAnimFrameRef.current) {
        cancelAnimationFrame(playbackAnimFrameRef.current);
      }
      if (audioCtxRef.current) {
        audioCtxRef.current.close();
      }
    };
  }, []);

  // File selection handler
  const handleFileChange = (e) => {
    if (e.target.files && e.target.files[0]) {
      setFile(e.target.files[0]);
      setErrorMsg('');
    }
  };

  // Drag and drop handlers
  const handleDragOver = (e) => {
    e.preventDefault();
    setDragOver(true);
  };

  const handleDragLeave = () => {
    setDragOver(false);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      const droppedFile = e.dataTransfer.files[0];
      const ext = droppedFile.name.split('.').pop().toLowerCase();
      if (['mp3', 'wav', 'm4a', 'flac', 'ogg'].includes(ext)) {
        setFile(droppedFile);
        setErrorMsg('');
      } else {
        setErrorMsg('Formato de arquivo não suportado. Por favor, arraste um arquivo MP3, WAV ou M4A.');
      }
    }
  };

  // Submit audio for transcription
  const startTranscription = async () => {
    if (!file) return;
    
    stopPlayback();
    notesDataRef.current = null;
    setIsTranscribing(true);
    setErrorMsg('');
    setTaskId(null);
    setTaskStatus('PENDING');
    setTaskProgress(0);
    setStatusMessage('Enviando arquivo de áudio...');
    setHasScoreRendered(false);
    if (osmdContainerRef.current) {
      osmdContainerRef.current.innerHTML = '';
    }

    const formData = new FormData();
    formData.append('file', file);
    formData.append('confidence_threshold', confidence);
    formData.append('min_duration_ms', minDuration);
    formData.append('quantize_grid', quantizeGrid);
    formData.append('auto_calibrate', autoCalibrate);
    formData.append('time_signature', timeSignature);
    formData.append('bpm', bpmMode === 'auto' ? 'auto' : bpm.toString());
    formData.append('split_point', splitPoint.toString());
    formData.append('filter_slips', filterSlips);
    formData.append('allow_triplets', allowTriplets);

    try {
      const response = await fetch(`${API_BASE}/api/transcribe`, {
        method: 'POST',
        body: formData,
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Falha ao iniciar a transcrição.');
      }

      const data = await response.json();
      setTaskId(data.task_id);
      setTaskStatus(data.status);
    } catch (err) {
      console.error(err);
      setErrorMsg(err.message || 'Erro de conexão com o servidor.');
      setIsTranscribing(false);
      setTaskStatus('FAILED');
    }
  };

  // Polling for task status updates
  useEffect(() => {
    if (!taskId || taskStatus === 'SUCCESS' || taskStatus === 'FAILED') return;

    const interval = setInterval(async () => {
      try {
        const response = await fetch(`${API_BASE}/api/tasks/${taskId}`);
        if (!response.ok) throw new Error('Não foi possível obter o status.');
        
        const data = await response.json();
        setTaskStatus(data.status);
        setTaskProgress(data.progress);
        setStatusMessage(data.message);
        
        if (data.status === 'SUCCESS') {
          clearInterval(interval);
          setIsTranscribing(false);
          // Set dynamic values from backend calibration
          setConfidence(data.confidence_threshold);
          setMinDuration(data.min_duration_ms);
          if (data.time_signature) setTimeSignature(data.time_signature);
          if (data.bpm) {
            setBpm(Math.round(data.bpm));
            setBpmMode('manual');
          }
          if (data.split_point) {
            setSplitPoint(data.split_point);
          }
          if (data.filter_slips !== undefined) {
            setFilterSlips(data.filter_slips);
          }
          if (data.allow_triplets !== undefined) {
            setAllowTriplets(data.allow_triplets);
          }
          triggerConfetti();
          setTimeout(() => {
            renderScore(taskId || data.task_id);
          }, 100);
        } else if (data.status === 'FAILED') {
          clearInterval(interval);
          setIsTranscribing(false);
          setErrorMsg(data.message);
        }
      } catch (err) {
        console.error(err);
        clearInterval(interval);
        setIsTranscribing(false);
        setTaskStatus('FAILED');
        setErrorMsg('Erro ao consultar o status do processamento.');
      }
    }, 1500);

    return () => clearInterval(interval);
  }, [taskId, taskStatus]);

  // Trigger celebration confetti
  const triggerConfetti = () => {
    confetti({
      particleCount: 150,
      spread: 80,
      origin: { y: 0.6 },
      colors: ['#c5a059', '#eae0ce', '#9e2c2c'] // Gold, Ivory, Felt Red
    });
  };

  // Load and render sheet music using OSMD
  const renderScore = async (id, mode = viewMode) => {
    if (!osmdContainerRef.current) return;
    
    try {
      setStatusMessage('Carregando partitura interativa...');
      osmdContainerRef.current.innerHTML = '';
      
      const xmlUrl = `${API_BASE}/api/download/${id}/xml`;
      const response = await fetch(xmlUrl);
      if (!response.ok) throw new Error('Erro ao baixar o arquivo XML.');
      
      const xmlText = await response.text();
      
      const osmd = new OpenSheetMusicDisplay(osmdContainerRef.current, {
        autoResize: true,
        drawTitle: false,
        drawSubtitle: false,
        drawComposer: false,
        backend: 'svg',
        renderLayout: 'PageFormat',
        drawingParameters: 'compacttight'
      });
      
      osmdRef.current = osmd;
      
      await osmd.load(xmlText);
      
      // Reduce zoom to allow more measures per line, drastically reducing the physical distance between notes
      // This mathematically forces the camera to travel much slower to maintain sync!
      osmd.Zoom = 1.3;
      osmd.EngravingRules.MinSkyBottomDistBetweenStaves = 1.0;
      osmd.EngravingRules.MinSkyBottomDistBetweenSystems = 1.5;
      
      osmd.render();
      osmd.cursor.hide();
      
      setHasScoreRendered(true);
      setStatusMessage('Partitura renderizada!');
    } catch (err) {
      console.error('Error rendering OSMD:', err);
      setErrorMsg('Erro ao carregar renderizador visual da partitura.');
    }
  };

  // Re-render score when viewMode changes
  useEffect(() => {
    if (taskStatus === 'SUCCESS' && taskId) {
      stopPlayback();
      renderScore(taskId, viewMode);
    }
  }, [viewMode]);

  // Synthesis engine for real-time playhead feedback (FM/Subtractive hybrid piano)
  const playSynthNote = (ctx, pitch, velocity, startTime, duration, streamDestination = null) => {
    const freq = 440 * Math.pow(2, (pitch - 69) / 12);
    
    const mainGain = ctx.createGain();
    const vol = (velocity / 127) * 0.35;
    
    mainGain.gain.setValueAtTime(0, startTime);
    mainGain.gain.linearRampToValueAtTime(vol, startTime + 0.006);
    mainGain.gain.exponentialRampToValueAtTime(0.0001, startTime + duration + 1.2);
    
    const filter = ctx.createBiquadFilter();
    filter.type = 'lowpass';
    filter.frequency.setValueAtTime(1500, startTime);
    filter.frequency.exponentialRampToValueAtTime(250, startTime + 0.8);
    
    const osc1 = ctx.createOscillator();
    osc1.type = 'triangle';
    osc1.frequency.setValueAtTime(freq, startTime);
    
    const osc2 = ctx.createOscillator();
    osc2.type = 'sine';
    osc2.frequency.setValueAtTime(freq * 2.0, startTime);
    
    const gain2 = ctx.createGain();
    gain2.gain.setValueAtTime(vol * 0.2, startTime);
    
    const hammer = ctx.createOscillator();
    hammer.type = 'sine';
    hammer.frequency.setValueAtTime(freq * 4.0, startTime);
    
    const hammerGain = ctx.createGain();
    hammerGain.gain.setValueAtTime(vol * 0.3, startTime);
    hammerGain.gain.exponentialRampToValueAtTime(0.0001, startTime + 0.035);
    
    osc1.connect(mainGain);
    osc2.connect(gain2);
    gain2.connect(mainGain);
    
    hammer.connect(hammerGain);
    hammerGain.connect(mainGain);
    
    mainGain.connect(filter);
    filter.connect(ctx.destination);
    if (streamDestination) {
      filter.connect(streamDestination);
    }
    
    osc1.start(startTime);
    osc2.start(startTime);
    hammer.start(startTime);
    
    osc1.stop(startTime + duration + 1.2);
    osc2.stop(startTime + duration + 1.2);
    hammer.stop(startTime + 0.04);
  };

  const startPlayback = async () => {
    if (isPlaying) {
      stopPlayback();
      return;
    }
    
    if (!osmdRef.current || !osmdContainerRef.current) return;
    
    try {
      let notesInfo = notesDataRef.current;
      if (!notesInfo) {
        try {
          setStatusMessage('Carregando notas para animação...');
          const res = await fetch(`${API_BASE}/api/tasks/${taskId}/notes`);
          if (!res.ok) throw new Error('Erro ao carregar as notas.');
          notesInfo = await res.json();
          notesDataRef.current = notesInfo;
        } catch (err) {
          console.error(err);
          alert('Não foi possível sincronizar as notas do instrumento.');
          return;
        }
      }
      
      const { notes, bpm: notesBpm } = notesInfo;
      const container = osmdContainerRef.current;
      const containerRect = container.getBoundingClientRect();
      
      // Calculate staff height to size the playhead line perfectly
      try {
        const staves = container.querySelectorAll('[class*="vf-stave"]');
        let minTop = 99999;
        let maxBottom = 0;
        staves.forEach(stave => {
          const r = stave.getBoundingClientRect();
          const relativeTop = r.top - containerRect.top + container.scrollTop;
          const relativeBottom = r.bottom - containerRect.top + container.scrollTop;
          if (relativeTop < minTop) minTop = relativeTop;
          if (relativeBottom > maxBottom) maxBottom = relativeBottom;
        });
        
        if (minTop < 99999 && maxBottom > 0) {
          setPlayheadStyle({
            top: `${minTop}px`,
            height: `${maxBottom - minTop}px`
          });
        } else {
          setPlayheadStyle({ top: '24px', height: 'calc(100% - 48px)' });
        }
      } catch (e) {
        console.warn("Could not calculate playhead height:", e);
        setPlayheadStyle({ top: '24px', height: 'calc(100% - 48px)' });
      }
      
      // Step 1: Pre-calculate playhead coordinate map using OSMD cursor
      const cursor = osmdRef.current.cursor;
      const playheadMap = [];
      
      try {
        cursor.show();
        cursor.reset();
        
        let safety = 0;
        while (!cursor.iterator.endReached && safety < 1500) {
          safety++;
          const beat = cursor.iterator.currentTimeStamp.RealValue;
          const cursorEl = cursor.cursorElement;
          
          let relativeX = 0;
          let relativeY = 0;
          
          if (cursorEl) {
            const rect = cursorEl.getBoundingClientRect();
            relativeX = rect.left - containerRect.left + container.scrollLeft;
            relativeY = rect.top - containerRect.top + container.scrollTop;
          } else {
            // Safe linear estimate: 200 pixels per beat
            relativeX = (beat * 200) + 120;
          }
          
          playheadMap.push({ beat, x: relativeX, y: relativeY });
          cursor.next();
        }
        cursor.reset();
        cursor.hide();
        
        playheadMapRef.current = playheadMap;
        console.log("Playhead Map calculated successfully:", playheadMap.slice(0, 10));
      } catch (calcErr) {
        console.warn("Could not pre-calculate cursor coordinates, utilizing linear map fallback:", calcErr);
        const fallbackMap = [];
        notes.forEach(n => {
          fallbackMap.push({ beat: n.onset_beat, x: (n.onset_beat * 200) + 120, y: 100 });
        });
        playheadMapRef.current = fallbackMap;
      }
      
      // Step 2: Cache all VexFlow elements positions (both X and Y)
      const cachedVf = [];
      const vfNodes = container.querySelectorAll('[class*="vf-"]');
      vfNodes.forEach(el => {
        const className = el.getAttribute('class') || '';
        if (
          className.includes('vf-stave') || 
          className.includes('vf-clef') || 
          className.includes('vf-key-signature') || 
          className.includes('vf-time-signature')
        ) {
          return;
        }
        const elRect = el.getBoundingClientRect();
        const elX = elRect.left - containerRect.left + container.scrollLeft;
        const elY = elRect.top - containerRect.top + container.scrollTop;
        
        // Hide notes immediately so they only appear exactly when played
        el.style.opacity = '0';
        el.style.transition = 'none'; // Instant pop (no fade-in)
        
        cachedVf.push({ el, x: elX, y: elY });
      });
      
      vfElementsRef.current = cachedVf;
      
      // Initialize camera tracking refs to the start of playback
      if (playheadMap.length > 0) {
        cameraXRef.current = playheadMap[0].x - (containerRect.width / 3);
        cameraYRef.current = playheadMap[0].y - (containerRect.height / 2);
        container.scrollLeft = cameraXRef.current;
        container.scrollTop = cameraYRef.current;
      }
      
      // Initialize Web Audio
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || window.webkitAudioContext)();
        audioStreamDestRef.current = audioCtxRef.current.createMediaStreamDestination();
      }
      const ctx = audioCtxRef.current;
      if (ctx.state === 'suspended') {
        await ctx.resume();
      }
      
      setIsPlaying(true);
      scheduledNoteIndicesRef.current.clear();
      
      const startAudioTime = ctx.currentTime;
      playbackStartTimeRef.current = startAudioTime;
      
      const beatDuration = 60.0 / notesBpm;
      
      const tick = () => {
        if (!audioCtxRef.current) return;
        const now = ctx.currentTime;
        const elapsedSec = (now - startAudioTime) * speedFactor;
        const elapsedBeats = elapsedSec / beatDuration;
        
        // Lookahead scheduler
        const lookaheadSec = 0.200;
        const maxScheduleBeat = elapsedBeats + (lookaheadSec * speedFactor) / beatDuration;
        
        notes.forEach((note, idx) => {
          if (note.onset_beat <= maxScheduleBeat && !scheduledNoteIndicesRef.current.has(idx)) {
            scheduledNoteIndicesRef.current.add(idx);
            const noteDelaySec = (note.onset_beat * beatDuration) / speedFactor;
            const scheduleTime = startAudioTime + noteDelaySec;
            const noteDurationSec = (note.duration_beat * beatDuration) / speedFactor;
            
            playSynthNote(ctx, note.pitch, note.velocity, scheduleTime, noteDurationSec, audioStreamDestRef.current);
          }
        });
        
        // Interpolate playhead coordinate from the map
        const map = playheadMapRef.current;
        let playheadX = 0;
        let playheadY = 0;
        
        let prevEntry = null;
        let nextEntry = null;
        for (let i = 0; i < map.length; i++) {
          if (map[i].beat <= elapsedBeats) {
            prevEntry = map[i];
          } else {
            nextEntry = map[i];
            break;
          }
        }
        
        if (prevEntry && nextEntry) {
          const ratio = (elapsedBeats - prevEntry.beat) / (nextEntry.beat - prevEntry.beat);
          playheadX = prevEntry.x + (nextEntry.x - prevEntry.x) * ratio;
          playheadY = prevEntry.y + (nextEntry.y - prevEntry.y) * ratio;
        } else if (prevEntry) {
          playheadX = prevEntry.x;
          playheadY = prevEntry.y;
        }
        
        // Smoothly scroll the container to center on the playhead (fast damping LERP)
        if (playheadX > 0) {
          const targetScrollLeft = playheadX - (containerRect.width / 2);
          const targetScrollTop = playheadY - (containerRect.height / 2);
          
          // Smooth 2D panning with spring-like physics
          // When playhead wraps to the next system, the camera glides down-left very rapidly!
          cameraXRef.current += (targetScrollLeft - cameraXRef.current) * 0.15;
          cameraYRef.current += (targetScrollTop - cameraYRef.current) * 0.45;

          
          container.scrollLeft = cameraXRef.current;
          container.scrollTop = cameraYRef.current;
          
          // Dynamic reveal animation (no fade-in, pop instantly when played)
          // A note is in the future if it is on a lower line (system) or ahead of playhead on current line
          vfElementsRef.current.forEach(item => {
            const isFuture = (item.y > playheadY + 40) || (Math.abs(item.y - playheadY) <= 40 && item.x > playheadX + 4);
            if (isFuture) {
              item.el.style.opacity = '0';
            } else {
              item.el.style.opacity = '1';
            }
          });
          
          // Internal video frame painting if recording
          if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording' && renderCanvasRef.current && bgImageRef.current && fgImageRef.current) {
            const canvas = renderCanvasRef.current;
            const canvasCtx = canvas.getContext('2d');
            
            // Draw background parchment color
            canvasCtx.fillStyle = '#fdfbf7';
            canvasCtx.fillRect(0, 0, canvas.width, canvas.height);
            
            canvasCtx.save();
            
            // Decouple video coordinates from DOM width!
            // Calculate absolute smoothed playhead coordinate in the SVG
            // Use exact playheadX for horizontal panning to eliminate any speed lag/acceleration (constant BPM speed)
            const exactCameraX = playheadX;
            const smoothedPlayheadY = cameraYRef.current + (containerRect.height / 2);
            
            // Apply optical zoom to keep video HD despite the math layout shrink
            const videoScale = 1.4;
            canvasCtx.translate(canvas.width / 2, canvas.height / 2);
            canvasCtx.scale(videoScale, videoScale);
            canvasCtx.translate(-canvas.width / 2, -canvas.height / 2);
            
            // Translate the canvas to center the absolute playhead perfectly at 1280x720 video center (640x360)
            const videoOffsetX = (canvas.width / 2) - exactCameraX;
            const videoOffsetY = (canvas.height / 2) - smoothedPlayheadY;
            
            canvasCtx.translate(videoOffsetX, videoOffsetY);
            
            // 1. Draw Background Layer (Staves, Clefs, empty)
            canvasCtx.drawImage(bgImageRef.current, 0, 0);
            
            // 2. Draw Foreground Layer (Notes) via precise Discrete Clipping Mask
            canvasCtx.beginPath();
            // Reveal everything in previous systems (above current playhead Y)
            canvasCtx.rect(0, 0, bgImageRef.current.width, playheadY - 20);
            
            // Reveal current system strictly up to the exact discrete X position of the last played beat
            // This forces notes to POP perfectly in sync with the audio, stopping the "fast wipe" effect
            const discreteX = prevEntry ? prevEntry.x : playheadX;
            canvasCtx.rect(0, playheadY - 20, discreteX + 15, 300);
            canvasCtx.clip();
            
            canvasCtx.drawImage(fgImageRef.current, 0, 0);
            canvasCtx.restore();
          }
        }
        
        // Stop check
        const lastBeat = notes[notes.length - 1]?.onset_beat || 0;
        if (elapsedBeats > lastBeat + 2.0) {
          stopPlayback();
          return;
        }
        
        playbackAnimFrameRef.current = requestAnimationFrame(tick);
      };
      
      playbackAnimFrameRef.current = requestAnimationFrame(tick);
    } catch (err) {
      console.error("Erro na animação:", err);
      alert("Falha ao iniciar a animação: " + err.message);
      setIsPlaying(false);
    }
  };

  const stopPlayback = () => {
    setIsPlaying(false);
    if (playbackAnimFrameRef.current) {
      cancelAnimationFrame(playbackAnimFrameRef.current);
      playbackAnimFrameRef.current = null;
    }
    if (osmdRef.current) {
      osmdRef.current.cursor.hide();
      osmdRef.current.cursor.reset();
    }
    scheduledNoteIndicesRef.current.clear();
    
    // Restore opacity to all elements
    if (vfElementsRef.current) {
      vfElementsRef.current.forEach(item => {
        item.el.style.opacity = '1';
      });
    }

    // Stop recording if active
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
  };

  const startRecording = async () => {
    try {
      setStatusMessage('Preparando estúdio de renderização invisível...');
      
      // 1. Prepare Audio Stream
      if (!audioCtxRef.current) {
        audioCtxRef.current = new (window.AudioContext || window.webkitAudioContext)();
        audioStreamDestRef.current = audioCtxRef.current.createMediaStreamDestination();
      }
      if (audioCtxRef.current.state === 'suspended') {
        await audioCtxRef.current.resume();
      }
      const audioStream = audioStreamDestRef.current.stream;
      
      // 2. Prepare Video Stream (Hidden Canvas)
      const canvas = document.createElement('canvas');
      canvas.width = 1280;
      canvas.height = 720;
      
      // CRITICAL FIX: Browsers heavily throttle requestAnimationFrame and Canvas updates for off-DOM or off-screen elements.
      // To force full 60fps rendering, we MUST mount it to the DOM where the browser considers it "visible",
      // but we hide it using near-zero opacity and pointer-events: none.
      canvas.style.position = 'absolute';
      canvas.style.top = '0';
      canvas.style.left = '0';
      canvas.style.opacity = '0.01'; // Not 0, some browsers throttle 0 opacity too
      canvas.style.pointerEvents = 'none';
      canvas.style.zIndex = '-1';
      document.body.appendChild(canvas);
      
      renderCanvasRef.current = canvas;
      
      // Ensure smooth video framerate
      const videoStream = canvas.captureStream(30);  
      
      // 3. Combine Streams
      const combinedStream = new MediaStream([
        ...videoStream.getVideoTracks(),
        ...audioStream.getAudioTracks()
      ]);
      
      streamRef.current = combinedStream;
      recordedChunksRef.current = [];
      
      // Try to use a standard webm codec that works without screen sharing constraints
      const options = { mimeType: 'video/webm' };
      const recorder = new MediaRecorder(combinedStream, options);
      
      recorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          recordedChunksRef.current.push(e.data);
        }
      };
      
      recorder.onstop = () => {
        const blob = new Blob(recordedChunksRef.current, { type: 'video/webm' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `partitura_animada_${taskId}.webm`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
        
        // Cleanup DOM
        if (renderCanvasRef.current && renderCanvasRef.current.parentNode) {
          renderCanvasRef.current.parentNode.removeChild(renderCanvasRef.current);
        }
        
        // Stop all tracks
        combinedStream.getTracks().forEach(track => track.stop());
        setIsRecording(false);
        setStatusMessage('Vídeo gerado e exportado com sucesso!');
      };
      
      mediaRecorderRef.current = recorder;
      recorder.start(100); // Collect data chunks frequently to avoid memory bloat
      setIsRecording(true);
      
      // -- ULTRA FAST DUAL-LAYER SVG CACHING --
      // Force explicit dimensions so XMLSerializer doesn't lose the viewport framing
      const container = osmdContainerRef.current;
      const svgElement = container.querySelector('svg');
      if (svgElement) {
        const svgRect = svgElement.getBoundingClientRect();
        svgElement.setAttribute('width', svgRect.width);
        svgElement.setAttribute('height', svgRect.height);
        
        // Background Layer (Empty Staves)
        const bgSvg = svgElement.cloneNode(true);
        bgSvg.querySelectorAll('.vf-stavenote, .vf-stem, .vf-beam, .vf-notehead, .vf-modifiers').forEach(el => el.remove());
        const bgData = new XMLSerializer().serializeToString(bgSvg);
        const bgUrl = URL.createObjectURL(new Blob([bgData], { type: 'image/svg+xml;charset=utf-8' }));
        const bgImg = new Image();
        await new Promise(r => { bgImg.onload = r; bgImg.src = bgUrl; });
        bgImageRef.current = bgImg;
        URL.revokeObjectURL(bgUrl);
        
        // Foreground Layer (All Notes Fully Visible)
        const fgSvg = svgElement.cloneNode(true);
        fgSvg.querySelectorAll('.vf-stavenote, .vf-notehead, .vf-stem, .vf-beam').forEach(el => {
          el.style.opacity = '1';
        });
        const fgData = new XMLSerializer().serializeToString(fgSvg);
        const fgUrl = URL.createObjectURL(new Blob([fgData], { type: 'image/svg+xml;charset=utf-8' }));
        const fgImg = new Image();
        await new Promise(r => { fgImg.onload = r; fgImg.src = fgUrl; });
        fgImageRef.current = fgImg;
        URL.revokeObjectURL(fgUrl);
      }
      
      // Auto-trigger playback animation which will rapidly paint our cached layers!
      startPlayback();
    } catch (err) {
      console.error("Recording setup error:", err);
      alert("Falha na renderização de fundo: " + err.message);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
  };

  // Run instant re-quantization
  const handleRequantize = async (newGrid, newTimeSig, newBpmMode, newBpmVal, newSplitVal, newConfVal, newMinDurVal, newFilterSlips, newAllowTriplets) => {
    if (!taskId || isRequantizing) return;
    
    setQuantizeGrid(newGrid);
    setTimeSignature(newTimeSig);
    
    const resolvedBpmVal = newBpmMode === 'auto' ? null : parseFloat(newBpmVal);
    const resolvedSplitVal = newSplitVal !== undefined ? parseInt(newSplitVal) : splitPoint;
    const resolvedConfVal = newConfVal !== undefined ? parseFloat(newConfVal) : confidence;
    const resolvedMinDurVal = newMinDurVal !== undefined ? parseFloat(newMinDurVal) : minDuration;
    const resolvedFilterSlips = newFilterSlips !== undefined ? newFilterSlips : filterSlips;
    const resolvedAllowTriplets = newAllowTriplets !== undefined ? newAllowTriplets : allowTriplets;

    if (newConfVal !== undefined) setConfidence(newConfVal);
    if (newMinDurVal !== undefined) setMinDuration(newMinDurVal);
    if (newFilterSlips !== undefined) setFilterSlips(newFilterSlips);
    if (newAllowTriplets !== undefined) setAllowTriplets(newAllowTriplets);
    
    setIsRequantizing(true);
    setErrorMsg('');
    setStatusMessage('Requantizando partitura em tempo real...');

    try {
      const response = await fetch(`${API_BASE}/api/requantize/${taskId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ 
          quantize_grid: parseFloat(newGrid),
          time_signature: newTimeSig,
          bpm: resolvedBpmVal,
          split_point: resolvedSplitVal,
          confidence_threshold: resolvedConfVal,
          min_duration_ms: resolvedMinDurVal,
          filter_slips: resolvedFilterSlips,
          allow_triplets: resolvedAllowTriplets
        })
      });

      if (!response.ok) {
        const err = await response.json();
        throw new Error(err.detail || 'Falha na requantização.');
      }
      
      const data = await response.json();
      if (data.bpm) setBpm(Math.round(data.bpm));
      if (data.time_signature) setTimeSignature(data.time_signature);
      if (data.split_point) setSplitPoint(data.split_point);
      if (data.confidence_threshold !== undefined) setConfidence(data.confidence_threshold);
      if (data.min_duration_ms !== undefined) setMinDuration(data.min_duration_ms);
      if (data.filter_slips !== undefined) setFilterSlips(data.filter_slips);
      if (data.allow_triplets !== undefined) setAllowTriplets(data.allow_triplets);

      stopPlayback();
      notesDataRef.current = null;
      
      // Re-render score
      await renderScore(taskId);
      triggerConfetti();
    } catch (err) {
      console.error(err);
      setErrorMsg(err.message || 'Erro ao requantizar.');
    } finally {
      setIsRequantizing(false);
    }
  };

  // Trigger print dialog for the score
  const handlePrint = () => {
    if (!hasScoreRendered) return;
    
    const printContent = osmdContainerRef.current.innerHTML;
    const windowUrl = 'about:blank';
    const uniqueName = new Date();
    const windowName = 'Print' + uniqueName.getTime();
    
    const printWindow = window.open(windowUrl, windowName, 'left=50,top=50,width=800,height=900');
    printWindow.document.write(`
      <html>
        <head>
          <title>Pianofy - Partitura Transcrita</title>
          <style>
            body { margin: 0; padding: 20px; display: flex; justify-content: center; }
            svg { width: 100%; height: auto; }
            @media print {
              body { padding: 0; }
              @page { size: portrait; }
            }
          </style>
        </head>
        <body>
          <div style="width: 100%">${printContent}</div>
          <script>
            window.onload = function() {
              window.print();
              window.close();
            }
          </script>
        </body>
      </html>
    `);
    printWindow.document.close();
  };

  return (
    <div className="app-container">
      {/* Header */}
      <header className="app-header">
        <div className="logo-wrapper">
          <div className="logo-icon">
            <Music />
          </div>
          <h1 className="app-title">
            Pianofy
          </h1>
        </div>
        <p className="app-subtitle">
          Transforme improvisos de piano solo em partituras limpas e profissionais em segundos usando Deep Learning de última geração.
        </p>
      </header>

      {/* Main Workspace */}
      <main className="main-workspace">
        
        {/* Left Side: Setup & Settings */}
        <section className="settings-sidebar">
          
          {/* Audio Dropzone */}
          <div 
            className={`glass-container upload-zone ${dragOver ? 'active' : ''}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => document.getElementById('audio-input').click()}
          >
            <input 
              id="audio-input" 
              type="file" 
              accept=".mp3,.wav,.m4a,.flac,.ogg" 
              className="hidden" 
              onChange={handleFileChange}
            />
            <UploadCloud />
            <p className="upload-filename">
              {file ? file.name : 'Arraste seu áudio aqui'}
            </p>
            <p className="upload-hint">
              Suporta MP3, WAV, M4A ou FLAC de piano solo.
            </p>
            {file && (
              <span className="upload-size-tag">
                {(file.size / 1024 / 1024).toFixed(2)} MB
              </span>
            )}
          </div>

          {/* MATH DIAGNOSTICS PANEL */}
          <div style={{ position: 'fixed', bottom: 10, right: 10, background: 'black', color: 'lime', padding: '10px', zIndex: 9999, fontFamily: 'monospace' }}>
            <div>Audio Last Beat: {(notesInfo && notesInfo.notes && notesInfo.notes.length > 0) ? notesInfo.notes[notesInfo.notes.length - 1].onset_beat.toFixed(2) : 'N/A'}</div>
            <div>Visual Last Beat: {(playheadMapRef.current && playheadMapRef.current.length > 0) ? playheadMapRef.current[playheadMapRef.current.length - 1].beat.toFixed(2) : 'N/A'}</div>
          </div>
          
          {/* Config Controls */}
          <div className="glass-container" style={{ marginTop: '24px' }}>
            <div className="card-header">
              <Settings className="text-pink" />
              <h2>Parâmetros do Pipeline</h2>
            </div>

            {/* Auto Calibrate Toggle */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '20px' }}>
              <input 
                id="auto-calibrate"
                type="checkbox"
                checked={autoCalibrate}
                onChange={(e) => setAutoCalibrate(e.target.checked)}
                disabled={isTranscribing}
                style={{ width: '16px', height: '16px', cursor: 'pointer' }}
              />
              <label htmlFor="auto-calibrate" style={{ fontSize: '0.85rem', fontWeight: 600, cursor: 'pointer', color: 'var(--accent-violet)' }}>
                Calibração Inteligente (Recomendado)
              </label>
            </div>

            {/* Filter Slips Toggle */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '12px' }}>
              <input 
                id="filter-slips"
                type="checkbox"
                checked={filterSlips}
                onChange={(e) => {
                  const val = e.target.checked;
                  setFilterSlips(val);
                  if (taskStatus === 'SUCCESS') {
                    handleRequantize(quantizeGrid, timeSignature, bpmMode, bpm, splitPoint, confidence, minDuration, val, allowTriplets);
                  }
                }}
                disabled={isTranscribing || isRequantizing}
                style={{ width: '16px', height: '16px', cursor: 'pointer' }}
              />
              <label htmlFor="filter-slips" style={{ fontSize: '0.85rem', fontWeight: 600, cursor: 'pointer', color: 'var(--accent-pink)' }}>
                Filtro de Escorregão (Appoggiaturas)
              </label>
            </div>

            {/* Allow Triplets Toggle */}
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '20px' }}>
              <input 
                id="allow-triplets"
                type="checkbox"
                checked={allowTriplets}
                onChange={(e) => {
                  const val = e.target.checked;
                  setAllowTriplets(val);
                  if (taskStatus === 'SUCCESS') {
                    handleRequantize(quantizeGrid, timeSignature, bpmMode, bpm, splitPoint, confidence, minDuration, filterSlips, val);
                  }
                }}
                disabled={isTranscribing || isRequantizing}
                style={{ width: '16px', height: '16px', cursor: 'pointer' }}
              />
              <label htmlFor="allow-triplets" style={{ fontSize: '0.85rem', fontWeight: 600, cursor: 'pointer', color: 'var(--accent-cyan)' }}>
                Permitir Tercinas (Ritmo Ternário)
              </label>
            </div>
                 {/* Confidence Slider */}
            <div className="control-field" style={{ opacity: autoCalibrate ? 0.5 : 1, transition: 'opacity 0.2s' }}>
              <div className="control-label">
                <span className="label-title">
                  Limiar de Confiança
                  <span className="label-help" title="Filtra notas fantasmas e ruídos. Valores mais altos evitam falsos positivos.">
                    ⓘ
                  </span>
                </span>
                <span className="control-value value-violet">{confidence.toFixed(2)}</span>
              </div>
              <input 
                type="range" 
                min="0.1" 
                max="0.9" 
                step="0.05"
                value={confidence} 
                onChange={(e) => setConfidence(parseFloat(e.target.value))}
                onMouseUp={() => {
                  if (taskStatus === 'SUCCESS') {
                    handleRequantize(quantizeGrid, timeSignature, bpmMode, bpm, splitPoint, confidence, minDuration, filterSlips, allowTriplets);
                  }
                }}
                onTouchEnd={() => {
                  if (taskStatus === 'SUCCESS') {
                    handleRequantize(quantizeGrid, timeSignature, bpmMode, bpm, splitPoint, confidence, minDuration, filterSlips, allowTriplets);
                  }
                }}
                disabled={isTranscribing || autoCalibrate || isRequantizing}
                className="range-input violet"
              />
            </div>

            {/* Pruning Slider */}
            <div className="control-field" style={{ opacity: autoCalibrate ? 0.5 : 1, transition: 'opacity 0.2s' }}>
              <div className="control-label">
                <span className="label-title">
                  Duração Mínima da Nota
                  <span className="label-help" title="Notas mais curtas que isso serão deletadas para remover 'esbarrões'.">
                    ⓘ
                  </span>
                </span>
                <span className="control-value value-pink">{minDuration} ms</span>
              </div>
              <input 
                type="range" 
                min="10" 
                max="250" 
                step="10"
                value={minDuration} 
                onChange={(e) => setMinDuration(parseInt(e.target.value))}
                onMouseUp={() => {
                  if (taskStatus === 'SUCCESS') {
                    handleRequantize(quantizeGrid, timeSignature, bpmMode, bpm, splitPoint, confidence, minDuration, filterSlips, allowTriplets);
                  }
                }}
                onTouchEnd={() => {
                  if (taskStatus === 'SUCCESS') {
                    handleRequantize(quantizeGrid, timeSignature, bpmMode, bpm, splitPoint, confidence, minDuration, filterSlips, allowTriplets);
                  }
                }}
                disabled={isTranscribing || autoCalibrate || isRequantizing}
                className="range-input pink"
              />
            </div>

            {/* Quantization Snapping Grid */}
            <div className="control-field">
              <label className="control-label">
                <span className="label-title">
                  Grade de Quantização Musical
                  <span className="label-help" title="Alinha o tempo das notas com as frações de tempo do metrônomo para facilitar a leitura.">
                    ⓘ
                  </span>
                </span>
              </label>
              <select
                value={quantizeGrid}
                onChange={(e) => {
                  const val = parseFloat(e.target.value);
                  if (taskStatus === 'SUCCESS') {
                    handleRequantize(val, timeSignature, bpmMode, bpm, splitPoint, confidence, minDuration, filterSlips, allowTriplets);
                  } else {
                    setQuantizeGrid(val);
                  }
                }}
                disabled={isTranscribing || isRequantizing}
                className="select-input"
              >
                <option value="0.25">Semicolcheia (1/16) - Muito Detalhada</option>
                <option value="0.5">Colcheia (1/8) - Equilibrada (Padrão)</option>
                <option value="1">Semínima (1/4) - Simplificada</option>
              </select>
            </div>

            {/* Time Signature Select */}
            <div className="control-field">
              <label className="control-label">
                <span className="label-title">
                  Fórmula de Compasso
                  <span className="label-help" title="Define a divisão de tempos do compasso. Importante para alinhar barras em valsas (3/4) ou ritmos compostos (6/8).">
                    ⓘ
                  </span>
                </span>
              </label>
              <select
                value={timeSignature}
                onChange={(e) => {
                  const val = e.target.value;
                  if (taskStatus === 'SUCCESS') {
                    handleRequantize(quantizeGrid, val, bpmMode, bpm, splitPoint, confidence, minDuration, filterSlips, allowTriplets);
                  } else {
                    setTimeSignature(val);
                  }
                }}
                disabled={isTranscribing || isRequantizing}
                className="select-input"
              >
                <option value="auto">Detectar Automatically (IA)</option>
                <option value="4/4">Quaternário (4/4)</option>
                <option value="3/4">Ternário (3/4) - Valsa / Minueto</option>
                <option value="2/4">Binário (2/4)</option>
                <option value="6/8">Composto (6/8)</option>
              </select>
            </div>

            {/* Tempo BPM Select */}
            <div className="control-field">
              <label className="control-label">
                <span className="label-title">
                  Tempo (Andamento BPM)
                  <span className="label-help" title="Velocidade da música. A IA pode estimar automaticamente o BPM de sua performance.">
                    ⓘ
                  </span>
                </span>
              </label>
              <select
                value={bpmMode}
                onChange={(e) => {
                  const val = e.target.value;
                  setBpmMode(val);
                  if (taskStatus === 'SUCCESS') {
                    handleRequantize(quantizeGrid, timeSignature, val, bpm, splitPoint, confidence, minDuration, filterSlips, allowTriplets);
                  }
                }}
                disabled={isTranscribing || isRequantizing}
                className="select-input"
              >
                <option value="auto">Detectar Automatically (IA)</option>
                <option value="manual">Manual (Personalizado)</option>
              </select>
            </div>

            {/* BPM Manual Slider */}
            {bpmMode === 'manual' && (
              <div className="control-field" style={{ marginTop: '4px' }}>
                <div className="control-label">
                  <span className="label-title">Andamento Manual</span>
                  <span className="control-value value-cyan">{bpm} BPM</span>
                </div>
                <input 
                  type="range" 
                  min="50" 
                  max="200" 
                  step="1"
                  value={bpm} 
                  onChange={(e) => setBpm(parseInt(e.target.value))}
                  onMouseUp={() => {
                    if (taskStatus === 'SUCCESS') {
                      handleRequantize(quantizeGrid, timeSignature, 'manual', bpm, splitPoint, confidence, minDuration, filterSlips, allowTriplets);
                    }
                  }}
                  onTouchEnd={() => {
                    if (taskStatus === 'SUCCESS') {
                      handleRequantize(quantizeGrid, timeSignature, 'manual', bpm, splitPoint, confidence, minDuration, filterSlips, allowTriplets);
                    }
                  }}
                  disabled={isTranscribing || isRequantizing}
                  className="range-input cyan"
                />
              </div>
            )}

            {/* Keyboard Split Point Select */}
            <div className="control-field">
              <label className="control-label">
                <span className="label-title">
                  Registro de Divisão (Split Point)
                  <span className="label-help" title="Nota limite onde as claves de Sol e Fá se dividem. Suba para Ré4 ou Mi4 para manter acordes médios da mão esquerda na clave de Fá.">
                    ⓘ
                  </span>
                </span>
              </label>
              <select
                value={splitPoint}
                onChange={(e) => {
                  const val = parseInt(e.target.value);
                  setSplitPoint(val);
                  if (taskStatus === 'SUCCESS') {
                    handleRequantize(quantizeGrid, timeSignature, bpmMode, bpm, val, confidence, minDuration, filterSlips, allowTriplets);
                  }
                }}
                disabled={isTranscribing || isRequantizing}
                className="select-input"
              >
                <option value="57">Lá3 (MIDI 57) - Melodia Grave</option>
                <option value="60">Dó4 / Central (MIDI 60) - Padrão</option>
                <option value="62">Ré4 (MIDI 62) - Valsa / Acompanhamento Médio</option>
                <option value="63">Ré#4 (MIDI 63) - Valsa Estendida</option>
                <option value="64">Mi4 (MIDI 64) - Registro Alto</option>
              </select>
            </div>

            {/* Start Button */}
            <button
              onClick={startTranscription}
              disabled={!file || isTranscribing}
              className="btn-primary"
              style={{ marginTop: '12px' }}
            >
              {isTranscribing ? (
                <>
                  <Loader2 className="animate-spin" style={{ width: '18px', height: '18px' }} />
                  Transcrevendo...
                </>
              ) : (
                'Iniciar Transcrição'
              )}
            </button>
          </div>
        </section>

        {/* Right Side: Progress & Status */}
        <section className="status-panel">
          <div className="glass-container" style={{ minHeight: '385px', justifyContent: 'space-between' }}>
            <div>
              <div className="card-header">
                <Sliders className="text-cyan" />
                <h2>Status do Processamento</h2>
              </div>

              {/* Default Empty State */}
              {!taskStatus && (
                <div className="empty-state">
                  <Music />
                  <p>Selecione um arquivo de áudio e clique em "Iniciar Transcrição" para rodar o pipeline.</p>
                </div>
              )}

              {/* Progress & Processing States */}
              {taskStatus && taskStatus !== 'SUCCESS' && taskStatus !== 'FAILED' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '16px', padding: '16px 0' }}>
                  {/* Wave Animation */}
                  <div className="wave-container">
                    <div className="wave-bar"></div>
                    <div className="wave-bar"></div>
                    <div className="wave-bar"></div>
                    <div className="wave-bar"></div>
                    <div className="wave-bar"></div>
                    <div className="wave-bar"></div>
                  </div>

                  <div className="progress-section">
                    <div className="progress-header">
                      <span className="progress-message">{statusMessage}</span>
                      <span className="progress-percent">{taskProgress}%</span>
                    </div>
                    {/* Progress bar */}
                    <div className="progress-bar-bg">
                      <div 
                        className="progress-bar-fill"
                        style={{ width: `${taskProgress}%` }}
                      ></div>
                    </div>
                  </div>

                  <div className="info-note">
                    O processamento inicial por redes neurais (Onsets and Frames) consome processamento de CPU no backend e leva em média de 10 a 20 segundos.
                  </div>
                </div>
              )}

              {/* Success State */}
              {taskStatus === 'SUCCESS' && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
                  <div className="status-box success">
                    <CheckCircle className="status-icon" style={{ width: '24px', height: '24px' }} />
                    <div className="status-content">
                      <h3>Transcrição Realizada com Sucesso!</h3>
                      <p>
                        Seu improviso foi limpo e quantizado. A partitura interativa está disponível abaixo.
                      </p>
                    </div>
                  </div>

                  {/* Actions & Export Panel */}
                  <div className="export-grid">
                    <a 
                      href={`${API_BASE}/api/download/${taskId}/xml`}
                      download
                      className="download-link xml"
                    >
                      <span className="download-label">
                        <Download style={{ width: '16px', height: '16px', color: 'var(--accent-violet)' }} />
                        Baixar MusicXML (.xml)
                      </span>
                      <span className="download-info">MuseScore / Sibelius</span>
                    </a>

                    <a 
                      href={`${API_BASE}/api/download/${taskId}/midi`}
                      download
                      className="download-link midi"
                    >
                      <span className="download-label">
                        <Download style={{ width: '16px', height: '16px', color: 'var(--accent-pink)' }} />
                        Baixar MIDI Quantizado
                      </span>
                      <span className="download-info">DAWs / Synthesia</span>
                    </a>
                  </div>

                  {/* Requantization Prompt */}
                  <div className="requantize-prompt-box">
                    <div className="requantize-prompt-header">
                      <span className="requantize-prompt-title">Ajuste Fino de Quantização</span>
                      {isRequantizing && <Loader2 className="animate-spin text-violet" style={{ width: '14px', height: '14px' }} />}
                    </div>
                    <p className="requantize-prompt-desc">
                      Não gostou do ritmo gerado? Modifique a grade na barra lateral. A requantização será aplicada **instantaneamente** sem reprocessar o áudio.
                    </p>
                  </div>
                </div>
              )}

              {/* Error State */}
              {taskStatus === 'FAILED' && (
                <div className="status-box failed">
                  <AlertTriangle className="status-icon" style={{ width: '24px', height: '24px' }} />
                  <div className="status-content">
                    <h3>Falha no Processamento</h3>
                    <p>{errorMsg || 'Erro interno desconhecido no servidor.'}</p>
                  </div>
                </div>
              )}
            </div>

            {/* Print trigger */}
            {taskStatus === 'SUCCESS' && hasScoreRendered && (
              <button
                onClick={handlePrint}
                className="btn-secondary"
                style={{ marginTop: '24px' }}
              >
                <Printer style={{ width: '16px', height: '16px' }} />
                Imprimir / Exportar PDF da Partitura
              </button>
            )}
          </div>
        </section>
      </main>

      {/* Sheet Music Visualizer Section */}
      {taskStatus === 'SUCCESS' && (
        <section className="glass-container score-section">
          <div className="score-header" style={{ display: 'flex', flexDirection: 'column', gap: '16px', borderBottom: '1px solid rgba(255,255,255,0.08)', paddingBottom: '16px', marginBottom: '20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '12px' }}>
              <div className="score-title">
                <div className="score-dot"></div>
                <h2>Partitura Interativa & Animação</h2>
              </div>
              
              {/* Animated Player Controls */}
              <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
                {/* Play / Stop Button */}
                <button
                  onClick={startPlayback}
                  className={`btn-play ${isPlaying ? 'playing' : ''}`}
                  style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    padding: '8px 16px',
                    borderRadius: '8px',
                    border: 'none',
                    fontWeight: 600,
                    cursor: 'pointer',
                    fontSize: '0.85rem',
                    backgroundColor: isPlaying ? 'var(--accent-pink)' : 'var(--accent-cyan)',
                    color: '#0d0d1e',
                    transition: 'all 0.2s ease',
                    boxShadow: '0 0 10px rgba(0, 240, 255, 0.2)'
                  }}
                >
                  {isPlaying ? (
                    <>
                      <Square style={{ width: '14px', height: '14px', fill: '#0d0d1e' }} />
                      Parar Animação
                    </>
                  ) : (
                    <>
                      <Play style={{ width: '14px', height: '14px', fill: '#0d0d1e' }} />
                      Iniciar Animação
                    </>
                  )}
                </button>

                {/* Gravar Vídeo / Record Button */}
                {isRecording ? (
                  <button
                    onClick={stopRecording}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '8px 16px',
                      borderRadius: '8px',
                      border: 'none',
                      fontWeight: 600,
                      cursor: 'pointer',
                      fontSize: '0.85rem',
                      backgroundColor: 'var(--accent-pink)',
                      color: '#ffffff',
                      transition: 'all 0.2s ease',
                      boxShadow: '0 0 12px rgba(158, 44, 44, 0.4)'
                    }}
                  >
                    <Video style={{ width: '14px', height: '14px', fill: '#ffffff' }} />
                    Parar Gravação
                  </button>
                ) : (
                  <button
                    onClick={startRecording}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: '8px',
                      padding: '8px 16px',
                      borderRadius: '8px',
                      border: '1px solid rgba(197, 160, 89, 0.3)',
                      fontWeight: 600,
                      cursor: 'pointer',
                      fontSize: '0.85rem',
                      backgroundColor: 'rgba(0,0,0,0.3)',
                      color: 'var(--accent-cyan)',
                      transition: 'all 0.2s ease'
                    }}
                  >
                    <Video style={{ width: '14px', height: '14px' }} />
                    Gravar Vídeo
                  </button>
                )}

                {/* Speed Multiplier Select */}
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '0.8rem', color: '#8c8ca2' }}>
                  <span>Velocidade:</span>
                  <select
                    value={speedFactor}
                    onChange={(e) => setSpeedFactor(parseFloat(e.target.value))}
                    style={{
                      backgroundColor: 'rgba(0,0,0,0.3)',
                      color: '#ffffff',
                      border: '1px solid rgba(255,255,255,0.1)',
                      borderRadius: '6px',
                      padding: '4px 8px',
                      cursor: 'pointer',
                      outline: 'none',
                      fontWeight: 600
                    }}
                  >
                    <option value="0.5">0.5x (Treinar)</option>
                    <option value="0.75">0.75x</option>
                    <option value="1.0">1.0x (Padrão)</option>
                    <option value="1.25">1.25x</option>
                    <option value="1.5">1.5x</option>
                  </select>
                </div>
              </div>
            </div>
            
            {viewMode === 'animation' && (
              <p style={{ fontSize: '0.75rem', color: '#8c8ca2', margin: 0 }}>
                💡 <b>Dica:</b> No modo contínuo, a tela deslizará da esquerda para a direita simulando um vídeo animado, ideal para acompanhar tocando em tempo real.
              </p>
            )}
          </div>
          
          <div style={{ position: 'relative', width: '100%' }}>
            <div 
              id="osmd-container" 
              className="osmd-container"
              ref={osmdContainerRef}
              style={{ 
                display: hasScoreRendered && !errorMsg ? 'block' : 'none',
                width: '100%',
                height: '420px',
                overflowX: 'auto',
                overflowY: 'auto',
                padding: '24px 16px',
                borderRadius: '12px',
                border: '1px solid rgba(255, 255, 255, 0.05)',
                boxShadow: 'inset 0 0 20px rgba(0,0,0,0.05)',
                scrollBehavior: 'smooth'
              }}
            ></div>
          </div>
          
          {!hasScoreRendered && !errorMsg && (
            <div className="osmd-loading">
              <Loader2 className="animate-spin" />
              <p style={{ fontSize: '0.85rem', marginTop: '8px' }}>Carregando visualização da partitura...</p>
            </div>
          )}
          
          {errorMsg && (
            <div className="status-box failed" style={{ margin: 0 }}>
              <AlertTriangle className="status-icon" style={{ width: '24px', height: '24px' }} />
              <div className="status-content">
                <h3>Erro ao carregar a partitura</h3>
                <p>{errorMsg}</p>
              </div>
            </div>
          )}
        </section>
      )}

      {/* Footer */}
      <footer className="app-footer">
        <p>© 2026 Pianofy App. Desenvolvido com Google Magenta's Onsets & Frames & music21.</p>
      </footer>
    </div>
  );
}
