const PAGE_URL = "http://localhost:8000";
const SERVER_URL = "http://localhost:8001";

const statusDot = document.getElementById("statusDot");
const statusText = document.getElementById("statusText");
const responseBox = document.getElementById("response");
const responseArea = document.getElementById("responseArea");
const questionBubble = document.getElementById("questionBubble");
const followUpBubble = document.getElementById("followUpBubble");
const scanBtn = document.getElementById("scan");
const modeToggle = document.getElementById("modeToggle");
const modeLabel = document.getElementById("modeLabel");

let mode = "page"; // "page" or "server"

function setStatus(state, text) {
  statusDot.className = "status-dot " + state;
  statusText.innerText = text;
}

function typeText(element, text, speed = 10) {
  element.innerText = "";
  let i = 0;
  function typing() {
    if (i < text.length) {
      element.innerText += text.charAt(i);
      i++;
      setTimeout(typing, speed);
    }
  }
  typing();
}

function showThinking() {
  responseBox.className = "";
  responseBox.innerHTML = `
    <div class="thinking">
      <div class="dots"><span></span><span></span><span></span></div>
      Thinking…
    </div>`;
  responseArea.classList.add("visible");
  followUpBubble.style.display = "none";
  followUpBubble.innerText = "";
  questionBubble.innerText = "";

  // Clear source tags
  const existing = document.getElementById("sourcesUsedPopup");
  if (existing) existing.innerHTML = "";
  const existingLabel = document.getElementById("sourcesLabelPopup");
  if (existingLabel) existingLabel.style.display = "none";
}

function applyMode() {
  if (mode === "page") {
    modeLabel.innerText = "Page Mode";
    scanBtn.style.display = "flex";
    setStatus("", "Not scanned — click Scan Page to begin");
  } else {
    modeLabel.innerText = "Server Mode";
    scanBtn.style.display = "none";
    setStatus("ready", "Using server knowledge base");
  }
}

modeToggle.onclick = () => {
  mode = mode === "page" ? "server" : "page";
  modeToggle.classList.toggle("active");
  applyMode();
};

// Scan page
scanBtn.onclick = async () => {
  scanBtn.disabled = true;
  setStatus("loading", "Scanning page…");

  let [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

  chrome.tabs.sendMessage(tab.id, { type: "GET_TEXT" }, async (response) => {
    if (!response || !response.text) {
      setStatus("error", "Could not extract page content");
      scanBtn.disabled = false;
      return;
    }

    try {
      await fetch(`${PAGE_URL}/embed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: response.text })
      });
      setStatus("ready", "Page scanned — ready to answer questions");
    } catch (err) {
      setStatus("error", "Backend not reachable");
    }

    scanBtn.disabled = false;
  });
};

// Enter key
document.getElementById("question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    document.getElementById("ask").click();
  }
});

// Ensure source tag elements exist in the DOM (appended once)
function ensureSourceElements() {
  if (!document.getElementById("sourcesLabelPopup")) {
    const label = document.createElement("div");
    label.id = "sourcesLabelPopup";
    label.style.cssText = "font-size:10px;color:#64748b;margin-top:8px;margin-bottom:4px;font-family:monospace;text-transform:uppercase;letter-spacing:0.08em;display:none;";
    label.innerText = "Sources";
    responseArea.appendChild(label);
  }
  if (!document.getElementById("sourcesUsedPopup")) {
    const wrap = document.createElement("div");
    wrap.id = "sourcesUsedPopup";
    wrap.style.cssText = "display:flex;flex-wrap:wrap;gap:5px;margin-top:2px;";
    responseArea.appendChild(wrap);
  }
}

// Ask
document.getElementById("ask").onclick = async () => {
  const question = document.getElementById("question").value.trim();
  const askBtn = document.getElementById("ask");

  if (!question) {
    responseBox.className = "answer-bubble";
    responseBox.innerText = "⚠️ Please enter a question.";
    responseArea.classList.add("visible");
    return;
  }

  const BASE = mode === "page" ? PAGE_URL : SERVER_URL;

  askBtn.disabled = true;
  showThinking();
  questionBubble.innerText = "You asked: " + question;
  setStatus("loading", "Generating answer…");

  ensureSourceElements();

  try {
    let res = await fetch(`${BASE}/ask`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question })
    });

    let data = await res.json();

    responseBox.className = "answer-bubble";
    typeText(responseBox, data.answer || "No answer returned.", 10);

    // Show source file tags
    const sourcesWrap = document.getElementById("sourcesUsedPopup");
    const sourcesLabel = document.getElementById("sourcesLabelPopup");
    sourcesWrap.innerHTML = "";

    if (data.sources && data.sources.length) {
      sourcesLabel.style.display = "block";
      data.sources.forEach(filename => {
        const tag = document.createElement("span");
        tag.style.cssText = "font-size:10px;padding:2px 8px;background:rgba(91,127,255,0.1);border:1px solid rgba(91,127,255,0.25);border-radius:4px;color:#93c5fd;font-family:monospace;display:inline-flex;align-items:center;gap:3px;";
        tag.innerText = "📄 " + filename;
        sourcesWrap.appendChild(tag);
      });
    } else {
      sourcesLabel.style.display = "none";
    }

    // AFTER
      if (data.follow_up) {
        const delay = (data.answer?.length || 0) * 10 + 300;
        setTimeout(() => {
          followUpBubble.style.display = "block";
          followUpBubble.style.cursor = "pointer";
          followUpBubble.title = "Click to ask this question";
          typeText(followUpBubble, "💡 " + data.follow_up, 14);
          followUpBubble.onclick = () => {
            document.getElementById("question").value = data.follow_up;
            document.getElementById("ask").click();
          };
        }, delay);
      }

    setStatus("ready", mode === "page" ? "Page scanned — ready to answer questions" : "Using server knowledge base");

    // Highlight only in page mode
    if (mode === "page") {
      let [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      chrome.tabs.sendMessage(tab.id, { type: "HIGHLIGHT", text: data.answer });
    }

  } catch (err) {
    responseBox.className = "answer-bubble";
    responseBox.innerText = "❌ Error: Backend not reachable.";
    setStatus("error", "Backend not reachable");
  }

  askBtn.disabled = false;
};

applyMode();