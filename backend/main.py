import os
import gc
import json
import uuid
import glob
import hashlib
import shutil
import subprocess
import tempfile
import pikepdf
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import whisper

app = FastAPI()

UPLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

JOBS_FILE = os.path.join(UPLOAD_DIR, "jobs.json")

# Duration (in seconds) of each chunk for split-and-transcribe.
CHUNK_DURATION = 1800  # 30 minutes

def load_jobs():
    if os.path.exists(JOBS_FILE):
        try:
            with open(JOBS_FILE) as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading jobs: {e}")
    return {}

def save_jobs():
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f)

jobs = load_jobs()

print("Loading Whisper model...")
model = whisper.load_model("tiny")
print("Model loaded.")


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
        print(f"[{job_id}] Splitting audio into {CHUNK_DURATION}s chunks …")
        chunks = _split_audio(audio_path, chunk_dir, CHUNK_DURATION)
        print(f"[{job_id}] Created {len(chunks)} chunk(s).")

        all_segments = []
        seg_id = 0
        time_offset = 0.0

        for i, chunk_path in enumerate(chunks):
            print(f"[{job_id}] Transcribing chunk {i + 1}/{len(chunks)} …")
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

            print(f"[{job_id}] Chunk {i + 1} done – {len(all_segments)} segment(s) so far.")

        return {"segments": all_segments}
    finally:
        # Clean up chunk files
        shutil.rmtree(chunk_dir, ignore_errors=True)


def transcribe_audio_task(job_id, audio_path, audio_hash):
    try:
        cache = os.path.join(UPLOAD_DIR, f"{audio_hash}.json")
        if os.path.exists(cache):
            print(f"[{job_id}] Cache hit — loading transcript from {cache}")
            with open(cache) as f:
                result = json.load(f)
            segments = result.get("segments", [])
            print(f"[{job_id}] Loaded {len(segments)} segment(s) from cache.")
        else:
            duration = _get_audio_duration(audio_path)
            print(f"[{job_id}] Audio duration: {duration:.0f}s" if duration else f"[{job_id}] Could not determine duration")

            if duration and duration > CHUNK_DURATION:
                # Long file → chunked transcription to avoid OOM
                print(f"[{job_id}] File is long ({duration:.0f}s), using chunked transcription")
                result = _transcribe_chunked(job_id, audio_path, audio_hash)
            else:
                print(f"[{job_id}] Starting Whisper transcription of: {audio_path}")
                result = model.transcribe(audio_path, word_timestamps=True, fp16=False)

            segments = result.get("segments", [])
            print(f"[{job_id}] Transcription complete. {len(segments)} segment(s) produced.")
            with open(cache, "w") as f:
                json.dump(result, f)
            print(f"[{job_id}] Transcript saved to cache: {cache}")

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["result"] = {"segments": segments}
        save_jobs()
        print(f"[{job_id}] Job marked as completed.")
    except Exception as e:
        print(f"[{job_id}] Error during transcription: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        save_jobs()


@app.get("/", response_class=FileResponse)
async def index():
    return os.path.join(FRONTEND_DIR, "index.html")


def _stream_to_temp(upload_file, dest_dir):
    """Stream an UploadFile to a temp file on disk, computing MD5 along the way.
    Returns (tmp_path, hex_digest, total_bytes). Raises HTTPException if empty."""
    md5 = hashlib.md5()
    total = 0
    tmp_fd, tmp_path = tempfile.mkstemp(dir=dest_dir)
    try:
        with os.fdopen(tmp_fd, "wb") as out:
            while True:
                chunk = upload_file.file.read(1024 * 1024)  # 1 MiB chunks
                if not chunk:
                    break
                out.write(chunk)
                md5.update(chunk)
                total += len(chunk)
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

    # Stream files to disk in chunks — never hold full file in RAM
    pdf_tmp, pdf_hash, pdf_size = _stream_to_temp(pdf_file, UPLOAD_DIR)
    audio_tmp, audio_hash, audio_size = _stream_to_temp(audio_file, UPLOAD_DIR)

    pdf_ext = os.path.splitext(pdf_file.filename)[1] or ".pdf"
    audio_ext = os.path.splitext(audio_file.filename)[1] or ".mp3"

    pdf_name = f"{pdf_hash}{pdf_ext}"
    audio_name = f"{audio_hash}{audio_ext}"
    pdf_path = os.path.join(UPLOAD_DIR, pdf_name)
    audio_path = os.path.join(UPLOAD_DIR, audio_name)

    # Move temp files to final location (skip if duplicate already exists)
    if os.path.exists(pdf_path):
        os.unlink(pdf_tmp)
        print(f"[{job_id}] PDF already on disk, skipping write")
    else:
        shutil.move(pdf_tmp, pdf_path)
        # Repair PDF structure (fixes missing endobj, broken xref, etc.)
        try:
            pdf = pikepdf.open(pdf_path, allow_overwriting_input=True)
            pdf.save(pdf_path, linearize=True)
            pdf.close()
            print(f"[{job_id}] Saved & repaired PDF: {os.path.getsize(pdf_path)} bytes")
        except Exception as e:
            print(f"[{job_id}] PDF repair failed ({e}), serving original")

    if os.path.exists(audio_path):
        os.unlink(audio_tmp)
        print(f"[{job_id}] Audio already on disk, skipping write")
    else:
        shutil.move(audio_tmp, audio_path)
        print(f"[{job_id}] Saved Audio: {audio_size} bytes")

    jobs[job_id] = {
        "id": job_id,
        "status": "processing",
        "pdf_url": f"/uploads/{pdf_name}",
        "audio_url": f"/uploads/{audio_name}",
        "title": os.path.splitext(audio_file.filename)[0],
        "result": None,
    }
    save_jobs()

    # Check transcript cache
    cache = os.path.join(UPLOAD_DIR, f"{audio_hash}.json")
    if os.path.exists(cache):
        print(f"[{job_id}] Cached transcript found.")
        with open(cache) as f:
            jobs[job_id]["result"] = json.load(f)
        jobs[job_id]["status"] = "completed"
    else:
        print(f"[{job_id}] Queuing background transcription task for audio: {audio_name}")
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
