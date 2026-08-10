const DEFAULT_URL = "http://10.0.0.252:8877";
const STORAGE_KEY = "draftDashboardUrl";

const urlInput = document.getElementById("urlInput");
const frame = document.getElementById("dashFrame");
const saveBtn = document.getElementById("saveBtn");
const reloadBtn = document.getElementById("reloadBtn");

function loadUrl(url) {
  frame.src = url;
}

chrome.storage.sync.get([STORAGE_KEY], (result) => {
  const saved = result[STORAGE_KEY] || DEFAULT_URL;
  urlInput.value = saved;
  loadUrl(saved);
});

saveBtn.addEventListener("click", () => {
  let url = urlInput.value.trim();
  if (url && !/^https?:\/\//i.test(url)) {
    url = "http://" + url;
    urlInput.value = url;
  }
  if (!url) return;
  chrome.storage.sync.set({ [STORAGE_KEY]: url }, () => {
    loadUrl(url);
  });
});

reloadBtn.addEventListener("click", () => {
  frame.src = frame.src;
});

urlInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveBtn.click();
});
