import os
import uuid
import traceback
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

# Import processing functions
from transcriber import transcribe_audio_to_raw_midi, quantize_and_export, post_process_and_save_midi

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
        
    except Exception as e:
        print(f"Error executing task {task_id}: {e}")
        traceback.print_exc()
        TASKS[task_id]["status"] = "FAILED"
        TASKS[task_id]["progress"] = 100
        TASKS[task_id]["message"] = f"Erro no processamento: {str(e)}"
    finally:
        # Clean up uploaded raw audio file to save disk space
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception as ex:
                print(f"Could not delete temp audio file {audio_path}: {ex}")

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
    else:
        raise HTTPException(status_code=400, detail="Formato de download inválido. Use 'xml', 'midi' ou 'raw_midi'.")
        
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Arquivo solicitado não foi gerado ou expirou.")
        
    return FileResponse(path=filepath, filename=filename, media_type=media_type)

@app.get("/api/debug-tasks")
async def list_tasks():
    return TASKS
