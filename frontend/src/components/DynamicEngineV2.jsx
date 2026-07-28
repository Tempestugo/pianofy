import React, { useEffect, useRef, useCallback } from 'react';

const PITCH_MIN = 21;
const PITCH_MAX = 108;
const TOTAL_PITCHES = PITCH_MAX - PITCH_MIN + 1;

class ColorMapper {
  static pitchToHsl(pitch) {
    const t = Math.max(0, Math.min(1, (pitch - PITCH_MIN) / TOTAL_PITCHES));
    const hue = 240 - (t * 240); // 240 (Blue) to 0 (Red)
    return { h: hue, s: 90, l: 55 };
  }
  static pitchToColor(pitch, velocity = 80, alpha = 1) {
    const hsl = this.pitchToHsl(pitch);
    const l = Math.min(85, hsl.l + (velocity / 127) * 25);
    return `hsla(${hsl.h}, ${hsl.s}%, ${l}%, ${alpha})`;
  }
}

const DynamicEngineV2 = React.forwardRef(({ notes, audioRef, osmdContainerRef, playheadMapRef }, ref) => {
  const bgCanvasRef = useRef(null);
  const fgCanvasRef = useRef(null);
  const rafRef = useRef(null);
  
  const fadeMapRef = useRef(new Map());
  const particlesRef = useRef([]);
  const svgNoteheadsRef = useRef([]);
  const coordsCacheRef = useRef(new Map());

  // Task 2: Extracao de noteheads do SVG
  useEffect(() => {
    if (!osmdContainerRef.current) return;
    
    const updateSvgMap = () => {
      const container = osmdContainerRef.current;
      // .vf-notehead contains path for the note
      const noteheadEls = Array.from(container.querySelectorAll('.vf-notehead path'));
      if (noteheadEls.length === 0) return;
      
      const containerRect = container.getBoundingClientRect();
      const coords = noteheadEls.map(el => {
        const r = el.getBoundingClientRect();
        return {
          x: r.left + r.width / 2 - containerRect.left + container.scrollLeft,
          y: r.top + r.height / 2 - containerRect.top + container.scrollTop
        };
      });
      svgNoteheadsRef.current = coords;
      coordsCacheRef.current.clear(); // Clear cache when layout changes
    };
    
    updateSvgMap();
    
    const observer = new ResizeObserver(updateSvgMap);
    observer.observe(osmdContainerRef.current);
    return () => observer.disconnect();
  }, [osmdContainerRef, notes]);

  // Interpolation for playhead Map to get expected X
  const getExpectedX = (beat) => {
    const map = playheadMapRef?.current;
    if (!map || map.length === 0) return 0;
    
    let prev = null, next = null;
    for (let i = 0; i < map.length; i++) {
      if (map[i].beat <= beat) prev = map[i];
      else { next = map[i]; break; }
    }
    
    if (prev && next) {
      const ratio = (beat - prev.beat) / (next.beat - prev.beat);
      return prev.x + (next.x - prev.x) * ratio;
    } else if (prev) return prev.x;
    return 0;
  };

  // Find exact coordinate of a note (Task 2 Mapping)
  const getNoteCoord = (note) => {
    const key = `${note.pitch}-${note.onset_time}`;
    if (coordsCacheRef.current.has(key)) {
      return coordsCacheRef.current.get(key);
    }
    
    const expectedX = getExpectedX(note.onset_beat);
    const svgNotes = svgNoteheadsRef.current;
    
    if (svgNotes.length === 0) return { x: expectedX + 15, y: 200 }; // Fallback
    
    // Find the minimum distance in X to any notehead
    let minDx = 99999;
    svgNotes.forEach(n => {
       const dx = Math.abs(n.x - expectedX);
       if (dx < minDx) minDx = dx;
    });
    
    // Gather all noteheads that share this minimum X distance (with a small 12px tolerance for chord clusters)
    const candidates = svgNotes.filter(n => Math.abs(n.x - expectedX) <= minDx + 12);
    
    if (candidates.length === 0) return { x: expectedX + 15, y: 200 };
    
    // Sort SVG candidates by Y (ascending -> higher pitch on staff is smaller Y)
    candidates.sort((a, b) => a.y - b.y);
    
    // Find all API notes at exactly this onset_beat to zip
    const siblingNotes = (notes || []).filter(n => Math.abs(n.onset_beat - note.onset_beat) < 0.05);
    // Sort API notes by pitch descending (higher pitch -> smaller Y)
    siblingNotes.sort((a, b) => b.pitch - a.pitch);
    
    // Map them
    siblingNotes.forEach((sib, idx) => {
      const sibKey = `${sib.pitch}-${sib.onset_time}`;
      const matchedSvg = candidates[Math.min(idx, candidates.length - 1)]; // zip or fallback to last
      // Apply a -3px vertical offset to perfectly center the glow over the notehead visual center
      coordsCacheRef.current.set(sibKey, { x: matchedSvg.x, y: matchedSvg.y - 3 });
    });
    
    return coordsCacheRef.current.get(key) || { x: expectedX + 15, y: 200 };
  };

  // Main Render Loop (Task 3, 4, 5)
  const smoothedHueRef = useRef(220);
  
  useEffect(() => {
    const bgCanvas = bgCanvasRef.current;
    const fgCanvas = fgCanvasRef.current;
    if (!bgCanvas || !fgCanvas || !osmdContainerRef.current) return;
    
    const bgCtx = bgCanvas.getContext('2d', { alpha: false }); // Optimization for bg
    const fgCtx = fgCanvas.getContext('2d');
    
    let dpr = window.devicePixelRatio || 1;
    
    const resize = () => {
      const container = bgCanvas.parentElement;
      const w = container.clientWidth;
      const h = container.clientHeight;
      
      dpr = window.devicePixelRatio || 1;
      
      bgCanvas.width = w * dpr;
      bgCanvas.height = h * dpr;
      bgCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
      
      fgCanvas.width = w * dpr;
      fgCanvas.height = h * dpr;
      fgCtx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    
    resize();
    window.addEventListener('resize', resize);

    let lastTime = performance.now();
    
    const render = () => {
      const now = performance.now();
      const dt = Math.min(now - lastTime, 50); // cap dt
      lastTime = now;
      
      const currentTime = audioRef.current ? audioRef.current.currentTime : 0;
      const w = bgCanvas.clientWidth / dpr;
      const h = bgCanvas.clientHeight / dpr;
      
      // Get scroll offsets of OSMD
      const scrollX = osmdContainerRef.current.scrollLeft;
      const scrollY = osmdContainerRef.current.scrollTop;
      
      // Active Notes
      const activeNotes = (notes || []).filter(
        n => currentTime >= n.onset_time && currentTime <= n.offset_time
      );
      
      // LAYER 0: Background
      let targetHue = smoothedHueRef.current;
      let avgVel = 0, bgIntensity = 0;
      if (activeNotes.length > 0) {
        let sumH = 0, sumV = 0;
        activeNotes.forEach(n => {
          sumH += ColorMapper.pitchToHsl(n.pitch).h;
          sumV += n.velocity;
        });
        targetHue = sumH / activeNotes.length;
        avgVel = sumV / activeNotes.length;
        bgIntensity = avgVel / 127;
      }
      
      // Smooth hue transition (lerp over time)
      smoothedHueRef.current += (targetHue - smoothedHueRef.current) * (dt * 0.001); // 1.0 per second
      const avgHue = smoothedHueRef.current;
      
      const pulse = 0.5 + bgIntensity * 0.5 + Math.sin(now / 400) * 0.1;
      const gradient = bgCtx.createRadialGradient(
        (w * dpr) / 2, (h * dpr) / 2, 0,
        (w * dpr) / 2, (h * dpr) / 2, Math.max(w * dpr, h * dpr) * 0.75
      );
      gradient.addColorStop(0, `hsla(${avgHue}, 85%, ${12 + bgIntensity * 18}%, 1)`);
      gradient.addColorStop(0.5, `hsla(${avgHue}, 75%, ${5 + bgIntensity * 8}%, 1)`);
      gradient.addColorStop(1, '#020206');
      bgCtx.fillStyle = gradient;
      bgCtx.fillRect(0, 0, w * dpr, h * dpr);
      
      // LAYER 2: Glow and Particles
      fgCtx.clearRect(0, 0, w * dpr, h * dpr); // Clear fg every frame
      fgCtx.save();
      fgCtx.scale(dpr, dpr);
      fgCtx.translate(-scrollX, -scrollY); // Pan the canvas drawing with the OSMD scroll!
      fgCtx.globalCompositeOperation = 'screen';
      
      // Track fades (when notes turn off)
      activeNotes.forEach(n => {
        const key = `${n.pitch}-${n.onset_time}`;
        if (!fadeMapRef.current.has(key)) {
          fadeMapRef.current.set(key, { start: now, note: n, glowIntensity: 1.0 });
          
          // Spawn Particles on Note On
          const coord = getNoteCoord(n);
          const color = ColorMapper.pitchToColor(n.pitch, Math.min(127, n.velocity * 1.2), 1);
          for(let i = 0; i < 4; i++) {
            particlesRef.current.push({
              x: coord.x + (Math.random() - 0.5) * 12,
              y: coord.y + (Math.random() - 0.5) * 12,
              vx: (Math.random() - 0.5) * 1.0,
              vy: -Math.random() * 2.0 - 0.5,
              life: 1.0,
              decay: 0.015 + Math.random() * 0.02,
              color: color,
              size: 1.5 + Math.random() * 2.5
            });
          }
        } else {
          const state = fadeMapRef.current.get(key);
          state.glowIntensity = 1.0; // keep fully bright while active
        }
      });
      
      // Draw Fades and Active Glows
      for (const [key, state] of fadeMapRef.current.entries()) {
        const stillActive = activeNotes.some(n => `${n.pitch}-${n.onset_time}` === key);
        if (!stillActive) {
          state.glowIntensity *= 0.94; // Slower fade out
          if (state.glowIntensity < 0.02) {
            fadeMapRef.current.delete(key);
            continue;
          }
        }
        
        const coord = getNoteCoord(state.note);
        const mappedColor = ColorMapper.pitchToColor(state.note.pitch, state.note.velocity, state.glowIntensity * 0.4);
        const coreColor = `rgba(255, 255, 255, ${state.glowIntensity * 0.7})`;
        
        // Soft Glow (no hard edge)
        const rad = 15 + (state.note.velocity * 0.08); // smaller, softer glow radius
        const grad = fgCtx.createRadialGradient(coord.x, coord.y, 0, coord.x, coord.y, rad);
        grad.addColorStop(0, mappedColor); // center
        grad.addColorStop(1, 'transparent'); // edge
        fgCtx.beginPath();
        fgCtx.arc(coord.x, coord.y, rad, 0, Math.PI * 2);
        fgCtx.fillStyle = grad;
        fgCtx.fill();
        
        // Core Note (Oval)
        fgCtx.beginPath();
        // Shift slightly left and up to perfectly center on the notehead visual center
        fgCtx.ellipse(coord.x - 2, coord.y - 1, 5.5, 4.0, -Math.PI / 7, 0, Math.PI * 2);
        fgCtx.fillStyle = coreColor;
        fgCtx.shadowBlur = 8;
        fgCtx.shadowColor = mappedColor;
        fgCtx.fill();
      }
      
      // Draw Particles
      for (let i = particlesRef.current.length - 1; i >= 0; i--) {
        const p = particlesRef.current[i];
        p.x += p.vx;
        p.y += p.vy;
        p.life -= p.decay;
        if (p.life <= 0) {
          particlesRef.current.splice(i, 1);
          continue;
        }
        
        fgCtx.beginPath();
        fgCtx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2);
        fgCtx.shadowBlur = 12;
        fgCtx.fillStyle = '#ffffff'; // Mock core color
        fgCtx.shadowBlur = 10;
        fgCtx.shadowColor = p.color;
        fgCtx.globalAlpha = p.life;
        fgCtx.fill();
      }
      fgCtx.globalAlpha = 1.0;
      
      fgCtx.restore();
      
      rafRef.current = requestAnimationFrame(render);
    };
    
    rafRef.current = requestAnimationFrame(render);
    
    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current);
      window.removeEventListener('resize', resize);
    };
  }, [notes, osmdContainerRef, playheadMapRef]);

  // Export drawing function for Video Recorder
  React.useImperativeHandle(ref, () => ({
    drawBackground: (ctx, w, h) => {
      const avgHue = smoothedHueRef.current;
      const gradient = ctx.createRadialGradient(
        w / 2, h / 2, 0,
        w / 2, h / 2, Math.max(w, h) * 0.75
      );
      gradient.addColorStop(0, `hsla(${avgHue}, 85%, 15%, 1)`);
      gradient.addColorStop(0.5, `hsla(${avgHue}, 75%, 8%, 1)`);
      gradient.addColorStop(1, '#020206');
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, w, h);
    },
    drawOverlay: (ctx) => {
      // Draw Fades and Active Glows directly in absolute world space
      for (const [key, state] of fadeMapRef.current.entries()) {
        const coord = getNoteCoord(state.note);
        const mappedColor = ColorMapper.pitchToColor(state.note.pitch, state.note.velocity, state.glowIntensity * 0.4);
        const coreColor = `rgba(255, 255, 255, ${state.glowIntensity * 0.7})`;
        
        // Soft Glow
        const rad = 15 + (state.note.velocity * 0.08); 
        const grad = ctx.createRadialGradient(coord.x, coord.y, 0, coord.x, coord.y, rad);
        grad.addColorStop(0, mappedColor);
        grad.addColorStop(1, 'transparent');
        ctx.beginPath();
        ctx.arc(coord.x, coord.y, rad, 0, Math.PI * 2);
        ctx.fillStyle = grad;
        ctx.fill();
        
        // Core Note
        ctx.beginPath();
        ctx.ellipse(coord.x - 2, coord.y - 1, 5.5, 4.0, -Math.PI / 7, 0, Math.PI * 2);
        ctx.fillStyle = coreColor;
        ctx.shadowBlur = 8;
        ctx.shadowColor = mappedColor;
        ctx.fill();
      }
      
      // Draw Particles
      for (let i = particlesRef.current.length - 1; i >= 0; i--) {
        const p = particlesRef.current[i];
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size * p.life, 0, Math.PI * 2);
        ctx.fillStyle = '#ffffff';
        ctx.shadowBlur = 10;
        ctx.shadowColor = p.color;
        ctx.globalAlpha = p.life;
        ctx.fill();
      }
      ctx.globalAlpha = 1.0;
    }
  }));
  
  return (
    <>
      <canvas id="v2-layer-0" ref={bgCanvasRef} style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 0, pointerEvents: 'none', borderRadius: '12px' }} />
      <canvas id="v2-layer-2" ref={fgCanvasRef} style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: '100%', zIndex: 2, pointerEvents: 'none', borderRadius: '12px' }} />
    </>
  );
});

export default DynamicEngineV2;
