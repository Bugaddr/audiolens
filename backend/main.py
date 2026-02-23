import os
import json
import uuid
import hashlib
import pikepdf
from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
import whisper

app = FastAPI()

UPLOAD_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "uploads"))
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

JOBS_FILE = os.path.join(UPLOAD_DIR, "jobs.json")

def load_jobs():
    if os.path.exists(JOBS_FILE):
        try:
            with open(JOBS_FILE) as f:
                return json.load(f)
        except:
            pass
    return {}

def save_jobs():
    with open(JOBS_FILE, "w") as f:
        json.dump(jobs, f)

jobs = load_jobs()

print("Loading Whisper model...")
model = whisper.load_model("base")
print("Model loaded.")


def transcribe_audio_task(job_id, audio_path, audio_hash):
    try:
        cache = os.path.join(UPLOAD_DIR, f"{audio_hash}.json")
        if os.path.exists(cache):
            with open(cache) as f:
                result = json.load(f)
        else:
            print(f"[{job_id}] Transcribing...")
            result = model.transcribe(audio_path, word_timestamps=True, verbose=True)
            with open(cache, "w") as f:
                json.dump(result, f)
            print(f"[{job_id}] Done.")

        jobs[job_id]["status"] = "completed"
        jobs[job_id]["result"] = result
        save_jobs()
    except Exception as e:
        print(f"[{job_id}] Error: {e}")
        jobs[job_id]["status"] = "error"
        jobs[job_id]["error"] = str(e)
        save_jobs()


@app.get("/", response_class=HTMLResponse)
async def index():
    with open(os.path.join(FRONTEND_DIR, "index.html")) as f:
        return f.read()


@app.post("/upload")
async def upload(background_tasks: BackgroundTasks, pdf_file: UploadFile = File(...), audio_file: UploadFile = File(...)):
    job_id = str(uuid.uuid4())

    # Read both files fully into memory
    pdf_bytes = await pdf_file.read()
    audio_bytes = await audio_file.read()

    if not pdf_bytes:
        raise HTTPException(400, "PDF file is empty")
    if not audio_bytes:
        raise HTTPException(400, "Audio file is empty")

    # Hash-based filenames to avoid duplicates
    pdf_hash = hashlib.md5(pdf_bytes).hexdigest()
    audio_hash = hashlib.md5(audio_bytes).hexdigest()
    pdf_ext = os.path.splitext(pdf_file.filename)[1] or ".pdf"
    audio_ext = os.path.splitext(audio_file.filename)[1] or ".mp3"

    pdf_name = f"{pdf_hash}{pdf_ext}"
    audio_name = f"{audio_hash}{audio_ext}"
    pdf_path = os.path.join(UPLOAD_DIR, pdf_name)
    audio_path = os.path.join(UPLOAD_DIR, audio_name)

    # Only write if not already on disk
    if os.path.exists(pdf_path):
        print(f"[{job_id}] PDF already on disk, skipping write")
    else:
        with open(pdf_path, "wb") as f:
            f.write(pdf_bytes)
        # Repair PDF structure (fixes missing endobj, broken xref, etc.)
        try:
            pdf = pikepdf.open(pdf_path, allow_overwriting_input=True)
            pdf.save(pdf_path, linearize=True)
            pdf.close()
            print(f"[{job_id}] Saved & repaired PDF: {os.path.getsize(pdf_path)} bytes")
        except Exception as e:
            print(f"[{job_id}] PDF repair failed ({e}), serving original")

    if os.path.exists(audio_path):
        print(f"[{job_id}] Audio already on disk, skipping write")
    else:
        with open(audio_path, "wb") as f:
            f.write(audio_bytes)
        print(f"[{job_id}] Saved Audio: {len(audio_bytes)} bytes")

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
