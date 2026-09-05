import { useState, useEffect, useRef } from 'react';
import { Video, Loader2, Download, RefreshCw, AlertCircle, Sparkles, Film } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

export default function NativeCinematicRenderView({ taskId }) {
  const [renderState, setRenderState] = useState(null);
  const [isTriggering, setIsTriggering] = useState(false);
  const pollingRef = useRef(null);

  const fetchStatus = async () => {
    if (!taskId) return;
    try {
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}/native-video-status`);
      if (res.ok) {
        const data = await res.json();
        setRenderState(data);
        if (data.status === 'SUCCESS' || data.status === 'FAILED') {
          if (pollingRef.current) clearInterval(pollingRef.current);
        }
      }
    } catch (e) {
      console.error("Error polling native video status:", e);
    }
  };

  useEffect(() => {
    fetchStatus();
  }, [taskId]);

  const handleStartRender = async () => {
    if (!taskId || isTriggering) return;
    setIsTriggering(true);
    try {
      const res = await fetch(`${API_BASE}/api/render-native-video/${taskId}`, { method: 'POST' });
      if (res.ok) {
        setRenderState({ status: 'PENDING', progress: 5, message: 'Iniciando Motor Nativo 2D...' });
        if (pollingRef.current) clearInterval(pollingRef.current);
        pollingRef.current = setInterval(fetchStatus, 1500);
      } else {
        let errDetail = "Erro ao solicitar renderização cinemática.";
        try {
          const errJson = await res.json();
          if (errJson.detail) errDetail = errJson.detail;
        } catch (_) {}
        alert(errDetail);
      }
    } catch (e) {
      console.error(e);
      alert("Falha de comunicação com o servidor.");
    } finally {
      setIsTriggering(false);
    }
  };

  useEffect(() => {
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  const videoUrl = `${API_BASE}/api/download/${taskId}/native_video`;

  return (
    <div style={{
      width: '100%',
      minHeight: '480px',
      background: 'linear-gradient(145deg, #0a0612, #140b20)',
      borderRadius: '16px',
      border: '1px solid rgba(197,160,89,0.3)',
      padding: '28px',
      boxSizing: 'border-box',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: '20px',
      color: '#eae0ce'
    }}>
      {(!renderState || renderState.status === 'NOT_STARTED') && (
        <div style={{ textAlign: 'center', maxWidth: '520px' }}>
          <div style={{
            width: '72px',
            height: '72px',
            borderRadius: '22px',
            background: 'linear-gradient(135deg, rgba(255,180,50,0.25), rgba(168,85,247,0.3))',
            border: '1px solid rgba(255,180,50,0.4)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 16px auto',
            boxShadow: '0 8px 32px rgba(168,85,247,0.25)'
          }}>
            <Film size={36} style={{ color: '#ffd67a' }} />
          </div>
          <h3 style={{
            fontSize: '1.5rem',
            fontWeight: 700,
            margin: '0 0 10px 0',
            background: 'linear-gradient(135deg, #ffffff, #ffd67a)',
            WebkitBackgroundClip: 'text',
            WebkitTextFillColor: 'transparent'
          }}>
            Partitura Cinemática 2D (HD 60 FPS)
          </h3>
          <p style={{ fontSize: '0.92rem', color: 'rgba(234,224,206,0.8)', lineHeight: 1.6, margin: '0 0 24px 0' }}>
            Renderize um vídeo cinematográfico em alta definição com notação clássica gravada (Verovio), efeito de iluminação e aura de Bloom (OpenCV) e partículas etéreas flutuantes, sincronizado ao milissegundo com a sua performance de áudio.
          </p>
          <button
            onClick={handleStartRender}
            disabled={isTriggering}
            style={{
              padding: '14px 32px',
              borderRadius: '12px',
              border: 'none',
              background: 'linear-gradient(135deg, #e6b85c, #a87625)',
              color: '#120a06',
              fontWeight: 700,
              fontSize: '1.05rem',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '10px',
              boxShadow: '0 6px 24px rgba(230,184,92,0.4)',
              transition: 'all 0.2s transform'
            }}
            onMouseOver={(e) => e.currentTarget.style.transform = 'translateY(-2px)'}
            onMouseOut={(e) => e.currentTarget.style.transform = 'translateY(0)'}
          >
            {isTriggering ? <Loader2 size={22} style={{ animation: 'spin 1s linear infinite' }} /> : <Sparkles size={22} />}
            Gerar Vídeo Cinemático HD (60 FPS)
          </button>
        </div>
      )}

      {(renderState?.status === 'PENDING' || renderState?.status === 'PROCESSING') && (
        <div style={{ textAlign: 'center', maxWidth: '460px', width: '100%' }}>
          <div style={{
            width: '64px',
            height: '64px',
            borderRadius: '50%',
            background: 'rgba(255,180,50,0.12)',
            border: '2px solid #e6b85c',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 16px auto',
            boxShadow: '0 0 24px rgba(230,184,92,0.35)'
          }}>
            <Loader2 size={34} style={{ color: '#ffd67a', animation: 'spin 1.2s linear infinite' }} />
          </div>
          <h4 style={{ fontSize: '1.25rem', margin: '0 0 8px 0', color: '#ffffff' }}>
            Renderizando Partitura Cinemática
          </h4>
          <p style={{ fontSize: '0.88rem', color: 'rgba(234,224,206,0.8)', margin: '0 0 16px 0' }}>
            {renderState.message || 'Compondo quadros vetoriais com iluminação e partículas...'}
          </p>
          <div style={{ width: '100%', height: '10px', background: 'rgba(255,255,255,0.08)', borderRadius: '5px', overflow: 'hidden' }}>
            <div style={{
              width: `${Math.max(5, renderState.progress || 10)}%`,
              height: '100%',
              background: 'linear-gradient(90deg, #e6b85c, #c084fc)',
              borderRadius: '5px',
              transition: 'width 0.4s ease'
            }} />
          </div>
          <p style={{ fontSize: '0.8rem', color: 'rgba(234,224,206,0.6)', marginTop: '8px' }}>
            {renderState.progress || 0}% concluído
          </p>
        </div>
      )}

      {renderState?.status === 'FAILED' && (
        <div style={{ textAlign: 'center', maxWidth: '460px' }}>
          <AlertCircle size={52} style={{ color: '#ef4444', marginBottom: '12px' }} />
          <h4 style={{ fontSize: '1.25rem', margin: '0 0 8px 0', color: '#ffffff' }}>Erro na Renderização</h4>
          <p style={{ fontSize: '0.88rem', color: '#fca5a5', margin: '0 0 20px 0' }}>{renderState.message}</p>
          <button
            onClick={handleStartRender}
            style={{
              padding: '12px 24px',
              borderRadius: '10px',
              border: '1px solid rgba(239,68,68,0.4)',
              background: 'rgba(239,68,68,0.18)',
              color: '#ffffff',
              fontWeight: 600,
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px'
            }}
          >
            <RefreshCw size={18} /> Tentar Novamente
          </button>
        </div>
      )}

      {renderState?.status === 'SUCCESS' && (
        <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '20px', alignItems: 'center' }}>
          <div style={{
            width: '100%',
            borderRadius: '14px',
            overflow: 'hidden',
            boxShadow: '0 12px 40px rgba(0,0,0,0.8), 0 0 20px rgba(230,184,92,0.2)',
            background: '#04020a',
            border: '1px solid rgba(230,184,92,0.35)'
          }}>
            <video
              controls
              autoPlay
              style={{ width: '100%', maxHeight: '460px', display: 'block' }}
              src={videoUrl}
            />
          </div>
          <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', justifyContent: 'center' }}>
            <a
              href={videoUrl}
              download={`pianofy_cinematic_${taskId}.mp4`}
              style={{
                padding: '12px 24px',
                borderRadius: '10px',
                background: 'linear-gradient(135deg, #e6b85c, #a87625)',
                color: '#120a06',
                fontWeight: 700,
                textDecoration: 'none',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '0.95rem',
                boxShadow: '0 4px 16px rgba(230,184,92,0.35)'
              }}
            >
              <Download size={18} /> Baixar Vídeo MP4 (1080p 60 FPS)
            </a>
            <button
              onClick={handleStartRender}
              style={{
                padding: '12px 20px',
                borderRadius: '10px',
                border: '1px solid rgba(255,255,255,0.15)',
                background: 'rgba(255,255,255,0.06)',
                color: '#eae0ce',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '0.92rem'
              }}
            >
              <RefreshCw size={18} /> Re-renderizar
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
