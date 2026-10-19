import {FrameLoop, fitRect, projectBox, freshOverlay} from './camera-core.js';

const $ = id => document.getElementById(id);
const video = $('video'), liveCanvas = $('overlay'), snapshot = $('snapshot');
let config = null, stream = null, cameraPending = false, cameraEpoch = 0;
let animation = null, lastPrediction = null, lastFrame = null, lastBudget = null;
let fatalInference = false, lastAgeUpdate = 0;
const labels = {
  inference_in_progress: '前のフレームをサーバーで処理中です。完了後に手動で再開してください。',
  sampling_interval: '送信間隔が短すぎます。少し待ってから再開してください。',
  request_limit_reached: 'この起動のAPI回数上限に達しました。推論を停止しました。',
  estimated_budget_reservation: '次の1件を予約できる費用枠がありません。推論を停止しました。',
  estimated_budget_exceeded: '見積費用の上限に達しました。推論を停止しました。',
  provider_stopped_no_retry: 'Jevへの送信に失敗しました。自動再試行はしません。使用量を確認してください。',
  invalid_model_answer: 'モデル応答を検証できませんでした。推論を停止しました。',
  invalid_frame_or_request: 'カメラフレームを処理できませんでした。推論を停止しました。',
  frame_size_limit: 'フレームがサイズ上限を超えました。',
  local_camera_token_required: 'ローカル接続を確認できません。画面を再読み込みしてください。'
};
function status(text) {$('status').textContent = text;}
function clearError() {$('error').textContent = '';}
function showError(text) {$('error').textContent = text;}
function clock(date) {return new Date(date).toLocaleTimeString('ja-JP', {hour12: false});}
function budget(value) {
  if (!value) return;
  lastBudget = value;
  $('apiCalls').textContent = value.requests_remaining === null ? `${value.api_calls}回` :
    `${value.api_calls} / ${value.api_calls + value.requests_remaining}回`;
  $('sessionCost').textContent = `US$${value.session_spent_usd.toFixed(6)}` +
    (value.session_limit_usd === undefined ? '' : ` / ${value.session_limit_usd.toFixed(2)}`);
  $('pocCost').textContent = `US$${value.poc_spent_usd.toFixed(6)} / 5`;
}
function buttons() {
  $('startCamera').disabled = !config || cameraPending || !!stream;
  $('startInference').disabled = !stream || cameraPending || loop.running || loop.busy || fatalInference ||
    !!lastBudget?.halt_reason || lastBudget?.requests_remaining === 0;
  $('pauseInference').disabled = !loop.running;
  $('stopAll').disabled = !stream && !cameraPending && !loop.busy;
  for (const id of ['method', 'interval', 'confidence']) $(id).disabled = loop.running;
}
function clearLive() {
  lastPrediction = null;
  liveCanvas.getContext('2d').clearRect(0, 0, liveCanvas.width, liveCanvas.height);
  $('overlayStatus').textContent = '予測枠なし';
}
function clearResults() {
  clearLive(); lastFrame = null;
  snapshot.width = snapshot.height = 0;
  $('snapshotEmpty').hidden = false;
  $('snapshotLabel').textContent = '未推論 / 停止中';
  $('predictions').replaceChildren();
  $('frameAge').textContent = '—'; $('boxCount').textContent = '—';
}
function drawBoxes(ctx, detections, rect, mirror, mock) {
  ctx.font = '600 13px system-ui, sans-serif'; ctx.lineWidth = 2;
  ctx.setLineDash([7, 4]);
  for (const detection of detections) {
    const b = projectBox(detection.box, rect, mirror);
    const color = detection.confidence < .6 ? '#ffc371' : '#ff82ad';
    ctx.strokeStyle = color; ctx.strokeRect(b.x, b.y, b.width, b.height);
    const text = `${mock ? 'MOCK ' : ''}${detection.id} · ${detection.confidence.toFixed(2)}`;
    const width = ctx.measureText(text).width + 12, y = Math.max(rect.y, b.y - 23);
    ctx.fillStyle = '#0b1325eb'; ctx.fillRect(b.x, y, width, 22);
    ctx.fillStyle = color; ctx.fillText(text, b.x + 6, y + 15);
  }
  ctx.setLineDash([]);
}
function render(now) {
  if (!stream) return;
  const stage = $('stage'), ratio = Math.min(window.devicePixelRatio || 1, 2);
  const width = stage.clientWidth, height = stage.clientHeight;
  if (liveCanvas.width !== Math.round(width * ratio) || liveCanvas.height !== Math.round(height * ratio)) {
    liveCanvas.width = Math.round(width * ratio); liveCanvas.height = Math.round(height * ratio);
  }
  const ctx = liveCanvas.getContext('2d'); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const age = lastFrame ? now - lastFrame.capturedAt : null;
  const sameAspect = lastPrediction && Math.abs(lastPrediction.frame.width / lastPrediction.frame.height - video.videoWidth / video.videoHeight) < .01;
  const fresh = lastPrediction && sameAspect && freshOverlay(lastPrediction.frame.capturedAt, now, config.overlay_ttl_ms, loop.busy);
  if (fresh) {
    const rect = fitRect(width, height, video.videoWidth, video.videoHeight);
    if (rect) drawBoxes(ctx, lastPrediction.data.detections, rect, $('mirror').checked, config.mode === 'mock');
  }
  if (now - lastAgeUpdate >= 200) {
    lastAgeUpdate = now;
    $('frameAge').textContent = age === null ? '—' : `${(age / 1000).toFixed(1)}秒`;
    $('overlayStatus').textContent = loop.busy ? '推論中 · 古い枠は非表示' : fresh ?
      `撮影から${((now - lastPrediction.frame.capturedAt) / 1000).toFixed(1)}秒 · 過去の予測` :
      lastFrame ? 'ライブ枠は期限切れ · 静止画で確認' : '予測枠なし';
  }
  animation = requestAnimationFrame(render);
}
function pauseInference(message = '推論を一時停止しました。') {
  const pending = loop.busy;
  loop.stop(); clearResults(); buttons();
  status(pending ? `${message} 送信済み1件はサーバーで終了待ちです。` : message);
}
function stopAll(message = 'カメラと推論を停止しました。') {
  ++cameraEpoch; cameraPending = false;
  pauseInference(message);
  if (stream) stream.getTracks().forEach(track => track.stop());
  stream = null; video.pause(); video.srcObject = null;
  if (animation !== null) cancelAnimationFrame(animation);
  animation = null;
  $('empty').hidden = false; $('cameraDot').classList.remove('on');
  $('cameraState').textContent = 'CAMERA OFF'; buttons();
}
async function cameras() {
  if (!navigator.mediaDevices?.enumerateDevices) return;
  try {
    const selected = $('cameraSelect').value;
    const devices = (await navigator.mediaDevices.enumerateDevices()).filter(d => d.kind === 'videoinput');
    const options = [new Option('既定のインカメラ', '')];
    devices.forEach((device, i) => options.push(new Option(device.label || `カメラ ${i + 1}（許可後に名前を表示）`, device.deviceId)));
    $('cameraSelect').replaceChildren(...options);
    if (options.some(o => o.value === selected)) $('cameraSelect').value = selected;
  } catch { /* Device labels may be withheld until the explicit permission. */ }
}
async function startCamera() {
  clearError();
  if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
    showError('カメラにはlocalhostまたは127.0.0.1の画面が必要です。MacのブラウザでローカルURLを開いてください。'); return;
  }
  const epoch = ++cameraEpoch; cameraPending = true; buttons();
  status('ブラウザのカメラ許可を確認してください。');
  let acquired = null;
  try {
    const deviceId = $('cameraSelect').value;
    acquired = await navigator.mediaDevices.getUserMedia({audio: false, video: {
      ...(deviceId ? {deviceId: {exact: deviceId}} : {facingMode: {ideal: 'user'}}),
      width: {ideal: 1280}, height: {ideal: 720}, frameRate: {ideal: 30}}});
    if (epoch !== cameraEpoch) {acquired.getTracks().forEach(t => t.stop()); return;}
    stream = acquired; video.srcObject = stream;
    video.classList.toggle('mirrored', $('mirror').checked);
    await video.play();
    if (epoch !== cameraEpoch) {acquired.getTracks().forEach(t => t.stop()); return;}
    cameraPending = false; $('empty').hidden = true;
    $('cameraState').textContent = 'CAMERA LIVE'; $('cameraDot').classList.add('on');
    stream.getVideoTracks().forEach(track => track.addEventListener('ended', () => {
      if (stream === acquired) stopAll('カメラ接続が終了しました。');
    }));
    animation = requestAnimationFrame(render);
    await cameras();
    if (epoch !== cameraEpoch) return;
    status('カメラを開始しました。推論はまだ開始していません。'); buttons();
  } catch (error) {
    if (acquired) acquired.getTracks().forEach(t => t.stop());
    if (epoch !== cameraEpoch) return;
    stream = null; video.srcObject = null; cameraPending = false; buttons();
    const messages = {NotAllowedError: 'カメラが許可されていません。ブラウザとmacOSのカメラ権限を確認してください。',
      NotFoundError: '利用できるカメラが見つかりません。', NotReadableError: 'カメラを開けません。ほかのアプリで使用中か確認してください。',
      OverconstrainedError: '選択したカメラを利用できません。別のカメラを選んでください。'};
    showError(messages[error.name] || 'カメラを開始できませんでした。'); status('カメラは停止中です。');
  }
}
async function captureAndSend(signal, current) {
  if (!stream || video.readyState < 2 || !video.videoWidth) throw Error('カメラの映像を取得できません。');
  const scale = Math.min(1, config.max_dimension / Math.max(video.videoWidth, video.videoHeight));
  const canvas = document.createElement('canvas');
  canvas.width = Math.max(1, Math.round(video.videoWidth * scale)); canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
  const capturedAt = performance.now(), wallTime = Date.now();
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .82));
  if (!current() || signal.aborted) throw new DOMException('Stopped', 'AbortError');
  if (!blob) throw Error('フレームをJPEGに変換できません。');
  const method = $('method').value, confidence = $('confidence').value;
  const response = await fetch(`/api/camera/frame?method=${encodeURIComponent(method)}&confidence=${encodeURIComponent(confidence)}`, {
    method: 'POST', headers: {'Content-Type': 'image/jpeg', 'X-Camera-Token': config.csrf_token}, body: blob, signal});
  const data = await response.json();
  if (!response.ok) {
    const error = Error(labels[data.error] || '推論を停止しました。ローカルサーバーの状態を確認してください。');
    error.budget = data.budget; error.permanent = ![409, 429].includes(response.status); throw error;
  }
  return {data, frame: {canvas, capturedAt, wallTime, width: canvas.width, height: canvas.height}, returnedAt: performance.now()};
}
function showResult(value) {
  const {data, frame, returnedAt} = value;
  lastPrediction = value; lastFrame = frame;
  snapshot.width = frame.width; snapshot.height = frame.height;
  const ctx = snapshot.getContext('2d'); ctx.drawImage(frame.canvas, 0, 0);
  drawBoxes(ctx, data.detections, {x: 0, y: 0, width: frame.width, height: frame.height}, false, data.mode === 'mock');
  $('snapshotEmpty').hidden = true; $('snapshotLabel').textContent = `${clock(frame.wallTime)} 撮影 · 左右反転なし`;
  $('capturedAt').textContent = clock(frame.wallTime); $('resultTime').textContent = clock(Date.now());
  $('latency').textContent = `${Math.round(returnedAt - frame.capturedAt)} ms`;
  $('boxCount').textContent = `${data.detections.length} / ${data.unresolved_candidates}`;
  $('methodLabel').textContent = data.method === 'single' ? '単一Box' : '短ordinal 4候補';
  $('predictions').replaceChildren();
  for (const detection of data.detections) {
    const row = document.createElement('div'), name = document.createElement('span'), score = document.createElement('strong');
    row.className = 'prediction-row'; name.textContent = `${data.mode === 'mock' ? 'MOCK ' : ''}${detection.id}`;
    score.textContent = `confidence ${detection.confidence.toFixed(2)}`; row.append(name, score); $('predictions').append(row);
  }
  budget(data.budget);
  status(data.mode === 'mock' ? 'MOCKの台本枠です。画像認識の結果ではありません。' :
    data.detections.length ? 'モデルの予測枠を受信しました。撮影時点の位置です。' :
    `採用枠なし。保留候補 ${data.unresolved_candidates}件。低確信と未検出は区別して確認してください。`);
}
const loop = new FrameLoop(captureAndSend, {
  interval: () => Math.max(config?.min_interval_ms || 2000, Number($('interval').value)),
  onResult: showResult,
  onBusy: busy => {if (busy) {clearLive(); status(config.mode === 'live' ? '撮影フレームをJevへ送信して推論中です。' : 'MOCKの動作確認中です。');} buttons();},
  onError: error => {clearLive(); fatalInference = error.permanent ?? true; budget(error.budget); showError(error.message); status('推論を停止しました。カメラ映像は続けています。'); buttons();}
});
$('startCamera').addEventListener('click', startCamera);
$('startInference').addEventListener('click', () => {clearError(); if (stream) {loop.start(); buttons();}});
$('pauseInference').addEventListener('click', () => pauseInference());
$('stopAll').addEventListener('click', () => stopAll());
$('cameraSelect').addEventListener('change', () => stopAll('カメラを選択しました。「カメラを開始」で開いてください。'));
$('mirror').addEventListener('change', () => video.classList.toggle('mirrored', $('mirror').checked));
$('confidence').addEventListener('input', () => {$('confidenceLabel').textContent = Number($('confidence').value).toFixed(2);});
for (const id of ['method', 'interval', 'confidence']) $(id).addEventListener('change', () => {
  if (loop.running) pauseInference('設定変更のため推論を停止しました。'); else clearResults();
});
window.addEventListener('pagehide', () => stopAll());
document.addEventListener('visibilitychange', () => {if (document.hidden) stopAll('タブが非表示になったため停止しました。');});
async function init() {
  try {
    const response = await fetch('/api/camera/config'); if (!response.ok) throw Error();
    config = await response.json(); budget(config.budget);
    $('modeBadge').textContent = config.mode === 'mock' ? 'MOCK · 実API 0回' : 'LIVE · Jev 1.14.0';
    $('privacyNotice').replaceChildren();
    const title = document.createElement('strong'); title.textContent = config.mode === 'mock' ? 'MOCKは台本です。認識しません。 ' : '選択カメラの間引いた静止フレームをJevへ送信します。 ';
    $('privacyNotice').append(title, document.createTextNode(config.mode === 'mock' ?
      '映像と操作をローカルで確認できます。外部APIへの送信はありません。' :
      '「Jevに送信して推論開始」から送信します。映像・画像は保存しません。キーはサーバー側だけで扱います。'));
    $('startInference').textContent = config.mode === 'mock' ? 'MOCKの動作確認を開始' : 'Jevに送信して推論開始';
    status('カメラは停止中です。開始ボタンを押してください。'); buttons();
    await cameras();
    navigator.mediaDevices?.addEventListener('devicechange', cameras);
  } catch {showError('ローカルサーバーに接続できません。cameraコマンドを起動して画面を開き直してください。');}
}
init();
