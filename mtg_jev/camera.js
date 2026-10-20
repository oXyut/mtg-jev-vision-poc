import {FrameLoop, fitRect, projectBox, freshOverlay} from './camera-core.js';

const $ = id => document.getElementById(id);
const video = $('video'), liveCanvas = $('overlay'), snapshot = $('snapshot');
let config = null, stream = null, cameraPending = false, cameraEpoch = 0;
let animation = null, lastPrediction = null, lastFrame = null, lastBudget = null;
let fatalInference = false, lastAgeUpdate = 0;
let inferenceRunId = null, promptEpoch = 0;
let pendingCaptureAt = null, lastResultCount = null;
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
function cancelRun() {
  const run_id = inferenceRunId; inferenceRunId = null;
  if (!run_id || !config) return;
  fetch('/api/camera/stop', {method: 'POST', keepalive: true,
    headers: {'Content-Type': 'application/json', 'X-Camera-Token': config.csrf_token},
    body: JSON.stringify({run_id})}).catch(() => {});
}
async function showPrompts() {
  if (!config) return;
  const epoch = ++promptEpoch, method = $('method').value;
  const multi = method === 'multi';
  $('confidenceTitle').textContent = multi ? 'Box単独採用confidence' : '表示する最低confidence';
  $('confidencePolicy').textContent = multi ?
    'Box確信が基準未満でも、別cropの枠とのIoU ≥ 0.75等で支持された候補は値を変えずに表示します。正解率の保証ではありません。' :
    'confidenceはアプリ側の表示基準です。正解率を保証する値ではありません。';
  $('methodLimits').textContent = multi ? `最大${config.max_cards}枚 / 最大${config.frame_max_api_calls} API・frame（途中停止あり）` :
    method === 'single' ? '最大1枚 / 1 API・frame' : `最大${config.max_cards}候補 / 1 API・frame（完全列挙は未保証）`;
  $('methodHint').textContent = multi ? 'LIVEでは静止フレームを1回探索して一時停止します。実写真では2.9〜4.6秒、8枚検出は未達です。過去フレームで確認後、手動で再実行できます。' :
    method === 'single' ? '1枚だけの提案です。複数カードの検出には使いません。' :
    `1APIで順位候補を比較します。速さ・一意な列挙は保証されません。次HTTPの保守的予約はUS$${config.ordinal_reservation_usd.toFixed(3)}（請求額ではありません）。重複/低確信で採用0件の場合もあります。`;
  try {
    const response = await fetch(`/api/camera/prompts?method=${encodeURIComponent(method)}`);
    if (!response.ok) throw Error();
    const value = await response.json();
    if (epoch === promptEpoch) $('promptText').textContent = JSON.stringify(value, null, 2);
  } catch {if (epoch === promptEpoch) $('promptText').textContent = 'プロンプトを取得できません。サーバーと画面を更新してください。';}
}
function status(text) {$('status').textContent = text;}
function clearError() {$('error').textContent = '';}
function showError(text) {$('error').textContent = text;}
function clock(date) {return new Date(date).toLocaleTimeString('ja-JP', {hour12: false});}
function confidenceText(value) {return Number.isFinite(value) ? value.toFixed(2) : '未提示';}
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
  clearLive(); lastFrame = null; lastResultCount = null;
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
    const color = !Number.isFinite(detection.confidence) || detection.confidence < .6 ? '#ffc371' : '#ff82ad';
    ctx.strokeStyle = color; ctx.strokeRect(b.x, b.y, b.width, b.height);
    const text = `${mock ? 'MOCK ' : ''}${detection.id} · ${confidenceText(detection.confidence)}`;
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
    if (loop.busy && pendingCaptureAt !== null) $('latency').textContent = `${((now-pendingCaptureAt)/1000).toFixed(1)}秒 経過`;
    $('overlayStatus').textContent = loop.busy ? '探索中 · 古い枠は非表示' : fresh ?
      `撮影から${((now - lastPrediction.frame.capturedAt) / 1000).toFixed(1)}秒 · 過去の予測` :
      lastFrame ? `検出${lastResultCount}件 · ライブ枠は期限切れ・静止画で確認` : '予測枠なし';
  }
  animation = requestAnimationFrame(render);
}
function pauseInference(message = '推論を一時停止しました。') {
  const pending = loop.busy;
  loop.stop(); cancelRun(); pendingCaptureAt = null; clearResults(); buttons();
  status(pending ? `${message} 探索を停止通知しました。送信済み1件はサーバーで終了待ちです。` : message);
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
  pendingCaptureAt = capturedAt;
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/jpeg', .82));
  if (!current() || signal.aborted) throw new DOMException('Stopped', 'AbortError');
  if (!blob) throw Error('フレームをJPEGに変換できません。');
  const method = $('method').value, confidence = $('confidence').value;
  const response = await fetch(`/api/camera/frame?method=${encodeURIComponent(method)}&confidence=${encodeURIComponent(confidence)}&run_id=${encodeURIComponent(inferenceRunId)}`, {
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
  pendingCaptureAt = null; lastResultCount = data.detections.length;
  snapshot.width = frame.width; snapshot.height = frame.height;
  const ctx = snapshot.getContext('2d'); ctx.drawImage(frame.canvas, 0, 0);
  drawBoxes(ctx, data.detections, {x: 0, y: 0, width: frame.width, height: frame.height}, false, data.mode === 'mock');
  $('snapshotEmpty').hidden = true; $('snapshotLabel').textContent = `${clock(frame.wallTime)} 撮影 · 左右反転なし`;
  $('capturedAt').textContent = clock(frame.wallTime); $('resultTime').textContent = clock(data.completed_at);
  $('latency').textContent = `${Math.round(returnedAt - frame.capturedAt)} ms`;
  $('boxCount').textContent = `${data.detections.length} / ${data.unresolved_candidates}`;
  $('methodLabel').textContent = `${data.method === 'multi' ? '複数枚探索' : data.method === 'single' ? '単一Box' : '順位一括'} · ${data.api_calls} API`;
  $('predictions').replaceChildren();
  for (const detection of data.detections) {
    const row = document.createElement('div'), name = document.createElement('span'), score = document.createElement('strong');
    row.className = 'prediction-row'; name.textContent = `${data.mode === 'mock' ? 'MOCK ' : ''}${detection.id}`;
    score.textContent = `confidence ${confidenceText(detection.confidence)}`;
    if (detection.support === 'cross_crop_agreement') name.textContent += ` / crop IoU ${detection.cross_view_iou.toFixed(2)}`;
    row.append(name, score); $('predictions').append(row);
  }
  budget(data.budget);
  status(data.mode === 'mock' ? 'MOCKの台本枠です。画像認識の結果ではありません。' :
    data.detections.length ? 'モデルの予測枠を受信しました。撮影時点の位置です。' :
    `採用枠なし。保留候補 ${data.unresolved_candidates}件。低確信と未検出は区別して確認してください。`);
  const slow = returnedAt-frame.capturedAt > config.slow_result_pause_ms;
  const oneFrame = data.method === 'multi';
  if (data.mode === 'live' && (oneFrame || data.auto_pause || data.budget.requests_remaining === 0 || slow)) {
    loop.stop(); cancelRun(); buttons();
    const reasons = {frame_request_limit: 'このフレームのAPI回数枠', request_limit_reached: 'この起動のAPI回数枠',
      estimated_budget_reservation: '次の呼出しの見積費用枠', scan_cancelled: '停止要求',
      estimated_budget_exceeded: '見積費用上限', provider_stopped_no_retry: '通信またはusageの未確定'};
    const advice = data.method === 'multi' ? '1APIの高速比較へ切り替えられます（精度保証なし）。' : '状態を確認してから手動で再開してください。';
    status(oneFrame && !data.auto_pause ? `検出${data.detections.length}件 / ${data.api_calls} API・${((returnedAt-frame.capturedAt)/1000).toFixed(1)}秒。1回の探索を終了し一時停止しました。過去フレームを確認し、再実行は手動で開始してください。` :
      slow ? `検出${data.detections.length}件 / ${data.api_calls} API・${((returnedAt-frame.capturedAt)/1000).toFixed(1)}秒。遅延のため自動一時停止し、過去フレームへ表示しました。${advice}` :
      `推論を一時停止しました（${reasons[data.stop_reason] || '探索の継続条件'}）。撮影済みフレームの結果を表示しています。`);
  }
}
const loop = new FrameLoop(captureAndSend, {
  interval: () => Math.max(config?.min_interval_ms || 2000, Number($('interval').value)),
  onResult: showResult,
  onBusy: busy => {if (busy) {clearLive(); status(config.mode === 'live' ?
    $('method').value === 'multi' ? `撮影フレームを複数枚探索中です（最大${config.frame_max_api_calls} API）。映像は続けています。` : '撮影フレームをJevへ送信して推論中です。' : 'MOCKの動作確認中です。');} buttons();},
  onError: error => {pendingCaptureAt = null; cancelRun(); clearLive(); fatalInference = error.permanent ?? true; budget(error.budget); showError(error.message); status('推論を停止しました。カメラ映像は続けています。'); buttons();}
});
$('startCamera').addEventListener('click', startCamera);
$('startInference').addEventListener('click', () => {
  clearError();
  if (stream && !loop.running && !loop.busy) {inferenceRunId = crypto.randomUUID(); loop.start(); buttons();}
});
$('pauseInference').addEventListener('click', () => pauseInference());
$('stopAll').addEventListener('click', () => stopAll());
$('cameraSelect').addEventListener('change', () => stopAll('カメラを選択しました。「カメラを開始」で開いてください。'));
$('mirror').addEventListener('change', () => video.classList.toggle('mirrored', $('mirror').checked));
$('confidence').addEventListener('input', () => {$('confidenceLabel').textContent = Number($('confidence').value).toFixed(2);});
for (const id of ['method', 'interval', 'confidence']) $(id).addEventListener('change', () => {
  if (loop.running) pauseInference('設定変更のため推論を停止しました。'); else clearResults();
  if (id === 'method') showPrompts();
});
window.addEventListener('pagehide', () => stopAll());
document.addEventListener('visibilitychange', () => {if (document.hidden) stopAll('タブが非表示になったため停止しました。');});
async function init() {
  try {
    const response = await fetch('/api/camera/config'); if (!response.ok) throw Error();
    config = await response.json();
    if (config.prompt_profile !== 'camera-v2') throw Error('server_version_mismatch');
    budget(config.budget);
    $('method').value = config.default_method;
    $('method').options[0].textContent = `複数枚探索 · 最大${config.max_cards}枚（実験）`;
    $('method').options[2].textContent = `高速比較 · 最大${config.max_cards}候補 / 1API`;
    $('modeBadge').textContent = config.mode === 'mock' ? 'MOCK · 実API 0回' : 'LIVE · Jev 1.14.0';
    $('privacyNotice').replaceChildren();
    const title = document.createElement('strong'); title.textContent = config.mode === 'mock' ? 'MOCKは台本です。認識しません。 ' : '選択カメラの間引いた静止フレームをJevへ送信します。 ';
    $('privacyNotice').append(title, document.createTextNode(config.mode === 'mock' ?
      '映像と操作をローカルで確認できます。外部APIへの送信はありません。' :
      '複数枚探索は1フレームで複数回APIを呼びます。開始ボタンから送信し、映像・画像は保存しません。キーはサーバー側だけで扱います。'));
    $('startInference').textContent = config.mode === 'mock' ? 'MOCKの動作確認を開始' : 'Jevに送信して推論開始';
    status('カメラは停止中です。開始ボタンを押してください。'); buttons();
    showPrompts();
    await cameras();
    navigator.mediaDevices?.addEventListener('devicechange', cameras);
  } catch (error) {showError(error.message === 'server_version_mismatch' ?
    '画面とサーバーの版が異なります。サーバーを停止し、更新後にcameraコマンドを再起動して画面も再読み込みしてください。' :
    'ローカルサーバーに接続できません。cameraコマンドを起動して画面を開き直してください。');}
}
init();
