import { useState, useEffect, useRef } from 'react';
import { Video, Loader2, Download, RefreshCw, AlertCircle, CheckCircle2, Play, Sparkles } from 'lucide-react';

const API_BASE = import.meta.env.VITE_API_BASE || 'http://localhost:8000';

export default function BlenderRenderView({ taskId }) {
  const [renderState, setRenderState] = useState(null);
  const [isTriggering, setIsTriggering] = useState(false);
  const pollingRef = useRef(null);

  const fetchStatus = async () => {
    if (!taskId) return;
    try {
      const res = await fetch(`${API_BASE}/api/tasks/${taskId}/blender-status`);
      if (res.ok) {
        const data = await res.json();
        setRenderState(data);
        if (data.status === 'SUCCESS' || data.status === 'FAILED') {
          if (pollingRef.current) clearInterval(pollingRef.current);
        }
      }
    } catch (e) {
      console.error("Error polling blender render status:", e);
    }
  };

  useEffect(() => {
    fetchStatus();
  }, [taskId]);

  const handleStartRender = async () => {
    if (!taskId || isTriggering) return;
    setIsTriggering(true);
    try {
      const res = await fetch(`${API_BASE}/api/render-blender/${taskId}`, { method: 'POST' });
      if (res.ok) {
        setRenderState({ status: 'PENDING', progress: 5, message: 'Iniciando renderizador Blender...' });
        if (pollingRef.current) clearInterval(pollingRef.current);
        pollingRef.current = setInterval(fetchStatus, 2500);
      } else {
        alert("Erro ao solicitar renderização em 3D.");
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

  const videoUrl = `${API_BASE}/api/download/${taskId}/video`;

  return (
    <div style={{
      width: '100%',
      minHeight: '480px',
      background: 'linear-gradient(145deg, #0d0914, #150d21)',
      borderRadius: '16px',
      border: '1px solid rgba(197,160,89,0.25)',
      padding: '24px',
      boxSizing: 'border-box',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center',
      gap: '20px',
      color: '#eae0ce'
    }}>
      {(!renderState || renderState.status === 'NOT_STARTED') && (
        <div style={{ textAlign: 'center', maxWidth: '480px' }}>
          <div style={{
            width: '68px',
            height: '68px',
            borderRadius: '20px',
            background: 'linear-gradient(135deg, rgba(197,160,89,0.2), rgba(124,58,237,0.25))',
            border: '1px solid rgba(197,160,89,0.4)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 16px auto',
            boxShadow: '0 8px 24px rgba(124,58,237,0.2)'
          }}>
            <Sparkles size={34} style={{ color: '#ebd08f' }} />
          </div>
          <h3 style={{ fontSize: '1.4rem', fontWeight: 700, margin: '0 0 8px 0', background: 'linear-gradient(135deg, #ffffff, #ebd08f)', WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent' }}>
            Partitura 3D com Blender MCP
          </h3>
          <p style={{ fontSize: '0.9rem', color: 'rgba(234,224,206,0.75)', lineHeight: 1.5, margin: '0 0 20px 0' }}>
            Gere uma animação cinematográfica em 3D da partitura dupla (Clave de Sol e Clave de Fá) com iluminação de estúdio, notas emissivas e câmera rastreadora sincronizada ao tempo da música.
          </p>
          <button
            onClick={handleStartRender}
            disabled={isTriggering}
            style={{
              padding: '14px 28px',
              borderRadius: '12px',
              border: 'none',
              background: 'linear-gradient(135deg, #c5a059, #9e7931)',
              color: '#120a06',
              fontWeight: 700,
              fontSize: '1rem',
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '10px',
              boxShadow: '0 4px 20px rgba(197,160,89,0.4)',
              transition: 'all 0.2s'
            }}
          >
            {isTriggering ? <Loader2 size={20} style={{ animation: 'spin 1s linear infinite' }} /> : <Video size={20} />}
            Renderizar Partitura 3D no Blender
          </button>
        </div>
      )}

      {(renderState?.status === 'PENDING' || renderState?.status === 'PROCESSING') && (
        <div style={{ textAlign: 'center', maxWidth: '420px', width: '100%' }}>
          <div style={{
            width: '60px',
            height: '60px',
            borderRadius: '50%',
            background: 'rgba(197,160,89,0.1)',
            border: '2px solid #c5a059',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            margin: '0 auto 16px auto',
            boxShadow: '0 0 20px rgba(197,160,89,0.3)'
          }}>
            <Loader2 size={32} style={{ color: '#ebd08f', animation: 'spin 1.2s linear infinite' }} />
          </div>
          <h4 style={{ fontSize: '1.2rem', margin: '0 0 8px 0', color: '#ffffff' }}>Renderizando Partitura 3D</h4>
          <p style={{ fontSize: '0.85rem', color: 'rgba(234,224,206,0.7)', margin: '0 0 16px 0' }}>
            {renderState.message || 'Processando cena no Blender via MCP...'}
          </p>
          <div style={{ width: '100%', height: '8px', background: 'rgba(255,255,255,0.08)', borderRadius: '4px', overflow: 'hidden' }}>
            <div style={{
              width: `${renderState.progress || 30}%`,
              height: '100%',
              background: 'linear-gradient(90deg, #c5a059, #a855f7)',
              borderRadius: '4px',
              transition: 'width 0.4s ease'
            }} />
          </div>
        </div>
      )}

      {renderState?.status === 'FAILED' && (
        <div style={{ textAlign: 'center', maxWidth: '440px' }}>
          <AlertCircle size={48} style={{ color: '#ef4444', marginBottom: '12px' }} />
          <h4 style={{ fontSize: '1.2rem', margin: '0 0 8px 0', color: '#ffffff' }}>Erro na Renderização 3D</h4>
          <p style={{ fontSize: '0.85rem', color: '#fca5a5', margin: '0 0 20px 0' }}>{renderState.message}</p>
          <button
            onClick={handleStartRender}
            style={{
              padding: '10px 20px',
              borderRadius: '8px',
              border: '1px solid rgba(239,68,68,0.4)',
              background: 'rgba(239,68,68,0.15)',
              color: '#ffffff',
              fontWeight: 600,
              cursor: 'pointer',
              display: 'inline-flex',
              alignItems: 'center',
              gap: '8px'
            }}
          >
            <RefreshCw size={16} /> Tentar Novamente
          </button>
        </div>
      )}

      {renderState?.status === 'SUCCESS' && (
        <div style={{ width: '100%', display: 'flex', flexDirection: 'column', gap: '16px', alignItems: 'center' }}>
          <div style={{
            width: '100%',
            borderRadius: '12px',
            overflow: 'hidden',
            boxShadow: '0 8px 32px rgba(0,0,0,0.6)',
            background: '#000000',
            border: '1px solid rgba(197,160,89,0.3)'
          }}>
            <video
              controls
              autoPlay
              style={{ width: '100%', maxHeight: '420px', display: 'block' }}
              src={videoUrl}
            />
          </div>
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', justifyContent: 'center' }}>
            <a
              href={videoUrl}
              download={`pianofy_3d_score_${taskId}.mp4`}
              style={{
                padding: '10px 20px',
                borderRadius: '8px',
                background: 'linear-gradient(135deg, #c5a059, #9e7931)',
                color: '#120a06',
                fontWeight: 700,
                textDecoration: 'none',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '0.9rem',
                boxShadow: '0 4px 12px rgba(197,160,89,0.3)'
              }}
            >
              <Download size={16} /> Baixar Vídeo MP4 (1080p)
            </a>
            <button
              onClick={handleStartRender}
              style={{
                padding: '10px 16px',
                borderRadius: '8px',
                border: '1px solid rgba(255,255,255,0.15)',
                background: 'rgba(255,255,255,0.06)',
                color: '#eae0ce',
                fontWeight: 600,
                cursor: 'pointer',
                display: 'inline-flex',
                alignItems: 'center',
                gap: '8px',
                fontSize: '0.9rem'
              }}
            >
              <RefreshCw size={16} /> Re-renderizar 3D
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
