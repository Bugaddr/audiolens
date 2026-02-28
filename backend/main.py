import os
import gc
import json
import uuid
import glob
import hashlib
import logging
import shutil
import subprocess
import tempfile
import pikepdf
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import whisper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("audiolens")

app = FastAPI(title="Audiolens", version="1.0.0")

MAX_PDF_SIZE = 200 * 1024 * 1024   # 200 MB
MAX_AUDIO_SIZE = 500 * 1024 * 1024  # 500 MB

UPLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

JOBS_FILE = os.path.join(UPLOAD_DIR, "jobs.json")

CHUNK_DURATION = 1800  # 30 minutes per chunk

def load_jobs():
    if os.path.exists(JOBS_FILE):
        try:
            with open(JOBS_FILE) as f:
                return json.load(f)
        except Exception as e:
            log.warning("Failed to load jobs file: %s", e)
    return {}

def save_jobs():
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f)

jobs = load_jobs()

log.info("Loading Whisper model…")
model = whisper.load_model("tiny")
log.info("Model loaded.")


def _get_audio_duration(audio_path):
    """Return duration in seconds via ffprobe, or None on error."""
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", audio_path],
            stderr=subprocess.DEVNULL,
        )
        return float(out.strip())
    except Exception:
        return None


def _split_audio(audio_path, chunk_dir, chunk_duration):
    """Split audio_path into chunk_duration-second WAV chunks in chunk_dir.
    Returns a sorted list of chunk file paths."""
    os.makedirs(chunk_dir, exist_ok=True)
    pattern = os.path.join(chunk_dir, "chunk_%04d.wav")
    subprocess.check_call([
        "ffmpeg", "-y", "-i", audio_path,
        "-f", "segment", "-segment_time", str(chunk_duration),
        "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
        pattern,
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chunks = sorted(glob.glob(os.path.join(chunk_dir, "chunk_*.wav")))
    return chunks


def _transcribe_chunked(job_id, audio_path, audio_hash):
    """Split a long audio file into chunks, transcribe each, merge results."""
    chunk_dir = os.path.join(UPLOAD_DIR, f".chunks_{audio_hash}")
    try:
        log.info("[%s] Splitting audio into %ds chunks…", job_id, CHUNK_DURATION)
        chunks = _split_audio(audio_path, chunk_dir, CHUNK_DURATION)
        log.info("[%s] Created %d chunk(s).", job_id, len(chunks))

        all_segments = []
        seg_id = 0
        time_offset = 0.0

        for i, chunk_path in enumerate(chunks):
            log.info("[%s] Transcribing chunk %d/%d…", job_id, i + 1, len(chunks))
            result = model.transcribe(chunk_path, word_timestamps=True, fp16=False)
            for seg in result.get("segments", []):
                seg["start"] += time_offset
                seg["end"] += time_offset
                seg["id"] = seg_id
                # Shift word-level timestamps too
                for w in seg.get("words", []):
                    w["start"] += time_offset
                    w["end"] += time_offset
                seg_id += 1
                all_segments.append(seg)

            # Determine actual chunk length for accurate offset
            chunk_dur = _get_audio_duration(chunk_path)
            if chunk_dur:
                time_offset += chunk_dur
            else:
                time_offset += CHUNK_DURATION

            # Free memory between chunks
            del result
            gc.collect()

            log.info("[%s] Chunk %d done – %d segment(s) so far.", job_id, i + 1, len(all_segments))

        return {"segments": all_segments}
    finally:
        # Clean up chunk files
        shutil.rmtree(chunk_dir, ignore_errors=True)


def transcribe_audio_task(job_id, audio_path, audio_hash):
    try:
        cache = os.path.join(UPLOAD_DIR, f"{audio_hash}.json")
        if os.path.exists(cache):
            log.info("[%s] Cache hit — loading transcript from %s", job_id, cache)
            with open(cache) as f:
                result = json.load(f)
            segments = result.get("segments", [])
            log.info("[%s] Loaded %d segment(s) from cache.", job_id, len(segments))
        else:
            duration = _get_audio_duration(audio_path)
            if duration:
                log.info("[%s] Audio duration: %.0fs", job_id, duration)
            else:
                log.info("[%s] Could not determine audio duration", job_id)

            if duration and duration > CHUNK_DURATION:
                log.info("[%s] Long file (%.0fs), using chunked transcription", job_id, duration)
                result = _transcribe_chunked(job_id, audio_path, audio_hash)
            else:
                log.info("[%s] Starting Whisper transcription of: %s", job_id, audio_path)
                result = model.transcribe(audio_path, word_timestamps=True, fp16=False)

            segments = result.get("segments", [])
            log.info("[%s] Transcription complete. %d segment(s).", job_id, len(segments))
            with open(cache, "w") as f:
                json.dump(result, f)
            log.info("[%s] Transcript cached at %s", job_id, cache)

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["result"] = {"segments": segments}
        save_jobs()
        log.info("[%s] Job completed.", job_id)
    except Exception as e:
        log.error("[%s] Transcription error: %s", job_id, e)
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        save_jobs()


@app.get("/", response_class=FileResponse)
async def index():
    return os.path.join(FRONTEND_DIR, "index.html")


@app.get("/health")
async def health():
    return {"status": "ok"}


def _stream_to_temp(upload_file, dest_dir, max_size):
    """Stream an UploadFile to a temp file on disk, computing MD5 along the way.
    Returns (tmp_path, hex_digest, total_bytes). Raises HTTPException if empty."""
    md5 = hashlib.md5()
    total = 0
    tmp_fd, tmp_path = tempfile.mkstemp(dir=dest_dir)
    try:
        with os.fdopen(tmp_fd, "wb") as out:
            while True:
                chunk = upload_file.file.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                md5.update(chunk)
                total += len(chunk)
                if total > max_size:
                    os.unlink(tmp_path)
                    raise HTTPException(
                        413,
                        f"{upload_file.filename} exceeds {max_size // (1024*1024)} MB limit",
                    )
    except Exception:
        os.unlink(tmp_path)
        raise
    if total == 0:
        os.unlink(tmp_path)
        raise HTTPException(400, f"{upload_file.filename} is empty")
    return tmp_path, md5.hexdigest(), total


@app.post("/upload")
async def upload(background_tasks: BackgroundTasks, pdf_file: UploadFile = File(...), audio_file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())

    # Stream files to disk — never hold full file in RAM
    pdf_tmp, pdf_hash, pdf_size = _stream_to_temp(pdf_file, UPLOAD_DIR, MAX_PDF_SIZE)
    audio_tmp, audio_hash, audio_size = _stream_to_temp(audio_file, UPLOAD_DIR, MAX_AUDIO_SIZE)

    pdf_ext = os.path.splitext(pdf_file.filename)[1] or ".pdf"
    audio_ext = os.path.splitext(audio_file.filename)[1] or ".mp3"

    pdf_name = f"{pdf_hash}{pdf_ext}"
    audio_name = f"{audio_hash}{audio_ext}"
    pdf_path = os.path.join(UPLOAD_DIR, pdf_name)
    audio_path = os.path.join(UPLOAD_DIR, audio_name)

    # Move temp files to final location (skip if duplicate already exists)
    if os.path.exists(pdf_path):
        os.unlink(pdf_tmp)
        log.info("[%s] PDF already on disk, skipping write", job_id)
    else:
        shutil.move(pdf_tmp, pdf_path)
        try:
            pdf = pikepdf.open(pdf_path, allow_overwriting_input=True)
            pdf.save(pdf_path, linearize=True)
            pdf.close()
            log.info("[%s] Saved & repaired PDF: %d bytes", job_id, os.path.getsize(pdf_path))
        except Exception as e:
            log.warning("[%s] PDF repair failed (%s), serving original", job_id, e)

    if os.path.exists(audio_path):
        os.unlink(audio_tmp)
        log.info("[%s] Audio already on disk, skipping write", job_id)
    else:
        shutil.move(audio_tmp, audio_path)
        log.info("[%s] Saved audio: %d bytes", job_id, audio_size)

    jobs[job_id] = {
        "id": job_id,
        "status": "processing",
        "pdf_url": f"/uploads/{pdf_name}",
        "audio_url": f"/uploads/{audio_name}",
        "title": os.path.splitext(audio_file.filename)[0],
        "result": None,
    }
    save_jobs()

    # Check transcript cache — skip background task if already transcribed
    cache = os.path.join(UPLOAD_DIR, f"{audio_hash}.json")
    if os.path.exists(cache):
        log.info("[%s] Cached transcript found.", job_id)
        with open(cache) as f:
            jobs[job_id]["result"] = json.load(f)
        jobs[job_id]["status"] = "completed"
        save_jobs()
    else:
        log.info("[%s] Queuing transcription for %s", job_id, audio_name)
        background_tasks.add_task(transcribe_audio_task, job_id, audio_path, audio_hash)

    return {"job_id": job_id}


@app.get("/status/{job_id}")
async def status(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    job = jobs[job_id]
    if job["status"] == "completed":
        return {"status": "completed", "pdf_url": job["pdf_url"], "audio_url": job["audio_url"], "title": job["title"], "transcript": job["result"]}
    if job["status"] == "error":
        return {"status": "error", "error_msg": job.get("error", "Unknown")}
    return {"status": "processing"}

@app.get("/history")
async def get_history():
    """Returns all completed jobs."""
    completed = []
    for jid, job in jobs.items():
        if job["status"] == "completed":
            completed.append({
                "id": jid,
                "title": job.get("title", "Unknown"),
                "pdf_url": job["pdf_url"],
                "audio_url": job["audio_url"]
            })
    return list(reversed(completed))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
