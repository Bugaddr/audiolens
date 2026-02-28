document.addEventListener('DOMContentLoaded', () => {
    const $ = id => document.getElementById(id);
    const uploadBtn = $('uploadBtn'), statusMsg = $('statusMessage');
    const pdfFrame = $('pdfFrame'), audioPlayer = $('audioPlayer');
    const captionOverlay = $('captionOverlay'), captionText = $('captionText');
    const pdfUpload = $('pdfUpload'), audioUpload = $('audioUpload');
    const pdfLabel = $('pdfLabel'), audioLabel = $('audioLabel');

    let poll = null, segments = [], activeIdx = -1, currentJobId = null;
    let saveThrottle = 0;

    /** Escape HTML to prevent XSS in dynamic content. */
    function esc(str) {
        const el = document.createElement('span');
        el.textContent = str;
        return el.innerHTML;
    }

    // File selection feedback
    pdfUpload.addEventListener('change', () => {
        if (pdfUpload.files[0]) {
            pdfLabel.textContent = pdfUpload.files[0].name;
            $('pdfChip').classList.add('selected');
        }
        checkReady();
    });
    audioUpload.addEventListener('change', () => {
        if (audioUpload.files[0]) {
            audioLabel.textContent = audioUpload.files[0].name;
            $('audioChip').classList.add('selected');
        }
        checkReady();
    });

    function checkReady() {
        uploadBtn.disabled = !(pdfUpload.files[0] && audioUpload.files[0]);
    }

    // Upload
    uploadBtn.addEventListener('click', async () => {
        const pdf = pdfUpload.files[0], audio = audioUpload.files[0];
        if (!pdf || !audio) return;

        const fd = new FormData();
        fd.append('pdf_file', pdf);
        fd.append('audio_file', audio);

        uploadBtn.disabled = true;
        uploadBtn.classList.add('loading');
        setStatus('Uploading…', 'working');

        try {
            const res = await fetch('/upload', { method: 'POST', body: fd });
            if (!res.ok) throw new Error(res.statusText);
            const { job_id } = await res.json();
            setStatus('Transcribing…', 'working');
            startPoll(job_id);
        } catch (err) {
            setStatus('Upload failed — ' + (err.message || 'network error'), 'error');
            uploadBtn.disabled = false;
            uploadBtn.classList.remove('loading');
        }
    });

    function setStatus(text, type) {
        statusMsg.textContent = text;
        statusMsg.className = 'status-msg ' + (type || '');
    }

    function startPoll(id) {
        clearInterval(poll);
        poll = setInterval(async () => {
            const res = await fetch('/status/' + id).catch(() => null);
            if (!res?.ok) return;
            const d = await res.json();
            if (d.status === 'completed') {
                clearInterval(poll);
                setStatus('Ready', 'ready');
                uploadBtn.disabled = false;
                uploadBtn.classList.remove('loading');
                currentJobId = id;
                show(d.pdf_url, d.audio_url, d.transcript, d.title);
            } else if (d.status === 'error') {
                clearInterval(poll);
                setStatus('Error', 'error');
                uploadBtn.disabled = false;
                uploadBtn.classList.remove('loading');
            }
        }, 2000);
    }

    // Show reader
    function show(pdfUrl, audioUrl, transcript, title) {
        $('pdfEmpty')?.remove();

        // Hide landing page
        if ($('landingPage')) {
            $('landingPage').style.display = 'none';
        }
        // Hide top navbar when reading
        if ($('topBar')) {
            $('topBar').style.display = 'none';
        }

        // Show main content + PDF panel
        $('mainContent').style.display = 'flex';
        $('pdfPanel').style.display = 'block';

        // Show panel toggles
        applyPanelVisibility();

        // Append PDF parameters for Odd Spread and Fit
        const paramStr = pdfUrl.includes('#') ? '&' : '#';
        pdfFrame.src = pdfUrl + paramStr + 'pageLayout=TwoPageRight&view=Fit';
        pdfFrame.style.display = 'block';

        audioPlayer.src = audioUrl;
        $('audioBar').style.display = 'block';

        buildcaptions(transcript);
    }

    // Build captions — just store segment data, no DOM elements
    function buildcaptions(data) {
        segments = [];
        activeIdx = -1;
        captionText.textContent = '';
        if (!data?.segments) return;

        for (const seg of data.segments) {
            const hasW = seg.words?.length > 0;
            const text = (hasW ? seg.words.map(w => w.word.trim()).join(' ') : seg.text || '').trim();
            if (!text) continue;

            const start = hasW ? seg.words[0].start : seg.start;
            const end = hasW ? seg.words.at(-1).end : seg.end;
            segments.push({ text, start, end });
        }
    }

    // Load History
    async function loadHistory() {
        try {
            const res = await fetch('/history');
            const history = await res.json();
            const bar = $('historyBar');
            const list = $('historyList');

            if (history.length > 0) {
                bar.style.display = 'block';
                list.innerHTML = '';
                history.forEach(job => {
                    const el = document.createElement('div');
                    el.className = 'history-item';

                    // Show progress if saved
                    const savedTime = localStorage.getItem('pos_' + job.audio_url);
                    let progressText = '';
                    if (savedTime) {
                        const mins = Math.floor(savedTime / 60);
                        const secs = Math.floor(savedTime % 60).toString().padStart(2, '0');
                        progressText = `<span class="history-progress">Resume at ${mins}:${secs}</span>`;
                    }

                    el.innerHTML = `
                        <div class="history-icon">📚</div>
                        <div class="history-info">
                            <div class="history-name">${esc(job.title)}</div>
                            ${progressText}
                        </div>
                    `;
                    el.onclick = () => {
                        uploadBtn.disabled = true;
                        uploadBtn.classList.add('loading');
                        setStatus('Loading…', 'working');
                        startPoll(job.id);
                    };
                    list.appendChild(el);
                });
            } else {
                bar.style.display = 'none';
            }
        } catch (e) {
            console.error('Failed to load history', e);
        }
    }
    loadHistory();

    // Sync captions to audio — movie-style: show current line only
    function synccaptions() {
        requestAnimationFrame(synccaptions);
        if (!segments.length || !captionsVisible) return;

        const t = audioPlayer.currentTime + 0.15;
        let idx = -1;
        for (let i = 0; i < segments.length; i++) {
            if (t >= segments[i].start && t <= segments[i].end) { idx = i; break; }
            if (segments[i].start > t) break;
        }

        if (idx !== activeIdx) {
            activeIdx = idx;
            if (idx !== -1) {
                captionText.textContent = segments[idx].text;
            } else {
                captionText.textContent = '';
            }
        }
    }
    requestAnimationFrame(synccaptions);

    // Save progress (throttled to once per 3 s)
    let audioPathName = '';
    audioPlayer.addEventListener('timeupdate', () => {
        const now = Date.now();
        if (now - saveThrottle < 3000) return;
        saveThrottle = now;
        if (audioPlayer.src && audioPlayer.currentTime > 0) {
            if (!audioPathName) audioPathName = new URL(audioPlayer.src).pathname;
            localStorage.setItem('pos_' + audioPathName, audioPlayer.currentTime);
        }
    });

    // Restore progress on loaded metadata
    audioPlayer.addEventListener('loadedmetadata', () => {
        audioPathName = new URL(audioPlayer.src).pathname;
        const savedTime = localStorage.getItem('pos_' + audioPathName);
        if (savedTime && parseFloat(savedTime) > 2) {
            audioPlayer.currentTime = parseFloat(savedTime);
        }
    });

    // --- Panel Toggle Logic ---
    const panelToggles = $('panelToggles');
    const toggleCaptionsBtn = $('toggleCaptions');

    // Restore saved toggle state
    const savedCaptionsVisible = localStorage.getItem('panelCaptionsVisible');
    let captionsVisible = savedCaptionsVisible !== 'false'; // default ON

    function applyPanelVisibility() {
        panelToggles.style.display = 'flex';
        captionOverlay.classList.toggle('hidden', !captionsVisible);
        toggleCaptionsBtn.classList.toggle('active', captionsVisible);
        toggleCaptionsBtn.setAttribute('aria-pressed', captionsVisible);
        localStorage.setItem('panelCaptionsVisible', captionsVisible);
    }

    toggleCaptionsBtn.addEventListener('click', () => {
        captionsVisible = !captionsVisible;
        applyPanelVisibility();
    });

    // --- Night Mode (PDF Invert) ---
    const nightBtn = $('toggleNightPdf');
    let nightMode = localStorage.getItem('pdfNightMode') === 'true';

    function applyNightMode() {
        pdfFrame.classList.toggle('night-mode', nightMode);
        nightBtn.classList.toggle('active', nightMode);
        nightBtn.setAttribute('aria-pressed', nightMode);
        localStorage.setItem('pdfNightMode', nightMode);
    }
    // Apply saved preference on load
    applyNightMode();

    nightBtn.addEventListener('click', () => {
        nightMode = !nightMode;
        applyNightMode();
        showToast(nightMode ? '🌙 Night PDF on' : '☀️ Night PDF off');
    });

    // --- Playback Speed Control ---
    const speedBtn = $('speedBtn');
    const speeds = [0.75, 1, 1.25, 1.5, 1.75, 2];
    let speedIdx = 1; // default 1x

    if (speedBtn) {
        speedBtn.addEventListener('click', () => {
            speedIdx = (speedIdx + 1) % speeds.length;
            const rate = speeds[speedIdx];
            audioPlayer.playbackRate = rate;
            speedBtn.textContent = rate + '×';
            showToast('Speed: ' + rate + '×');
        });
    }

    // --- Keyboard Shortcuts ---
    const shortcutToast = $('shortcutToast');
    let toastTimer = null;

    function showToast(msg) {
        if (!shortcutToast) return;
        shortcutToast.innerHTML = msg;
        shortcutToast.classList.add('visible');
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => shortcutToast.classList.remove('visible'), 1200);
    }

    document.addEventListener('keydown', (e) => {
        // Don't capture when user is typing in an input
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;

        switch (e.code) {
            case 'Space':
                e.preventDefault();
                if (audioPlayer.paused) { audioPlayer.play(); showToast('▶ Play'); }
                else { audioPlayer.pause(); showToast('⏸ Pause'); }
                break;
            case 'ArrowLeft':
                e.preventDefault();
                audioPlayer.currentTime = Math.max(0, audioPlayer.currentTime - 10);
                showToast('<kbd>←</kbd> −10s');
                break;
            case 'ArrowRight':
                e.preventDefault();
                audioPlayer.currentTime = Math.min(
                    isFinite(audioPlayer.duration) ? audioPlayer.duration : Infinity,
                    audioPlayer.currentTime + 10
                );
                showToast('<kbd>→</kbd> +10s');
                break;
            case 'ArrowUp':
                e.preventDefault();
                audioPlayer.volume = Math.min(1, audioPlayer.volume + 0.1);
                showToast('🔊 ' + Math.round(audioPlayer.volume * 100) + '%');
                break;
            case 'ArrowDown':
                e.preventDefault();
                audioPlayer.volume = Math.max(0, audioPlayer.volume - 0.1);
                showToast('🔉 ' + Math.round(audioPlayer.volume * 100) + '%');
                break;
            case 'KeyM':
                audioPlayer.muted = !audioPlayer.muted;
                showToast(audioPlayer.muted ? '🔇 Muted' : '🔊 Unmuted');
                break;
            case 'KeyS':
                if (speedBtn) speedBtn.click();
                break;
            case 'KeyN':
                nightBtn.click();
                break;
        }
    });
});
