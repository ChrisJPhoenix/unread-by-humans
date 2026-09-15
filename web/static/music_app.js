'use strict';
// music_app.js — browser-only glue for the Music web app.
// Untested by design (DOM + AudioContext + fetch; no node:test coverage).
//
// Wires:
//   • Score-text editor  → POST /music/parse
//   • Play transport     → POST /music/play      → Web Audio (float32 PCM)
//   • FLAC transport     → GET  /music/flac?text= → download link
//   • Instruments panel  → GET/POST /music/instruments  → docked panel + modal editor

import {
  frameCountFromByteLength,
  deinterleave,
  applyFadeInOut,
  nextAuditionToken,
  isCurrent,
} from '/static/music_audition.mjs';

import { Curve2D, buildSegments, evaluate } from '/static/curve2d.mjs';

// ── GM instrument data (mirrors music/gm_instruments.py) ─────────────────────
// 16 rows × 8 columns of [abbr, fullName]. Program = row*8 + col.

const MELODIC_GRID = [
  [["Ac.Grand","Acoustic Grand Piano"],["Br.Acoust","Bright Acoustic Piano"],["El.Grand","Electric Grand Piano"],["Hnk-tonk","Honky-tonk Piano"],["ElPiano1","Electric Piano 1"],["ElPiano2","Electric Piano 2"],["Hrpschrd","Harpsichord"],["Clavinet","Clavinet"]],
  [["Celesta","Celesta"],["Glocken","Glockenspiel"],["MusicBox","Music Box"],["Vibrphn","Vibraphone"],["Marimba","Marimba"],["Xylophn","Xylophone"],["TubBells","Tubular Bells"],["Dulcimer","Dulcimer"]],
  [["Drawbar","Drawbar Organ"],["PercOrg","Percussive Organ"],["RockOrg","Rock Organ"],["ChrchOrg","Church Organ"],["ReedOrg","Reed Organ"],["Accordion","Accordion"],["Harmonica","Harmonica"],["TangoAcc","Tango Accordion"]],
  [["Nylon Gt","Acoustic Guitar (nylon)"],["Steel Gt","Acoustic Guitar (steel)"],["Jazz Gt","Electric Guitar (jazz)"],["Clean Gt","Electric Guitar (clean)"],["Muted Gt","Electric Guitar (muted)"],["Ovrdr Gt","Overdriven Guitar"],["Dist Gt","Distortion Guitar"],["GtrHarmn","Guitar Harmonics"]],
  [["AcsticBs","Acoustic Bass"],["FingerBs","Electric Bass (finger)"],["PickedBs","Electric Bass (pick)"],["FretlsBs","Fretless Bass"],["SlapBs 1","Slap Bass 1"],["SlapBs 2","Slap Bass 2"],["SynBass1","Synth Bass 1"],["SynBass2","Synth Bass 2"]],
  [["Violin","Violin"],["Viola","Viola"],["Cello","Cello"],["Contrabs","Contrabass"],["TremStrg","Tremolo Strings"],["PizzStrg","Pizzicato Strings"],["OrchHarp","Orchestral Harp"],["Timpani","Timpani"]],
  [["StrEns 1","String Ensemble 1"],["StrEns 2","String Ensemble 2"],["SynStr 1","Synth Strings 1"],["SynStr 2","Synth Strings 2"],["ChoirAah","Choir Aahs"],["VoiceOoh","Voice Oohs"],["SynVoice","Synth Voice"],["OrchHit","Orchestra Hit"]],
  [["Trumpet","Trumpet"],["Trombone","Trombone"],["Tuba","Tuba"],["MuteTrp","Muted Trumpet"],["FrenchHn","French Horn"],["BrassSec","Brass Section"],["SynBrs 1","Synth Brass 1"],["SynBrs 2","Synth Brass 2"]],
  [["SopSax","Soprano Sax"],["AltoSax","Alto Sax"],["TenorSax","Tenor Sax"],["BariSax","Baritone Sax"],["Oboe","Oboe"],["EngHorn","English Horn"],["Bassoon","Bassoon"],["Clarinet","Clarinet"]],
  [["Piccolo","Piccolo"],["Flute","Flute"],["Recorder","Recorder"],["PanFlute","Pan Flute"],["Bottle","Blown Bottle"],["Shakuhci","Shakuhachi"],["Whistle","Whistle"],["Ocarina","Ocarina"]],
  [["Square","Lead 1 (square)"],["Sawtooth","Lead 2 (sawtooth)"],["Calliope","Lead 3 (calliope)"],["Chiff","Lead 4 (chiff)"],["Charang","Lead 5 (charang)"],["Voice","Lead 6 (voice)"],["Fifths","Lead 7 (fifths)"],["Bass+Ld","Lead 8 (bass + lead)"]],
  [["NewAge","Pad 1 (new age)"],["Warm","Pad 2 (warm)"],["Polysyn","Pad 3 (polysynth)"],["Choir","Pad 4 (choir)"],["Bowed","Pad 5 (bowed)"],["Metallic","Pad 6 (metallic)"],["Halo","Pad 7 (halo)"],["Sweep","Pad 8 (sweep)"]],
  [["Rain","FX 1 (rain)"],["Soundtrk","FX 2 (soundtrack)"],["Crystal","FX 3 (crystal)"],["Atmosphr","FX 4 (atmosphere)"],["Bright","FX 5 (brightness)"],["Goblins","FX 6 (goblins)"],["Echoes","FX 7 (echoes)"],["Sci-Fi","FX 8 (sci-fi)"]],
  [["Sitar","Sitar"],["Banjo","Banjo"],["Shamisen","Shamisen"],["Koto","Koto"],["Kalimba","Kalimba"],["Bagpipe","Bagpipe"],["Fiddle","Fiddle"],["Shanai","Shanai"]],
  [["TinklBel","Tinkle Bell"],["Agogo","Agogo"],["SteelDrm","Steel Drums"],["WoodBlk","Woodblock"],["Taiko","Taiko Drum"],["MelodTom","Melodic Tom"],["SynthDrm","Synth Drum"],["RevCym","Reverse Cymbal"]],
  [["GtrFret","Guitar Fret Noise"],["Breath","Breath Noise"],["Seashore","Seashore"],["Birds","Bird Tweet"],["Phone","Telephone Ring"],["Helicptr","Helicopter"],["Applause","Applause"],["Gunshot","Gunshot"]],
];

// 8 percussion kits: [abbr, fullName, bank, program]
const PERCUSSION_KITS = [
  ["P.Std",  "Standard Kit",   128, 0],
  ["P.Room", "Room Kit",       128, 8],
  ["P.Power","Power Kit",      128, 16],
  ["P.Elec", "Electronic Kit", 128, 24],
  ["P.808",  "TR-808 Kit",     128, 25],
  ["P.Jazz", "Jazz Kit",       128, 32],
  ["P.Brush","Brush Kit",      128, 40],
  ["P.Orch", "Orchestra Kit",  128, 48],
];

// 47 percussion sounds [abbr, midiNote]; 2 null pads fill a 7×7=49-cell grid.
const PERCUSSION_NOTES_PADDED = [
  ["AcsBassDr",35],["BassDrum1",36],["SideStick",37],["AcsSnare",38],
  ["HandClap",39],["ElSnare",40],["LowFlrTom",41],
  ["ClsdHiHat",42],["HiFlrTom",43],["PdlHiHat",44],["LowTom",45],
  ["OpnHiHat",46],["LowMidTm",47],["HiMidTm",48],
  ["CrshCym1",49],["HighTom",50],["RideCym1",51],["ChnsCym",52],
  ["RideBell",53],["Tambrne",54],["SplshCym",55],
  ["Cowbell",56],["CrshCym2",57],["Vibraslp",58],["RideCym2",59],
  ["HiBongo",60],["LowBongo",61],["MuteHiCg",62],
  ["OpnHiCg",63],["LowConga",64],["HiTimbal",65],["LowTimbl",66],
  ["HiAgogo",67],["LowAgogo",68],["Cabasa",69],
  ["Maracas",70],["ShrtWhis",71],["LongWhis",72],["ShrtGuir",73],
  ["LongGuir",74],["Claves",75],["HiWoodBk",76],
  ["LoWoodBk",77],["MuteCuca",78],["OpnCuica",79],["MuteTri",80],
  ["OpenTri",81],null,null,
];

// ── Sample rate for audition (must match the server constant) ─────────────────
const AUDITION_SAMPLE_RATE = 48000;
const AUDITION_FADE_FRAMES = Math.round(AUDITION_SAMPLE_RATE * 0.02); // 20 ms

// ── Web Audio playback state ──────────────────────────────────────────────────
let _soundToken = 0;
let _audioCtx = null;
let _currentSource = null;
let _statusEl = null;

function _acquireAudioContext() {
  if (!_audioCtx) {
    _audioCtx = new AudioContext({ sampleRate: AUDITION_SAMPLE_RATE });
  }
  if (_audioCtx.state === 'suspended') {
    _audioCtx.resume();
  }
  return _audioCtx;
}

function _cancelInFlightSound() {
  if (_currentSource) {
    try { _currentSource.stop(); } catch (_) { /* already stopped */ }
    _currentSource = null;
  }
}

// ── Web Audio PCM playback helper ─────────────────────────────────────────────

function playPcmArrayBuffer(arrayBuf, statusEl, playingLabel, doneLabel) {
  const frameCount = frameCountFromByteLength(arrayBuf.byteLength);
  const interleaved = new Float32Array(arrayBuf);
  const channels = deinterleave(interleaved);
  const faded = applyFadeInOut(channels, AUDITION_FADE_FRAMES, AUDITION_FADE_FRAMES);

  const ctx = _acquireAudioContext();
  const audioBuf = ctx.createBuffer(2, frameCount, AUDITION_SAMPLE_RATE);
  faded.forEach((ch, i) => audioBuf.copyToChannel(ch, i));

  _cancelInFlightSound();
  const src = ctx.createBufferSource();
  src.buffer = audioBuf;
  src.connect(ctx.destination);
  _currentSource = src;
  statusEl.textContent = playingLabel;
  src.onended = () => {
    if (_currentSource === src) {
      _currentSource = null;
      statusEl.textContent = doneLabel;
    }
  };
  src.start();
}

// ── Audition helper — POST one row to /music/audition ────────────────────────

async function auditionRow(idx) {
  const token = nextAuditionToken(_soundToken);
  _soundToken = token;
  _cancelInFlightSound();
  try {
    const resp = await fetch('/music/audition', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_rows[idx]),
    });
    if (!isCurrent(token, _soundToken)) return;
    if (!resp.ok) {
      if (_statusEl) _statusEl.textContent = `Audition error: ${resp.status}`;
      return;
    }
    const buf = await resp.arrayBuffer();
    if (!isCurrent(token, _soundToken)) return;
    playPcmArrayBuffer(buf, _statusEl, 'Auditioning…', 'Audition done.');
  } catch (e) {
    if (_statusEl) _statusEl.textContent = `Audition failed: ${e.message}`;
  }
}

// ── Play transport (POST → float32 PCM → Web Audio) ──────────────────────────

async function startPlay(textarea, statusEl) {
  const token = nextAuditionToken(_soundToken);
  _soundToken = token;
  _cancelInFlightSound();
  statusEl.textContent = 'Rendering…';
  try {
    const resp = await fetch('/music/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: textarea.value, caret: textarea.selectionStart }),
    });
    if (!isCurrent(token, _soundToken)) return;
    if (!resp.ok) { statusEl.textContent = `Play error: ${resp.status}`; return; }
    const buf = await resp.arrayBuffer();
    if (!isCurrent(token, _soundToken)) return;
    playPcmArrayBuffer(buf, statusEl, 'Playing…', 'Playback done.');
  } catch (err) {
    statusEl.textContent = `Play failed: ${err.message}`;
  }
}

function stopPlay(statusEl) {
  _cancelInFlightSound();
  statusEl.textContent = 'Stopped.';
}

// ── Score editor — parse on change ───────────────────────────────────────────

let _parseDebounceId = null;

function scheduleParseUpdate(textarea, parseResultEl) {
  clearTimeout(_parseDebounceId);
  _parseDebounceId = setTimeout(() => runParse(textarea, parseResultEl), 600);
}

async function runParse(textarea, parseResultEl) {
  const text = textarea.value;
  try {
    const resp = await fetch('/music/parse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    const data = await resp.json();
    renderParseResult(data, parseResultEl);
  } catch (err) {
    parseResultEl.textContent = `Parse error: ${err.message}`;
  }
}

function renderParseResult(data, el) {
  if (!data) { el.textContent = ''; return; }
  const lines = [];
  const errors = data.errors || [];
  if (errors.length > 0) {
    lines.push(`Errors: ${errors.map(e => e.message).join('; ')}`);
  }
  const sections = data.sections || {};
  const beatCounts = data.beat_counts || {};
  for (const [name, sec] of Object.entries(sections)) {
    const beats = beatCounts[name] ?? '?';
    const evCount = (sec.events || []).length;
    const secErrors = (sec.errors || []).map(e => e.message).join('; ');
    lines.push(`[${name}] ${evCount} events, ${beats} beats${secErrors ? ' — ' + secErrors : ''}`);
  }
  el.textContent = lines.join('\n') || 'No sections.';
}

// ── Score files — disk-backed persistence (design/music-web-files.md) ────────
// State: the current file name; all score text and view state (caret/scroll)
// live on disk via these routes, never in localStorage.

let _currentFile = null;
let _scoreSaveDebounceId = null;
let _sessionSaveDebounceId = null;
let _pendingScore = null;   // { name, text } awaiting PUT, or null
let _pendingSession = null; // { caret, scroll } awaiting POST, or null

async function fetchScoresList() {
  const resp = await fetch('/music/scores');
  return resp.json();
}

async function fetchScoreText(name) {
  const resp = await fetch('/music/score?name=' + encodeURIComponent(name));
  return resp.json();
}

async function putScore(name, text, makeCurrent = false) {
  try {
    await fetch('/music/score', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, text, make_current: makeCurrent }),
    });
  } catch (_) { /* best-effort */ }
}

async function postNewScore(name) {
  const resp = await fetch('/music/scores/new', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  let body = null;
  try { body = await resp.json(); } catch (_) { /* no body */ }
  return { status: resp.status, body };
}

async function postSession(patch) {
  try {
    await fetch('/music/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
  } catch (_) { /* best-effort */ }
}

function setSaveIndicator(saveIndicator, text) {
  if (saveIndicator) saveIndicator.textContent = text;
}

function scheduleScoreAutosave(textarea, saveIndicator) {
  _pendingScore = { name: _currentFile, text: textarea.value };
  setSaveIndicator(saveIndicator, 'saving…');
  clearTimeout(_scoreSaveDebounceId);
  _scoreSaveDebounceId = setTimeout(async () => {
    const toSave = _pendingScore;
    _pendingScore = null;
    await putScore(toSave.name, toSave.text);
    setSaveIndicator(saveIndicator, 'saved ✓');
  }, 300);
}

function scheduleSessionAutosave(textarea) {
  _pendingSession = { caret: textarea.selectionStart, scroll: textarea.scrollTop };
  clearTimeout(_sessionSaveDebounceId);
  _sessionSaveDebounceId = setTimeout(async () => {
    const toSave = _pendingSession;
    _pendingSession = null;
    await postSession(toSave);
  }, 300);
}

function flushPendingWritesOnExit(textarea) {
  clearTimeout(_scoreSaveDebounceId);
  clearTimeout(_sessionSaveDebounceId);
  if (_pendingScore) {
    const toSave = _pendingScore;
    _pendingScore = null;
    fetch('/music/score', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: toSave.name, text: toSave.text }),
      keepalive: true,
    }).catch(() => {});
  }
  if (_pendingSession) {
    const toSave = _pendingSession;
    _pendingSession = null;
    fetch('/music/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(toSave),
      keepalive: true,
    }).catch(() => {});
  }
}

function populateScoreSelect(scoreSelect, names, current) {
  if (!scoreSelect) return;
  const uniqueNames = names.includes(current) ? names : [...names, current];
  scoreSelect.innerHTML = '';
  uniqueNames.forEach(name => {
    const opt = document.createElement('option');
    opt.value = name;
    opt.textContent = name;
    scoreSelect.appendChild(opt);
  });
  scoreSelect.value = current;
}

// Resolve + open the startup score per design/music-web-files.md decision #3,
// restoring caret/scroll only when the resolved file matches the session's
// last-open file.
async function bootstrapScoreFile(textarea, scoreSelect) {
  const data = await fetchScoresList();
  const files = data.files || [];
  const sessionCurrent = data.current;

  let resolvedName;
  if (sessionCurrent && files.includes(sessionCurrent)) {
    resolvedName = sessionCurrent;
  } else if (files.length) {
    resolvedName = files[0];
  } else {
    const { status, body } = await postNewScore('untitled.music');
    resolvedName = status === 200 ? body.name : 'untitled.music';
  }

  _currentFile = resolvedName;

  const scoreData = await fetchScoreText(resolvedName);
  textarea.value = scoreData.text;

  populateScoreSelect(scoreSelect, files, resolvedName);

  if (resolvedName === sessionCurrent) {
    textarea.setSelectionRange(data.caret, data.caret);
    textarea.scrollTop = data.scroll;
  } else {
    textarea.setSelectionRange(0, 0);
    textarea.scrollTop = 0;
  }
}

// Switch the editor to a different on-disk score (the switcher's change event).
async function switchToScore(newName, textarea, parseResultEl, saveIndicator) {
  if (!newName || newName === _currentFile) return;
  clearTimeout(_scoreSaveDebounceId);
  clearTimeout(_sessionSaveDebounceId);
  const outgoingName = _currentFile;
  _pendingScore = null;
  _pendingSession = null;

  await putScore(outgoingName, textarea.value);
  const data = await fetchScoreText(newName);
  await postSession({ current: newName });

  _currentFile = newName;
  textarea.value = data.text;
  textarea.setSelectionRange(0, 0);
  textarea.scrollTop = 0;
  setSaveIndicator(saveIndicator, '');
  runParse(textarea, parseResultEl);
}

// New-score name-or-cancel dialog (design/music-web-files.md decision #6);
// re-prompts on a 409 name clash.
async function createNewScore(scoreSelect, textarea, parseResultEl, saveIndicator) {
  let name = prompt('New score name:');
  while (name) {
    const { status, body } = await postNewScore(name);
    if (status === 200) {
      clearTimeout(_scoreSaveDebounceId);
      clearTimeout(_sessionSaveDebounceId);
      _pendingScore = null;
      _pendingSession = null;
      _currentFile = body.name;
      populateScoreSelect(scoreSelect, [...(scoreSelect ? Array.from(scoreSelect.options).map(o => o.value) : []), body.name], body.name);
      textarea.value = '';
      textarea.setSelectionRange(0, 0);
      textarea.scrollTop = 0;
      setSaveIndicator(saveIndicator, '');
      runParse(textarea, parseResultEl);
      return;
    }
    if (status === 409) {
      name = prompt('Name already exists. Choose another:');
      continue;
    }
    setSaveIndicator(saveIndicator, `New failed: ${status}`);
    return;
  }
}

// ── Instruments panel ─────────────────────────────────────────────────────────
// State: array of row objects persisted to/from /music/instruments.

let _rows = [];  // full schema — see _DEFAULT_ROW

const _DEFAULT_ROW = () => ({
  name: '', pitch: 'C4', velocity: 100, duration_s: 1.0,
  bank: 0, program: 0, is_percussion: false, drum_note: null,
  instrument_label: 'Ac.Grand',
  volume: 100, pan: 64, reverb: 40, chorus: 0,
  vibrato_depth: 0, vibrato_rate_hz: null, vibrato_delay_s: 0.01,
  attack_s: 0.01, decay_s: 0.1, sustain_pct: 100, release_s: 0.1,
  expr_repeat_s: 1.0, brightness_repeat_s: 1.0, bend_repeat_s: 1.0,
});

async function loadInstruments(panelEl) {
  try {
    const resp = await fetch('/music/instruments');
    const data = await resp.json();
    _rows = (Array.isArray(data) ? data : []).map(r => ({ ..._DEFAULT_ROW(), ...r }));
  } catch (_) {
    _rows = [];
  }
  renderInstrumentRows(panelEl);
}

async function persistInstruments() {
  try {
    await fetch('/music/instruments', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(_rows),
    });
  } catch (_) { /* best-effort */ }
}

// ── Instrument row rendering ──────────────────────────────────────────────────

function renderInstrumentRows(panelEl) {
  const listEl = panelEl.querySelector('.inst-list');
  listEl.innerHTML = '';
  _rows.forEach((row, idx) => {
    listEl.appendChild(buildInstrumentRowEl(row, idx, panelEl));
  });
}

function buildInstrumentRowEl(row, idx, panelEl) {
  const rowEl = document.createElement('div');
  rowEl.className = 'inst-row';

  // Name input — no audition on name change
  const nameIn = document.createElement('input');
  nameIn.className = 'inst-name';
  nameIn.type = 'text';
  nameIn.value = row.name;
  nameIn.placeholder = 'Name';
  nameIn.addEventListener('change', () => {
    _rows[idx].name = nameIn.value;
    persistInstruments();
  });

  // Instrument label (shows current program name or kit name)
  const labelEl = document.createElement('span');
  labelEl.className = 'inst-label';
  labelEl.textContent = instrumentDisplayLabel(row);

  // Edit button — opens stay-open editor
  const editBtn = document.createElement('button');
  editBtn.className = 'inst-pick-btn';
  editBtn.textContent = 'Edit ▾';
  editBtn.addEventListener('click', () => openInstrumentEditor(idx, panelEl));

  // Remove button
  const removeBtn = document.createElement('button');
  removeBtn.className = 'inst-remove-btn';
  removeBtn.textContent = '✕';
  removeBtn.title = 'Remove instrument';
  removeBtn.addEventListener('click', () => {
    _rows.splice(idx, 1);
    persistInstruments();
    renderInstrumentRows(panelEl);
  });

  rowEl.append(nameIn, labelEl, editBtn, removeBtn);
  return rowEl;
}

function instrumentDisplayLabel(row) {
  if (row.is_percussion) {
    return row.instrument_label || 'Percussion';
  }
  const rowIdx = Math.floor((row.program || 0) / 8);
  const colIdx = (row.program || 0) % 8;
  return (MELODIC_GRID[rowIdx] && MELODIC_GRID[rowIdx][colIdx])
    ? MELODIC_GRID[rowIdx][colIdx][0]
    : 'Unknown';
}

// ── Instrument editor modal (stay-open) ───────────────────────────────────────

function closeInstrumentEditor() {
  const existing = document.getElementById('inst-editor-modal');
  if (existing) existing.remove();
  closePercussionPicker();
}

function closePercussionPicker() {
  const percModal = document.getElementById('perc-picker-modal');
  if (percModal) percModal.remove();
}

// closePicker kept for curve-editor compatibility (was the old name)
function closePicker() {
  closeInstrumentEditor();
}

function openInstrumentEditor(idx, panelEl) {
  closeInstrumentEditor();

  const modal = document.createElement('div');
  modal.id = 'inst-editor-modal';
  modal.className = 'picker-modal inst-editor-modal';

  // ── Header ──
  const header = document.createElement('div');
  header.className = 'picker-header';
  const headerTitle = document.createElement('span');
  headerTitle.textContent = 'Edit Instrument';
  const closeBtn = document.createElement('button');
  closeBtn.className = 'picker-close';
  closeBtn.textContent = '✕';
  closeBtn.addEventListener('click', closeInstrumentEditor);
  header.append(headerTitle, closeBtn);
  modal.appendChild(header);

  // ── Per-note row ──
  const perNoteRow = document.createElement('div');
  perNoteRow.className = 'editor-row editor-per-note-row';

  // Name
  const nameGroup = document.createElement('label');
  nameGroup.className = 'editor-field-group';
  const nameLabel = document.createElement('span');
  nameLabel.textContent = 'Name';
  const nameIn = document.createElement('input');
  nameIn.type = 'text';
  nameIn.className = 'editor-text-input';
  nameIn.value = _rows[idx].name;
  nameIn.placeholder = 'Name';
  nameIn.addEventListener('change', () => {
    _rows[idx].name = nameIn.value;
    persistInstruments();
    // update strip name
    const stripName = _stripNameInputFor(idx);
    if (stripName) stripName.value = nameIn.value;
  });
  nameGroup.append(nameLabel, nameIn);

  // Pitch
  const pitchGroup = document.createElement('label');
  pitchGroup.className = 'editor-field-group';
  const pitchLabel = document.createElement('span');
  pitchLabel.textContent = 'Pitch';
  const pitchIn = document.createElement('input');
  pitchIn.type = 'text';
  pitchIn.className = 'editor-text-input';
  pitchIn.value = _rows[idx].pitch || 'C4';
  if (_rows[idx].is_percussion) {
    pitchIn.value = _rows[idx].instrument_label || 'Percussion';
    pitchIn.readOnly = true;
    pitchIn.style.opacity = '0.6';
  }
  const auditOnPitchChange = () => {
    if (!_rows[idx].is_percussion) {
      _rows[idx].pitch = pitchIn.value;
      persistInstruments();
      auditionRow(idx);
    }
  };
  pitchIn.addEventListener('change', auditOnPitchChange);
  pitchIn.addEventListener('keydown', e => { if (e.key === 'Enter') auditOnPitchChange(); });
  pitchGroup.append(pitchLabel, pitchIn);

  // Velocity
  const velGroup = buildSliderGroup('Vel', 0, 127, 1, _rows[idx].velocity, (v) => {
    _rows[idx].velocity = v;
    persistInstruments();
    auditionRow(idx);
  });

  // Duration
  const durGroup = buildSliderGroup('Dur', 0, 4, 0.1, _rows[idx].duration_s, (v) => {
    _rows[idx].duration_s = v;
    persistInstruments();
    auditionRow(idx);
  }, true);

  perNoteRow.append(nameGroup, pitchGroup, velGroup, durGroup);
  modal.appendChild(perNoteRow);

  // ── Channel basics row ──
  const channelRow = document.createElement('div');
  channelRow.className = 'editor-row editor-channel-row';

  channelRow.appendChild(buildSliderGroup('Volume', 0, 127, 1, _rows[idx].volume, (v) => {
    _rows[idx].volume = v;
    persistInstruments();
    auditionRow(idx);
  }));
  channelRow.appendChild(buildSliderGroup('Pan', 0, 127, 1, _rows[idx].pan, (v) => {
    _rows[idx].pan = v;
    persistInstruments();
    auditionRow(idx);
  }));
  channelRow.appendChild(buildSliderGroup('Reverb', 0, 127, 1, _rows[idx].reverb, (v) => {
    _rows[idx].reverb = v;
    persistInstruments();
    auditionRow(idx);
  }));
  channelRow.appendChild(buildSliderGroup('Chorus', 0, 127, 1, _rows[idx].chorus, (v) => {
    _rows[idx].chorus = v;
    persistInstruments();
    auditionRow(idx);
  }));

  modal.appendChild(channelRow);

  // ── Advanced controls (Vibrato / ADSR / 2D curves) ───────────────────────────
  const advancedPlaceholder = document.createElement('div');
  advancedPlaceholder.className = 'editor-advanced';

  // ── Vibrato line ──
  const vibratoRow = document.createElement('div');
  vibratoRow.className = 'editor-row editor-advanced-row';

  const vibratoLabel = document.createElement('span');
  vibratoLabel.className = 'editor-advanced-section-label';
  vibratoLabel.textContent = 'Vibrato';
  vibratoRow.appendChild(vibratoLabel);

  vibratoRow.appendChild(buildSliderGroup('Vib Depth', 0, 127, 1, _rows[idx].vibrato_depth, (v) => {
    _rows[idx].vibrato_depth = v;
    persistInstruments();
    auditionRow(idx);
  }));
  vibratoRow.appendChild(buildExpSliderGroup('Rate Hz', 100, 0.1, _rows[idx].vibrato_rate_hz, (v) => {
    _rows[idx].vibrato_rate_hz = v;
    persistInstruments();
    auditionRow(idx);
  }));
  vibratoRow.appendChild(buildExpSliderGroup('Delay s', 0.01, 10, _rows[idx].vibrato_delay_s, (v) => {
    _rows[idx].vibrato_delay_s = v;
    persistInstruments();
    auditionRow(idx);
  }));
  advancedPlaceholder.appendChild(vibratoRow);

  // ── ADSR line ──
  const adsrRow = document.createElement('div');
  adsrRow.className = 'editor-row editor-advanced-row';

  const adsrLabel = document.createElement('span');
  adsrLabel.className = 'editor-advanced-section-label';
  adsrLabel.textContent = 'ADSR';
  adsrRow.appendChild(adsrLabel);

  adsrRow.appendChild(buildExpSliderGroup('Attack s', 0.001, 2, _rows[idx].attack_s, (v) => {
    _rows[idx].attack_s = v;
    persistInstruments();
    auditionRow(idx);
  }));
  adsrRow.appendChild(buildExpSliderGroup('Decay s', 0.01, 4, _rows[idx].decay_s, (v) => {
    _rows[idx].decay_s = v;
    persistInstruments();
    auditionRow(idx);
  }));
  adsrRow.appendChild(buildSliderGroup('Sustain %', 0, 100, 1, _rows[idx].sustain_pct, (v) => {
    _rows[idx].sustain_pct = v;
    persistInstruments();
    auditionRow(idx);
  }));
  adsrRow.appendChild(buildExpSliderGroup('Release s', 0.001, 2, _rows[idx].release_s, (v) => {
    _rows[idx].release_s = v;
    persistInstruments();
    auditionRow(idx);
  }));
  advancedPlaceholder.appendChild(adsrRow);

  // ── 2D CC-curve line ──
  const curvesRow = document.createElement('div');
  curvesRow.className = 'editor-row editor-advanced-row';

  const curvesLabel = document.createElement('span');
  curvesLabel.className = 'editor-advanced-section-label';
  curvesLabel.textContent = 'CC';
  curvesRow.appendChild(curvesLabel);

  // Expression curve + repeat
  const exprGroup = buildCurveButtonGroup(
    'Expression', idx,
    () => valueDictToNormPoints(_rows[idx].expr_curve, 0, 127),
    (pts) => {
      _rows[idx].expr_curve = normPointsToValueDict(pts, 0, 127, '0', '127');
      persistInstruments();
      auditionRow(idx);
    }
  );
  curvesRow.appendChild(exprGroup);
  curvesRow.appendChild(buildExpSliderGroup('Repeat s', 0.05, 10, _rows[idx].expr_repeat_s, (v) => {
    _rows[idx].expr_repeat_s = v;
    persistInstruments();
    auditionRow(idx);
  }));

  // Brightness curve + repeat
  const brightnessGroup = buildCurveButtonGroup(
    'Brightness', idx,
    () => valueDictToNormPoints(_rows[idx].brightness_curve, 0, 127),
    (pts) => {
      _rows[idx].brightness_curve = normPointsToValueDict(pts, 0, 127, '0', '127');
      persistInstruments();
      auditionRow(idx);
    }
  );
  curvesRow.appendChild(brightnessGroup);
  curvesRow.appendChild(buildExpSliderGroup('Repeat s', 0.05, 10, _rows[idx].brightness_repeat_s, (v) => {
    _rows[idx].brightness_repeat_s = v;
    persistInstruments();
    auditionRow(idx);
  }));

  // Bend curve + repeat
  const bendGroup = buildCurveButtonGroup(
    'Bend', idx,
    () => valueDictToNormPoints(_rows[idx].bend_curve, -6, 6),
    (pts) => {
      _rows[idx].bend_curve = normPointsToValueDict(pts, -6, 6, '-6', '+6');
      persistInstruments();
      auditionRow(idx);
    }
  );
  curvesRow.appendChild(bendGroup);
  curvesRow.appendChild(buildExpSliderGroup('Repeat s', 0.05, 10, _rows[idx].bend_repeat_s, (v) => {
    _rows[idx].bend_repeat_s = v;
    persistInstruments();
    auditionRow(idx);
  }));

  advancedPlaceholder.appendChild(curvesRow);
  modal.appendChild(advancedPlaceholder);

  // ── Instrument grid (embedded, stays open) ──
  const gridSection = document.createElement('div');
  gridSection.className = 'editor-grid-section';

  const grid = document.createElement('div');
  grid.className = 'gm-grid';

  MELODIC_GRID.forEach((cols, rowIdx) => {
    cols.forEach((cell, colIdx) => {
      const program = rowIdx * 8 + colIdx;
      const btn = document.createElement('button');
      btn.className = 'gm-cell';
      btn.textContent = cell[0];
      btn.title = cell[1];
      if (!_rows[idx].is_percussion && _rows[idx].program === program && _rows[idx].bank === 0) {
        btn.classList.add('gm-cell-selected');
      }
      btn.addEventListener('click', () => {
        _rows[idx] = { ..._rows[idx], bank: 0, program, is_percussion: false, drum_note: null, instrument_label: cell[0] };
        // Update pitch input (no longer locked to kit abbr)
        pitchIn.readOnly = false;
        pitchIn.style.opacity = '';
        pitchIn.value = _rows[idx].pitch || 'C4';
        persistInstruments();
        auditionRow(idx);
        // Refresh selection highlight in grid without closing editor
        grid.querySelectorAll('.gm-cell-selected').forEach(el => el.classList.remove('gm-cell-selected'));
        btn.classList.add('gm-cell-selected');
        // Update strip label
        updateStripLabel(idx);
        // Close percussion sub-grid if open
        closePercussionPicker();
      });
      grid.appendChild(btn);
    });
  });

  // Percussion kits row
  const percRow = document.createElement('div');
  percRow.className = 'gm-perc-row';
  PERCUSSION_KITS.forEach(([abbr, fullName, bank, program]) => {
    const btn = document.createElement('button');
    btn.className = 'gm-perc-cell';
    btn.textContent = abbr;
    btn.title = fullName;
    if (_rows[idx].is_percussion && _rows[idx].bank === bank && _rows[idx].program === program) {
      btn.classList.add('gm-cell-selected');
    }
    btn.addEventListener('click', () => {
      openPercussionPickerInEditor(idx, panelEl, abbr, fullName, bank, program, pitchIn, grid, percRow);
    });
    percRow.appendChild(btn);
  });

  gridSection.append(grid, percRow);
  modal.appendChild(gridSection);

  document.body.appendChild(modal);
}

// Builds a labeled slider group with live numeric readout.
// onAuditionChange is called with the numeric value on 'change' (mouse-up).
function buildSliderGroup(labelText, min, max, step, initial, onAuditionChange, isFloat) {
  const group = document.createElement('div');
  group.className = 'editor-field-group editor-slider-group';

  const label = document.createElement('span');
  label.className = 'editor-slider-label';
  label.textContent = labelText;

  const slider = document.createElement('input');
  slider.type = 'range';
  slider.min = min;
  slider.max = max;
  slider.step = step;
  slider.value = initial;
  slider.className = 'editor-slider';

  const readout = document.createElement('span');
  readout.className = 'editor-slider-readout';
  readout.textContent = isFloat ? Number(initial).toFixed(1) : initial;

  slider.addEventListener('input', () => {
    readout.textContent = isFloat ? Number(slider.value).toFixed(1) : slider.value;
  });
  slider.addEventListener('change', () => {
    const v = isFloat ? parseFloat(slider.value) : parseInt(slider.value, 10);
    onAuditionChange(v);
  });

  group.append(label, slider, readout);
  return group;
}

// ── Exponential slider helper ─────────────────────────────────────────────────
// Builds a labeled slider where position in [0,1] maps to physical value
// via: physical(pos) = lo * (hi/lo) ** pos   (lo may be > hi, e.g. vibrato rate)
// onAuditionChange(physical) is called on 'change' (mouse-up).
function buildExpSliderGroup(labelText, lo, hi, initialPhysical, onAuditionChange, formatFn) {
  const group = document.createElement('div');
  group.className = 'editor-field-group editor-slider-group';

  const label = document.createElement('span');
  label.className = 'editor-slider-label';
  label.textContent = labelText;

  const slider = document.createElement('input');
  slider.type = 'range';
  slider.min = 0;
  slider.max = 1000;
  slider.step = 1;
  slider.className = 'editor-slider';

  const defaultFormat = (v) => v >= 1 ? v.toFixed(1) : v.toFixed(2);
  const fmt = formatFn || defaultFormat;

  function physicalFromSlider() {
    const pos = parseInt(slider.value, 10) / 1000;
    return lo * Math.pow(hi / lo, pos);
  }

  function sliderPosFromPhysical(v) {
    if (v == null || isNaN(v) || lo <= 0 || hi <= 0 || v <= 0) return 0.5;
    const pos = Math.log(v / lo) / Math.log(hi / lo);
    return Math.max(0, Math.min(1, pos));
  }

  const initPos = sliderPosFromPhysical(initialPhysical);
  slider.value = Math.round(initPos * 1000);

  const readout = document.createElement('span');
  readout.className = 'editor-slider-readout';
  readout.textContent = fmt(physicalFromSlider());

  slider.addEventListener('input', () => {
    readout.textContent = fmt(physicalFromSlider());
  });
  slider.addEventListener('change', () => {
    onAuditionChange(physicalFromSlider());
  });

  group.append(label, slider, readout);
  return group;
}

// Builds a labeled button that opens the curve editor for one automation channel.
// getCurveNormPoints() returns the current normalized points; onSave(pts) receives
// the edited normalized points after the user saves.
function buildCurveButtonGroup(title, _idx, getCurveNormPoints, onSave) {
  const group = document.createElement('div');
  group.className = 'editor-field-group editor-curve-btn-group';

  const btn = document.createElement('button');
  btn.className = 'editor-curve-btn';
  btn.textContent = title + ' ▱';  // ▱
  btn.addEventListener('click', () => {
    openCurveEditor(title, getCurveNormPoints(), onSave);
  });
  group.appendChild(btn);
  return group;
}

// ── Curve normalization adapters ──────────────────────────────────────────────
// Converts between value-unit curve dicts (as stored in _rows) and the
// normalized 0..1 y coordinates used by curve2d / openCurveEditor.

function valueDictToNormPoints(dict, vMin, vMax) {
  if (!dict || !Array.isArray(dict.points) || dict.points.length < 2) {
    // Return flat default at mid-norm
    return [[0.0, 0.5], [1.0, 0.5]];
  }
  return dict.points.map(([x, y]) => [x, (y - vMin) / (vMax - vMin)]);
}

function normPointsToValueDict(points, vMin, vMax, minLabel, maxLabel) {
  const valuePoints = points.map(([x, y]) => [x, vMin + y * (vMax - vMin)]);
  return {
    value_min: vMin,
    value_max: vMax,
    value_min_label: minLabel,
    value_max_label: maxLabel,
    time_left_label: '',
    time_right_label: '',
    initial_left_y: valuePoints[0][1],
    initial_right_y: valuePoints[valuePoints.length - 1][1],
    points: valuePoints,
  };
}

// Opens the percussion sub-grid modal (stays open, auditions on pick).
function openPercussionPickerInEditor(idx, panelEl, kitAbbr, kitFull, kitBank, kitProgram, pitchIn, gridEl, percRowEl) {
  closePercussionPicker();

  const modal = document.createElement('div');
  modal.id = 'perc-picker-modal';
  modal.className = 'picker-modal picker-modal-perc';

  const header = document.createElement('div');
  header.className = 'picker-header';
  header.textContent = `${kitAbbr} — ${kitFull}`;
  modal.appendChild(header);

  const grid = document.createElement('div');
  grid.className = 'perc-grid';

  PERCUSSION_NOTES_PADDED.forEach(entry => {
    const btn = document.createElement('button');
    btn.className = 'perc-cell';
    if (entry === null) {
      btn.className += ' perc-cell-blank';
      btn.disabled = true;
      btn.textContent = '';
    } else {
      const [abbr, midiNote] = entry;
      btn.textContent = abbr;
      btn.title = `MIDI ${midiNote}`;
      if (_rows[idx].is_percussion && _rows[idx].bank === kitBank && _rows[idx].program === kitProgram && _rows[idx].drum_note === midiNote) {
        btn.classList.add('gm-cell-selected');
      }
      btn.addEventListener('click', () => {
        const label = `${kitAbbr}: ${abbr}`;
        _rows[idx] = {
          ..._rows[idx],
          bank: kitBank,
          program: kitProgram,
          is_percussion: true,
          drum_note: midiNote,
          instrument_label: label,
        };
        // Lock pitch box to kit abbr
        pitchIn.value = label;
        pitchIn.readOnly = true;
        pitchIn.style.opacity = '0.6';
        persistInstruments();
        auditionRow(idx);
        // Refresh selection in perc grid
        grid.querySelectorAll('.gm-cell-selected').forEach(el => el.classList.remove('gm-cell-selected'));
        btn.classList.add('gm-cell-selected');
        // Clear melodic selection highlight, highlight perc kit
        gridEl.querySelectorAll('.gm-cell-selected').forEach(el => el.classList.remove('gm-cell-selected'));
        percRowEl.querySelectorAll('.gm-cell-selected').forEach(el => el.classList.remove('gm-cell-selected'));
        const kitBtns = percRowEl.querySelectorAll('.gm-perc-cell');
        kitBtns.forEach(kb => {
          if (kb.textContent === kitAbbr) kb.classList.add('gm-cell-selected');
        });
        // Update strip label
        updateStripLabel(idx);
      });
    }
    grid.appendChild(btn);
  });

  modal.appendChild(grid);
  document.body.appendChild(modal);
}

// Updates the instrument label in the docked strip for a given row index.
function updateStripLabel(idx) {
  const listEl = document.querySelector('.inst-list');
  if (!listEl) return;
  const rowEls = listEl.querySelectorAll('.inst-row');
  if (rowEls[idx]) {
    const labelEl = rowEls[idx].querySelector('.inst-label');
    if (labelEl) labelEl.textContent = instrumentDisplayLabel(_rows[idx]);
  }
}

// Returns the name <input> in the strip for a given row index (for sync).
function _stripNameInputFor(idx) {
  const listEl = document.querySelector('.inst-list');
  if (!listEl) return null;
  const rowEls = listEl.querySelectorAll('.inst-row');
  if (rowEls[idx]) return rowEls[idx].querySelector('.inst-name');
  return null;
}

// ── Curve-edit popup (CC automation via curve2d.mjs) ─────────────────────────

// Builds a Curve2D editor instance over the normalized 0..1 value range from
// a normalized points array (endpoints at pts[0]/pts[pts.length-1], interior
// points inserted in order).
function buildEditorCurveFromNormPoints(pts) {
  const curve = new Curve2D({
    valueMin: 0, valueMax: 1,
    valueMinLabel: '', valueMaxLabel: '',
    timeLeftLabel: '', timeRightLabel: '',
    initialLeftY: pts[0][1],
    initialRightY: pts[pts.length - 1][1],
    id: 'curve-editor',
  });
  for (let i = 1; i < pts.length - 1; i++) {
    curve.insert(pts[i][0], pts[i][1]);
  }
  return curve;
}

function openCurveEditor(title, curveData, onSave) {
  const existing = document.getElementById('curve-editor-modal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'curve-editor-modal';
  modal.className = 'picker-modal curve-editor-modal';

  const header = document.createElement('div');
  header.className = 'picker-header';
  header.textContent = title;

  const closeBtn = document.createElement('button');
  closeBtn.className = 'picker-close';
  closeBtn.textContent = '✕';
  closeBtn.addEventListener('click', () => modal.remove());
  header.appendChild(closeBtn);
  modal.appendChild(header);

  // Canvas for curve editing
  const canvas = document.createElement('canvas');
  canvas.width = 400;
  canvas.height = 160;
  canvas.className = 'curve-canvas';
  modal.appendChild(canvas);

  // Instruction
  const hint = document.createElement('p');
  hint.className = 'curve-hint';
  hint.textContent = 'Click to add points. Drag to move. Right-click to remove.';
  modal.appendChild(hint);

  // Initialise the single Curve2D instance shared by buttons + gestures for
  // the lifetime of this editor.
  const curve = buildEditorCurveFromNormPoints(
    curveData && curveData.length >= 2 ? curveData : [[0, 0.5], [1, 0.5]]
  );

  // Save / Reset buttons
  const btnRow = document.createElement('div');
  btnRow.className = 'curve-btn-row';

  const resetBtn = document.createElement('button');
  resetBtn.textContent = 'Reset';
  resetBtn.addEventListener('click', () => {
    curve.reset();
    renderCurveCanvas(canvas, curve);
  });

  const saveBtn = document.createElement('button');
  saveBtn.textContent = 'Save';
  saveBtn.addEventListener('click', () => {
    onSave(curve.points);
    modal.remove();
  });

  btnRow.append(resetBtn, saveBtn);
  modal.appendChild(btnRow);
  document.body.appendChild(modal);

  renderCurveCanvas(canvas, curve);
  attachCurveGestures(canvas, curve, () => renderCurveCanvas(canvas, curve));
}

function renderCurveCanvas(canvas, curve) {
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  // Background
  ctx.fillStyle = '#1a1a1a';
  ctx.fillRect(0, 0, w, h);

  // Mid-line guide
  ctx.strokeStyle = '#444444';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, h / 2);
  ctx.lineTo(w, h / 2);
  ctx.stroke();

  const segs = buildSegments(curve.points, canvas.width);
  if (segs.length === 0) return;

  // Curve line
  ctx.strokeStyle = '#00d4ff';
  ctx.lineWidth = 2;
  ctx.beginPath();
  const steps = 200;
  for (let i = 0; i <= steps; i++) {
    const t = i / steps;
    const v = evaluate(segs, t);
    const px = t * w;
    const py = (1 - v) * h;
    if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
  }
  ctx.stroke();

  // Control points
  ctx.fillStyle = '#ffffff';
  for (const [x, y] of curve.points) {
    ctx.beginPath();
    ctx.arc(x * w, (1 - y) * h, 5, 0, Math.PI * 2);
    ctx.fill();
  }
}

function attachCurveGestures(canvas, curve, onUpdate) {
  let dragging = null;
  let phantom = null;

  // Clamp v into [lo, hi].
  function clampUnit(v, lo, hi) {
    return Math.max(lo, Math.min(hi, v));
  }

  // Clamp a y coordinate into the curve's [0,1] value range.
  function clampY(v) {
    return clampUnit(v, 0, 1);
  }

  function nearestPoint(cx, cy) {
    const w = canvas.width;
    const h = canvas.height;
    const HIT_RADIUS_PX = 12;
    let best = null;
    let bestDist = Infinity;
    curve.points.forEach(([x, y], i) => {
      const dx = x * w - cx;
      const dy = (1 - y) * h - cy;
      const dist = Math.sqrt(dx * dx + dy * dy);
      if (dist < HIT_RADIUS_PX && dist < bestDist) {
        best = i;
        bestDist = dist;
      }
    });
    return best;
  }

  canvas.addEventListener('mousedown', e => {
    const rect = canvas.getBoundingClientRect();
    const cx = (e.clientX - rect.left) * (canvas.width / rect.width);
    const cy = (e.clientY - rect.top) * (canvas.height / rect.height);

    if (e.button === 2) {
      // Right-click: remove an interior point (endpoints are refused).
      const idx = nearestPoint(cx, cy);
      if (idx !== null && idx !== 0 && idx !== curve.points.length - 1) {
        curve.remove(idx);
        onUpdate();
      }
      return;
    }

    const idx = nearestPoint(cx, cy);
    if (idx !== null) {
      dragging = idx;
      return;
    }

    // Add a new point on empty canvas.
    const nx = clampUnit(cx / canvas.width, 0.001, 0.999);
    const ny = clampY(1 - cy / canvas.height);
    try {
      const newIdx = curve.insertWithEditorWidth(nx, ny, canvas.width, { source: 'editor' });
      dragging = newIdx;
      onUpdate();
    } catch (err) {
      if (!(err instanceof RangeError)) throw err;
      // Collision with an existing point at this edit-pixel: no-op.
    }
  });

  canvas.addEventListener('mousemove', e => {
    if (dragging === null && phantom === null) return;

    const rect = canvas.getBoundingClientRect();
    const cx = (e.clientX - rect.left) * (canvas.width / rect.width);
    const cy = (e.clientY - rect.top) * (canvas.height / rect.height);
    const rawX = cx / canvas.width;
    const rawY = 1 - cy / canvas.height;

    if (dragging !== null) {
      const isEndpoint = dragging === 0 || dragging === curve.points.length - 1;
      if (isEndpoint) {
        curve.move(dragging, curve.points[dragging][0], clampY(rawY), { source: 'editor' });
        onUpdate();
        return;
      }

      if (rawY >= 0 && rawY <= 1) {
        const eps = 1e-4;
        const xClamped = Math.max(
          curve.points[dragging - 1][0] + eps,
          Math.min(curve.points[dragging + 1][0] - eps, rawX)
        );
        curve.move(dragging, xClamped, clampY(rawY), { source: 'editor' });
        onUpdate();
        return;
      }

      // Dragged past the top/bottom edge: stash as a phantom and remove.
      phantom = { x: curve.points[dragging][0], y: curve.points[dragging][1] };
      curve.remove(dragging, { source: 'editor' });
      dragging = null;
      onUpdate();
      return;
    }

    if (phantom !== null && rawY >= 0 && rawY <= 1) {
      const xClamped = clampUnit(rawX, 0.001, 0.999);
      const yClamped = clampY(rawY);
      try {
        const back = curve.insertWithEditorWidth(xClamped, yClamped, canvas.width, { source: 'editor' });
        dragging = back;
        phantom = null;
        onUpdate();
      } catch (err) {
        if (!(err instanceof RangeError)) throw err;
        // Collision: stay removed (phantom).
      }
    }
  });

  canvas.addEventListener('mouseup', () => { dragging = null; phantom = null; });
  canvas.addEventListener('mouseleave', () => { dragging = null; phantom = null; });
  canvas.addEventListener('contextmenu', e => e.preventDefault());
}

// ── Main init ─────────────────────────────────────────────────────────────────

export async function init({ textarea, parseResultEl, statusEl, panelEl, scoreSelect, newBtn, saveIndicator }) {
  _statusEl = statusEl;

  // Resolve + open the startup score from disk (design/music-web-files.md).
  await bootstrapScoreFile(textarea, scoreSelect);
  runParse(textarea, parseResultEl);

  // Score editor — parse on change + short-debounce autosave to disk
  textarea.addEventListener('input', () => {
    scheduleParseUpdate(textarea, parseResultEl);
    scheduleScoreAutosave(textarea, saveIndicator);
  });

  // View-state (caret + scroll) short-debounce autosave — session-only, cheap.
  document.addEventListener('selectionchange', () => {
    if (document.activeElement === textarea) scheduleSessionAutosave(textarea);
  });
  textarea.addEventListener('keyup', () => scheduleSessionAutosave(textarea));
  textarea.addEventListener('click', () => scheduleSessionAutosave(textarea));
  textarea.addEventListener('scroll', () => scheduleSessionAutosave(textarea));

  // Exit flush — replaces the Save button (decision #2, no confirmation dialog).
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushPendingWritesOnExit(textarea);
  });
  window.addEventListener('pagehide', () => flushPendingWritesOnExit(textarea));

  // Score switcher
  if (scoreSelect) {
    scoreSelect.addEventListener('change', () => {
      switchToScore(scoreSelect.value, textarea, parseResultEl, saveIndicator);
    });
  }

  // New button
  if (newBtn) {
    newBtn.addEventListener('click', () => {
      createNewScore(scoreSelect, textarea, parseResultEl, saveIndicator);
    });
  }

  // Play button
  const playBtn = document.getElementById('btn-play');
  if (playBtn) {
    playBtn.addEventListener('click', () => startPlay(textarea, statusEl));
  }

  // Stop button
  const stopBtn = document.getElementById('btn-stop');
  if (stopBtn) {
    stopBtn.addEventListener('click', () => stopPlay(statusEl));
  }

  // FLAC button
  const flacBtn = document.getElementById('btn-flac');
  if (flacBtn) {
    flacBtn.addEventListener('click', e => {
      e.preventDefault();
      window.location.href = '/music/flac?text=' + encodeURIComponent(textarea.value);
    });
  }

  // Instruments panel — add row button
  const addRowBtn = panelEl.querySelector('.inst-add-btn');
  if (addRowBtn) {
    addRowBtn.addEventListener('click', () => {
      _rows.push(_DEFAULT_ROW());
      persistInstruments();
      renderInstrumentRows(panelEl);
    });
  }

  // Load instruments on startup
  loadInstruments(panelEl);
}
