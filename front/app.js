const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const newConversationId = () => {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16);
    return (char === 'x' ? value : (value & 0x3) | 0x8).toString(16);
  });
};
const state = {
  // The frontend Nginx proxies this same-origin path to rag-app-service:8000.
  apiUrl: '/api',
  conversations: [],
  documents: [],
  conversationId: localStorage.getItem('frodo-conversation-id') || localStorage.getItem('frodo-session-id') || newConversationId(),
  checkpointId: null,
  selectedModel: localStorage.getItem('frodo-model') || 'gpt-5-mini',
  token: localStorage.getItem('frodo-access-token'),
  user: null,
  authMode: 'login',
};
localStorage.setItem('frodo-conversation-id', state.conversationId);
localStorage.removeItem('frodo-session-id');

function showGraphIdentifiers() {
  const threadElement = $('#threadId');
  const checkpointElement = $('#checkpointId');
  threadElement.textContent = `THREAD · ${state.conversationId}`;
  threadElement.dataset.threadId = state.conversationId;
  checkpointElement.textContent = state.checkpointId ? `CHECKPOINT · ${state.checkpointId}` : 'CHECKPOINT · 아직 없음';
  checkpointElement.dataset.checkpointId = state.checkpointId || '';
}

function authHeaders(headers = {}) {
  return state.token ? { ...headers, Authorization: `Bearer ${state.token}` } : headers;
}

async function apiFetch(path, options = {}) {
  const response = await fetch(`${state.apiUrl}${path}`, {
    ...options,
    headers: authHeaders(options.headers || {}),
  });
  if (response.status === 401 && !path.startsWith('/auth/')) logout(false);
  return response;
}

function showUser(user) {
  state.user = user;
  $('#userName').textContent = user.display_name;
  $('#userAvatar').textContent = user.display_name.trim().slice(0, 1).toUpperCase() || 'U';
  $('#authGate').classList.add('hidden');
}

function logout(showMessage = true) {
  state.token = null; state.user = null;
  localStorage.removeItem('frodo-access-token');
  state.conversationId = newConversationId(); localStorage.setItem('frodo-conversation-id', state.conversationId);
  state.checkpointId = null; showGraphIdentifiers();
  $('#authGate').classList.remove('hidden');
  if (showMessage) toast('로그아웃했습니다.');
}

function setAuthMode(mode) {
  state.authMode = mode; const registering = mode === 'register';
  $('#authTitle').textContent = registering ? '회원가입' : '로그인';
  $('#authSubmit').textContent = registering ? '계정 만들기' : '로그인';
  $('#authSwitch').textContent = registering ? '이미 계정이 있나요? 로그인' : '계정이 없나요? 회원가입';
  $('#nameField').classList.toggle('hidden', !registering);
  $('#authName').required = registering;
  $('#authPassword').autocomplete = registering ? 'new-password' : 'current-password';
  $('#authError').classList.add('hidden');
}

async function restoreSession() {
  if (!state.token) return;
  try {
    const response = await apiFetch('/auth/me');
    if (!response.ok) throw new Error();
    showUser((await response.json()).user);
    await Promise.all([loadDocuments(), loadConversations(false)]);
    if (state.conversations.some((item) => item.id === state.conversationId)) {
      await openConversation(state.conversationId);
    }
  } catch { logout(false); }
}

function toast(message) {
  $('#toast').textContent = message;
  $('#toast').classList.remove('hidden');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $('#toast').classList.add('hidden'), 3500);
}

async function errorMessage(response) {
  try { const data = await response.json(); return data.detail || data.message || `요청 실패 (${response.status})`; }
  catch { return `요청 실패 (${response.status})`; }
}

function showApiInstance(hostname, ip) {
  $('#apiHostname').textContent = hostname || 'unknown';
  $('#apiIp').textContent = ip || 'unknown';
}

async function health() {
  const dot = $('#statusDot');
  try {
    const response = await fetch(`${state.apiUrl}/health`, { signal: AbortSignal.timeout(4000) });
    if (!response.ok) throw new Error();
    const data = await response.json();
    $('#apiVersion').textContent = `API v${data.version || '1.0'}`;
    showApiInstance(data.hostname, data.ip);
    dot.className = 'online'; $('#statusText').textContent = '시스템 정상'; $('#statusDetail').textContent = 'Agent API 연결됨';
  } catch {
    $('#apiVersion').textContent = 'API v1.0';
    showApiInstance('연결 안 됨', '-');
    dot.className = 'offline'; $('#statusText').textContent = '연결 안 됨'; $('#statusDetail').textContent = 'Agent API 확인 필요';
  }
}

async function loadModels() {
  try {
    const response = await fetch(`${state.apiUrl}/models`);
    if (!response.ok) throw new Error(await errorMessage(response));
    const data = await response.json();
    const models = data.models || [];
    const selected = models.includes(state.selectedModel) ? state.selectedModel : data.default_model;
    const select = $('#modelSelect'); select.innerHTML = '';
    models.forEach((model) => {
      const option = document.createElement('option'); option.value = model; option.textContent = model; select.appendChild(option);
    });
    select.value = selected; state.selectedModel = selected; localStorage.setItem('frodo-model', selected);
  } catch (error) { toast(`모델 목록을 불러오지 못했습니다: ${error.message}`); }
}

const titles = { chat: '지식 검색', documents: '문서 보관함', history: '히스토리' };
function switchView(name) {
  $$('.view').forEach((view) => view.classList.toggle('active', view.id === `${name}View`));
  $$('.nav-link[data-view]').forEach((link) => link.classList.toggle('active', link.dataset.view === name));
  $('#pageTitle').textContent = titles[name];
  $('#sidebar').classList.remove('open');
  if (name === 'documents') loadDocuments();
  if (name === 'history') loadConversations();
}

function addTurn(role, content, sources = [], rewrittenQueries = []) {
  $('#welcome').classList.add('hidden');
  $('#conversation').classList.remove('hidden');
  const turn = document.createElement('div');
  turn.className = `turn ${role}`;
  if (role === 'user') {
    const bubble = document.createElement('div'); bubble.className = 'user-bubble'; bubble.textContent = content; turn.appendChild(bubble);
  } else {
    turn.innerHTML = '<div class="agent-response"><div class="agent-mini">✦</div><div><div class="agent-copy"></div></div></div>';
    turn.querySelector('.agent-copy').textContent = content;
    if (rewrittenQueries.length) {
      const details = document.createElement('details'); details.className = 'rewritten-queries';
      const summary = document.createElement('summary'); summary.textContent = `재작성된 검색 질문 ${rewrittenQueries.length}개`; details.appendChild(summary);
      rewrittenQueries.forEach((query) => { const item = document.createElement('div'); item.className = 'rewritten-query'; item.textContent = query; details.appendChild(item); });
      turn.querySelector('.agent-response>div:last-child').appendChild(details);
    }
    if (sources.length) {
      const details = document.createElement('details'); details.className = 'sources';
      const summary = document.createElement('summary'); summary.textContent = `참조 컨텍스트 ${sources.length}개`; details.appendChild(summary);
      sources.forEach((source) => { const item = document.createElement('div'); item.className = 'source'; item.textContent = source; details.appendChild(item); });
      turn.querySelector('.agent-response>div:last-child').appendChild(details);
    }
  }
  $('#conversation').appendChild(turn);
  turn.scrollIntoView({ behavior: 'smooth', block: 'end' });
  return turn;
}

async function ask(question) {
  const value = question.trim(); if (!value) return;
  addTurn('user', value); $('#question').value = ''; resizeComposer();
  const pending = addTurn('agent', '질문을 분석하고 필요한 검색 도구를 선택하는 중'); pending.querySelector('.agent-copy').classList.add('thinking');
  $('#sendButton').disabled = true;
  try {
    const response = await apiFetch('/query', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({ question:value, conversation_id:state.conversationId, model:state.selectedModel }) });
    if (!response.ok) throw new Error(await errorMessage(response));
    const data = await response.json();
    showApiInstance(data.api_hostname, data.api_ip);
    pending.remove(); addTurn('agent', data.generation || '답변을 생성하지 못했습니다.', data.documents || [], data.rewritten_queries || []);
    state.conversationId = data.conversation_id || state.conversationId; localStorage.setItem('frodo-conversation-id', state.conversationId);
    state.checkpointId = data.checkpoint_id || null; showGraphIdentifiers();
    state.selectedModel = data.model || state.selectedModel; localStorage.setItem('frodo-model', state.selectedModel);
    await loadConversations(false);
  } catch (error) {
    pending.remove(); addTurn('agent', `요청을 완료하지 못했습니다.\n${error.message}`); toast(error.message);
  } finally { $('#sendButton').disabled = false; }
}

function resizeComposer() { const input = $('#question'); input.style.height = 'auto'; input.style.height = `${Math.min(input.scrollHeight, 130)}px`; }

function formatBytes(bytes = 0) {
  if (!bytes) return '텍스트'; const units = ['B','KB','MB']; const index = Math.min(Math.floor(Math.log(bytes)/Math.log(1024)),2);
  return `${(bytes/1024**index).toFixed(index ? 1 : 0)} ${units[index]}`;
}

async function loadDocuments() {
  try {
    const response = await apiFetch('/documents'); if (!response.ok) throw new Error(await errorMessage(response));
    const data = await response.json(); state.documents = data.documents || []; renderDocuments();
  } catch (error) { $('#documentGrid').innerHTML = `<div class="empty"><span>!</span><p>${error.message}</p></div>`; }
}

function renderDocuments() {
  $('#docBadge').textContent = state.documents.length; $('#libraryCount').textContent = `${state.documents.length}개의 문서`;
  const grid = $('#documentGrid'); grid.innerHTML = '';
  if (!state.documents.length) { grid.innerHTML = '<div class="empty"><span>▱</span><p>아직 등록된 문서가 없습니다.<br>첫 번째 지식을 업로드해 보세요.</p></div>'; return; }
  state.documents.forEach((doc) => {
    const ext = (doc.filename.split('.').pop() || 'TXT').toUpperCase(); const card = document.createElement('article'); card.className = 'doc-card';
    card.innerHTML = `<div class="file-ext"></div><div><h4></h4><p>${formatBytes(doc.file_size)} · ${doc.chunk_count} chunks</p></div><button class="delete-doc" title="삭제">×</button>`;
    card.querySelector('.file-ext').textContent = `.${ext}`;
    card.querySelector('h4').textContent = doc.filename;
    card.querySelector('.delete-doc').addEventListener('click', () => removeDocument(doc.document_id, doc.filename)); grid.appendChild(card);
  });
}

async function removeDocument(id, filename) {
  if (!confirm(`'${filename}' 문서를 Vector DB에서 삭제할까요?`)) return;
  try { const response = await apiFetch(`/documents/${id}`, { method:'DELETE' }); if (!response.ok) throw new Error(await errorMessage(response)); toast('문서를 삭제했습니다.'); loadDocuments(); }
  catch (error) { toast(error.message); }
}

async function uploadFiles(files) {
  const allowed = ['pdf','docx','txt','md','csv'];
  for (const file of files) {
    if (!allowed.includes(file.name.split('.').pop().toLowerCase())) { toast(`${file.name}: 지원하지 않는 형식입니다.`); continue; }
    if (file.size > 20*1024*1024) { toast(`${file.name}: 20MB를 초과합니다.`); continue; }
    $('#uploadQueue').classList.remove('hidden'); const row = document.createElement('div'); row.className = 'queue-item';
    row.innerHTML = `<div class="file-ext"></div><div><strong></strong><small>${formatBytes(file.size)}</small></div><span class="progress">업로드 중</span>`;
    row.querySelector('.file-ext').textContent = `.${file.name.split('.').pop().toUpperCase()}`;
    row.querySelector('strong').textContent = file.name; $('#uploadQueue').prepend(row);
    try {
      const body = new FormData(); body.append('file', file);
      const response = await apiFetch('/documents/upload', { method:'POST', body }); if (!response.ok) throw new Error(await errorMessage(response));
      const data = await response.json(); row.querySelector('.progress').textContent = `${data.stored_count} CHUNKS · 완료`; toast(`${file.name} 저장 완료`);
    } catch (error) { row.querySelector('.progress').textContent = '실패'; row.querySelector('.progress').style.color = '#ad292e'; toast(error.message); }
  }
  loadDocuments();
}

async function loadConversations(render = true) {
  try {
    const response = await apiFetch('/conversations');
    if (!response.ok) throw new Error(await errorMessage(response));
    const data = await response.json(); state.conversations = data.conversations || [];
    if (render) renderHistory();
  } catch (error) {
    if (render) $('#historyList').innerHTML = `<div class="empty"><span>!</span><p>${error.message}</p></div>`;
  }
}

async function openConversation(id) {
  try {
    const response = await apiFetch(`/conversations/${id}`);
    if (!response.ok) throw new Error(await errorMessage(response));
    const data = await response.json();
    state.conversationId = data.id; localStorage.setItem('frodo-conversation-id', state.conversationId);
    state.checkpointId = data.checkpoint_id || null; showGraphIdentifiers();
    $('#conversation').innerHTML = '';
    (data.messages || []).forEach((message) => {
      const sources = message.role === 'assistant' ? (message.metadata?.documents || []) : [];
      const rewrittenQueries = message.role === 'assistant' ? (message.metadata?.rewritten_queries || []) : [];
      addTurn(message.role === 'assistant' ? 'agent' : 'user', message.content, sources, rewrittenQueries);
    });
    if (!(data.messages || []).length) {
      $('#conversation').classList.add('hidden'); $('#welcome').classList.remove('hidden');
    }
    switchView('chat');
  } catch (error) { toast(error.message); }
}

function renderHistory() {
  const list = $('#historyList'); list.innerHTML = '';
  if (!state.conversations.length) { list.innerHTML = '<div class="empty"><span>◷</span><p>아직 대화 기록이 없습니다.</p></div>'; return; }
  state.conversations.forEach((item) => { const button = document.createElement('button'); button.className = 'history-item'; button.innerHTML = '<strong></strong><small></small>'; button.querySelector('strong').textContent = item.title; button.querySelector('small').textContent = `${new Date(item.updated_at).toLocaleString('ko-KR')} · ${item.message_count}개 메시지`; button.addEventListener('click', () => openConversation(item.id)); list.appendChild(button); });
}

$('#queryForm').addEventListener('submit', (event) => { event.preventDefault(); ask($('#question').value); });
$('#question').addEventListener('input', resizeComposer); $('#question').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); $('#queryForm').requestSubmit(); } });
$('#modelSelect').addEventListener('change', (event) => { state.selectedModel=event.target.value; localStorage.setItem('frodo-model',state.selectedModel); });
$$('.nav-link[data-view]').forEach((button) => button.addEventListener('click', () => switchView(button.dataset.view)));
$('#newChat').addEventListener('click', () => { state.conversationId=newConversationId(); state.checkpointId=null; localStorage.setItem('frodo-conversation-id',state.conversationId); showGraphIdentifiers(); $('#conversation').innerHTML=''; $('#conversation').classList.add('hidden'); $('#welcome').classList.remove('hidden'); switchView('chat'); });
$('#menuButton').addEventListener('click', () => $('#sidebar').classList.toggle('open'));
$('#apiDocs').addEventListener('click', () => window.open(`${state.apiUrl}/docs`, '_blank', 'noopener,noreferrer'));
$('#uploadCta').addEventListener('click', () => $('#fileInput').click()); $('#dropzone').addEventListener('click', (event) => { if (event.target !== $('#fileInput')) $('#fileInput').click(); });
$('#fileInput').addEventListener('click', (event) => event.stopPropagation());
$('#dropzone').addEventListener('keydown', (event) => { if (event.key === 'Enter' || event.key === ' ') $('#fileInput').click(); });
$('#fileInput').addEventListener('change', async (event) => {
  const files = [...event.target.files];
  event.target.value = '';
  await uploadFiles(files);
});
['dragenter','dragover'].forEach((type) => $('#dropzone').addEventListener(type, (event) => { event.preventDefault(); $('#dropzone').classList.add('drag'); }));
['dragleave','drop'].forEach((type) => $('#dropzone').addEventListener(type, (event) => { event.preventDefault(); $('#dropzone').classList.remove('drag'); }));
$('#dropzone').addEventListener('drop', (event) => uploadFiles([...event.dataTransfer.files])); $('#refreshDocs').addEventListener('click', loadDocuments);
$('#clearHistory').addEventListener('click', async () => {
  if (!state.conversations.length || !confirm('저장된 모든 대화를 영구 삭제할까요?')) return;
  try {
    const responses = await Promise.all(state.conversations.map((item) => apiFetch(`/conversations/${item.id}`, { method:'DELETE' })));
    if (responses.some((response) => !response.ok)) throw new Error('일부 대화를 삭제하지 못했습니다.');
    state.conversationId = newConversationId(); localStorage.setItem('frodo-conversation-id', state.conversationId);
    state.checkpointId = null; showGraphIdentifiers();
    await loadConversations(); toast('모든 대화를 삭제했습니다.');
  } catch (error) { toast(error.message); }
});
$('#authSwitch').addEventListener('click', () => setAuthMode(state.authMode === 'login' ? 'register' : 'login'));
$('#userMenu').addEventListener('click', () => { if (confirm('로그아웃할까요?')) logout(); });
$('#threadId').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText(state.conversationId); toast('thread_id를 복사했습니다.'); }
  catch { toast(`thread_id: ${state.conversationId}`); }
});
$('#checkpointId').addEventListener('click', async () => {
  if (!state.checkpointId) { toast('아직 생성된 Checkpoint가 없습니다.'); return; }
  try { await navigator.clipboard.writeText(state.checkpointId); toast('checkpoint_id를 복사했습니다.'); }
  catch { toast(`checkpoint_id: ${state.checkpointId}`); }
});
$('#authForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const endpoint = state.authMode === 'register' ? '/auth/register' : '/auth/login';
  const body = { email: $('#authEmail').value.trim(), password: $('#authPassword').value };
  if (state.authMode === 'register') body.display_name = $('#authName').value.trim();
  $('#authSubmit').disabled = true; $('#authError').classList.add('hidden');
  try {
    const response = await fetch(`${state.apiUrl}${endpoint}`, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
    if (!response.ok) throw new Error(await errorMessage(response));
    const data = await response.json(); state.token = data.access_token;
    localStorage.setItem('frodo-access-token', state.token); showUser(data.user);
    $('#authForm').reset();
    await Promise.all([loadDocuments(), loadConversations(false)]);
  } catch (error) { $('#authError').textContent = error.message; $('#authError').classList.remove('hidden'); }
  finally { $('#authSubmit').disabled = false; }
});
showGraphIdentifiers(); health(); loadModels(); restoreSession();
