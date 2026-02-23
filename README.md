<div align="center">
  <img src="preview.png" alt="AudioLens Preview" width="100%" />

  # AudioLens 🎧📖
  
  **A dyslexia-friendly web app that uses local AI to perfectly sync audiobooks with PDFs.**
</div>

---

## 🌟 Overview

**AudioLens** is an offline-first, privacy-focused reading companion designed to make reading more accessible. It seamlessly pairs your PDFs with audiobooks, utilizing OpenAI's Whisper model (running entirely on your machine) to generate word-level timestamps. The result is a synchronized, highlighted reading experience where the text glows block-by-block exactly as it is spoken.

## ✨ Features

- **🧠 Local AI Transcription:** Uses the Whisper `tiny` model to transcribe and accurately timestamp audiobooks completely offline. No API keys required, no data leaves your machine.
- **🎯 Word-for-Word Sync:** Enjoy precise, 60fps caption-style highlighting synced directly to the audio track.
- **📚 Smart Dashboard & Resume:** Automatically saves your uploaded books and playback progress to your browser (`localStorage`), resuming exactly where you left off.
- **🛠️ Adjustable Split-View:** A premium, glassmorphism dark-mode UI with a draggable divider lets you resize the PDF and transcript panels exactly to your liking.
- **🛡️ Auto-Repairing PDFs:** Built-in PDF structural repair (`pikepdf`) ensures that damaged or malformed PDFs load perfectly in the browser.
- **⚡ Content-Addressable Storage:** Smart file deduplication prevents wasting disk space if you upload the large audiobooks multiple times.

## 🚀 Installation

Ensure you have Python 3.8+ and `ffmpeg` installed on your system.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/audiolens.git
   cd audiolens
   ```

2. **Run the setup script:**
   The included `run.sh` script will automatically create a virtual environment, install the necessary dependencies, and start the local server.
   ```bash
   ./run.sh
   ```

3. **Open the App:**
   Navigate to `http://127.0.0.1:8000` in your web browser.

## 📁 Project Structure

- `backend/main.py`: The FastAPI server handling uploads, Whisper transcription, background caching, and PDF repairing.
- `frontend/`: Contains the vanilla HTML, CSS, and JS powering the premium split-view interface.
- `uploads/`: Where your files, transcript caches (`.json`), and database (`jobs.json`) are stored locally.

## 🤝 Contributing
Contributions, issues, and feature requests are welcome!

## 📝 License
This project is open-source and available under the MIT License.
