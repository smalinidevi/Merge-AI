const BRIDGE = "http://127.0.0.1:8765";

async function post(path, body) {
  try {
    const res = await fetch(BRIDGE + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return await res.json();
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function get(path) {
  try {
    const res = await fetch(BRIDGE + path);
    return await res.json();
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}

async function collectTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs.map((t) => ({
    id: t.id,
    url: t.url || "",
    title: t.title || "",
    active: !!t.active,
    windowId: t.windowId,
  }));
}

async function activeTabPayload() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab) return null;
  let text = "";
  try {
    const resp = await chrome.tabs.sendMessage(tab.id, { type: "merge_ai_get_text" });
    text = (resp && resp.text) || "";
  } catch (_e) {
    // content script may be missing on chrome:// pages
  }
  return {
    id: tab.id,
    url: tab.url || "",
    title: tab.title || "",
    text,
  };
}

async function syncState() {
  const tabs = await collectTabs();
  const active_tab = await activeTabPayload();
  await post("/extension/update", { tabs, active_tab });
}

async function handleCommand(cmd) {
  if (!cmd || !cmd.op) return;
  let active_tab = null;
  if (cmd.op === "navigate" && cmd.url) {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) {
      await chrome.tabs.update(tab.id, { url: cmd.url });
    } else {
      await chrome.tabs.create({ url: cmd.url });
    }
  } else if (cmd.op === "google_search" && cmd.query) {
    const url = "https://www.google.com/search?q=" + encodeURIComponent(cmd.query);
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab) await chrome.tabs.update(tab.id, { url });
    else await chrome.tabs.create({ url });
  } else if (cmd.op === "activate_tab" && cmd.tab_id != null) {
    const id = Number(cmd.tab_id);
    await chrome.tabs.update(id, { active: true });
    const t = await chrome.tabs.get(id);
    if (t && t.windowId != null) {
      await chrome.windows.update(t.windowId, { focused: true });
    }
  }
  // brief delay for navigation
  await new Promise((r) => setTimeout(r, 400));
  const tabs = await collectTabs();
  active_tab = await activeTabPayload();
  await post("/extension/command_result", {
    ok: true,
    op: cmd.op,
    tabs,
    active_tab,
  });
}

async function tick() {
  await syncState();
  const polled = await get("/poll_command");
  if (polled && polled.command) {
    await handleCommand(polled.command);
  }
}

chrome.runtime.onInstalled.addListener(() => {
  post("/extension/hello", { hello: true });
});

chrome.tabs.onActivated.addListener(() => {
  syncState();
});
chrome.tabs.onUpdated.addListener((_id, info) => {
  if (info.status === "complete") syncState();
});

setInterval(tick, 1500);
tick();
