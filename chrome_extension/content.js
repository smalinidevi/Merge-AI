// Extract visible text for Merge AI RAG-style browser context.
(function () {
  function pageText() {
    try {
      const body = document.body ? document.body.innerText : "";
      return (body || "").replace(/\s+\n/g, "\n").trim().slice(0, 50000);
    } catch (e) {
      return "";
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg && msg.type === "merge_ai_get_text") {
      sendResponse({ text: pageText(), url: location.href, title: document.title });
      return true;
    }
    return false;
  });
})();
