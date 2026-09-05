import os
import uuid
import traceback
import imageio_ffmpeg

# Auto-configure static FFmpeg path on Windows for audioread / librosa
os.environ["PATH"] += os.pathsep + os.path.dirname(imageio_ffmpeg.get_ffmpeg_exe())
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Import processing functions
from transcriber import transcribe_audio_to_raw_midi, quantize_and_export, post_process_and_save_midi
from blender_sheet_music import render_blender_sheet_music_task
from native_sheet_renderer import render_ethereal_score_video


app = FastAPI(title="Pianofy API", version="1.0.0")

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Adjust for production as needed
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Setup directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")

os.makedirs(UPLOADS_DIR, exist_ok=True)
os.makedirs(OUTPUTS_DIR, exist_ok=True)

# In-memory task status storage
TASKS = {}

class RequantizeRequest(BaseModel):
    quantize_grid: float
    time_signature: Optional[str] = None
    bpm: Optional[float] = None
    split_point: Optional[int] = None
    confidence_threshold: Optional[float] = None
    min_duration_ms: Optional[float] = None
    filter_slips: Optional[bool] = None
    allow_triplets: Optional[bool] = None

def run_transcription_task(
    task_id: str,
    audio_path: str,
    confidence_threshold: float,
    min_duration_ms: float,
    quantize_grid: float,
    auto_calibrate: bool = False,
    time_signature: str = "auto",
    bpm: str = "auto",
    split_point: int = 60,
    filter_slips: bool = True,
    allow_triplets: bool = False
):
    try:
        TASKS[task_id]["status"] = "PROCESSING"
        TASKS[task_id]["progress"] = 25
        TASKS[task_id]["message"] = "Carregando áudio e rodando IA de transcrição..."
        
        raw_midi_path = os.path.join(OUTPUTS_DIR, f"{task_id}_raw.mid")
        output_dict_path = os.path.join(OUTPUTS_DIR, f"{task_id}_output_dict.npz")
        output_xml_path = os.path.join(OUTPUTS_DIR, f"{task_id}.musicxml")
        output_midi_path = os.path.join(OUTPUTS_DIR, f"{task_id}.mid")
        
        # Step 1: Heavy Neural Network Inference with Auto-BPM/Meter & Activation Saving
        opt_conf, opt_min_dur, resolved_bpm, resolved_meter, audio_duration = transcribe_audio_to_raw_midi(
            audio_path=audio_path,
            raw_midi_path=raw_midi_path,
            output_dict_path=output_dict_path,
            confidence_threshold=confidence_threshold,
            min_duration_ms=min_duration_ms,
            auto_calibrate=auto_calibrate,
            time_signature=time_signature,
            bpm=bpm,
            filter_slips=filter_slips
        )
        
        TASKS[task_id]["progress"] = 75
        TASKS[task_id]["message"] = "IA concluída. Quantizando partitura..."
        
        # Step 2: Music21 Quantization & Export
        quantize_and_export(
            raw_midi_path=raw_midi_path,
            output_xml_path=output_xml_path,
            output_midi_path=output_midi_path,
            quantize_grid=quantize_grid,
            time_signature=resolved_meter,
            bpm=resolved_bpm,
            split_point=split_point,
            allow_triplets=allow_triplets
        )
        
        TASKS[task_id]["status"] = "SUCCESS"
        TASKS[task_id]["progress"] = 100
        TASKS[task_id]["message"] = "Processamento concluído com sucesso!"
        TASKS[task_id]["quantize_grid"] = quantize_grid
        TASKS[task_id]["confidence_threshold"] = opt_conf
        TASKS[task_id]["min_duration_ms"] = opt_min_dur
        TASKS[task_id]["auto_calibrate"] = auto_calibrate
        TASKS[task_id]["time_signature"] = resolved_meter
        TASKS[task_id]["bpm"] = resolved_bpm
        TASKS[task_id]["split_point"] = split_point
        TASKS[task_id]["filter_slips"] = filter_slips
        TASKS[task_id]["allow_triplets"] = allow_triplets
        TASKS[task_id]["audio_duration"] = audio_duration
        TASKS[task_id]["audio_path"] = audio_path
        
    except Exception as e:
        print(f"Error executing task {task_id}: {e}")
        traceback.print_exc()
        TASKS[task_id]["status"] = "FAILED"
        TASKS[task_id]["progress"] = 100
        TASKS[task_id]["message"] = f"Erro no processamento: {str(e)}"
    finally:
        pass

@app.post("/api/transcribe")
async def transcribe(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    confidence_threshold: float = Form(0.45),
    min_duration_ms: float = Form(30.0),
    quantize_grid: float = Form(0.25),
    auto_calibrate: bool = Form(True),
    time_signature: str = Form("auto"),
    bpm: str = Form("auto"),
    split_point: int = Form(60),
    filter_slips: bool = Form(True),
    allow_triplets: bool = Form(False)
):
    # Validate file extension
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".mp3", ".wav", ".m4a", ".flac", ".ogg"]:
        raise HTTPException(status_code=400, detail="Formato de áudio não suportado. Use MP3, WAV, M4A, FLAC ou OGG.")
        
    task_id = str(uuid.uuid4())
    audio_path = os.path.join(UPLOADS_DIR, f"{task_id}{ext}")
    
    # Save upload file
    with open(audio_path, "wb") as buffer:
        buffer.write(await file.read())
        
    # Set initial task state
    TASKS[task_id] = {
        "status": "PENDING",
        "progress": 0,
        "message": "Upload recebido. Aguardando processamento...",
        "filename": file.filename,
        "quantize_grid": quantize_grid,
        "confidence_threshold": confidence_threshold,
        "min_duration_ms": min_duration_ms,
        "auto_calibrate": auto_calibrate,
        "time_signature": time_signature,
        "bpm": bpm,
        "split_point": split_point,
        "filter_slips": filter_slips,
        "allow_triplets": allow_triplets
    }
    
    # Start asynchronous background worker
    background_tasks.add_task(
        run_transcription_task,
        task_id=task_id,
        audio_path=audio_path,
        confidence_threshold=confidence_threshold,
        min_duration_ms=min_duration_ms,
        quantize_grid=quantize_grid,
        auto_calibrate=auto_calibrate,
        time_signature=time_signature,
        bpm=bpm,
        split_point=split_point,
        filter_slips=filter_slips,
        allow_triplets=allow_triplets
    )
    
    return {"task_id": task_id, "status": "PENDING"}

@app.get("/api/tasks/{task_id}")
async def get_task_status(task_id: str):
    if task_id not in TASKS:
        xml_path = os.path.join(OUTPUTS_DIR, f"{task_id}.musicxml")
        if os.path.exists(xml_path):
            TASKS[task_id] = {
                "status": "SUCCESS",
                "progress": 100,
                "message": "Processamento concluído com sucesso!",
                "filename": f"{task_id}.mid"
            }
        else:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return TASKS[task_id]

@app.post("/api/requantize/{task_id}")
async def requantize_task(task_id: str, req: RequantizeRequest):
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        
    task = TASKS[task_id]
    if task["status"] != "SUCCESS":
        raise HTTPException(status_code=400, detail="Tarefa não está concluída. Não é possível requantizar.")
        
    raw_midi_path = os.path.join(OUTPUTS_DIR, f"{task_id}_raw.mid")
    output_dict_path = os.path.join(OUTPUTS_DIR, f"{task_id}_output_dict.npz")
    
    # Check if a custom BPM is requested manually
    try:
        output_dict_path = os.path.join(OUTPUTS_DIR, f"{task_id}_output_dict.npz")
        raw_midi_path = os.path.join(OUTPUTS_DIR, f"{task_id}_raw.mid")
        output_xml_path = os.path.join(OUTPUTS_DIR, f"{task_id}.musicxml")
        output_midi_path = os.path.join(OUTPUTS_DIR, f"{task_id}.mid")
        
        # If user changed BPM mode to auto, we read the original estimated meter & BPM
        resolved_bpm = req.bpm if req.bpm is not None else task.get("bpm", 120.0)
        time_sig = req.time_signature if req.time_signature else task.get("time_signature", "4/4")
        split_point = req.split_point if req.split_point is not None else task.get("split_point", 60)
        
        conf = req.confidence_threshold if req.confidence_threshold is not None else task.get("confidence_threshold", 0.45)
        min_dur = req.min_duration_ms if req.min_duration_ms is not None else task.get("min_duration_ms", 30.0)
        
        f_slips = req.filter_slips if req.filter_slips is not None else task.get("filter_slips", True)
        a_triplets = req.allow_triplets if req.allow_triplets is not None else task.get("allow_triplets", False)
        
        # If output_dict_path exists, run fast re-thresholding in memory
        if os.path.exists(output_dict_path):
            post_process_and_save_midi(
                output_dict_path=output_dict_path,
                raw_midi_path=raw_midi_path,
                confidence_threshold=conf,
                min_duration_ms=min_dur,
                bpm=resolved_bpm,
                filter_slips=f_slips,
                allow_triplets=a_triplets,
                time_signature=time_sig
            )
        elif not os.path.exists(raw_midi_path):
            raise HTTPException(status_code=404, detail="Dados MIDI originais indisponíveis para requantização.")
            
        # Re-run only quantization & export (extremely fast)
        quantize_and_export(
            raw_midi_path=raw_midi_path,
            output_xml_path=output_xml_path,
            output_midi_path=output_midi_path,
            quantize_grid=req.quantize_grid,
            time_signature=time_sig,
            bpm=resolved_bpm,
            split_point=split_point,
            allow_triplets=a_triplets
        )
        # Update current task config
        task["quantize_grid"] = req.quantize_grid
        task["time_signature"] = time_sig
        task["bpm"] = resolved_bpm
        task["split_point"] = split_point
        task["confidence_threshold"] = conf
        task["min_duration_ms"] = min_dur
        task["filter_slips"] = f_slips
        task["allow_triplets"] = a_triplets
        task["message"] = f"Requantizado com sucesso!"
        return task
    except Exception as e:
        print(f"Error in requantize: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro na requantização: {str(e)}")

@app.get("/api/tasks/{task_id}/notes")
async def get_task_notes(task_id: str):
    if task_id not in TASKS:
        xml_path = os.path.join(OUTPUTS_DIR, f"{task_id}.musicxml")
        mid_path = os.path.join(OUTPUTS_DIR, f"{task_id}.mid")
        if os.path.exists(xml_path) and os.path.exists(mid_path):
            import mido
            try:
                mid = mido.MidiFile(mid_path)
                dur = mid.length
            except Exception:
                dur = 30.0
            TASKS[task_id] = {
                "status": "SUCCESS",
                "progress": 100,
                "bpm": 120.0,
                "time_signature": "4/4",
                "message": "Carregado do disco",
                "quantize_grid": 0.25,
                "split_point": 60,
                "confidence_threshold": 0.45,
                "min_duration_ms": 30.0,
                "filter_slips": True,
                "allow_triplets": False,
                "audio_duration": dur
            }
        else:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        
    midi_path = os.path.join(OUTPUTS_DIR, f"{task_id}.mid")
    if not os.path.exists(midi_path):
        raise HTTPException(status_code=404, detail="MIDI quantizado não encontrado.")
        
    try:
        import music21
        score = music21.converter.parse(midi_path)
        notes = []
        
        flat_notes = score.flatten().notes
        for element in flat_notes:
            onset_beat = float(element.offset)
            duration_beat = float(element.duration.quarterLength)
            
            if isinstance(element, music21.chord.Chord):
                for pitch_obj in element.pitches:
                    notes.append({
                        "pitch": int(pitch_obj.midi),
                        "onset_beat": onset_beat,
                        "duration_beat": duration_beat,
                        "velocity": int(element.volume.velocity or 80)
                    })
            else:
                notes.append({
                    "pitch": int(element.pitch.midi),
                    "onset_beat": onset_beat,
                    "duration_beat": duration_beat,
                    "velocity": int(element.volume.velocity or 80)
                })
                
        notes = sorted(notes, key=lambda x: x['onset_beat'])
        
        task = TASKS[task_id]
        return {
            "notes": notes,
            "bpm": task.get("bpm", 120.0),
            "time_signature": task.get("time_signature", "4/4")
        }
    except Exception as e:
        print(f"Error extracting notes from MIDI: {e}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Erro ao extrair notas do MIDI: {str(e)}")

def run_blender_render_task(task_id: str, notes_res: dict):
    try:
        TASKS[task_id]["blender_render"] = {
            "status": "PROCESSING",
            "progress": 20,
            "message": "Construindo partitura 3D no Blender..."
        }
        
        output_video_path = os.path.join(OUTPUTS_DIR, f"{task_id}_3d_score.mp4")
        output_thumb_path = os.path.join(OUTPUTS_DIR, f"{task_id}_thumb.jpg")
        
        audio_path = ""
        if os.path.exists(UPLOADS_DIR):
            for f in os.listdir(UPLOADS_DIR):
                if f.startswith(task_id):
                    audio_path = os.path.join(UPLOADS_DIR, f)
                    break
                
        notes = notes_res.get("notes", [])
        bpm = notes_res.get("bpm", 120.0)
        time_sig = notes_res.get("time_signature", "4/4")
        audio_dur = TASKS[task_id].get("audio_duration", 30.0)
        
        beat_to_sec = 60.0 / bpm
        converted_notes = []
        for n in notes:
            converted_notes.append({
                "pitch": n["pitch"],
                "onset_time": n["onset_beat"] * beat_to_sec,
                "offset_time": (n["onset_beat"] + n["duration_beat"]) * beat_to_sec,
                "velocity": n.get("velocity", 80)
            })
            
        res = render_blender_sheet_music_task(
            notes=converted_notes,
            audio_duration=audio_dur,
            audio_path=audio_path,
            output_video_path=output_video_path,
            output_thumb_path=output_thumb_path,
            fps=30,
            bpm=bpm,
            time_signature=time_sig
        )
        
        if res["status"] == "ok":
            TASKS[task_id]["blender_render"] = {
                "status": "SUCCESS",
                "progress": 100,
                "message": "Renderização da Partitura 3D no Blender concluída!",
                "video_url": f"/api/download/{task_id}/video"
            }
        else:
            TASKS[task_id]["blender_render"] = {
                "status": "FAILED",
                "progress": 100,
                "message": f"Erro no Blender: {res.get('error', 'Desconhecido')}"
            }
    except Exception as e:
        print(f"Error in blender render task: {e}")
        traceback.print_exc()
        TASKS[task_id]["blender_render"] = {
            "status": "FAILED",
            "progress": 100,
            "message": f"Erro ao renderizar partitura 3D: {str(e)}"
        }

@app.post("/api/render-blender/{task_id}")
async def trigger_blender_render(task_id: str, background_tasks: BackgroundTasks):
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        
    task = TASKS[task_id]
    if task["status"] != "SUCCESS":
        raise HTTPException(status_code=400, detail="A transcrição precisa ser concluída antes de renderizar em 3D.")
        
    notes_res = await get_task_notes(task_id)
    
    task["blender_render"] = {
        "status": "PENDING",
        "progress": 5,
        "message": "Fila de renderização 3D no Blender iniciada..."
    }
    
    background_tasks.add_task(run_blender_render_task, task_id=task_id, notes_res=notes_res)
    
    return {"status": "PENDING", "message": "Renderização 3D iniciada no Blender em segundo plano."}

@app.get("/api/tasks/{task_id}/blender-status")
async def get_blender_status(task_id: str):
    video_path = os.path.join(OUTPUTS_DIR, f"{task_id}_3d_score.mp4")
    if os.path.exists(video_path):
        return {
            "status": "SUCCESS",
            "progress": 100,
            "message": "Partitura 3D no Blender gerada com sucesso!",
            "video_url": f"/api/download/{task_id}/video"
        }
    if task_id not in TASKS:
        xml_path = os.path.join(OUTPUTS_DIR, f"{task_id}.musicxml")
        if os.path.exists(xml_path):
            return {"status": "NOT_STARTED"}
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return TASKS[task_id].get("blender_render", {"status": "NOT_STARTED"})

def run_native_video_render_task(task_id: str):
    try:
        TASKS[task_id]["native_video_render"] = {
            "status": "PROCESSING",
            "progress": 5,
            "message": "Inicializando Motor Nativo de Partitura 2D (Verovio)..."
        }
        
        xml_path = os.path.join(OUTPUTS_DIR, f"{task_id}.musicxml")
        if not os.path.exists(xml_path):
            raise FileNotFoundError(f"MusicXML não encontrado: {xml_path}")
            
        output_video_path = os.path.join(OUTPUTS_DIR, f"{task_id}_native_2d.mp4")
        
        # Look for audio file in task dict, UPLOADS_DIR or project root
        audio_path = TASKS[task_id].get("audio_path", "")
        if not audio_path or not os.path.exists(audio_path):
            if os.path.exists(UPLOADS_DIR):
                for f in os.listdir(UPLOADS_DIR):
                    if f.startswith(task_id):
                        audio_path = os.path.join(UPLOADS_DIR, f)
                        break
        if not audio_path or not os.path.exists(audio_path):
            # Fallback to local audio if testing
            for f in os.listdir("."):
                if f.endswith(".m4a") or f.endswith(".mp3"):
                    audio_path = os.path.abspath(f)
                    break

        def on_render_prog(pct, current_frame, total_frames):
            if task_id in TASKS and "native_video_render" in TASKS[task_id]:
                TASKS[task_id]["native_video_render"]["progress"] = pct
                TASKS[task_id]["native_video_render"]["message"] = f"Renderizando partitura cinemática: {current_frame}/{total_frames} quadros ({pct}%)..."

        res = render_ethereal_score_video(
            musicxml_path=xml_path,
            audio_path=audio_path,
            output_video_path=output_video_path,
            fps=60,
            progress_callback=on_render_prog
        )
        
        if res.get("status") == "ok":
            TASKS[task_id]["native_video_render"] = {
                "status": "SUCCESS",
                "progress": 100,
                "message": f"Partitura Cinemática 2D gerada com sucesso ({res.get('fps_achieved', 60)} FPS)!",
                "video_url": f"/api/download/{task_id}/native_video"
            }
        else:
            TASKS[task_id]["native_video_render"] = {
                "status": "FAILED",
                "progress": 100,
                "message": f"Erro na renderização nativa: {res.get('error', 'Desconhecido')}"
            }
    except Exception as e:
        print(f"Error in native render task: {e}")
        traceback.print_exc()
        TASKS[task_id]["native_video_render"] = {
            "status": "FAILED",
            "progress": 100,
            "message": f"Erro ao renderizar partitura nativa: {str(e)}"
        }

@app.post("/api/render-native-video/{task_id}")
async def trigger_native_video_render(task_id: str, background_tasks: BackgroundTasks):
    if task_id not in TASKS:
        xml_path = os.path.join(OUTPUTS_DIR, f"{task_id}.musicxml")
        if os.path.exists(xml_path):
            TASKS[task_id] = {
                "status": "SUCCESS",
                "progress": 100,
                "message": "Processamento recuperado do disco."
            }
        else:
            raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        
    task = TASKS[task_id]
    if task["status"] != "SUCCESS":
        raise HTTPException(status_code=400, detail="A transcrição precisa ser concluída antes de renderizar o vídeo.")
        
    task["native_video_render"] = {
        "status": "PENDING",
        "progress": 2,
        "message": "Fila de renderização nativa iniciada..."
    }
    
    background_tasks.add_task(run_native_video_render_task, task_id=task_id)
    return {"status": "PENDING", "message": "Renderização nativa iniciada em segundo plano."}

@app.get("/api/tasks/{task_id}/native-video-status")
async def get_native_video_status(task_id: str):
    video_path = os.path.join(OUTPUTS_DIR, f"{task_id}_native_2d.mp4")
    if os.path.exists(video_path):
        return {
            "status": "SUCCESS",
            "progress": 100,
            "message": "Partitura Cinemática 2D gerada com sucesso!",
            "video_url": f"/api/download/{task_id}/native_video"
        }
    if task_id not in TASKS:
        xml_path = os.path.join(OUTPUTS_DIR, f"{task_id}.musicxml")
        if os.path.exists(xml_path):
            return {"status": "NOT_STARTED"}
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
    return TASKS[task_id].get("native_video_render", {"status": "NOT_STARTED"})

@app.get("/api/download/{task_id}/{file_format}")
async def download_file(task_id: str, file_format: str):
    if task_id not in TASKS:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada.")
        
    if file_format == "xml":
        filepath = os.path.join(OUTPUTS_DIR, f"{task_id}.musicxml")
        media_type = "application/vnd.recordare.musicxml+xml"
        filename = f"{task_id}.musicxml"
    elif file_format == "midi":
        filepath = os.path.join(OUTPUTS_DIR, f"{task_id}.mid")
        media_type = "audio/midi"
        filename = f"{task_id}.mid"
    elif file_format == "raw_midi":
        filepath = os.path.join(OUTPUTS_DIR, f"{task_id}_raw.mid")
        media_type = "audio/midi"
        filename = f"{task_id}_raw.mid"
    elif file_format in ("native_video", "cinematic_video"):
        filepath = os.path.join(OUTPUTS_DIR, f"{task_id}_native_2d.mp4")
        media_type = "video/mp4"
        filename = f"{task_id}_cinematic_score.mp4"
    elif file_format in ("video", "3d_video"):
        filepath = os.path.join(OUTPUTS_DIR, f"{task_id}_3d_score.mp4")
        media_type = "video/mp4"
        filename = f"{task_id}_3d_score.mp4"
    elif file_format == "thumb":
        filepath = os.path.join(OUTPUTS_DIR, f"{task_id}_thumb.jpg")
        media_type = "image/jpeg"
        filename = f"{task_id}_thumb.jpg"
    else:
        raise HTTPException(status_code=400, detail="Formato de download inválido.")
        
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Arquivo solicitado não foi gerado ou expirou.")
        
    return FileResponse(path=filepath, filename=filename, media_type=media_type)

@app.get("/api/debug-tasks")
async def list_tasks():
    return TASKS

