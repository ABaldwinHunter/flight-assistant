"use strict";

const STORAGE = {
  conversation: "flight-desk.selected-conversation",
  trace: "flight-desk.show-tool-activity",
};
const NEW_CHAT = "new";
const elements = {
  newChat: document.getElementById("new-chat"),
  historyStatus: document.getElementById("history-status"),
  list: document.getElementById("conversation-list"),
  count: document.getElementById("conversation-count"),
  title: document.getElementById("conversation-title"),
  configNotice: document.getElementById("config-notice"),
  failureNotice: document.getElementById("failure-notice"),
  globalError: document.getElementById("global-error"),
  demoDate: document.getElementById("demo-date"),
  demoRoutes: document.getElementById("demo-routes"),
  scroll: document.getElementById("chat-scroll"),
  welcome: document.getElementById("welcome"),
  messages: document.getElementById("messages"),
  form: document.getElementById("message-form"),
  input: document.getElementById("message-input"),
  send: document.getElementById("send-button"),
  sendLabel: document.getElementById("send-label"),
  sendError: document.getElementById("send-error"),
  countInput: document.getElementById("input-count"),
  trace: document.getElementById("trace-toggle"),
  rating: document.getElementById("conversation-rating"),
  ratingUp: document.getElementById("rating-up"),
  ratingDown: document.getElementById("rating-down"),
  ratingStatus: document.getElementById("rating-status"),
  ratingError: document.getElementById("rating-error"),
  model: document.getElementById("model-label"),
  live: document.getElementById("live-status"),
  examples: [...document.querySelectorAll("[data-example]")],
};

function storedValue(key) {
  try { return localStorage.getItem(key); } catch { return null; }
}

function saveValue(key, value) {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    // The server still saves conversations when browser storage is unavailable.
  }
}

const state = {
  config: null,
  configError: false,
  selectedId: storedValue(STORAGE.conversation),
  conversations: [],
  historyLoading: true,
  historyError: null,
  listRequest: 0,
  creating: false,
  messages: new Map(),
  drafts: new Map(),
  pending: new Map(),
  loading: new Set(),
  loadErrors: new Map(),
  sendErrors: new Map(),
  ratingPending: new Set(),
  ratingErrors: new Map(),
  showTrace: storedValue(STORAGE.trace) === "true",
};

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function announce(message) {
  elements.live.textContent = message;
}

function draftKey() { return state.selectedId || NEW_CHAT; }

function showGlobalError(message) {
  elements.globalError.textContent = message || "";
  elements.globalError.hidden = !message;
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
  } catch {
    throw new Error("Could not reach the server. Check that it is running, then try again.");
  }
  let data;
  try { data = await response.json(); } catch {
    throw new Error("The server returned an unreadable response. Please try again.");
  }
  if (!response.ok) {
    const error = new Error(typeof data.detail === "string"
      ? data.detail
      : `The request could not be completed (HTTP ${response.status}). Please try again.`);
    error.status = response.status;
    throw error;
  }
  return data;
}

function readableDate(value, options) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, options).format(date);
}

function renderSidebar() {
  elements.count.textContent = String(state.conversations.length);
  elements.list.replaceChildren();
  for (const conversation of state.conversations) {
    const button = node("button", "conversation-item");
    button.type = "button";
    button.disabled = state.creating;
    button.title = conversation.title;
    if (conversation.id === state.selectedId) button.setAttribute("aria-current", "page");
    button.append(node("span", "conversation-item-title", conversation.title));
    const pending = state.pending.has(conversation.id);
    const subtitle = node("span", `conversation-item-date${pending ? " conversation-item-pending" : ""}`,
      pending ? "Reply in progress…" : readableDate(conversation.created_at, { month: "short", day: "numeric" }));
    button.append(subtitle);
    button.addEventListener("click", () => selectConversation(conversation.id));
    elements.list.append(button);
  }
  elements.historyStatus.hidden = !state.historyLoading && !state.historyError && state.conversations.length > 0;
  elements.historyStatus.replaceChildren();
  if (state.historyLoading) elements.historyStatus.textContent = "Loading conversations…";
  else if (state.historyError) {
    elements.historyStatus.append(node("span", "", "History unavailable. "));
    const retry = node("button", "history-retry", "Retry");
    retry.type = "button";
    retry.addEventListener("click", refreshConversations);
    elements.historyStatus.append(retry);
  } else elements.historyStatus.textContent = "Your conversations will appear here.";
  elements.newChat.disabled = state.creating;
}

function renderControls() {
  const id = state.selectedId;
  const pending = state.pending.has(id);
  const loading = state.loading.has(id) || state.loadErrors.has(id);
  const blocked = pending || state.creating || loading;
  const draft = state.drafts.get(draftKey()) || "";
  if (elements.input.value !== draft) elements.input.value = draft;
  elements.input.disabled = blocked;
  elements.input.placeholder = pending ? "The assistant is working on your reply…" : "Where would you like to go?";
  elements.countInput.textContent = `${draft.length.toLocaleString()} / 4,000`;
  elements.send.disabled = blocked || !state.config?.llm_configured || !draft.trim() || draft.length > 4000;
  elements.sendLabel.textContent = pending ? "Working" : state.creating ? "Starting" : "Send";
  elements.send.setAttribute("aria-label", pending ? "Waiting for assistant reply" : "Send message");
  for (const example of elements.examples) example.disabled = blocked;
  const error = state.sendErrors.get(draftKey());
  elements.sendError.hidden = !error;
  elements.sendError.textContent = error || "";
  const conversation = state.conversations.find((item) => item.id === id);
  elements.title.textContent = conversation?.title || (id ? "Conversation" : "Flight assistant");
  elements.title.title = elements.title.textContent;
  renderRating();
}

function renderRating() {
  const id = state.selectedId;
  const conversation = state.conversations.find((item) => item.id === id);
  const rating = conversation?.rating ?? null;
  const saving = state.ratingPending.has(id);
  const hasReply = (state.messages.get(id) || []).some((message) => message.role === "assistant");
  const disabled = !conversation || !hasReply || saving || state.creating || state.loading.has(id) || state.loadErrors.has(id) || state.pending.has(id);
  elements.rating.hidden = !id;
  elements.ratingUp.disabled = disabled;
  elements.ratingDown.disabled = disabled;
  elements.ratingUp.setAttribute("aria-pressed", String(rating === "up"));
  elements.ratingDown.setAttribute("aria-pressed", String(rating === "down"));
  elements.ratingStatus.textContent = saving ? "Saving rating…"
    : !hasReply ? "Send a message before rating."
    : rating ? `Rated ${rating === "up" ? "helpful" : "not helpful"}. Select it again to clear.`
    : "Optional feedback, saved with this conversation.";
  const error = state.ratingErrors.get(id);
  elements.ratingError.hidden = !error;
  elements.ratingError.textContent = error || "";
}

async function saveRating(value) {
  const id = state.selectedId;
  const conversation = state.conversations.find((item) => item.id === id);
  if (!conversation || elements.ratingUp.disabled) return;
  const rating = conversation.rating === value ? null : value;
  state.ratingPending.add(id);
  state.ratingErrors.delete(id);
  renderControls();
  try {
    const result = await api(`/api/conversations/${encodeURIComponent(id)}/rating`, {
      method: "PUT",
      body: JSON.stringify({ rating }),
    });
    const current = state.conversations.find((item) => item.id === id);
    if (current) current.rating = result.rating;
    // Ignore sidebar responses fetched before this rating was saved.
    state.listRequest += 1;
    state.historyLoading = false;
    announce(result.rating === null ? "Conversation rating cleared." : "Conversation rating saved.");
  } catch (error) {
    state.ratingErrors.set(id, error.message);
    announce("Could not save the conversation rating. Your previous rating is unchanged.");
  } finally {
    state.ratingPending.delete(id);
    renderSidebar();
    if (state.selectedId === id) renderControls();
  }
}

function renderConfig() {
  elements.configNotice.replaceChildren();
  elements.configNotice.hidden = Boolean(state.config?.llm_configured);
  if (state.configError) {
    elements.configNotice.append(node("strong", "", "Server configuration could not be loaded."));
    elements.configNotice.append(node("p", "", "You can still prepare a question. Reconnect before sending it."));
    const retry = node("button", "", "Reconnect");
    retry.type = "button";
    retry.addEventListener("click", loadConfig);
    elements.configNotice.append(retry);
    elements.model.textContent = "Server unavailable";
  } else if (!state.config) {
    elements.configNotice.append(node("span", "", "Checking the assistant’s server configuration…"));
    elements.model.textContent = "Connecting to server…";
  } else if (!state.config.llm_configured) {
    elements.configNotice.append(node("strong", "", "One setup step before takeoff: connect OpenAI."));
    elements.configNotice.append(node("p", "", "Set OPENAI_API_KEY in the server’s .env file or export it in the shell, then restart the server."));
    elements.configNotice.append(node("code", "", 'export OPENAI_API_KEY="your-api-key"'));
    elements.configNotice.append(node("p", "", "Never enter your API key in this chat. You can explore saved conversations and prepare a question below."));
    elements.model.textContent = `${state.config.model} · not configured`;
  } else elements.model.textContent = `${state.config.model} · OpenAI`;
  elements.failureNotice.hidden = !state.config?.tool_failure_enabled;
  if (state.config) {
    elements.demoDate.textContent = readableDate(`${state.config.demo_date}T12:00:00`, { day: "numeric", month: "short", year: "numeric" });
    elements.demoRoutes.textContent = state.config.demo_routes.join("  ·  ");
  }
}

function renderToolEvents(events) {
  const trace = node("div", "tool-trace");
  trace.append(node("p", "trace-heading", "Tool activity · simulated flight data"));
  for (const event of events) {
    const details = node("details", "tool-event");
    const summary = node("summary", "", event.name);
    summary.append(node("span", `tool-status${event.status === "error" ? " tool-status-error" : ""}`, event.status === "error" ? "Error" : "Complete"));
    summary.append(node("span", "tool-duration", `${Math.round(event.duration_ms)} ms`));
    details.append(summary);
    details.append(node("h4", "", "Arguments"));
    details.append(node("pre", "", typeof event.arguments === "string" ? event.arguments : JSON.stringify(event.arguments, null, 2)));
    details.append(node("h4", "", "Result"));
    details.append(node("pre", "", JSON.stringify(event.result, null, 2)));
    trace.append(details);
  }
  return trace;
}

function renderMessage(message) {
  const user = message.role === "user";
  const failed = message.status === "error";
  const article = node("article", `message message-${user ? "user" : "assistant"}${failed ? " message-error" : ""}`);
  const header = node("div", "message-header");
  const avatar = node("span", "message-avatar", user ? "Y" : "↗");
  avatar.setAttribute("aria-hidden", "true");
  header.append(avatar, node("span", "message-author", user ? "You" : "Flight desk"));
  if (failed) header.append(node("span", "message-error-label", "Reply failed"));
  const time = node("time", "message-time", readableDate(message.created_at, { hour: "numeric", minute: "2-digit" }));
  time.dateTime = message.created_at;
  header.append(time);
  article.append(header, node("p", "message-body", message.content));
  if (state.showTrace && message.tool_events?.length) article.append(renderToolEvents(message.tool_events));
  return article;
}

function renderChat(scrollToBottom = false) {
  const id = state.selectedId;
  const messages = state.messages.get(id) || [];
  const pending = state.pending.get(id);
  elements.welcome.hidden = Boolean(id);
  elements.messages.hidden = !id;
  elements.messages.setAttribute("aria-busy", String(Boolean(pending || state.loading.has(id))));
  elements.messages.replaceChildren();
  if (state.loading.has(id)) {
    elements.messages.append(node("p", "chat-loading", "Opening your conversation…"));
  } else if (state.loadErrors.has(id)) {
    const error = node("div", "chat-load-error", state.loadErrors.get(id));
    const retry = node("button", "", "Try opening again");
    retry.type = "button";
    retry.addEventListener("click", () => loadConversation(id));
    error.append(retry);
    elements.messages.append(error);
  } else if (id) {
    for (const message of messages) elements.messages.append(renderMessage(message));
    if (pending) {
      elements.messages.append(renderMessage({ role: "user", content: pending.content, created_at: pending.createdAt, status: "complete" }));
      const article = node("article", "message");
      const header = node("div", "message-header");
      const avatar = node("span", "message-avatar", "↗");
      avatar.setAttribute("aria-hidden", "true");
      header.append(avatar, node("span", "message-author", "Flight desk"));
      const body = node("p", "message-body pending-body");
      const dot = node("span", "pending-dot");
      dot.setAttribute("aria-hidden", "true");
      body.append(dot, document.createTextNode("Working on your question…"));
      article.append(header, body);
      elements.messages.append(article);
    } else if (!messages.length) {
      elements.messages.append(node("p", "chat-loading", "This conversation is ready. Ask about a simulated flight below."));
    }
  }
  if (scrollToBottom) elements.scroll.scrollTop = elements.scroll.scrollHeight;
}

function renderAll(scrollToBottom = false) {
  renderSidebar();
  renderChat(scrollToBottom);
  renderControls();
}

async function refreshConversations() {
  const request = ++state.listRequest;
  state.historyLoading = true;
  state.historyError = null;
  renderSidebar();
  try {
    const data = await api("/api/conversations");
    if (request !== state.listRequest) return;
    state.conversations = data.conversations;
  } catch (error) {
    if (request !== state.listRequest) return;
    state.historyError = error.message;
  } finally {
    if (request === state.listRequest) {
      state.historyLoading = false;
      renderSidebar();
      renderControls();
    }
  }
}

async function loadConfig() {
  state.configError = false;
  renderConfig();
  try { state.config = await api("/api/config"); }
  catch { state.configError = true; }
  renderConfig();
  renderControls();
}

function selectConversation(id) {
  if (state.creating) return;
  state.selectedId = id;
  saveValue(STORAGE.conversation, id);
  showGlobalError(null);
  renderAll(true);
  if (!state.messages.has(id) && !state.loading.has(id)) loadConversation(id);
}

async function loadConversation(id) {
  if (state.loading.has(id)) return;
  state.loading.add(id);
  state.loadErrors.delete(id);
  if (state.selectedId === id) {
    renderChat();
    renderControls();
  }
  try {
    const conversation = await api(`/api/conversations/${encodeURIComponent(id)}`);
    state.messages.set(id, conversation.messages);
    const existing = state.conversations.find((item) => item.id === id);
    if (existing) Object.assign(existing, { title: conversation.title, rating: conversation.rating });
    else state.conversations.unshift(conversation);
  } catch (error) {
    if (error.status === 404) {
      state.conversations = state.conversations.filter((item) => item.id !== id);
      if (state.selectedId === id) {
        state.selectedId = null;
        saveValue(STORAGE.conversation, null);
        showGlobalError("That saved conversation no longer exists. You can start a new one below.");
      }
    } else state.loadErrors.set(id, error.message);
  } finally {
    state.loading.delete(id);
    if (state.selectedId === id || state.selectedId === null) renderAll(true);
  }
}

async function createConversation(draft = "") {
  state.creating = true;
  showGlobalError(null);
  renderAll();
  try {
    const conversation = await api("/api/conversations", { method: "POST", body: "{}" });
    state.messages.set(conversation.id, conversation.messages);
    state.conversations = [conversation, ...state.conversations.filter((item) => item.id !== conversation.id)];
    // Discard older sidebar responses that predate this newly created chat.
    state.listRequest += 1;
    state.historyLoading = false;
    state.selectedId = conversation.id;
    state.drafts.set(conversation.id, draft);
    state.drafts.delete(NEW_CHAT);
    saveValue(STORAGE.conversation, conversation.id);
    return conversation.id;
  } finally {
    state.creating = false;
    renderAll(true);
  }
}

async function newConversation() {
  if (state.creating) return;
  try {
    await createConversation();
    elements.input.focus();
    announce("New conversation saved. Your other conversations are unchanged.");
  } catch (error) {
    showGlobalError(error.message);
    announce("Could not create a conversation. Your current conversation and draft are unchanged.");
  }
}

async function sendMessage(event) {
  event.preventDefault();
  if (state.creating || !state.config?.llm_configured) return;
  let id = state.selectedId;
  if (state.pending.has(id) || state.loading.has(id) || state.loadErrors.has(id)) return;
  const originalDraft = elements.input.value;
  const content = originalDraft.trim();
  if (!content || originalDraft.length > 4000) return;
  state.sendErrors.delete(draftKey());
  if (!id) {
    try {
      id = await createConversation(originalDraft);
    } catch (error) {
      state.sendErrors.set(NEW_CHAT, error.message);
      announce("Could not start the conversation. Your draft has been kept.");
      return;
    } finally {
      renderControls();
    }
  }
  const previousMessages = state.messages.get(id) || [];
  state.pending.set(id, { content, createdAt: new Date().toISOString() });
  state.drafts.set(id, "");
  state.sendErrors.delete(id);
  renderAll(true);
  announce("Message sent. Waiting for the assistant. You can open another conversation while it works.");
  try {
    const result = await api(`/api/conversations/${encodeURIComponent(id)}/messages`, {
      method: "POST",
      body: JSON.stringify({ content }),
    });
    state.messages.set(id, [...previousMessages, result.user_message, result.assistant_message]);
    if (result.assistant_message.status === "error") {
      state.drafts.set(id, originalDraft);
      state.sendErrors.set(id, "The assistant could not complete this reply. Your draft is below so you can edit or resend it.");
      announce("The assistant could not complete the reply. Your draft has been kept.");
    } else announce(state.selectedId === id ? "The assistant’s reply is ready." : "A reply is ready in another conversation.");
    refreshConversations();
  } catch (error) {
    state.drafts.set(id, originalDraft);
    state.sendErrors.set(id, error.message);
    announce("The message could not be completed. Your draft has been kept.");
  } finally {
    state.pending.delete(id);
    renderSidebar();
    // A reply belongs to its original conversation, even after navigation.
    if (state.selectedId === id) {
      renderChat(true);
      renderControls();
      elements.input.focus();
    }
  }
}

elements.newChat.addEventListener("click", newConversation);
elements.ratingUp.addEventListener("click", () => saveRating("up"));
elements.ratingDown.addEventListener("click", () => saveRating("down"));
elements.form.addEventListener("submit", sendMessage);
elements.input.addEventListener("input", () => {
  state.drafts.set(draftKey(), elements.input.value);
  state.sendErrors.delete(draftKey());
  renderControls();
});
elements.input.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
    event.preventDefault();
    if (!elements.send.disabled) elements.form.requestSubmit();
  }
});
elements.trace.checked = state.showTrace;
elements.trace.addEventListener("change", () => {
  state.showTrace = elements.trace.checked;
  saveValue(STORAGE.trace, String(state.showTrace));
  const scrollPosition = elements.scroll.scrollTop;
  renderChat();
  elements.scroll.scrollTop = scrollPosition;
});
for (const example of elements.examples) {
  example.addEventListener("click", () => {
    state.drafts.set(draftKey(), example.dataset.example);
    state.sendErrors.delete(draftKey());
    renderControls();
    elements.input.focus();
    announce("Example added to your draft. Edit it or send when ready.");
  });
}

renderAll();
loadConfig();
refreshConversations();
if (state.selectedId) loadConversation(state.selectedId);
