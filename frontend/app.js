document.addEventListener('DOMContentLoaded', () => {
    const $ = id => document.getElementById(id);
    const uploadBtn = $('uploadBtn'), statusMsg = $('statusMessage');
    const pdfFrame = $('pdfFrame'), audioPlayer = $('audioPlayer');
    const captionsScroll = $('captionsScroll'), captionsContent = $('captionsContent');
    const pdfUpload = $('pdfUpload'), audioUpload = $('audioUpload');
    const pdfLabel = $('pdfLabel'), audioLabel = $('audioLabel');
    const trackTitle = $('trackTitle');

    let poll = null, lines = [], activeLine = null, currentJobId = null;

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
            const { job_id } = await res.json();
            setStatus('Transcribing…', 'working');
            startPoll(job_id);
        } catch {
            setStatus('Upload failed', 'error');
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
                localStorage.setItem('lastJobId', id);
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
        $('captionsEmpty')?.remove();

        // Hide history when reading
        if ($('historyBar')) {
            $('historyBar').style.display = 'none';
        }
        // Hide top navbar when reading
        if ($('topBar')) {
            $('topBar').style.display = 'none';
        }

        // Show panel toggles (they live inside audioBar, which is shown via flex)
        applyPanelVisibility();

        // Append PDF parameters for Odd Spread and Fit
        const paramStr = pdfUrl.includes('#') ? '&' : '#';
        pdfFrame.src = pdfUrl + paramStr + 'pageLayout=TwoPageRight&view=Fit';
        pdfFrame.style.display = 'block';

        audioPlayer.src = audioUrl;
        $('audioBar').style.display = 'flex';

        trackTitle.textContent = title || 'Now Playing';

        buildcaptions(transcript);
    }

    // Build captions
    function buildcaptions(data) {
        captionsContent.innerHTML = '';
        lines = [];
        if (!data?.segments) return;

        for (const seg of data.segments) {
            const hasW = seg.words?.length > 0;
            const text = (hasW ? seg.words.map(w => w.word.trim()).join(' ') : seg.text || '').trim();
            if (!text) continue;

            const el = document.createElement('div');
            el.className = 'caption-line';
            el.textContent = text;

            const start = hasW ? seg.words[0].start : seg.start;
            const end = hasW ? seg.words.at(-1).end : seg.end;

            // Timestamp badge
            const timeBadge = document.createElement('span');
            timeBadge.className = 'caption-time';
            const m = Math.floor(start / 60);
            const s = Math.floor(start % 60).toString().padStart(2, '0');
            timeBadge.textContent = m + ':' + s;
            el.appendChild(timeBadge);

            el.onclick = () => { audioPlayer.currentTime = start; audioPlayer.play(); };

            captionsContent.appendChild(el);
            lines.push({ el, start, end });
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
                            <div class="history-name">${job.title}</div>
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

    // Restore last open page
    const lastJobId = localStorage.getItem('lastJobId');
    if (lastJobId) {
        setStatus('Restoring last session…', 'working');
        startPoll(lastJobId);
    }

    // Sync captions to audio (60fps loop instead of laggy timeupdate)
    let scrollClientHeight = captionsScroll.clientHeight;
    // Update cached height on resize
    window.addEventListener('resize', () => { scrollClientHeight = captionsScroll.clientHeight; });

    function synccaptions() {
        requestAnimationFrame(synccaptions);

        // Add 0.15s lead time so highlights trigger exactly as the word is spoken
        const t = audioPlayer.currentTime + 0.15;
        if (!lines.length) return;

        let idx = -1;
        for (let i = 0; i < lines.length; i++) {
            if (t >= lines[i].start && t <= lines[i].end) { idx = i; break; }
            if (lines[i].start > t) break;
        }

        if (idx !== -1 && activeLine !== lines[idx].el) {
            lines.forEach(l => l.el.classList.remove('active', 'near'));
            lines[idx].el.classList.add('active');

            for (let o = -2; o <= 2; o++) {
                const j = idx + o;
                if (o && j >= 0 && j < lines.length) lines[j].el.classList.add('near');
            }

            const el = lines[idx].el;
            captionsScroll.scrollTo({
                top: el.offsetTop - scrollClientHeight / 2 + el.offsetHeight / 2,
                behavior: 'smooth'
            });
            activeLine = el;
        } else if (idx === -1 && activeLine) {
            lines.forEach(l => l.el.classList.remove('active', 'near'));
            activeLine = null;
        }
    }
    requestAnimationFrame(synccaptions);

    // Save progress periodically
    let audioPathName = '';
    audioPlayer.addEventListener('timeupdate', () => {
        if (audioPlayer.src && audioPlayer.currentTime > 0) {
            // Save current time keyed by the audio URL
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
            // Run one sync frame immediately to scroll the captions down
            synccaptions();
        }
    });

    // --- Panel Toggle Logic ---
    const panelToggles = $('panelToggles');
    const togglePdfBtn = $('togglePdf');
    const toggleCaptionsBtn = $('toggleCaptions');
    const pdfPanel = $('pdfPanel');
    const resizerEl = $('resizer');
    const captionsPanelEl = $('captionsPanel');

    // Restore saved toggle state
    const savedPdfVisible = localStorage.getItem('panelPdfVisible');
    const savedCaptionsVisible = localStorage.getItem('panelCaptionsVisible');
    let pdfVisible = savedPdfVisible !== 'false';
    let captionsVisible = savedCaptionsVisible !== 'false';

    function applyPanelVisibility() {
        panelToggles.style.display = 'flex';
        pdfPanel.classList.toggle('panel-hidden', !pdfVisible);
        captionsPanelEl.classList.toggle('panel-hidden', !captionsVisible);

        const solo = captionsVisible && !pdfVisible;
        captionsPanelEl.classList.toggle('panel-solo', solo);
        // Clear inline width when solo so CSS flex:1 takes over; restore when split
        if (solo) {
            captionsPanelEl.style.width = '';
        } else {
            const saved = localStorage.getItem('captionsWidth');
            if (saved) captionsPanelEl.style.width = saved;
        }

        resizerEl.classList.toggle('panel-hidden', !pdfVisible || !captionsVisible);
        togglePdfBtn.classList.toggle('active', pdfVisible);
        toggleCaptionsBtn.classList.toggle('active', captionsVisible);
        // Mark the sole-active button as locked so user knows it can't be hidden
        togglePdfBtn.classList.toggle('locked', pdfVisible && !captionsVisible);
        toggleCaptionsBtn.classList.toggle('locked', captionsVisible && !pdfVisible);
        localStorage.setItem('panelPdfVisible', pdfVisible);
        localStorage.setItem('panelCaptionsVisible', captionsVisible);
    }

    togglePdfBtn.addEventListener('click', () => {
        // Don't allow hiding both panels
        if (pdfVisible && !captionsVisible) return;
        pdfVisible = !pdfVisible;
        applyPanelVisibility();
    });

    toggleCaptionsBtn.addEventListener('click', () => {
        // Don't allow hiding both panels
        if (captionsVisible && !pdfVisible) return;
        captionsVisible = !captionsVisible;
        applyPanelVisibility();
    });

    // --- Adjustable Split View Resizer ---
    const resizer = $('resizer');
    const captionsPanel = $('captionsPanel');
    const pdfFrameEl = $('pdfFrame');
    let isResizing = false;

    // Restore saved width (skip if subtitle-only mode — solo uses flex:1)
    const savedWidth = localStorage.getItem('captionsWidth');
    if (savedWidth && captionsPanel && !captionsPanel.classList.contains('panel-solo')) {
        captionsPanel.style.width = savedWidth;
    }

    if (resizer && captionsPanel) {
        resizer.addEventListener('mousedown', (e) => {
            isResizing = true;
            resizer.classList.add('active');
            document.body.style.cursor = 'col-resize';
            if (pdfFrameEl) pdfFrameEl.style.pointerEvents = 'none'; // Prevent iframe from eating mouse events
        });

        document.addEventListener('mousemove', (e) => {
            if (!isResizing) return;
            // Calculate new width for the right panel (distance from right edge of window to mouse)
            const newWidth = document.body.clientWidth - e.clientX;

            // Constrain width between 250px and (window width - 250px)
            if (newWidth >= 250 && newWidth <= document.body.clientWidth - 250) {
                captionsPanel.style.width = newWidth + 'px';
            }
        });

        document.addEventListener('mouseup', () => {
            if (isResizing) {
                isResizing = false;
                resizer.classList.remove('active');
                document.body.style.cursor = '';
                if (pdfFrameEl) pdfFrameEl.style.pointerEvents = 'auto';

                // Save preferred width
                localStorage.setItem('captionsWidth', captionsPanel.style.width);
            }
        });
    }

    // --- Night Mode (PDF Invert) ---
    const nightBtn = $('toggleNightPdf');
    let nightMode = localStorage.getItem('pdfNightMode') === 'true';

    function applyNightMode() {
        pdfFrame.classList.toggle('night-mode', nightMode);
        nightBtn.classList.toggle('active', nightMode);
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
                audioPlayer.currentTime = Math.min(audioPlayer.duration || 0, audioPlayer.currentTime + 10);
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
