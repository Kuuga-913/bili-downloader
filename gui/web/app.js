/* ==========================================================================
   Bili-Pulse Cyber Studio · Frontend Controller & Web Audio Synth Engine
   ========================================================================== */

(() => {
  'use strict';

  const QUALITIES = [
    { value: '', label: '自动最高', sub: '权限内最高' },
    { value: '127', label: '8K 超高清', sub: 'qn 127' },
    { value: '120', label: '4K 超清', sub: 'qn 120' },
    { value: '116', label: '1080P60', sub: '高帧率 116' },
    { value: '112', label: '1080P+', sub: '高码率 112' },
    { value: '80', label: '1080P', sub: '高清 80' },
    { value: '64', label: '720P', sub: '准高清 64' },
    { value: '32', label: '480P', sub: '清晰 32' },
    { value: '16', label: '360P', sub: '流畅 16' },
  ];

  const AUDIOS = [
    { value: 'auto', label: '自动最高', sub: '最高码率' },
    { value: 'hires', label: 'Hi-Res 无损', sub: 'id 30251' },
    { value: 'dolby', label: '杜比全景声', sub: 'id 30250' },
    { value: '192k', label: '192K 高音质', sub: 'id 30280' },
    { value: '132k', label: '132K 标准', sub: 'id 30232' },
    { value: '64k', label: '64K 省流量', sub: 'id 30216' },
  ];

  // ==========================================================================
  // 1. Web Audio API 程序化音效合成引擎
  // ==========================================================================
  class SoundEngine {
    constructor() {
      this.ctx = null;
      this.enabled = localStorage.getItem('bili_pulse_sound') !== 'off';
      this.lastHoverAt = 0;
    }

    ensureCtx() {
      if (!this.enabled) return null;
      if (!this.ctx) {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return null;
        this.ctx = new AudioCtx();
      }
      if (this.ctx.state === 'suspended') {
        this.ctx.resume().catch(() => {});
      }
      return this.ctx;
    }

    tone(freqStart, freqEnd, duration, type = 'sine', gainPeak = 0.08, delay = 0) {
      const ctx = this.ensureCtx();
      if (!ctx) return;
      const now = ctx.currentTime + delay;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = type;
      osc.frequency.setValueAtTime(freqStart, now);
      if (freqEnd && freqEnd !== freqStart) {
        osc.frequency.exponentialRampToValueAtTime(Math.max(20, freqEnd), now + duration);
      }
      gain.gain.setValueAtTime(0.0001, now);
      gain.gain.linearRampToValueAtTime(gainPeak, now + 0.008);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + duration);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now);
      osc.stop(now + duration + 0.02);
    }

    playClick() {
      this.tone(760, 1140, 0.036, 'triangle', 0.06);
    }

    playHover() {
      const now = performance.now();
      if (now - this.lastHoverAt < 70) return;
      this.lastHoverAt = now;
      this.tone(1320, 1480, 0.018, 'sine', 0.022);
    }

    playSwitch() {
      this.tone(587.33, 587.33, 0.055, 'sine', 0.055, 0);
      this.tone(880, 920, 0.075, 'triangle', 0.05, 0.032);
    }

    playPaste() {
      this.tone(440, 660, 0.06, 'sine', 0.06, 0);
      this.tone(660, 990, 0.085, 'triangle', 0.055, 0.045);
    }

    playLaunch() {
      const notes = [523.25, 659.25, 783.99, 1046.5];
      notes.forEach((n, idx) => {
        this.tone(n, n * 1.01, 0.14, 'triangle', 0.065, idx * 0.048);
      });
    }

    playSuccess() {
      const chord = [523.25, 659.25, 783.99, 987.77, 1174.66];
      chord.forEach((n, idx) => {
        this.tone(n, n, 0.34, 'sine', 0.06, idx * 0.052);
      });
    }

    playWarn() {
      this.tone(360, 280, 0.09, 'triangle', 0.065, 0);
      this.tone(280, 220, 0.11, 'triangle', 0.055, 0.095);
    }

    playError() {
      this.tone(210, 140, 0.14, 'sawtooth', 0.045, 0);
      this.tone(165, 110, 0.16, 'sawtooth', 0.04, 0.12);
    }
  }

  // ==========================================================================
  // 2. Canvas 声波频谱可视化器
  // ==========================================================================
  class WaveVisualizer {
    constructor(canvas) {
      this.canvas = canvas;
      this.ctx = canvas.getContext('2d');
      this.phase = 0;
      this.energy = 0.08;
      this.targetEnergy = 0.08;
      this.mode = 'idle'; // idle | running | indeterminate | done | error
      this.rafId = null;
      this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      this.paused = false;
      this.renderStatic();
    }

    setState(mode, speedMbs = 0) {
      this.mode = mode;
      if (mode === 'running') {
        const clamped = Math.min(1, Math.max(0.18, speedMbs / 18));
        this.targetEnergy = 0.24 + clamped * 0.72;
      } else if (mode === 'indeterminate') {
        this.targetEnergy = 0.42;
      } else if (mode === 'done') {
        this.targetEnergy = 0.16;
      } else {
        this.targetEnergy = 0.08;
      }
      this.ensureLoop();
    }

    setPaused(paused) {
      this.paused = paused;
      if (paused) {
        this.stopLoop();
        this.renderStatic();
      } else {
        this.ensureLoop();
      }
    }

    shouldAnimate() {
      return !this.reduced && !this.paused && !document.hidden && (this.mode === 'running' || this.mode === 'indeterminate');
    }

    ensureLoop() {
      if (!this.shouldAnimate()) {
        this.stopLoop();
        this.renderStatic();
        return;
      }
      if (this.rafId == null) {
        const step = () => {
          this.phase += this.mode === 'indeterminate' ? 0.07 : 0.11;
          this.energy += (this.targetEnergy - this.energy) * 0.12;
          this.draw();
          if (this.shouldAnimate()) {
            this.rafId = requestAnimationFrame(step);
          } else {
            this.rafId = null;
          }
        };
        this.rafId = requestAnimationFrame(step);
      }
    }

    stopLoop() {
      if (this.rafId != null) {
        cancelAnimationFrame(this.rafId);
        this.rafId = null;
      }
    }

    renderStatic() {
      this.energy = this.targetEnergy;
      this.draw();
    }

    draw() {
      const { canvas, ctx } = this;
      if (!ctx) return;
      const w = canvas.width;
      const h = canvas.height;
      ctx.clearRect(0, 0, w, h);

      const style = getComputedStyle(document.documentElement);
      const cyan = style.getPropertyValue('--accent-cyan').trim() || '#00b5f7';
      const pink = style.getPropertyValue('--accent-pink').trim() || '#fb7299';
      const emerald = style.getPropertyValue('--accent-emerald').trim() || '#22d39c';

      const primaryColor = this.mode === 'done' ? emerald : cyan;
      const midY = h * 0.62;
      const amp = (h * 0.28) * this.energy;

      // 主波与副波
      const waves = [
        { color: primaryColor, alpha: 0.72, freq: 0.028, speed: 1, width: 2.2 },
        { color: pink, alpha: 0.45, freq: 0.041, speed: -1.35, width: 1.6 },
      ];

      for (const wv of waves) {
        ctx.save();
        ctx.beginPath();
        ctx.strokeStyle = wv.color;
        ctx.globalAlpha = wv.alpha;
        ctx.lineWidth = wv.width;
        for (let x = 0; x <= w; x += 4) {
          const env = Math.sin((x / w) * Math.PI);
          const y = midY + Math.sin(x * wv.freq + this.phase * wv.speed) * amp * env;
          if (x === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.stroke();
        ctx.restore();
      }
    }
  }

  // ==========================================================================
  // 3. Top-Layer 退场动画与特征探测 (animate-to-from-top-layer & dialog)
  // ==========================================================================
  let supportsDisplayTransition;
  function canTransitionDisplay() {
    if (supportsDisplayTransition !== undefined) return supportsDisplayTransition;
    if (!window.CSS?.supports?.('transition-behavior', 'allow-discrete') || !document.body) {
      return false;
    }
    const probe = document.createElement('div');
    probe.style.cssText = 'transition: display 1s allow-discrete !important; display: block;';
    document.body.appendChild(probe);
    getComputedStyle(probe).display;
    probe.style.display = 'none';
    supportsDisplayTransition = getComputedStyle(probe).display === 'block';
    probe.remove();
    return supportsDisplayTransition;
  }

  function supportsTopLayerExit() {
    return Boolean(window.CSS?.supports?.('overlay', 'auto') && canTransitionDisplay());
  }

  async function closeTopLayer(element) {
    if (!element.open) return;
    if (!supportsTopLayerExit()) {
      element.setAttribute('data-closing', '');
      const animations = element.getAnimations({ subtree: true });
      if (animations.length > 0) {
        await Promise.race([
          Promise.allSettled(animations.map((a) => a.finished)),
          new Promise((r) => setTimeout(r, 2000)),
        ]);
      }
      if (!element.hasAttribute('data-closing')) return;
      element.removeAttribute('data-closing');
    }
    element.close();
  }

  // ==========================================================================
  // 4. 主应用状态与 DOM 引用
  // ==========================================================================
  const sound = new SoundEngine();
  const els = {
    urlInput: document.getElementById('url-input'),
    urlBadge: document.getElementById('url-type-badge'),
    engineHint: document.getElementById('engine-hint'),
    btnPaste: document.getElementById('btn-paste'),
    btnInfo: document.getElementById('btn-info'),
    btnClearUrl: document.getElementById('btn-clear-url'),
    btnDiag: document.getElementById('btn-diag'),
    btnQr: document.getElementById('btn-qr'),
    btnSound: document.getElementById('btn-sound'),
    soundLabel: document.getElementById('sound-label'),
    btnMotion: document.getElementById('btn-motion'),
    motionLabel: document.getElementById('motion-label'),
    btnTheme: document.getElementById('btn-theme'),
    btnEnvBadge: document.getElementById('btn-env-badge'),
    envPopover: document.getElementById('env-popover'),
    envDot: document.getElementById('env-dot'),
    envSummary: document.getElementById('env-summary'),
    envDetailList: document.getElementById('env-detail-list'),
    btnRefreshEnv: document.getElementById('btn-refresh-env'),
    btnAuthBadge: document.getElementById('btn-auth-badge'),
    authDot: document.getElementById('auth-dot'),
    authSummary: document.getElementById('auth-summary'),

    modeSegmented: document.getElementById('mode-segmented'),
    qualitySection: document.getElementById('quality-section'),
    audioSection: document.getElementById('audio-section'),
    qualityMatrix: document.getElementById('quality-matrix'),
    audioMatrix: document.getElementById('audio-matrix'),
    qualityNote: document.getElementById('quality-active-note'),
    audioNote: document.getElementById('audio-active-note'),

    episodesPanel: document.getElementById('episodes-panel'),
    episodesChips: document.getElementById('episodes-chips'),
    btnClearEpPick: document.getElementById('btn-clear-ep-pick'),

    btnAdvanced: document.getElementById('btn-advanced'),
    advancedDrawer: document.getElementById('advanced-drawer'),
    pagesInput: document.getElementById('pages-input'),
    threadsRange: document.getElementById('threads-range'),
    threadsValue: document.getElementById('threads-value'),
    extraInput: document.getElementById('extra-input'),
    cookieInput: document.getElementById('cookie-input'),
    btnToggleCookie: document.getElementById('btn-toggle-cookie'),
    btnSaveCookie: document.getElementById('btn-save-cookie'),
    noMergeCheck: document.getElementById('no-merge-check'),

    btnStart: document.getElementById('btn-start'),
    btnStartText: document.getElementById('btn-start-text'),
    btnStop: document.getElementById('btn-stop'),
    btnCli: document.getElementById('btn-cli'),
    outputPathText: document.getElementById('output-path-text'),

    liveIndicator: document.getElementById('live-indicator'),
    stageText: document.getElementById('stage-text'),
    epProgressBadge: document.getElementById('ep-progress-badge'),
    ringFill: document.getElementById('ring-fill'),
    pctText: document.getElementById('pct-text'),
    speedText: document.getElementById('speed-text'),
    sizeText: document.getElementById('size-text'),
    videoTitleText: document.getElementById('video-title-text'),
    downloadProgress: document.getElementById('download-progress'),

    logViewport: document.getElementById('log-viewport'),
    logOutput: document.getElementById('log-output'),
    liveCrLine: document.getElementById('live-cr-line'),
    consoleLineCount: document.getElementById('console-line-count'),
    btnCopyLog: document.getElementById('btn-copy-log'),
    btnClearLog: document.getElementById('btn-clear-log'),

    filesList: document.getElementById('files-list'),
    btnOpenOutput: document.getElementById('btn-open-output'),

    qrDialog: document.getElementById('qr-dialog'),
    qrFrame: document.getElementById('qr-frame'),
    qrImg: document.getElementById('qr-img'),
    qrPlaceholder: document.getElementById('qr-placeholder'),
    qrStatusPill: document.getElementById('qr-status-pill'),
    qrAscii: document.getElementById('qr-ascii'),
    qrLog: document.getElementById('qr-log'),
    btnQrClose: document.getElementById('btn-qr-close'),
    btnQrCancel: document.getElementById('btn-qr-cancel'),
    btnQrRetry: document.getElementById('btn-qr-retry'),

    toastContainer: document.getElementById('toast-container'),
  };

  const wave = new WaveVisualizer(document.getElementById('wave-canvas'));

  const appState = {
    env: null,
    lastSeq: 0,
    running: false,
    qrRunning: false,
    userLockedEngine: false,
    logCount: 0,
    pickedEps: new Set(),
  };

  // ==========================================================================
  // 5. 辅助函数：Toast、API 请求、主题与开关同步
  // ==========================================================================
  function toast(msg, kind = 'info') {
    const el = document.createElement('div');
    el.className = `toast-item is-${kind}`;
    el.textContent = msg;
    els.toastContainer.appendChild(el);
    setTimeout(() => {
      el.style.opacity = '0';
      setTimeout(() => el.remove(), 220);
    }, 2800);
  }

  async function apiJson(url, method = 'GET', body = null) {
    const opts = { method, headers: {} };
    if (body !== null) {
      opts.headers['Content-Type'] = 'application/json; charset=utf-8';
      opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok && !data.error) {
      data.error = `HTTP ${resp.status}`;
    }
    return data;
  }

  function applyTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('bili_pulse_theme', theme);
    wave.draw();
  }

  function updateSoundBtn() {
    const onIcon = els.btnSound.querySelector('.icon-sound-on');
    const offIcon = els.btnSound.querySelector('.icon-sound-off');
    if (sound.enabled) {
      onIcon.hidden = false;
      offIcon.hidden = true;
      els.soundLabel.textContent = '音效开';
      els.btnSound.classList.remove('is-muted');
    } else {
      onIcon.hidden = true;
      offIcon.hidden = false;
      els.soundLabel.textContent = '静音';
      els.btnSound.classList.add('is-muted');
    }
  }

  function applyMotionPaused(paused) {
    if (paused) {
      document.documentElement.setAttribute('data-motion', 'paused');
      els.motionLabel.textContent = '已暂停';
      els.btnMotion.classList.add('is-paused');
      els.btnMotion.setAttribute('aria-label', '恢复背景动效');
    } else {
      document.documentElement.removeAttribute('data-motion');
      els.motionLabel.textContent = '动效';
      els.btnMotion.classList.remove('is-paused');
      els.btnMotion.setAttribute('aria-label', '暂停背景动效');
    }
    wave.setPaused(paused);
    localStorage.setItem('bili_pulse_motion_paused', paused ? '1' : '0');
  }

  // ==========================================================================
  // 6. 构建画质 / 音质矩阵 & 控件联动
  // ==========================================================================
  function renderPillMatrix(container, items, groupName, defaultVal) {
    container.innerHTML = '';
    for (const item of items) {
      const label = document.createElement('label');
      label.className = 'pill-card';
      label.dataset.val = item.value;

      const input = document.createElement('input');
      input.type = 'radio';
      input.name = groupName;
      input.value = item.value;
      input.className = 'sr-only';
      if (item.value === defaultVal) input.checked = true;

      const main = document.createElement('span');
      main.className = 'pill-main';
      main.textContent = item.label;

      const sub = document.createElement('span');
      sub.className = 'pill-sub';
      sub.textContent = item.sub;

      label.append(input, main, sub);
      container.appendChild(label);
    }
  }

  function getSelectedTool() {
    return document.querySelector('input[name="tool"]:checked')?.value || 'bili';
  }

  function getSelectedMode() {
    return document.querySelector('input[name="mode"]:checked')?.value || 'both';
  }

  function getSelectedQuality() {
    return document.querySelector('input[name="quality"]:checked')?.value || '';
  }

  function getSelectedAudio() {
    return document.querySelector('input[name="audio"]:checked')?.value || 'auto';
  }

  function syncModeUI() {
    const mode = getSelectedMode();
    const idx = { both: 0, video: 1, audio: 2 }[mode] ?? 0;
    els.modeSegmented.style.setProperty('--seg-idx', String(idx));

    els.qualitySection.classList.toggle('is-disabled', mode === 'audio');
    els.audioSection.classList.toggle('is-disabled', mode === 'video');
    els.noMergeCheck.disabled = mode !== 'both' || getSelectedTool() !== 'bili';
  }

  function syncToolUI() {
    const tool = getSelectedTool();
    const isBili = tool === 'bili';
    els.btnInfo.disabled = appState.running || !isBili;
    els.btnDiag.disabled = appState.running || !isBili;
    els.noMergeCheck.disabled = getSelectedMode() !== 'both' || !isBili;
    if (appState.env?.tools?.[tool]) {
      els.outputPathText.textContent = appState.env.tools[tool].out;
    }
  }

  function detectUrlType(raw) {
    const url = raw.trim();
    if (!url) {
      els.urlBadge.hidden = true;
      els.engineHint.textContent = 'B 站资源推荐 Bili-Core，通用网页或 m3u8 推荐 Spider-Core';
      return;
    }

    let text = '通用网页';
    let kind = 'emerald';
    let rec = 'spider';

    if (/bilibili\.com\/bangumi\/play\/ss\d+/i.test(url) || /\bss\d+\b/i.test(url)) {
      text = 'B 站番剧 (ss)';
      kind = 'pink';
      rec = 'bili';
    } else if (/bilibili\.com\/bangumi\/play\/ep\d+/i.test(url) || /\bep\d+\b/i.test(url)) {
      text = 'B 站单集 (ep)';
      kind = 'pink';
      rec = 'bili';
    } else if (/BV[0-9A-Za-z]{6,}/i.test(url)) {
      text = 'B 站视频 (BV)';
      kind = 'cyan';
      rec = 'bili';
    } else if (/av\d+/i.test(url)) {
      text = 'B 站视频 (av)';
      kind = 'cyan';
      rec = 'bili';
    } else if (/b23\.tv/i.test(url)) {
      text = 'B 站短链 (b23.tv)';
      kind = 'pink';
      rec = 'bili';
    } else if (/\.m3u8(\?|$)/i.test(url)) {
      text = 'HLS 流媒体 (m3u8)';
      kind = 'emerald';
      rec = 'spider';
    } else if (/\.(mp4|webm|mov|mkv|flv|m4v)(\?|$)/i.test(url)) {
      text = '媒体文件直链';
      kind = 'emerald';
      rec = 'spider';
    }

    els.urlBadge.textContent = text;
    els.urlBadge.dataset.kind = kind;
    els.urlBadge.hidden = false;

    if (!appState.userLockedEngine && getSelectedTool() !== rec) {
      const radio = document.querySelector(`input[name="tool"][value="${rec}"]`);
      if (radio) {
        radio.checked = true;
        syncToolUI();
      }
    }

    els.engineHint.textContent =
      rec === 'bili'
        ? `已识别「${text}」，推荐 Bili-Core 专用引擎（支持 --info 探测与无损混流）`
        : `已识别「${text}」，推荐 Spider-Core 通用引擎（多线程直链 / m3u8 分片）`;
  }

  // ==========================================================================
  // 7. 遥测更新、终端日志与可用画质高亮
  // ==========================================================================
  const RING_CIRC = 251.33;

  function applyTelemetry(tel) {
    if (!tel) return;

    els.stageText.textContent = tel.stage || '待命就绪';
    if (tel.ep_progress) {
      els.epProgressBadge.textContent = tel.ep_progress;
      els.epProgressBadge.hidden = false;
    } else {
      els.epProgressBadge.hidden = true;
    }

    if (tel.title) {
      els.videoTitleText.textContent = tel.title;
    }

    const pct = Math.max(0, Math.min(100, Number(tel.percent) || 0));
    els.pctText.textContent = `${Math.round(pct)}%`;
    els.ringFill.style.strokeDashoffset = String(RING_CIRC * (1 - pct / 100));

    if (tel.indeterminate && tel.running) {
      els.downloadProgress.removeAttribute('value');
    } else {
      els.downloadProgress.setAttribute('value', String(pct));
    }

    const spd = Number(tel.speed_mbs) || 0;
    els.speedText.textContent = `${spd.toFixed(2)} MB/s`;

    const dl = Number(tel.downloaded_mb) || 0;
    const tot = Number(tel.total_mb) || 0;
    els.sizeText.textContent = tot > 0 ? `${dl.toFixed(1)} / ${tot.toFixed(1)} MB` : `${dl.toFixed(1)} MB`;

    let visualMode = 'idle';
    if (tel.running) {
      visualMode = tel.indeterminate ? 'indeterminate' : 'running';
      els.liveIndicator.dataset.state = 'running';
    } else if (tel.exit_code === 0) {
      visualMode = 'done';
      els.liveIndicator.dataset.state = 'done';
    } else if (tel.exit_code !== null && tel.exit_code !== 0) {
      visualMode = 'error';
      els.liveIndicator.dataset.state = 'error';
    } else {
      els.liveIndicator.dataset.state = 'idle';
    }
    wave.setState(visualMode, spd);

    // 高亮 --info 探测到的可用画质 / 音质
    const availQ = new Set((tel.available_qualities || []).map(String));
    for (const card of els.qualityMatrix.querySelectorAll('.pill-card')) {
      const val = card.dataset.val;
      card.classList.remove('is-supported', 'is-unsupported');
      if (availQ.size > 0 && val) {
        card.classList.add(availQ.has(val) ? 'is-supported' : 'is-unsupported');
      }
    }

    const availA = new Set(tel.available_audios || []);
    for (const card of els.audioMatrix.querySelectorAll('.pill-card')) {
      const val = card.dataset.val;
      card.classList.remove('is-supported', 'is-unsupported');
      if (availA.size > 0 && val !== 'auto') {
        card.classList.add(availA.has(val) ? 'is-supported' : 'is-unsupported');
      }
    }

    // 渲染探测到的分P芯片
    if (Array.isArray(tel.episodes) && tel.episodes.length > 0) {
      if (els.episodesChips.dataset.count !== String(tel.episodes.length)) {
        els.episodesChips.dataset.count = String(tel.episodes.length);
        els.episodesChips.innerHTML = '';
        for (const ep of tel.episodes) {
          const btn = document.createElement('button');
          btn.type = 'button';
          btn.className = 'ep-chip';
          btn.dataset.num = ep.num;
          btn.textContent = `${ep.tag} · ${ep.title}`;
          if (appState.pickedEps.has(ep.num)) btn.classList.add('is-picked');
          btn.addEventListener('click', () => {
            sound.playClick();
            if (appState.pickedEps.has(ep.num)) {
              appState.pickedEps.delete(ep.num);
              btn.classList.remove('is-picked');
            } else {
              appState.pickedEps.add(ep.num);
              btn.classList.add('is-picked');
            }
            const sorted = [...appState.pickedEps].map(Number).sort((a, b) => a - b);
            els.pagesInput.value = sorted.join(',');
          });
          els.episodesChips.appendChild(btn);
        }
      }
      els.episodesPanel.hidden = false;
    }
  }

  function appendLogLine(text, level = 'plain') {
    els.liveCrLine.hidden = true;
    const div = document.createElement('div');
    div.className = `log-line level-${level}`;
    div.textContent = text;
    els.logOutput.appendChild(div);
    appState.logCount += 1;

    while (els.logOutput.children.length > 800) {
      els.logOutput.firstElementChild.remove();
    }
    els.consoleLineCount.textContent = `${els.logOutput.children.length} 行`;
    els.logViewport.scrollTop = els.logViewport.scrollHeight;
  }

  function clearConsole() {
    els.logOutput.innerHTML = '';
    els.liveCrLine.hidden = true;
    appState.logCount = 0;
    els.consoleLineCount.textContent = '0 行';
  }

  function renderEnv(env) {
    appState.env = env;
    const biliOk = env.tools?.bili?.py_ok && env.tools?.bili?.script_ok;
    const spiderOk = env.tools?.spider?.py_ok && env.tools?.spider?.script_ok;
    const ffOk = Boolean(env.ffmpeg);

    if (biliOk && spiderOk && ffOk) {
      els.envDot.className = 'status-dot is-ok';
      els.envSummary.textContent = '双核 + FFmpeg 就绪';
    } else if (biliOk || spiderOk) {
      els.envDot.className = 'status-dot is-warn';
      els.envSummary.textContent = ffOk ? '部分内核就绪' : '缺少 FFmpeg';
    } else {
      els.envDot.className = 'status-dot is-err';
      els.envSummary.textContent = '环境待修复';
    }

    const rows = [
      ['Bili-Core 脚本', env.tools?.bili?.script_ok, env.tools?.bili?.script],
      ['Spider-Core 脚本', env.tools?.spider?.script_ok, env.tools?.spider?.script],
      ['Python 运行时', env.tools?.bili?.py_ok, env.tools?.bili?.py],
      ['FFmpeg 混流引擎', Boolean(env.ffmpeg), env.ffmpeg || '未检测到 ffmpeg'],
      ['FFprobe 校验器', Boolean(env.ffprobe), env.ffprobe || '未检测到 ffprobe'],
    ];
    els.envDetailList.innerHTML = '';
    for (const [name, ok, val] of rows) {
      const li = document.createElement('li');
      li.className = 'env-detail-row';

      const nameEl = document.createElement('span');
      nameEl.className = 'env-detail-name';
      nameEl.textContent = name;

      const valEl = document.createElement('span');
      valEl.className = 'env-detail-val';
      valEl.title = val || '';
      valEl.textContent = val || '';

      const tagEl = document.createElement('span');
      tagEl.className = ok ? 'env-tag-ok' : 'env-tag-miss';
      tagEl.textContent = ok ? 'OK' : '缺失';

      li.append(nameEl, valEl, tagEl);
      els.envDetailList.appendChild(li);
    }

    if (els.logOutput.children.length === 0) {
      appendLogLine('=== Bili-Pulse 工作台 · 本地环境自检 ===', 'heading');
      appendLogLine(
        `  [${biliOk ? 'OK' : '缺失'}] Bili-Core 专用内核 -> ${env.tools?.bili?.script || ''}`,
        biliOk ? 'ok' : 'err',
      );
      appendLogLine(
        `  [${spiderOk ? 'OK' : '缺失'}] Spider-Core 通用内核 -> ${env.tools?.spider?.script || ''}`,
        spiderOk ? 'ok' : 'err',
      );
      appendLogLine(
        `  [${ffOk ? 'OK' : '缺失'}] FFmpeg / FFprobe -> ${env.ffmpeg || '未在 PATH 中找到'}`,
        ffOk ? 'ok' : 'warn',
      );
      appendLogLine(
        env.cookie_exists
          ? '  [OK] Cookie 登录态已就绪（支持大会员 / 高画质 / 充电专属）'
          : '  [提示] 暂未检测到本地 Cookie，可点击右上角「扫码登录」解锁高画质',
        env.cookie_exists ? 'ok' : 'warn',
      );
      appendLogLine('=== 就绪：粘贴链接即可探测画质或直接开始下载 ===', 'info');
    }

    if (env.cookie_exists) {
      els.authDot.className = 'status-dot is-ok';
      els.authSummary.textContent = 'Cookie 已载入';
      if (!els.cookieInput.value) {
        els.cookieInput.value = env.cookie_value || '';
      }
    } else {
      els.authDot.className = 'status-dot is-warn';
      els.authSummary.textContent = '未登录 · 点此扫码';
    }

    renderRecentFiles(env.recent_files || []);
    syncToolUI();
  }

  function renderRecentFiles(files) {
    els.filesList.innerHTML = '';
    if (!files.length) {
      const li = document.createElement('li');
      li.className = 'files-empty';
      li.textContent = '暂无已下载的媒体文件';
      els.filesList.appendChild(li);
      return;
    }
    for (const f of files) {
      const li = document.createElement('li');
      li.className = 'file-row';

      const meta = document.createElement('div');
      meta.className = 'file-meta';
      const ext = document.createElement('span');
      ext.className = 'file-ext';
      ext.textContent = f.ext;
      const name = document.createElement('span');
      name.className = 'file-name';
      name.textContent = f.rel || f.name;
      name.title = f.path;
      meta.append(ext, name);

      const right = document.createElement('div');
      right.className = 'file-right';
      const sz = document.createElement('span');
      sz.className = 'file-size';
      sz.textContent = f.size_text;
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn-mini file-locate';
      btn.textContent = '定位';
      btn.addEventListener('click', () => {
        sound.playClick();
        apiJson('/api/open_folder', 'POST', { tool: f.tool, file_path: f.path });
      });
      right.append(sz, btn);

      li.append(meta, right);
      els.filesList.appendChild(li);
    }
  }

  function setRunningState(running) {
    appState.running = running;
    els.btnStart.disabled = running;
    els.btnStop.disabled = !running;
    const isBili = getSelectedTool() === 'bili';
    els.btnInfo.disabled = running || !isBili;
    els.btnDiag.disabled = running || !isBili;
    els.btnStartText.textContent = running ? '任务执行中…' : '开始下载';
  }

  // ==========================================================================
  // 8. 轮询后端实时事件流
  // ==========================================================================
  async function pollLoop() {
    let delay = 400;
    try {
      const res = await apiJson(`/api/poll?since=${appState.lastSeq}`);
      if (typeof res.seq === 'number') {
        appState.lastSeq = res.seq;
      }
      setRunningState(Boolean(res.running));
      appState.qrRunning = Boolean(res.qr_running);
      applyTelemetry(res.telemetry);

      for (const ev of res.events || []) {
        if (ev.type === 'clear_log') {
          clearConsole();
          els.episodesChips.dataset.count = '';
          els.episodesPanel.hidden = true;
          appState.pickedEps.clear();
        } else if (ev.type === 'log') {
          appendLogLine(ev.text, ev.level);
        } else if (ev.type === 'progress_line') {
          els.liveCrLine.textContent = ev.text;
          els.liveCrLine.hidden = false;
          els.logViewport.scrollTop = els.logViewport.scrollHeight;
        } else if (ev.type === 'done') {
          els.liveCrLine.hidden = true;
          if (Array.isArray(ev.recent_files)) {
            renderRecentFiles(ev.recent_files);
          }
          if (ev.code === 0) {
            sound.playSuccess();
            toast(
              ev.action === 'info'
                ? '片源画质与分P信息探测完成'
                : ev.action === 'diag'
                  ? '账号权限诊断完成'
                  : '下载任务已完成并校验通过！',
              'ok',
            );
          } else {
            sound.playError();
            toast(`任务已结束（退出码 ${ev.code}），请查看终端日志`, 'err');
          }
        } else if (ev.type === 'qr_img') {
          els.qrImg.src = `/api/qr_image?t=${ev.ts}`;
          els.qrImg.hidden = false;
          els.qrPlaceholder.hidden = true;
          els.qrAscii.hidden = true;
          els.qrFrame.classList.add('is-scanning');
          els.qrStatusPill.textContent = '请使用手机 B 站 App 扫码';
        } else if (ev.type === 'qr_ascii') {
          if (els.qrImg.hidden) {
            els.qrAscii.textContent += `${ev.line}\n`;
            els.qrAscii.hidden = false;
            els.qrFrame.classList.add('is-scanning');
          }
        } else if (ev.type === 'qr_status') {
          const line = document.createElement('div');
          line.textContent = ev.text;
          els.qrLog.appendChild(line);
          els.qrLog.scrollTop = els.qrLog.scrollHeight;
          els.qrStatusPill.textContent = ev.text.replace(/^\[.*?\]\s*/, '');
        } else if (ev.type === 'qr_done') {
          els.qrFrame.classList.remove('is-scanning');
          if (ev.ok) {
            sound.playSuccess();
            els.qrStatusPill.textContent = '登录成功！已同步双核 Cookie';
            if (ev.cookie_value) {
              els.cookieInput.value = ev.cookie_value;
            }
            toast('扫码登录成功，Cookie 已同步！', 'ok');
            loadEnv();
            setTimeout(() => closeTopLayer(els.qrDialog), 1100);
          } else {
            sound.playWarn();
            if (els.qrImg.hidden) {
              els.qrPlaceholder.textContent = '未能生成二维码，请查看右侧日志提示';
            }
            els.qrStatusPill.textContent = '扫码已结束或超时，可点击刷新重试';
          }
        }
      }

      if (res.running || res.qr_running) {
        delay = 140;
      }
    } catch {
      delay = 800;
    }
    setTimeout(pollLoop, delay);
  }

  // ==========================================================================
  // 9. 启动下载 / 探测 / 诊断 & 扫码模态框
  // ==========================================================================
  async function startAction(action) {
    const url = els.urlInput.value.trim();
    if (!url) {
      sound.playWarn();
      toast('请先粘贴或输入视频链接', 'warn');
      els.urlInput.focus();
      return;
    }

    const payload = {
      action,
      tool: getSelectedTool(),
      url,
      mode: getSelectedMode(),
      quality: getSelectedQuality() ? Number(getSelectedQuality()) : null,
      audio_quality: getSelectedAudio(),
      pages: els.pagesInput.value.trim(),
      threads: Number(els.threadsRange.value) || 8,
      no_merge: els.noMergeCheck.checked,
      extra: els.extraInput.value.trim(),
      cookie: els.cookieInput.value.trim(),
    };

    sound.playLaunch();
    const res = await apiJson('/api/start', 'POST', payload);
    if (!res.ok) {
      sound.playError();
      toast(res.error || '任务启动失败', 'err');
    } else {
      setRunningState(true);
    }
  }

  async function openQrLogin() {
    sound.playClick();
    els.qrFrame.classList.remove('is-scanning');
    els.qrImg.hidden = true;
    els.qrPlaceholder.textContent = '正在向 B 站请求登录二维码…';
    els.qrPlaceholder.hidden = false;
    els.qrAscii.hidden = true;
    els.qrAscii.textContent = '';
    els.qrLog.innerHTML = '';
    els.qrStatusPill.textContent = '正在请求 B 站登录二维码…';

    if (!els.qrDialog.open) {
      els.qrDialog.showModal();
    }

    const res = await apiJson('/api/qr_start', 'POST', {});
    if (!res.ok) {
      sound.playError();
      els.qrStatusPill.textContent = res.error || '二维码请求失败';
    }
  }

  // ==========================================================================
  // 10. 事件绑定与无障碍回退
  // ==========================================================================
  function bindEvents() {
    // 主题与音效、动效控制
    const savedTheme = localStorage.getItem('bili_pulse_theme') || 'dark';
    applyTheme(savedTheme);
    updateSoundBtn();
    applyMotionPaused(localStorage.getItem('bili_pulse_motion_paused') === '1');

    els.btnTheme.addEventListener('click', () => {
      sound.playSwitch();
      const next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
      applyTheme(next);
    });

    els.btnSound.addEventListener('click', () => {
      sound.enabled = !sound.enabled;
      localStorage.setItem('bili_pulse_sound', sound.enabled ? 'on' : 'off');
      updateSoundBtn();
      if (sound.enabled) sound.playSwitch();
    });

    els.btnMotion.addEventListener('click', () => {
      sound.playClick();
      const nowPaused = document.documentElement.getAttribute('data-motion') === 'paused';
      applyMotionPaused(!nowPaused);
    });

    // Anchor positioning 回退（用于 #env-popover）
    if (els.envPopover && !CSS.supports('position-area: bottom')) {
      els.envPopover.addEventListener('beforetoggle', (e) => {
        if (e.newState === 'open') {
          const r = els.btnEnvBadge.getBoundingClientRect();
          els.envPopover.style.top = `${r.bottom + 8}px`;
          els.envPopover.style.left = `${Math.max(12, r.left)}px`;
        }
      });
    }

    els.btnRefreshEnv.addEventListener('click', () => {
      sound.playClick();
      loadEnv();
      toast('已刷新本地运行环境状态', 'info');
    });

    els.btnAuthBadge.addEventListener('click', openQrLogin);
    els.btnQr.addEventListener('click', openQrLogin);

    // URL 输入与粘贴
    els.urlInput.addEventListener('input', () => detectUrlType(els.urlInput.value));
    els.urlInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.isComposing) {
        e.preventDefault();
        startAction('download');
      }
    });

    els.btnPaste.addEventListener('click', async () => {
      try {
        const text = (await navigator.clipboard.readText()).trim();
        if (!text) {
          sound.playWarn();
          toast('剪贴板中没有文本内容', 'warn');
          return;
        }
        els.urlInput.value = text;
        detectUrlType(text);
        sound.playPaste();
        toast('已粘贴视频链接', 'ok');
      } catch {
        sound.playWarn();
        els.urlInput.focus();
        toast('浏览器未授予剪贴板读取权限，请按 Ctrl+V 粘贴', 'warn');
      }
    });

    els.btnClearUrl.addEventListener('click', () => {
      sound.playClick();
      els.urlInput.value = '';
      detectUrlType('');
      els.urlInput.focus();
    });

    els.btnInfo.addEventListener('click', () => startAction('info'));
    els.btnDiag.addEventListener('click', () => startAction('diag'));
    els.btnStart.addEventListener('click', () => startAction('download'));

    els.btnStop.addEventListener('click', async () => {
      sound.playWarn();
      const res = await apiJson('/api/stop', 'POST', {});
      if (!res.ok) toast(res.error || '停止失败', 'warn');
    });

    // 引擎切换
    for (const radio of document.querySelectorAll('input[name="tool"]')) {
      radio.addEventListener('change', () => {
        appState.userLockedEngine = true;
        sound.playSwitch();
        syncToolUI();
      });
    }

    // 轨道模式分段滑块
    for (const radio of document.querySelectorAll('input[name="mode"]')) {
      radio.addEventListener('change', () => {
        sound.playSwitch();
        syncModeUI();
      });
    }

    // 画质 / 音质矩阵切换音效与提示
    els.qualityMatrix.addEventListener('change', (e) => {
      if (e.target.name === 'quality') {
        sound.playClick();
        const found = QUALITIES.find((x) => x.value === e.target.value);
        els.qualityNote.textContent = `当前：${found ? found.label : '自动最高'}`;
      }
    });

    els.audioMatrix.addEventListener('change', (e) => {
      if (e.target.name === 'audio') {
        sound.playClick();
        const found = AUDIOS.find((x) => x.value === e.target.value);
        els.audioNote.textContent = `当前：${found ? found.label : '自动最高'}`;
      }
    });

    els.btnClearEpPick.addEventListener('click', () => {
      sound.playClick();
      appState.pickedEps.clear();
      els.pagesInput.value = '';
      for (const chip of els.episodesChips.querySelectorAll('.ep-chip')) {
        chip.classList.remove('is-picked');
      }
    });

    // 高级抽屉 (animate-to-intrinsic-sizes)
    els.btnAdvanced.addEventListener('click', () => {
      sound.playClick();
      const expanded = els.btnAdvanced.getAttribute('aria-expanded') === 'true';
      if (!expanded) {
        els.advancedDrawer.hidden = false;
        void els.advancedDrawer.offsetHeight;
        els.advancedDrawer.classList.add('is-open');
        els.btnAdvanced.setAttribute('aria-expanded', 'true');
      } else {
        els.advancedDrawer.classList.remove('is-open');
        els.btnAdvanced.setAttribute('aria-expanded', 'false');
        const transitions = els.advancedDrawer.getAnimations().filter((a) => a instanceof CSSTransition);
        if (!transitions.length) {
          els.advancedDrawer.hidden = true;
        } else {
          Promise.allSettled(transitions.map((t) => t.finished)).then(() => {
            if (!els.advancedDrawer.classList.contains('is-open')) {
              els.advancedDrawer.hidden = true;
            }
          });
        }
      }
    });

    els.threadsRange.addEventListener('input', () => {
      els.threadsValue.textContent = `${els.threadsRange.value} 线程`;
    });

    els.btnToggleCookie.addEventListener('click', () => {
      sound.playClick();
      const hidden = els.cookieInput.type === 'password';
      els.cookieInput.type = hidden ? 'text' : 'password';
      els.btnToggleCookie.textContent = hidden ? '隐藏' : '显示';
    });

    els.btnSaveCookie.addEventListener('click', async () => {
      sound.playClick();
      const res = await apiJson('/api/save_cookie', 'POST', { cookie: els.cookieInput.value.trim() });
      if (res.ok) {
        toast('Cookie 已保存到本地 cookie_string.txt', 'ok');
        loadEnv();
      } else {
        toast(res.error || '保存失败', 'err');
      }
    });

    // 打开输出文件夹 & 命令行
    els.btnOpenOutput.addEventListener('click', () => {
      sound.playClick();
      apiJson('/api/open_folder', 'POST', { tool: getSelectedTool() });
    });

    els.btnCli.addEventListener('click', async () => {
      sound.playClick();
      const res = await apiJson('/api/open_cli', 'POST', { tool: getSelectedTool() });
      if (!res.ok) toast(res.error || '无法打开命令行版', 'err');
    });

    // 日志操作
    els.btnClearLog.addEventListener('click', () => {
      sound.playClick();
      clearConsole();
    });

    els.btnCopyLog.addEventListener('click', async () => {
      sound.playClick();
      const text = [...els.logOutput.querySelectorAll('.log-line')].map((el) => el.textContent).join('\n');
      try {
        await navigator.clipboard.writeText(text);
        toast('已复制全部终端日志', 'ok');
      } catch {
        toast('复制日志失败', 'warn');
      }
    });

    // 扫码弹窗关闭与 light-dismiss 回退
    const closeQr = () => {
      sound.playClick();
      apiJson('/api/qr_stop', 'POST', {});
      closeTopLayer(els.qrDialog);
    };
    els.btnQrClose.addEventListener('click', closeQr);
    els.btnQrCancel.addEventListener('click', closeQr);
    els.btnQrRetry.addEventListener('click', openQrLogin);

    els.qrDialog.addEventListener('cancel', (event) => {
      event.preventDefault();
      apiJson('/api/qr_stop', 'POST', {});
      closeTopLayer(els.qrDialog);
    });

    if (!('closedBy' in HTMLDialogElement.prototype)) {
      const outside = (e, dialog) => {
        const r = dialog.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      let pressedOutside = false;
      els.qrDialog.addEventListener('pointerdown', (e) => {
        pressedOutside = outside(e, els.qrDialog);
      });
      els.qrDialog.addEventListener('pointerup', (e) => {
        if (pressedOutside && outside(e, els.qrDialog)) {
          apiJson('/api/qr_stop', 'POST', {});
          closeTopLayer(els.qrDialog);
        }
      });
    }

    // 轻柔的悬停音效（为主要按钮添加）
    document.addEventListener('pointerenter', (e) => {
      if (e.target instanceof Element && e.target.closest('.btn-launch, .engine-card, .pill-card')) {
        sound.playHover();
      }
    }, true);

    // 窗口可见性与关闭生命周期
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        wave.stopLoop();
      } else {
        wave.ensureLoop();
      }
    });

    window.addEventListener('pagehide', () => {
      navigator.sendBeacon('/api/shutdown', '{}');
    });
  }

  async function loadEnv() {
    try {
      const env = await apiJson('/api/env');
      renderEnv(env);
    } catch {
      els.envDot.className = 'status-dot is-err';
      els.envSummary.textContent = '无法连接本地服务';
    }
  }

  // ==========================================================================
  // 初始化启动
  // ==========================================================================
  renderPillMatrix(els.qualityMatrix, QUALITIES, 'quality', '');
  renderPillMatrix(els.audioMatrix, AUDIOS, 'audio', 'auto');
  bindEvents();
  syncModeUI();
  syncToolUI();
  loadEnv();
  pollLoop();
})();
