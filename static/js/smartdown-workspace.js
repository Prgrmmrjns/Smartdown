      (function () {
        function apiUrl(path) {
          return new URL(path, window.location.href).toString();
        }

        function isPdfFile(f) {
          if (!f || !f.name) return false;
          var lower = f.name.toLowerCase();
          if (lower.endsWith(".pdf")) return true;
          var t = (f.type || "").toLowerCase();
          return t === "application/pdf" || t === "application/x-pdf";
        }

        function formatApiError(data, res) {
          if (res.status === 413) {
            return (
              "Request body too large for this server (common with nginx: increase client_max_body_size). " +
              "For Notion, try \"Text only — skip images\" to shrink the JSON."
            );
          }
          var d = data && data.detail;
          if (Array.isArray(d)) {
            return d
              .map(function (x) {
                return typeof x === "string" ? x : x && x.msg ? x.msg : JSON.stringify(x);
              })
              .join("; ");
          }
          if (d != null && typeof d === "object") {
            return JSON.stringify(d);
          }
          if (typeof d === "string") return d;
          return res.statusText || "Request failed";
        }

        /** FastAPI errors are JSON; proxies / crashes often return HTML or plain text. */
        function parseApiJsonBody(res, text) {
          var raw = text == null ? "" : String(text);
          var trimmed = raw.trim();
          if (!trimmed) {
            if (!res.ok) {
              throw new Error((res.statusText || "Error") + " (" + res.status + ")");
            }
            return {};
          }
          try {
            return JSON.parse(trimmed);
          } catch (e) {
            if (!res.ok) {
              var snip = trimmed.replace(/\s+/g, " ").slice(0, 420);
              throw new Error("Server returned non-JSON (" + res.status + "): " + snip);
            }
            throw new Error("Server response was not valid JSON.");
          }
        }

        if (
          typeof window.markdownit !== "function" ||
          typeof window.markdownitTexmath !== "function" ||
          !window.katex ||
          !window.DOMPurify ||
          !window.JSZip ||
          typeof TurndownService === "undefined" ||
          typeof turndownPluginGfm === "undefined"
        ) {
          var err0 = document.getElementById("error");
          err0.textContent = "Preview libraries failed to load. Check /static/vendor assets.";
          err0.classList.remove("hidden");
          return;
        }

        var md = window.markdownit({ html: true, linkify: true, typographer: false });
        if (typeof md.enable === "function") {
          md.enable(["table"]);
        }
        md.use(window.markdownitTexmath, {
          engine: window.katex,
          delimiters: ["dollars", "brackets"],
          katexOptions: { throwOnError: false, output: "html" },
        });
        var DOMPurify = window.DOMPurify;
        var JSZip = window.JSZip;

        var turndownService = new TurndownService({
          headingStyle: "atx",
          codeBlockStyle: "fenced",
          bulletListMarker: "-",
          emDelimiter: "*",
        });
        turndownService.use(turndownPluginGfm.gfm);
        /* Default Turndown escapes *, #, -, etc. in text nodes so “markdown-looking” typing in the
           preview becomes \\* and \\# in the source. Keep backslashes and backticks only so raw
           # headings, *emphasis*, lists, and pipe tables round-trip when edited on the canvas. */
        turndownService.escape = function (string) {
          return String(string).replace(/\\/g, "\\\\").replace(/`/g, "\\`");
        };
        turndownService.addRule("preserveKatex", {
          filter: function (node) {
            if (node.nodeName !== "SPAN" || !node.className) return false;
            var cn = typeof node.className === "string" ? node.className : "";
            if (!/\bkatex\b/.test(cn)) return false;
            var p = node.parentElement;
            if (p && p.className && /\bkatex\b/.test(String(p.className))) return false;
            return true;
          },
          replacement: function (content, node) {
            return "\n\n" + node.outerHTML + "\n\n";
          },
        });

        var drop = document.getElementById("drop");
        var fileInput = document.getElementById("file");
        var pdfUrlInput = document.getElementById("pdfUrl");
        var errorEl = document.getElementById("error");
        var mainEmpty = document.getElementById("mainEmpty");
        var mainWorkspace = document.getElementById("mainWorkspace");
        var workspaceError = document.getElementById("workspaceError");
        var pdfFrame = document.getElementById("pdfFrame");
        var mdEditor = document.getElementById("mdEditor");
        var mdPlaceholder = document.getElementById("mdPlaceholder");
        var mdRendered = document.getElementById("mdRendered");
        var chatMessages = document.getElementById("chatMessages");
        var chatInput = document.getElementById("chatInput");
        var chatSend = document.getElementById("chatSend");
        var clarifyScope = document.getElementById("clarifyScope");
        var workspaceTitle = document.getElementById("workspaceTitle");
        var btnOtherPdf = document.getElementById("btnOtherPdf");
        var dlZip = document.getElementById("dlZip");
        var dlMd = document.getElementById("dlMd");
        var dlDocx = document.getElementById("dlDocx");
        var copyMd = document.getElementById("copyMd");
        var viewTogglePdf = document.getElementById("viewTogglePdf");
        var viewToggleMdPrev = document.getElementById("viewToggleMdPrev");
        var viewToggleSidebar = document.getElementById("viewToggleSidebar");
        var appShell = document.getElementById("appShell");
        var sidebarEl = document.getElementById("sidebar");
        var sidebarResizer = document.getElementById("sidebarResizer");
        var agentProgressBar = document.getElementById("agentProgressBar");
        var agentProgressText = document.getElementById("agentProgressText");
        var notionCanvas = document.getElementById("notionCanvas");
        var viewStack = document.getElementById("viewStack");
        var viewPanePool = document.getElementById("viewPanePool");
        var viewPanePdf = document.getElementById("viewPanePdf");
        var viewPanePreview = document.getElementById("viewPanePreview");
        var llmChoice = document.getElementById("llmChoice");
        var LLM_CHOICE_SEP = "\t";

        function encodeLlmChoice(provider, modelId) {
          return (provider || "mistral") + LLM_CHOICE_SEP + (modelId || "");
        }

        function parseLlmChoiceValue(v) {
          if (!v || typeof v !== "string") return { provider: "mistral", model: null };
          var i = v.indexOf(LLM_CHOICE_SEP);
          if (i < 0) return { provider: "mistral", model: null };
          return { provider: v.slice(0, i), model: v.slice(i + LLM_CHOICE_SEP.length) || null };
        }

        function currentLlmProvider() {
          return parseLlmChoiceValue(llmChoice && llmChoice.value).provider;
        }

        function currentLlmModel() {
          return parseLlmChoiceValue(llmChoice && llmChoice.value).model;
        }
        var llmOptionsHint = document.getElementById("llmOptionsHint");
        var llmOptions = null;
        var blockNotesToolbar = document.getElementById("blockNotesToolbar");
        var viewPanesByKey = {
          pdf: viewPanePdf,
          notes: viewPanePreview,
        };
        var prevViewOrderKey = "";

        var selectedFile = null;
        var pdfObjectUrl = null;
        var documentId = null;
        var urlAutoImportTimer = null;
        var URL_AUTO_IMPORT_DEBOUNCE_MS = 500;
        var importInFlight = false;
        var extractDeferred = false;
        var lastImportMarkdownReady = false;
        var rawMarkdown = "";
        var lastImages = {};
        var previewRaf = null;
        var previewSyncTimer = null;
        /** @type {Record<string, number> | null} */
        var previewDirtyBlockIndices = null;
        var lastBase = "converted";
        var chatHistory = [];
        var activeBlockMenuIndex = null;
        /** @type {{ allIndices: number[] } | null} */
        var activeClarifyBlock = null;
        var clarifyRoundInFlight = false;
        var CLARIFY_DEFAULT_ONE = "Explain this section clearly.";
        var CLARIFY_DEFAULT_MULTI = "Explain these selected sections clearly.";
        var lastBlockCheckboxIndex = null;
        var MISTRAL_KEY_SS = "smartdown_mistral_api_key";
        var MD_UNDO_MAX = 40;
        var mdUndoStack = [];
        var mdRedoStack = [];
        var mdUndoBurstOpen = false;
        var mdUndoBurstTimer = null;
        var mdApplyingUndoRedo = false;

        function isFunctionalHttpUrl(str) {
          var u = (str || "").trim();
          try {
            var p = new URL(u);
            return p.protocol === "http:" || p.protocol === "https:";
          } catch (e) {
            return false;
          }
        }

        function hasImportSource() {
          if (selectedFile) return true;
          var u = (pdfUrlInput && pdfUrlInput.value ? pdfUrlInput.value : "").trim();
          return isFunctionalHttpUrl(u);
        }

        function syncPdfUrlFieldVisibility() {
          if (!pdfUrlInput) return;
          pdfUrlInput.classList.toggle("hidden", !!selectedFile);
        }

        function updateImportSourceStatus() {
          var el = document.getElementById("importSourceStatus");
          if (!el) return;
          if (selectedFile) {
            if (documentId && !extractDeferred) el.textContent = "PDF ready";
            else if (documentId && extractDeferred) el.textContent = "PDF on server — extract for Markdown";
            else if (importInFlight) el.textContent = "Importing PDF…";
            else el.textContent = "PDF selected";
          } else {
            var u = (pdfUrlInput && pdfUrlInput.value ? pdfUrlInput.value : "").trim();
            if (isFunctionalHttpUrl(u)) {
              el.textContent = "PDF URL set";
            } else {
              el.textContent = "No PDF or URL yet";
            }
          }
        }

        function syncImportActionButtons() {
          var mdReady = !!(documentId && !extractDeferred);
          var det = document.getElementById("filePanelDetails");
          if (det) {
            if (mdReady && !lastImportMarkdownReady) {
              det.open = false;
            } else if (!mdReady) {
              det.open = true;
            }
          }
          lastImportMarkdownReady = mdReady;
          updateImportSourceStatus();
        }

        function syncProviderAuxUi() {
          var prov = currentLlmProvider();
          var mRow = document.getElementById("mistralKeyRow");
          if (mRow) mRow.classList.toggle("hidden", prov !== "mistral");
          var ohh = document.getElementById("ollamaHostHint");
          if (ohh) ohh.classList.toggle("hidden", prov !== "ollama");
        }

        function mdConversionReady() {
          return !!(documentId && !extractDeferred);
        }

        function syncImportModeUi() {
          var ok = mdConversionReady();
          var aiSec = document.getElementById("aiToolsSection");
          if (aiSec) aiSec.classList.toggle("hidden", !ok);
          syncProviderAuxUi();
        }

        function mistralApiKeyPayload() {
          if (!llmChoice || currentLlmProvider() !== "mistral") return null;
          var el = document.getElementById("mistralApiKey");
          var v = el && el.value ? el.value.trim() : "";
          return v || null;
        }

        function mistralOcrApiKeyPayload() {
          var el = document.getElementById("mistralApiKey");
          var v = el && el.value ? el.value.trim() : "";
          if (v) return v;
          try {
            var ss = sessionStorage.getItem(MISTRAL_KEY_SS);
            return ss && ss.trim() ? ss.trim() : null;
          } catch (e0) {
            return null;
          }
        }

        function assertMistralKeyIfNeeded() {
          if (!llmChoice || currentLlmProvider() !== "mistral") return;
          if (!mistralApiKeyPayload()) {
            throw new Error(
              "Enter your Mistral API key (AI section) or switch provider to Ollama."
            );
          }
        }

        function bindMistralKeySessionStorage() {
          var el = document.getElementById("mistralApiKey");
          if (!el) return;
          try {
            var v = sessionStorage.getItem(MISTRAL_KEY_SS);
            if (v) el.value = v;
          } catch (e0) {}
          el.addEventListener("input", function () {
            try {
              sessionStorage.setItem(MISTRAL_KEY_SS, el.value || "");
            } catch (e1) {}
          });
        }

        function mistralModelList() {
          return (llmOptions && llmOptions.mistral_models) || [];
        }
        function ollamaModelList() {
          return (llmOptions && llmOptions.ollama_models) || [];
        }

        function rebuildLlmChoiceSelect() {
          if (!llmChoice) return;
          var prev = parseLlmChoiceValue(llmChoice.value);
          llmChoice.innerHTML = "";

          function addGroup(label, providerKey, list) {
            if (!list || !list.length) return;
            var og = document.createElement("optgroup");
            og.label = label;
            list.forEach(function (m) {
              var opt = document.createElement("option");
              opt.value = encodeLlmChoice(providerKey, m.id);
              opt.textContent = m.label || m.id;
              og.appendChild(opt);
            });
            llmChoice.appendChild(og);
          }

          addGroup("Mistral (cloud)", "mistral", mistralModelList());
          addGroup("Ollama (local)", "ollama", ollamaModelList());

          function pickValue(provider, modelId) {
            var want = encodeLlmChoice(provider, modelId);
            for (var i = 0; i < llmChoice.options.length; i++) {
              if (llmChoice.options[i].value === want) {
                llmChoice.selectedIndex = i;
                return true;
              }
            }
            return false;
          }

          if (!pickValue(prev.provider, prev.model)) {
            var dp = (llmOptions && llmOptions.defaults && llmOptions.defaults.provider) || "mistral";
            var dm = (llmOptions && llmOptions.defaults && llmOptions.defaults.model) || null;
            if (!pickValue(dp, dm) && llmChoice.options.length) {
              llmChoice.selectedIndex = 0;
            }
          }

          if (llmOptionsHint) {
            if (ollamaModelList().length === 0) {
              llmOptionsHint.textContent =
                "No Ollama models at " +
                ((llmOptions && llmOptions.ollama_host) || "Ollama") +
                ". Run `ollama serve`, pull a model, then refresh this page.";
            } else {
              llmOptionsHint.textContent = "";
            }
          }
          llmChoice.disabled = !llmChoice.options.length;
          syncProviderAuxUi();
        }

        function applyLlmOptionsPayload(data) {
          llmOptions = data || {};
          if (!llmChoice) return;
          rebuildLlmChoiceSelect();
        }

        function loadLlmOptions() {
          return fetch(apiUrl("/api/llm-options"))
            .then(function (res) {
              return res.json().then(function (data) {
                return { ok: res.ok, data: data };
              });
            })
            .then(function (x) {
              if (x.ok) applyLlmOptionsPayload(x.data);
              else if (llmOptionsHint) llmOptionsHint.textContent = "Could not load model list.";
            })
            .catch(function () {
              if (llmOptionsHint) llmOptionsHint.textContent = "Could not load model list.";
            });
        }

        if (llmChoice) {
          llmChoice.addEventListener("change", syncProviderAuxUi);
        }
        syncProviderAuxUi();
        loadLlmOptions();
        bindMistralKeySessionStorage();
        bindBlockNotesUi();
        window.addEventListener("keydown", notesWorkspaceHotkeys, true);
        function syncExtractPdfButton() {
          var btn = document.getElementById("btnExtractPdf");
          if (btn) {
            btn.classList.toggle("hidden", !documentId || !extractDeferred);
          }
          var meta = document.getElementById("workspaceMetaBlock");
          if (meta) {
            meta.classList.toggle("hidden", !documentId);
          }
          var exportsBlk = document.getElementById("postMdExportsBlock");
          if (exportsBlk) {
            exportsBlk.classList.toggle("hidden", !mdConversionReady());
          }
        }

        syncImportModeUi();
        syncPdfUrlFieldVisibility();
        syncImportActionButtons();

        function resetFirstPassOptions() {
          syncImportModeUi();
          syncImportActionButtons();
        }

        function showUploadError(msg) {
          errorEl.textContent = msg;
          errorEl.classList.toggle("hidden", !msg);
        }

        function showWorkspaceError(msg) {
          workspaceError.textContent = msg;
          workspaceError.classList.toggle("hidden", !msg);
        }

        function getVisibleViewOrder() {
          var o = [];
          if (viewTogglePdf && viewTogglePdf.checked) o.push("pdf");
          if (viewToggleMdPrev && viewToggleMdPrev.checked) o.push("notes");
          if (!o.length) {
            if (viewToggleMdPrev) viewToggleMdPrev.checked = true;
            o.push("notes");
          }
          return o;
        }

        function layoutViewStack() {
          if (!viewStack || !viewPanePool) return;
          var order = getVisibleViewOrder();
          var orderKey = order.join(",");
          var resetPaneSizes = orderKey !== prevViewOrderKey;
          prevViewOrderKey = orderKey;

          ["pdf", "notes"].forEach(function (k) {
            var el = viewPanesByKey[k];
            if (!el) return;
            if (el.parentNode) el.parentNode.removeChild(el);
            viewPanePool.appendChild(el);
            if (resetPaneSizes) {
              el.style.flex = "";
              el.style.height = "";
            }
          });
          while (viewStack.firstChild) {
            viewStack.removeChild(viewStack.firstChild);
          }
          order.forEach(function (key, idx) {
            var pane = viewPanesByKey[key];
            if (!pane) return;
            viewStack.appendChild(pane);
            if (idx < order.length - 1) {
              var r = document.createElement("div");
              r.className = "view-resizer";
              r.setAttribute("role", "separator");
              r.setAttribute("aria-orientation", "horizontal");
              r.setAttribute("aria-label", "Drag to resize panels");
              r.tabIndex = 0;
              viewStack.appendChild(r);
            }
          });
        }

        if (viewStack) {
          viewStack.addEventListener("pointerdown", function (e) {
            var t = e.target;
            if (!t || !t.classList || !t.classList.contains("view-resizer")) return;
            if (e.pointerType === "mouse" && e.button !== 0) return;
            e.preventDefault();
            var topPane = t.previousElementSibling;
            var bottomPane = t.nextElementSibling;
            if (
              !topPane ||
              !bottomPane ||
              !topPane.classList.contains("view-pane") ||
              !bottomPane.classList.contains("view-pane")
            ) {
              return;
            }
            var startY = e.clientY;
            var h1 = topPane.getBoundingClientRect().height;
            var h2 = bottomPane.getBoundingClientRect().height;
            var minH = 100;
            function onMove(ev) {
              var dy = ev.clientY - startY;
              var nh1 = h1 + dy;
              var nh2 = h2 - dy;
              if (nh1 < minH) {
                dy = minH - h1;
                nh1 = minH;
                nh2 = h2 - dy;
              } else if (nh2 < minH) {
                dy = h2 - minH;
                nh2 = minH;
                nh1 = h1 + dy;
              }
              topPane.style.flex = "none";
              bottomPane.style.flex = "none";
              topPane.style.height = nh1 + "px";
              bottomPane.style.height = nh2 + "px";
            }
            function onUp() {
              t.releasePointerCapture(e.pointerId);
              document.removeEventListener("pointermove", onMove);
              document.removeEventListener("pointerup", onUp);
              document.removeEventListener("pointercancel", onUp);
            }
            t.setPointerCapture(e.pointerId);
            document.addEventListener("pointermove", onMove);
            document.addEventListener("pointerup", onUp);
            document.addEventListener("pointercancel", onUp);
          });
        }

        if (viewTogglePdf) viewTogglePdf.addEventListener("change", layoutViewStack);
        if (viewToggleMdPrev) viewToggleMdPrev.addEventListener("change", layoutViewStack);

        function syncSidebarVisibilityFromToggle() {
          if (!appShell || !viewToggleSidebar) return;
          appShell.classList.toggle("sidebar-hidden", !viewToggleSidebar.checked);
        }
        if (viewToggleSidebar) viewToggleSidebar.addEventListener("change", syncSidebarVisibilityFromToggle);

        var SIDEBAR_MIN_W = 260;
        var SIDEBAR_MAX_CAP = 560;
        function sidebarMaxWidthPx() {
          return Math.min(SIDEBAR_MAX_CAP, Math.floor(window.innerWidth * 0.58));
        }
        function clampSidebarWidth(w) {
          return Math.max(SIDEBAR_MIN_W, Math.min(sidebarMaxWidthPx(), w));
        }
        if (sidebarResizer && sidebarEl) {
          sidebarResizer.addEventListener("pointerdown", function (e) {
            if (window.innerWidth <= 820) return;
            if (e.pointerType === "mouse" && e.button !== 0) return;
            e.preventDefault();
            var startX = e.clientX;
            var startW = sidebarEl.getBoundingClientRect().width;
            function onMove(ev) {
              var nw = clampSidebarWidth(startW + (ev.clientX - startX));
              sidebarEl.style.width = nw + "px";
            }
            function onUp() {
              sidebarResizer.releasePointerCapture(e.pointerId);
              document.removeEventListener("pointermove", onMove);
              document.removeEventListener("pointerup", onUp);
              document.removeEventListener("pointercancel", onUp);
            }
            sidebarResizer.setPointerCapture(e.pointerId);
            document.addEventListener("pointermove", onMove);
            document.addEventListener("pointerup", onUp);
            document.addEventListener("pointercancel", onUp);
          });
          sidebarResizer.addEventListener("keydown", function (e) {
            if (window.innerWidth <= 820) return;
            if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
            e.preventDefault();
            var step = e.shiftKey ? 32 : 12;
            var w = sidebarEl.getBoundingClientRect().width;
            var nw = e.key === "ArrowRight" ? w + step : w - step;
            sidebarEl.style.width = clampSidebarWidth(nw) + "px";
          });
        }

        function setAgentProgress(active, message) {
          if (agentProgressBar) {
            agentProgressBar.classList.toggle("hidden", !active);
            agentProgressBar.setAttribute("aria-busy", active ? "true" : "false");
          }
          if (agentProgressText && message) agentProgressText.textContent = message;
          if (notionCanvas) notionCanvas.classList.toggle("agent-working", !!active);
          if (mainWorkspace) mainWorkspace.setAttribute("aria-busy", active ? "true" : "false");
        }

        function openDocumentUI() {
          mainEmpty.classList.add("hidden");
          mainEmpty.setAttribute("aria-hidden", "true");
          mainWorkspace.classList.remove("hidden");
          layoutViewStack();
          syncImportModeUi();
          syncExtractPdfButton();
        }

        function closeDocumentUI() {
          mainEmpty.classList.remove("hidden");
          mainEmpty.setAttribute("aria-hidden", "false");
          mainWorkspace.classList.add("hidden");
          var aiSec = document.getElementById("aiToolsSection");
          if (aiSec) aiSec.classList.add("hidden");
        }

        function normalizeImageKey(rawPath) {
          var path = rawPath.split("?")[0].trim().replace(/\\/g, "/");
          if (path.indexOf("images/") === 0) return path;
          var i = path.indexOf("images/");
          if (i >= 0) return path.slice(i);
          return path;
        }

        function inlineImages(markdown, images) {
          return markdown.replace(/!\[([^\]]*)\]\(([^)]+)\)/g, function (full, alt, rawPath) {
            var key = normalizeImageKey(rawPath);
            var b64 = images[key];
            if (!b64) return full;
            return "![" + alt + "](data:image/png;base64," + b64 + ")";
          });
        }

        function normalizeB64Chunk(s) {
          return (s || "").replace(/\s+/g, "");
        }

        /** Turn data-URL images in markdown back into images/ paths (keeps API payloads small). */
        function restoreDataUriImagesToPaths(text) {
          if (!text || !lastImages) return text || "";
          return text.replace(
            /!\[([^\]]*)\]\(data:image\/[a-z0-9.+-]+;base64,([^)]+)\)/gi,
            function (_full, alt, b64) {
              var nb = normalizeB64Chunk(b64);
              for (var key in lastImages) {
                if (Object.prototype.hasOwnProperty.call(lastImages, key) && normalizeB64Chunk(lastImages[key]) === nb) {
                  return "![" + alt + "](" + key + ")";
                }
              }
              return "![" + alt + "](images/_unknown_inline.png)";
            }
          );
        }

        function markdownForApi() {
          return restoreDataUriImagesToPaths(mdEditor.value || "");
        }

        function getMarkdownForPreview() {
          return mdEditor.value || "";
        }

        function getPreviewBlockAtCaret() {
          var sel = window.getSelection();
          if (!sel.rangeCount) return null;
          var n = sel.anchorNode;
          if (!n) return null;
          if (n.nodeType === 3) n = n.parentElement;
          var root = mdRendered;
          while (n && n !== root) {
            if (!n || n === document.body) return null;
            var tag = n.tagName;
            if (/^(P|H[1-6]|LI|BLOCKQUOTE|PRE|TD|TH)$/i.test(tag)) return n;
            n = n.parentElement;
          }
          return null;
        }

        function textFromBlockStartToCaret(block) {
          var sel = window.getSelection();
          if (!sel.rangeCount || !block) return "";
          try {
            var r = document.createRange();
            r.selectNodeContents(block);
            r.setEnd(sel.anchorNode, sel.anchorOffset);
            return r.toString().replace(/\u00a0/g, " ");
          } catch (e) {
            return "";
          }
        }

        function placeCaretInElement(el) {
          if (!el) return;
          try {
            el.focus();
          } catch (e2) {}
          var s = window.getSelection();
          var r = document.createRange();
          function firstText(node) {
            if (!node) return null;
            if (node.nodeType === 3) return node;
            for (var i = 0; i < node.childNodes.length; i++) {
              var x = firstText(node.childNodes[i]);
              if (x) return x;
            }
            return null;
          }
          var t = firstText(el);
          if (t) {
            var len = t.length;
            r.setStart(t, len ? len : 0);
            r.collapse(true);
          } else {
            var z = document.createTextNode("\u200b");
            el.appendChild(z);
            r.setStart(z, 0);
            r.collapse(true);
          }
          s.removeAllRanges();
          s.addRange(r);
        }

        function placeCaretAtEndOfElement(el) {
          if (!el) return;
          try {
            el.focus();
          } catch (e2) {}
          var s = window.getSelection();
          var r = document.createRange();
          function lastText(node) {
            if (!node) return null;
            if (node.nodeType === 3) return node;
            for (var i = node.childNodes.length - 1; i >= 0; i--) {
              var x = lastText(node.childNodes[i]);
              if (x) return x;
            }
            return null;
          }
          var t = lastText(el);
          if (t) {
            r.setStart(t, t.length);
            r.collapse(true);
          } else {
            var z = document.createTextNode("\u200b");
            el.appendChild(z);
            r.setStart(z, 0);
            r.collapse(true);
          }
          s.removeAllRanges();
          s.addRange(r);
        }

        function getCaretOffsetInRoot(root) {
          var sel = window.getSelection();
          if (!sel.rangeCount) return 0;
          try {
            var range = sel.getRangeAt(0);
            var pre = document.createRange();
            pre.selectNodeContents(root);
            pre.setEnd(range.startContainer, range.startOffset);
            return pre.toString().length;
          } catch (ex) {
            return 0;
          }
        }

        function setCaretOffsetInRoot(root, offset) {
          offset = Math.max(0, offset | 0);
          if (!root) return false;
          var walk = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
          var n = walk.nextNode();
          var pos = 0;
          while (n) {
            var len = n.length;
            if (pos + len >= offset) {
              var r = document.createRange();
              r.setStart(n, Math.min(len, offset - pos));
              r.collapse(true);
              var s = window.getSelection();
              s.removeAllRanges();
              s.addRange(r);
              return true;
            }
            pos += len;
            n = walk.nextNode();
          }
          placeCaretInElement(root);
          return false;
        }

        function insertHrAndNewParagraph(block) {
          var hr = document.createElement("hr");
          var parent = block.parentNode;
          if (!parent) return false;
          parent.insertBefore(hr, block);
          parent.removeChild(block);
          var np = document.createElement("p");
          np.appendChild(document.createTextNode("\u200b"));
          if (hr.nextSibling) parent.insertBefore(np, hr.nextSibling);
          else parent.appendChild(np);
          placeCaretInElement(np);
          return true;
        }

        function tryApplyClosingBoldShortcut(e) {
          if (!e || e.data !== "*") return false;
          var sel = window.getSelection();
          if (!sel.rangeCount || !sel.isCollapsed) return false;
          var node = sel.anchorNode;
          if (!node || node.nodeType !== 3 || !mdRendered.contains(node)) return false;
          var off = sel.anchorOffset;
          var text = node.textContent || "";
          var before = text.slice(0, off);
          var m = before.match(/\*\*([^*]+)\*$/);
          if (!m || !m[1]) return false;
          e.preventDefault();
          var inner = m[1];
          var delStart = before.length - m[0].length;
          try {
            var r = document.createRange();
            r.setStart(node, delStart);
            r.setEnd(node, off);
            r.deleteContents();
            var strong = document.createElement("strong");
            strong.appendChild(document.createTextNode(inner));
            r.insertNode(strong);
            var tail = document.createTextNode("\u200b");
            if (strong.nextSibling) strong.parentNode.insertBefore(tail, strong.nextSibling);
            else strong.parentNode.appendChild(tail);
            var s2 = window.getSelection();
            var r2 = document.createRange();
            r2.setStart(tail, 0);
            r2.collapse(true);
            s2.removeAllRanges();
            s2.addRange(r2);
          } catch (err) {
            return false;
          }
          mdRendered.dispatchEvent(new Event("input", { bubbles: true }));
          return true;
        }

        function tryApplyHrOnEnter(e) {
          if (!e || e.key !== "Enter" || e.shiftKey) return false;
          var block = getPreviewBlockAtCaret();
          if (!block || !mdRendered.contains(block)) return false;
          var t = (block.textContent || "").replace(/\u200b/g, "").replace(/\s+/g, "");
          if (!/^(-{3,}|\*{3,}|_{3,})$/.test(t)) return false;
          e.preventDefault();
          insertHrAndNewParagraph(block);
          mdRendered.dispatchEvent(new Event("input", { bubbles: true }));
          return true;
        }

        function tryMergeWithNextBlockOnModifierM(e) {
          if (!e || (e.key !== "m" && e.key !== "M")) return false;
          if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return false;
          var inner =
            e.target && e.target.closest && e.target.closest(".sd-doc-block-inner");
          if (!inner || !mdRendered.contains(inner)) return false;
          if (inner.getAttribute("contenteditable") !== "true") return false;
          var sec = inner.closest(".sd-doc-block");
          if (!sec) return false;
          var idx = parseInt(sec.getAttribute("data-sd-index"), 10);
          if (idx !== idx) return false;
          var sl = window._sdBlockSlices;
          if (!sl || idx + 1 >= sl.length) return false;
          syncBlockNotesBeforeSliceMutation([idx, idx + 1]);
          sl = window._sdBlockSlices;
          if (!sl || idx + 1 >= sl.length) return false;
          var a = (sl[idx] || "").trim();
          var b = (sl[idx + 1] || "").trim();
          if (!a && !b) return false;
          e.preventDefault();
          if (!mergeConsecutiveBlockSlices([idx, idx + 1])) return true;
          var mergedSec = mdRendered.querySelector('.sd-doc-block[data-sd-index="' + idx + '"]');
          var mergedInner = mergedSec && mergedSec.querySelector(".sd-doc-block-inner");
          if (mergedInner) placeCaretAtEndOfElement(mergedInner);
          return true;
        }

        function tryApplyBlockMarkdownShortcut() {
          var block = getPreviewBlockAtCaret();
          if (!block || !mdRendered.contains(block)) return false;
          var raw = textFromBlockStartToCaret(block);
          var t = raw.replace(/\u200b/g, "").replace(/\n/g, "");

          var hm = t.match(/^(#{1,6})$/);
          if (hm) {
            var level = hm[1].length;
            var h = document.createElement("h" + level);
            h.appendChild(document.createTextNode("\u200b"));
            block.parentNode.replaceChild(h, block);
            placeCaretInElement(h);
            return true;
          }
          if (/^[-*]$/.test(t)) {
            var ul = document.createElement("ul");
            var li = document.createElement("li");
            li.appendChild(document.createTextNode("\u200b"));
            ul.appendChild(li);
            block.parentNode.replaceChild(ul, block);
            placeCaretInElement(li);
            return true;
          }
          var om = t.match(/^(\d+)\.$/);
          if (om) {
            var ol = document.createElement("ol");
            var li2 = document.createElement("li");
            li2.appendChild(document.createTextNode("\u200b"));
            ol.appendChild(li2);
            block.parentNode.replaceChild(ol, block);
            placeCaretInElement(li2);
            return true;
          }
          if (/^>$/.test(t)) {
            var bq = document.createElement("blockquote");
            var p = document.createElement("p");
            p.appendChild(document.createTextNode("\u200b"));
            bq.appendChild(p);
            block.parentNode.replaceChild(bq, block);
            placeCaretInElement(p);
            return true;
          }
          if (/^[-*+]\s\[\s\]$/.test(t) || /^[-*+]\s\[[xX]\]$/.test(t)) {
            var ulc = document.createElement("ul");
            var lic = document.createElement("li");
            lic.appendChild(document.createTextNode("\u200b"));
            ulc.appendChild(lic);
            block.parentNode.replaceChild(ulc, block);
            placeCaretInElement(lic);
            return true;
          }
          if (/^(-{3,}|\*{3,}|_{3,})$/.test(t)) {
            insertHrAndNewParagraph(block);
            return true;
          }
          return false;
        }

        var PURIFY_PREVIEW = {
          USE_PROFILES: { html: true },
          ADD_TAGS: [
            "button",
            "input",
            "eq",
            "eqn",
            "section",
            "svg",
            "path",
            "g",
            "line",
            "rect",
            "circle",
            "ellipse",
            "polygon",
            "polyline",
            "defs",
            "clipPath",
            "use",
            "math",
            "semantics",
            "mrow",
            "mi",
            "mo",
            "mn",
            "mspace",
            "mtext",
            "annotation",
          ],
          ADD_ATTR: [
            "class",
            "style",
            "contenteditable",
            "spellcheck",
            "type",
            "tabindex",
            "title",
            "checked",
            "aria-hidden",
            "aria-label",
            "aria-checked",
            "role",
            "xmlns",
            "width",
            "height",
            "viewBox",
            "fill",
            "stroke",
            "d",
            "x",
            "y",
            "x1",
            "y1",
            "x2",
            "y2",
            "stroke-width",
            "stroke-linecap",
            "stroke-linejoin",
            "transform",
            "encoding",
          ],
        };

        function setBlockInnersEditable(on) {
          var allow = !!on && !!documentId;
          var inners = mdRendered.querySelectorAll(".sd-doc-block-inner");
          for (var i = 0; i < inners.length; i++) {
            inners[i].setAttribute("contenteditable", allow ? "true" : "false");
            inners[i].setAttribute("spellcheck", allow ? "true" : "false");
          }
          if (allow && inners.length) {
            mdRendered.classList.add("notion-editable");
            mdRendered.setAttribute(
              "aria-label",
              "Document — edit text in blocks; toolbar: Add bullet, Explain, Merge, Delete"
            );
          } else {
            mdRendered.classList.remove("notion-editable");
            mdRendered.removeAttribute("aria-label");
          }
        }

        function blockIndexFromEditEventTarget(target) {
          var el = target && target.nodeType === 3 ? target.parentElement : target;
          if (!el || !el.closest || !mdRendered) return NaN;
          var inner = el.closest(".sd-doc-block-inner");
          if (!inner || !mdRendered.contains(inner)) return NaN;
          var sec = inner.closest(".sd-doc-block");
          if (!sec) return NaN;
          var idx = parseInt(sec.getAttribute("data-sd-index"), 10);
          return idx === idx ? idx : NaN;
        }

        function clearPreviewDirtyAndTimer() {
          if (previewSyncTimer) {
            clearTimeout(previewSyncTimer);
            previewSyncTimer = null;
          }
          previewDirtyBlockIndices = null;
        }

        function flushPendingDomEditsIfDirty() {
          var hadTimer = previewSyncTimer != null;
          if (previewSyncTimer) {
            clearTimeout(previewSyncTimer);
            previewSyncTimer = null;
          }
          if (!mdRendered || !mdRendered.classList.contains("block-notes-on")) return;
          var d = previewDirtyBlockIndices;
          if (d && Object.keys(d).length) {
            previewDirtyBlockIndices = null;
            applyDomEditsToSliceIndices(
              Object.keys(d).map(function (k) {
                return parseInt(k, 10);
              })
            );
            return;
          }
          if (hadTimer) {
            applyDomEditsToSlices();
          }
        }

        function flushPendingDomEditsIncrementalOrFull() {
          if (previewSyncTimer) {
            clearTimeout(previewSyncTimer);
            previewSyncTimer = null;
          }
          if (!mdRendered || !mdRendered.classList.contains("block-notes-on")) return;
          var d = previewDirtyBlockIndices;
          previewDirtyBlockIndices = null;
          if (d && Object.keys(d).length) {
            applyDomEditsToSliceIndices(
              Object.keys(d).map(function (k) {
                return parseInt(k, 10);
              })
            );
            return;
          }
          applyDomEditsToSlices();
        }

        function applyDomEditsToSlices() {
          var inners = mdRendered.querySelectorAll(".sd-doc-block-inner");
          if (!inners.length) return;
          var parts = [];
          for (var i = 0; i < inners.length; i++) {
            try {
              parts.push(turndownService.turndown(inners[i]).trim());
            } catch (e) {
              console.warn("turndown", e);
              parts.push("");
            }
          }
          mdEditor.value = restoreDataUriImagesToPaths(parts.join("\n\n"));
          window._sdBlockSlices = parts.slice();
        }

        function syncBlockNotesToMarkdown() {
          flushPendingDomEditsIncrementalOrFull();
        }

        function applyDomEditsToSliceIndices(indices) {
          if (!mdRendered || !mdRendered.classList.contains("block-notes-on")) return;
          var slices = window._sdBlockSlices;
          var inners = mdRendered.querySelectorAll(".sd-doc-block-inner");
          if (!slices || !inners.length || slices.length !== inners.length) {
            applyDomEditsToSlices();
            return;
          }
          var need = uniqueSortedIndices(indices);
          for (var u = 0; u < need.length; u++) {
            var i = need[u];
            if (i < 0 || i >= inners.length) continue;
            try {
              slices[i] = turndownService.turndown(inners[i]).trim();
            } catch (e) {
              console.warn("turndown", e);
            }
          }
          mdEditor.value = restoreDataUriImagesToPaths(slices.join("\n\n"));
        }

        function syncBlockNotesBeforeSliceMutation(participantIndices) {
          var hadPending = previewSyncTimer != null;
          if (hadPending) {
            clearTimeout(previewSyncTimer);
            previewSyncTimer = null;
          }
          if (!mdRendered || !mdRendered.classList.contains("block-notes-on")) return;
          if (hadPending) {
            flushPendingDomEditsIncrementalOrFull();
            return;
          }
          var need = uniqueSortedIndices(participantIndices);
          var ae = document.activeElement;
          var inner = ae && ae.closest && ae.closest(".sd-doc-block-inner");
          if (inner && mdRendered.contains(inner)) {
            var sec = inner.closest(".sd-doc-block");
            var fidx = sec ? parseInt(sec.getAttribute("data-sd-index"), 10) : NaN;
            if (fidx === fidx) {
              var inNeed = false;
              for (var q = 0; q < need.length; q++) {
                if (need[q] === fidx) {
                  inNeed = true;
                  break;
                }
              }
              if (!inNeed) need = uniqueSortedIndices(need.concat([fidx]));
            }
          }
          applyDomEditsToSliceIndices(need);
        }

        function splitMarkdownIntoBlocks(text) {
          if (!text || !String(text).trim()) return [];
          var t = String(text).replace(/\r\n/g, "\n");
          var chunks = t.split(/(?=^\s{0,3}#{1,6}(?!#)\s*[^\n]*$)/m);
          var out = [];
          for (var c = 0; c < chunks.length; c++) {
            var ch = chunks[c].trim();
            if (!ch) continue;
            var paras = ch.split(/\n{2,}/);
            for (var p = 0; p < paras.length; p++) {
              var s = paras[p].trim();
              if (s) out.push(s);
            }
          }
          return out;
        }

        function syncClarifyScope() {
          if (!clarifyScope) return;
          if (!activeClarifyBlock || !activeClarifyBlock.allIndices || !activeClarifyBlock.allIndices.length) {
            clarifyScope.textContent =
              "Select block(s) with the square toggles, then Explain (⌘E) or Add bullet (⌘B).";
            return;
          }
          var ids = activeClarifyBlock.allIndices;
          var labels = ids.map(function (i) {
            return "#" + (i + 1);
          });
          clarifyScope.textContent =
            ids.length > 1
              ? "Explaining " + ids.length + " blocks (" + labels.join(", ") + ")."
              : "Explaining block " + labels[0] + ".";
        }

        function normalizeNoteBulletForDoc(noteMd) {
          var note = String(noteMd || "").trim();
          if (!note) return "";
          if (/^\s*[-*+]\s+/m.test(note)) return note;
          return "- " + note.replace(/^\s+/, "");
        }

        function uniqueSortedIndices(arr) {
          var seen = {};
          var out = [];
          for (var i = 0; i < arr.length; i++) {
            var x = arr[i] | 0;
            if (seen[x]) continue;
            seen[x] = 1;
            out.push(x);
          }
          return out.sort(function (a, b) {
            return a - b;
          });
        }

        function getCheckedBlockIndices() {
          var out = [];
          [].forEach.call(mdRendered.querySelectorAll(".sd-doc-block"), function (sec) {
            var tgl = sec.querySelector(".sd-block-select-toggle");
            if (tgl && tgl.getAttribute("aria-checked") === "true") {
              var idx = parseInt(sec.getAttribute("data-sd-index"), 10);
              if (idx === idx) out.push(idx);
            }
          });
          return out.sort(function (a, b) {
            return a - b;
          });
        }

        function getIndicesForBlockActions(fallbackIndex) {
          var c = getCheckedBlockIndices();
          if (c.length) return c;
          if (fallbackIndex != null && fallbackIndex === fallbackIndex) return [fallbackIndex];
          return [];
        }

        function mergeConsecutiveBlockSlices(indices) {
          var ind = uniqueSortedIndices(indices);
          if (ind.length < 2) return false;
          for (var k = 0; k < ind.length - 1; k++) {
            if (ind[k + 1] !== ind[k] + 1) {
              showWorkspaceError(
                "Can only merge adjacent blocks — select consecutive rows (checkboxes) or use Merge on one block to join with the next."
              );
              return false;
            }
          }
          var slices = window._sdBlockSlices;
          if (!slices) return false;
          for (var i = 0; i < ind.length; i++) {
            if (ind[i] < 0 || ind[i] >= slices.length) return false;
          }
          syncBlockNotesBeforeSliceMutation(ind);
          var parts = [];
          for (var j = 0; j < ind.length; j++) {
            var t = (slices[ind[j]] || "").trim();
            if (t) parts.push(t);
          }
          var combined = parts.join("\n\n");
          if (!combined) return false;
          pushUndoSnapshot();
          var first = ind[0];
          for (var r = ind.length - 1; r >= 0; r--) {
            slices.splice(ind[r], 1);
          }
          slices.splice(first, 0, combined);
          mdEditor.value = slices.join("\n\n");
          setToolbarActiveBlock(first);
          renderBlockedMarkdownPreviewMergePatch(first, ind.length - 1, combined);
          if (activeClarifyBlock && activeClarifyBlock.allIndices) {
            var aix = activeClarifyBlock.allIndices;
            var hit = ind.some(function (x) {
              return aix.indexOf(x) >= 0;
            });
            if (hit) {
              activeClarifyBlock = { allIndices: [first] };
              syncClarifyScope();
            }
          }
          showWorkspaceError("");
          return true;
        }

        function mergeReplaceSlicesWithOne(indices, newMarkdownPiece) {
          var slices = window._sdBlockSlices;
          if (!slices || !indices || !indices.length) return false;
          var piece = String(newMarkdownPiece || "").trim();
          if (!piece) return false;
          var ind = uniqueSortedIndices(indices);
          for (var i = 0; i < ind.length; i++) {
            if (ind[i] < 0 || ind[i] >= slices.length) return false;
          }
          syncBlockNotesBeforeSliceMutation(ind);
          pushUndoSnapshot();
          var first = ind[0];
          for (var j = ind.length - 1; j >= 0; j--) {
            slices.splice(ind[j], 1);
          }
          slices.splice(first, 0, piece);
          mdEditor.value = slices.join("\n\n");
          setToolbarActiveBlock(first);
          renderBlockedMarkdownPreviewMergePatch(first, ind.length - 1, piece);
          if (activeClarifyBlock && activeClarifyBlock.allIndices) {
            var hit = ind.some(function (x) {
              return activeClarifyBlock.allIndices.indexOf(x) >= 0;
            });
            if (hit) {
              activeClarifyBlock = { allIndices: [first] };
              syncClarifyScope();
            }
          }
          return true;
        }

        function deleteBlockSlicesAtIndices(indices) {
          var ind = uniqueSortedIndices(indices);
          var slices = window._sdBlockSlices;
          if (!slices || !ind.length) return false;
          for (var i = 0; i < ind.length; i++) {
            if (ind[i] < 0 || ind[i] >= slices.length) return false;
          }
          syncBlockNotesBeforeSliceMutation(ind);
          pushUndoSnapshot();
          if (activeClarifyBlock && activeClarifyBlock.allIndices) {
            var aix = activeClarifyBlock.allIndices;
            var overlap = ind.some(function (x) {
              return aix.indexOf(x) >= 0;
            });
            if (overlap) {
              activeClarifyBlock = null;
              chatHistory = [];
              if (chatMessages) chatMessages.innerHTML = "";
            }
          }
          for (var j = ind.length - 1; j >= 0; j--) {
            slices.splice(ind[j], 1);
          }
          mdEditor.value = slices.join("\n\n");
          clearBlockToolbarState();
          if (!slices.length || !String(mdEditor.value || "").trim()) {
            renderBlockedMarkdownPreview();
          } else {
            renderBlockedMarkdownPreviewDeletePatch(ind);
          }
          syncClarifyScope();
          return true;
        }

        function replaceBlockSliceAtIndex(index, newMarkdownPiece) {
          var ix = index | 0;
          syncBlockNotesBeforeSliceMutation([ix]);
          var slices = window._sdBlockSlices;
          if (!slices || ix < 0 || ix >= slices.length) return false;
          var piece = String(newMarkdownPiece || "").trim();
          if (!piece) return false;
          pushUndoSnapshot();
          slices[ix] = piece;
          mdEditor.value = slices.join("\n\n");
          setToolbarActiveBlock(ix);
          renderBlockedMarkdownPreviewSlicePatch(ix, piece);
          syncClarifyScope();
          return true;
        }

        function deleteBlockSliceAtIndex(index) {
          return deleteBlockSlicesAtIndices([index]);
        }

        function syncBlockNotesToolbarVisibility() {
          if (!blockNotesToolbar) return;
          var on =
            mdRendered &&
            !mdRendered.classList.contains("hidden") &&
            mdRendered.classList.contains("block-notes-on");
          blockNotesToolbar.classList.toggle("hidden", !on);
        }

        function clearBlockToolbarState() {
          if (mdRendered) {
            [].forEach.call(mdRendered.querySelectorAll(".sd-doc-block.sd-selected"), function (el) {
              el.classList.remove("sd-selected");
            });
          }
          activeBlockMenuIndex = null;
        }

        function setToolbarActiveBlock(idx) {
          if (idx == null || idx !== idx) {
            clearBlockToolbarState();
            return;
          }
          activeBlockMenuIndex = idx;
          if (!mdRendered || mdRendered.classList.contains("hidden")) return;
          [].forEach.call(mdRendered.querySelectorAll(".sd-doc-block.sd-selected"), function (el) {
            el.classList.remove("sd-selected");
          });
          var sec = mdRendered.querySelector('.sd-doc-block[data-sd-index="' + idx + '"]');
          if (sec) sec.classList.add("sd-selected");
        }

        function getToolbarTargetBlockIndex() {
          if (!mdRendered || mdRendered.classList.contains("hidden")) return null;
          if (activeBlockMenuIndex != null && activeBlockMenuIndex === activeBlockMenuIndex) {
            return activeBlockMenuIndex;
          }
          var a = document.activeElement;
          if (a && a.closest && mdRendered.contains(a)) {
            var sec = a.closest(".sd-doc-block");
            if (sec) {
              var idx = parseInt(sec.getAttribute("data-sd-index"), 10);
              if (idx === idx) return idx;
            }
          }
          return null;
        }

        function refreshToolbarBlockHighlight() {
          if (!mdRendered || mdRendered.classList.contains("hidden")) return;
          [].forEach.call(mdRendered.querySelectorAll(".sd-doc-block.sd-selected"), function (el) {
            el.classList.remove("sd-selected");
          });
          var n = window._sdBlockSlices && window._sdBlockSlices.length;
          if (!n) return;
          if (activeBlockMenuIndex != null) {
            var ix = Math.min(activeBlockMenuIndex | 0, n - 1);
            activeBlockMenuIndex = ix;
            var sec = mdRendered.querySelector('.sd-doc-block[data-sd-index="' + ix + '"]');
            if (sec) sec.classList.add("sd-selected");
          }
        }

        function getBlockByIndex(index) {
          var slices = window._sdBlockSlices;
          var ix = index | 0;
          if (!slices || ix < 0 || ix >= slices.length) return null;
          var mdPart = (slices[ix] || "").trim();
          return {
            index: ix,
            markdown: mdPart || "(This block has no text yet — ask a general question or add content first.)",
          };
        }

        function _canonicalMdNewlines(s) {
          return String(s || "").replace(/\r\n/g, "\n");
        }

        function blockSlicesMatchEditor(slices, src) {
          if (!slices || !slices.length) return false;
          var j = slices.map(function (p) {
            return String(p || "");
          }).join("\n\n");
          return _canonicalMdNewlines(j) === _canonicalMdNewlines(src);
        }

        function reindexSdDocBlocksAfterStructuralChange(expectedCount) {
          if (!mdRendered) return false;
          var all = mdRendered.querySelectorAll(".sd-doc-block");
          if (all.length !== expectedCount) return false;
          for (var u = 0; u < all.length; u++) {
            all[u].setAttribute("data-sd-index", String(u));
            var n = u + 1;
            all[u].setAttribute("aria-label", "Block " + n + " of " + expectedCount);
            var tgl = all[u].querySelector(".sd-block-select-toggle");
            if (tgl) {
              tgl.setAttribute("aria-checked", "false");
              tgl.setAttribute("aria-label", "Select block " + n);
              tgl.setAttribute(
                "title",
                "Select for multi-block actions (merge, delete). Shift+click another row for a range."
              );
            }
          }
          lastBlockCheckboxIndex = null;
          return true;
        }

        function renderBlockedMarkdownPreviewSlicePatch(ix, mdPiece) {
          clearPreviewDirtyAndTimer();
          var blocks = window._sdBlockSlices;
          var src = getMarkdownForPreview() || "";
          if (!blockSlicesMatchEditor(blocks, src)) {
            renderBlockedMarkdownPreview();
            return;
          }
          var sec = mdRendered.querySelector('.sd-doc-block[data-sd-index="' + ix + '"]');
          if (!sec) {
            renderBlockedMarkdownPreview();
            return;
          }
          var inner = sec.querySelector(".sd-doc-block-inner");
          if (!inner) {
            renderBlockedMarkdownPreview();
            return;
          }
          var forPrev = inlineImages(mdPiece, lastImages);
          inner.innerHTML = DOMPurify.sanitize(md.render(forPrev), PURIFY_PREVIEW);
          setBlockInnersEditable(true);
        }

        function renderBlockedMarkdownPreviewMergePatch(firstIdx, sectionsToRemoveAfterFirst, mergedMd) {
          clearPreviewDirtyAndTimer();
          var blocks = window._sdBlockSlices;
          var src = getMarkdownForPreview() || "";
          if (!blockSlicesMatchEditor(blocks, src)) {
            renderBlockedMarkdownPreview();
            return;
          }
          var sec = mdRendered.querySelector('.sd-doc-block[data-sd-index="' + firstIdx + '"]');
          if (!sec) {
            renderBlockedMarkdownPreview();
            return;
          }
          var inner = sec.querySelector(".sd-doc-block-inner");
          if (!inner) {
            renderBlockedMarkdownPreview();
            return;
          }
          var forPrev = inlineImages(mergedMd, lastImages);
          inner.innerHTML = DOMPurify.sanitize(md.render(forPrev), PURIFY_PREVIEW);
          for (var r = 0; r < sectionsToRemoveAfterFirst; r++) {
            var v = mdRendered.querySelector('.sd-doc-block[data-sd-index="' + (firstIdx + 1) + '"]');
            if (v && v.parentNode) v.parentNode.removeChild(v);
          }
          if (!reindexSdDocBlocksAfterStructuralChange(blocks.length)) {
            renderBlockedMarkdownPreview();
            return;
          }
          setBlockInnersEditable(true);
          refreshToolbarBlockHighlight();
        }

        function renderBlockedMarkdownPreviewDeletePatch(deletedSortedAsc) {
          clearPreviewDirtyAndTimer();
          var blocks = window._sdBlockSlices;
          var src = getMarkdownForPreview() || "";
          if (!blocks.length || !src.trim()) {
            renderBlockedMarkdownPreview();
            return;
          }
          if (!blockSlicesMatchEditor(blocks, src)) {
            renderBlockedMarkdownPreview();
            return;
          }
          for (var r = deletedSortedAsc.length - 1; r >= 0; r--) {
            var di = deletedSortedAsc[r];
            var node = mdRendered.querySelector('.sd-doc-block[data-sd-index="' + di + '"]');
            if (node && node.parentNode) node.parentNode.removeChild(node);
          }
          if (!reindexSdDocBlocksAfterStructuralChange(blocks.length)) {
            renderBlockedMarkdownPreview();
            return;
          }
          setBlockInnersEditable(true);
          refreshToolbarBlockHighlight();
        }

        function renderBlockedMarkdownPreview() {
          clearPreviewDirtyAndTimer();
          var src = getMarkdownForPreview() || "";
          if (!src.trim()) {
            setBlockInnersEditable(false);
            mdRendered.innerHTML = "";
            mdRendered.classList.add("hidden");
            mdRendered.classList.remove("block-notes-on");
            mdPlaceholder.textContent = "No notes yet.";
            mdPlaceholder.classList.remove("hidden");
            window._sdBlockSlices = [];
            syncBlockNotesToolbarVisibility();
            return;
          }
          mdPlaceholder.classList.add("hidden");
          mdRendered.classList.remove("hidden");
          mdRendered.classList.add("block-notes-on");
          var prev = window._sdBlockSlices;
          var blocks;
          if (blockSlicesMatchEditor(prev, src)) {
            blocks = prev;
          } else {
            blocks = splitMarkdownIntoBlocks(src);
            window._sdBlockSlices = blocks;
            var joined = blocks.join("\n\n");
            if (_canonicalMdNewlines(joined) !== _canonicalMdNewlines(src)) {
              mdEditor.value = joined;
              rawMarkdown = joined;
            }
          }
          var htmlParts = [];
          for (var i = 0; i < blocks.length; i++) {
            var forPrev = inlineImages(blocks[i], lastImages);
            var inner = md.render(forPrev);
            htmlParts.push(
              '<section class="sd-doc-block" data-sd-index="' +
                i +
                '" aria-label="Block ' +
                (i + 1) +
                " of " +
                blocks.length +
                '">' +
                '<button type="button" class="sd-block-select-toggle" role="checkbox" aria-checked="false" tabindex="0" aria-label="Select block ' +
                (i + 1) +
                '" title="Select for multi-block actions (merge, delete). Shift+click another row for a range."></button>' +
                '<div class="sd-doc-block-inner">' +
                inner +
                "</div></section>"
            );
          }
          mdRendered.innerHTML = DOMPurify.sanitize(htmlParts.join(""), PURIFY_PREVIEW);
          setBlockInnersEditable(true);
          syncBlockNotesToolbarVisibility();
          refreshToolbarBlockHighlight();
        }

        function requestBlockNoteForIndices(indices) {
          if (!documentId) return;
          var ind = uniqueSortedIndices(indices);
          if (!ind.length) return;
          var blocks = [];
          for (var b = 0; b < ind.length; b++) {
            var blk = getBlockByIndex(ind[b]);
            if (blk) blocks.push(blk);
          }
          if (!blocks.length) return;
          try {
            assertMistralKeyIfNeeded();
          } catch (eKey) {
            showWorkspaceError(eKey.message || String(eKey));
            return;
          }
          showWorkspaceError("");
          setAgentProgress(true, "Converting to bullet point(s)…");
          var payload = {
            document_id: documentId,
            format_instructions: "",
            provider: currentLlmProvider() || "mistral",
            model: currentLlmModel() || null,
            mistral_api_key: mistralApiKeyPayload(),
          };
          if (blocks.length === 1) payload.block = blocks[0];
          else payload.blocks = blocks;
          fetch(apiUrl("/api/agent-block-note"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
          })
            .then(function (res) {
              return (res.headers.get("content-type") || "").toLowerCase().indexOf("application/json") >= 0
                ? res.json().then(function (data) {
                    return { res: res, data: data };
                  })
                : res.text().then(function (t) {
                    return { res: res, data: { detail: t } };
                  });
            })
            .then(function (_ref) {
              var res = _ref.res;
              var data = _ref.data;
              if (!res.ok) throw new Error(formatApiError(data, res));
              var bullet = normalizeNoteBulletForDoc(data.note_markdown || "");
              var ok =
                blocks.length === 1
                  ? replaceBlockSliceAtIndex(ind[0], bullet)
                  : mergeReplaceSlicesWithOne(ind, bullet);
              if (!ok) throw new Error("Could not update the document.");
            })
            .catch(function (err) {
              showWorkspaceError(err.message || String(err));
            })
            .finally(function () {
              setAgentProgress(false);
            });
        }

        function activateClarifyFromMenu() {
          var ids = getIndicesForBlockActions(getToolbarTargetBlockIndex());
          if (!ids.length) {
            showWorkspaceError("Click a block or select blocks with the checkboxes.");
            return;
          }
          var preset = (chatInput && chatInput.value ? chatInput.value : "").trim();
          var same =
            activeClarifyBlock &&
            activeClarifyBlock.allIndices &&
            activeClarifyBlock.allIndices.length === ids.length &&
            activeClarifyBlock.allIndices.every(function (v, i) {
              return v === ids[i];
            });
          if (!same) {
            chatHistory = [];
            if (chatMessages) chatMessages.innerHTML = "";
          }
          activeClarifyBlock = { allIndices: ids };
          syncClarifyScope();
          setToolbarActiveBlock(ids[0]);
          if (chatInput) chatInput.value = "";
          if (!documentId) {
            showWorkspaceError("Import a PDF (file or URL) first.");
            return;
          }
          if (clarifyRoundInFlight || (chatSend && chatSend.disabled)) return;
          kickoffClarifyRoundWithPrompt(preset);
        }

        function resetMdUndoStacks() {
          mdUndoStack = [];
          mdRedoStack = [];
          mdUndoBurstOpen = false;
          clearTimeout(mdUndoBurstTimer);
          mdUndoBurstTimer = null;
        }

        function pushUndoSnapshot() {
          if (mdApplyingUndoRedo || !documentId) return;
          var s = markdownForApi();
          if (mdUndoStack.length && mdUndoStack[mdUndoStack.length - 1] === s) return;
          mdUndoStack.push(s);
          if (mdUndoStack.length > MD_UNDO_MAX) mdUndoStack.shift();
          mdRedoStack = [];
        }

        function armMdUndoBurstCapture() {
          if (mdApplyingUndoRedo || !documentId) return;
          if (mdUndoBurstOpen) return;
          mdUndoBurstOpen = true;
          if (previewSyncTimer != null || (previewDirtyBlockIndices && Object.keys(previewDirtyBlockIndices).length)) {
            flushPendingDomEditsIncrementalOrFull();
          }
          pushUndoSnapshot();
          clearTimeout(mdUndoBurstTimer);
          mdUndoBurstTimer = setTimeout(function () {
            mdUndoBurstOpen = false;
          }, 700);
        }

        function applyMarkdownUndoState(text) {
          mdApplyingUndoRedo = true;
          try {
            mdEditor.value = text || "";
            rawMarkdown = mdEditor.value;
            updatePreviewFromEditor();
            clearBlockToolbarState();
            syncClarifyScope();
          } finally {
            mdApplyingUndoRedo = false;
          }
        }

        function tryUndoRedoFromHotkey(e) {
          var isZ = e.key === "z" || e.key === "Z";
          if (!isZ || !(e.metaKey || e.ctrlKey) || e.altKey) return false;
          if (!documentId || !mdRendered || mdRendered.classList.contains("hidden")) return false;
          var a = document.activeElement;
          if (a && a.closest && a.closest("#sidebar")) return false;
          if (a && a.id === "chatInput") return false;
          if (a && a.closest && a.closest(".visually-hidden-md-store")) return false;
          if (a && a.closest && a.closest("dialog")) return false;
          flushPendingDomEditsIfDirty();
          if (e.shiftKey) {
            if (!mdRedoStack.length) return false;
            e.preventDefault();
            e.stopPropagation();
            var nxt = mdRedoStack.pop();
            mdUndoStack.push(markdownForApi());
            applyMarkdownUndoState(nxt);
            return true;
          }
          if (!mdUndoStack.length) return false;
          e.preventDefault();
          e.stopPropagation();
          var prev = mdUndoStack.pop();
          mdRedoStack.push(markdownForApi());
          applyMarkdownUndoState(prev);
          return true;
        }

        function blockToolbarHotkeyContextOk(e) {
          if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return false;
          if (!documentId || !mdRendered || mdRendered.classList.contains("hidden")) return false;
          var a = document.activeElement;
          if (!a) return true;
          if (a.closest && a.closest("dialog")) return false;
          if (a.closest && a.closest("#sidebar")) return false;
          if (a.id === "chatInput") return false;
          var tag = (a.tagName || "").toLowerCase();
          if (
            (tag === "textarea" || tag === "input" || tag === "select") &&
            !a.isContentEditable
          ) {
            return false;
          }
          return true;
        }

        function tryBlockToolbarHotkeys(e) {
          var k = e.key;
          if (k !== "b" && k !== "B" && k !== "e" && k !== "E") return false;
          if (!blockToolbarHotkeyContextOk(e)) return false;
          if (k === "b" || k === "B") {
            e.preventDefault();
            e.stopPropagation();
            var idsB = getIndicesForBlockActions(getToolbarTargetBlockIndex());
            if (!idsB.length) {
              showWorkspaceError("Click a block or select blocks with the checkboxes.");
              return true;
            }
            requestBlockNoteForIndices(idsB);
            return true;
          }
          if (k === "e" || k === "E") {
            e.preventDefault();
            e.stopPropagation();
            activateClarifyFromMenu();
            return true;
          }
          return false;
        }

        function notesWorkspaceHotkeys(e) {
          if (tryUndoRedoFromHotkey(e)) return;
          if (tryBlockToolbarHotkeys(e)) return;
          tryDeleteNotesBlocksFromHotkey(e);
        }

        function notesDeleteHotkeyAllowed(e) {
          if (e.key !== "Delete" && e.key !== "Backspace") return false;
          if (!documentId || !mdRendered || mdRendered.classList.contains("hidden")) return false;
          if (e.ctrlKey || e.metaKey || e.altKey) return false;
          var a = document.activeElement;
          if (a && a.closest && a.closest(".sd-doc-block-inner")) return false;
          if (a && a.closest && a.closest(".visually-hidden-md-store")) return false;
          if (a && a.closest && a.closest("#sidebar")) return false;
          if (a && a.closest && a.closest(".view-toolbar")) return false;
          if (a && a.closest && a.closest(".sd-block-notes-toolbar")) return false;
          if (a && a.closest && a.closest("dialog")) return false;
          if (!a || a === document.body || a === document.documentElement) {
            return getCheckedBlockIndices().length > 0;
          }
          return !!(mainWorkspace && mainWorkspace.contains(a));
        }

        function tryDeleteNotesBlocksFromHotkey(e) {
          if (!notesDeleteHotkeyAllowed(e)) return;
          var ids = getCheckedBlockIndices();
          if (!ids.length) {
            var a = document.activeElement;
            if (a && a.closest && mdRendered.contains(a)) {
              var sec = a.closest(".sd-doc-block");
              if (sec) {
                var idx = parseInt(sec.getAttribute("data-sd-index"), 10);
                if (idx === idx) ids = [idx];
              }
            }
          }
          if (!ids.length) return;
          e.preventDefault();
          e.stopPropagation();
          deleteBlockSlicesAtIndices(ids);
        }

        function bindBlockNotesUi() {
          var addBtn = document.getElementById("blockActionAddNote");
          var clarifyBtn = document.getElementById("blockActionClarify");
          var mergeBtn = document.getElementById("blockActionMerge");
          var delBtn = document.getElementById("blockActionDelete");
          if (addBtn) {
            addBtn.addEventListener("click", function (e) {
              e.preventDefault();
              e.stopPropagation();
              var ids = getIndicesForBlockActions(getToolbarTargetBlockIndex());
              if (!ids.length) {
                showWorkspaceError("Click a block or select blocks with the checkboxes.");
                return;
              }
              requestBlockNoteForIndices(ids);
            });
          }
          if (clarifyBtn) {
            clarifyBtn.addEventListener("click", function (e) {
              e.preventDefault();
              e.stopPropagation();
              activateClarifyFromMenu();
            });
          }
          if (mergeBtn) {
            mergeBtn.addEventListener("click", function (e) {
              e.preventDefault();
              e.stopPropagation();
              var ids = getIndicesForBlockActions(getToolbarTargetBlockIndex());
              if (!ids.length) {
                showWorkspaceError("Click a block or select blocks with the checkboxes.");
                return;
              }
              if (ids.length >= 2) {
                mergeConsecutiveBlockSlices(ids);
                return;
              }
              if (ids.length === 1) {
                var i = ids[0];
                var sl = window._sdBlockSlices;
                if (sl && i + 1 < sl.length) {
                  mergeConsecutiveBlockSlices([i, i + 1]);
                } else {
                  showWorkspaceError("No block below to merge with.");
                }
              }
            });
          }
          if (delBtn) {
            delBtn.addEventListener("click", function (e) {
              e.preventDefault();
              e.stopPropagation();
              var ids = getIndicesForBlockActions(getToolbarTargetBlockIndex());
              if (!ids.length) {
                showWorkspaceError("Click a block or select blocks with the checkboxes.");
                return;
              }
              deleteBlockSlicesAtIndices(ids);
            });
          }
          mdRendered.addEventListener(
            "focusin",
            function (e) {
              var inner = e.target && e.target.closest && e.target.closest(".sd-doc-block-inner");
              if (!inner || !mdRendered.contains(inner)) return;
              var sec = inner.closest(".sd-doc-block");
              if (!sec) return;
              var idx = parseInt(sec.getAttribute("data-sd-index"), 10);
              if (idx !== idx) return;
              setToolbarActiveBlock(idx);
            },
            true
          );
          mdRendered.addEventListener("click", function (e) {
            var tgl = e.target.closest && e.target.closest(".sd-block-select-toggle");
            if (tgl && mdRendered.contains(tgl)) {
              e.preventDefault();
              var sec0 = tgl.closest(".sd-doc-block");
              if (!sec0) return;
              var idx0 = parseInt(sec0.getAttribute("data-sd-index"), 10);
              if (idx0 !== idx0) return;
              if (e.shiftKey && lastBlockCheckboxIndex != null && lastBlockCheckboxIndex !== idx0) {
                var a = Math.min(lastBlockCheckboxIndex, idx0);
                var b = Math.max(lastBlockCheckboxIndex, idx0);
                for (var x = a; x <= b; x++) {
                  var s = mdRendered.querySelector('.sd-doc-block[data-sd-index="' + x + '"]');
                  if (s) {
                    var c = s.querySelector(".sd-block-select-toggle");
                    if (c) c.setAttribute("aria-checked", "true");
                  }
                }
              } else {
                var on = tgl.getAttribute("aria-checked") === "true";
                tgl.setAttribute("aria-checked", on ? "false" : "true");
              }
              lastBlockCheckboxIndex = idx0;
              setToolbarActiveBlock(idx0);
              return;
            }
            var secClick = e.target.closest && e.target.closest(".sd-doc-block");
            if (secClick && mdRendered.contains(secClick) && !e.target.closest(".sd-block-select-toggle")) {
              var idx1 = parseInt(secClick.getAttribute("data-sd-index"), 10);
              if (idx1 === idx1) setToolbarActiveBlock(idx1);
            }
          });
        }

        function updatePreviewFromEditor() {
          clearPreviewDirtyAndTimer();
          var src = getMarkdownForPreview() || "";
          if (!src.trim()) {
            setBlockInnersEditable(false);
            mdRendered.innerHTML = "";
            mdRendered.classList.add("hidden");
            mdRendered.classList.remove("block-notes-on");
            mdPlaceholder.textContent = "No notes yet.";
            mdPlaceholder.classList.remove("hidden");
            window._sdBlockSlices = [];
            syncBlockNotesToolbarVisibility();
            return;
          }
          renderBlockedMarkdownPreview();
        }

        function scheduleLivePreview() {
          if (previewRaf) cancelAnimationFrame(previewRaf);
          previewRaf = requestAnimationFrame(function () {
            previewRaf = null;
            updatePreviewFromEditor();
          });
        }

        function setEditorMarkdown(text) {
          mdEditor.value = text || "";
          updatePreviewFromEditor();
        }

        function clearMdPreview() {
          resetMdUndoStacks();
          clearPreviewDirtyAndTimer();
          setBlockInnersEditable(false);
          clearBlockToolbarState();
          syncBlockNotesToolbarVisibility();
          mdEditor.value = "";
          mdEditor.disabled = true;
          mdRendered.innerHTML = "";
          mdRendered.classList.add("hidden");
          mdRendered.classList.remove("block-notes-on");
          window._sdBlockSlices = [];
          mdPlaceholder.textContent = "No notes yet.";
          mdPlaceholder.classList.remove("hidden");
        }

        function setFile(file) {
          selectedFile = file;
          if (file && pdfUrlInput) pdfUrlInput.value = "";
          syncPdfUrlFieldVisibility();
          syncImportActionButtons();
          if (file && !importInFlight) {
            runImport();
          }
        }

        function revokePdfUrl() {
          if (pdfObjectUrl) {
            URL.revokeObjectURL(pdfObjectUrl);
            pdfObjectUrl = null;
          }
          pdfFrame.removeAttribute("src");
        }

        function finishConversionSuccess(data, pdfSetup) {
          documentId = data.document_id || null;
          extractDeferred = !!data.extract_deferred;
          rawMarkdown = data.markdown || "";
          lastImages = data.images || {};
          lastBase =
            (data.filename_base || "converted").replace(/[^\w\-]+/g, "_") || "converted";

          if (!documentId) {
            throw new Error("Server did not return a document_id.");
          }

          revokePdfUrl();
          if (data.pdf_via_server) {
            pdfFrame.src = apiUrl("/api/document/" + encodeURIComponent(documentId) + "/pdf");
            workspaceTitle.textContent =
              (pdfSetup.file && pdfSetup.file.name) ||
              pdfSetup.title ||
              data.filename_base ||
              "Document";
          } else if (pdfSetup.mode === "blob" && pdfSetup.file) {
            pdfObjectUrl = URL.createObjectURL(pdfSetup.file);
            pdfFrame.src = pdfObjectUrl;
            workspaceTitle.textContent = pdfSetup.file.name || "Document";
          } else if (pdfSetup.mode === "server") {
            pdfFrame.src = apiUrl("/api/document/" + encodeURIComponent(documentId) + "/pdf");
            workspaceTitle.textContent = pdfSetup.title || data.filename_base || "Document";
          }

          chatHistory = [];
          activeClarifyBlock = null;
          activeBlockMenuIndex = null;
          chatMessages.innerHTML = "";
          syncClarifyScope();

          mdEditor.disabled = false;
          resetMdUndoStacks();
          mdEditor.value = rawMarkdown || "";
          updatePreviewFromEditor();

          openDocumentUI();
          syncImportModeUi();
          syncImportActionButtons();
          if (pdfUrlInput) pdfUrlInput.value = "";
        }

        function appendChatBubble(role, text) {
          var wrap = document.createElement("div");
          wrap.className = "chat-bubble " + (role === "user" ? "user" : "assistant");
          var who = document.createElement("div");
          who.className = "who";
          who.textContent = role === "user" ? "You" : "Assistant";
          var body = document.createElement("div");
          body.textContent = text;
          wrap.appendChild(who);
          wrap.appendChild(body);
          chatMessages.appendChild(wrap);
          chatMessages.scrollTop = chatMessages.scrollHeight;
        }

        function resetWorkspace() {
          if (urlAutoImportTimer) clearTimeout(urlAutoImportTimer);
          urlAutoImportTimer = null;
          clearBlockToolbarState();
          syncBlockNotesToolbarVisibility();
          revokePdfUrl();
          documentId = null;
          importInFlight = false;
          extractDeferred = false;
          syncExtractPdfButton();
          rawMarkdown = "";
          resetMdUndoStacks();
          lastImages = {};
          chatHistory = [];
          activeClarifyBlock = null;
          activeBlockMenuIndex = null;
          chatMessages.innerHTML = "";
          chatInput.value = "";
          syncClarifyScope();
          resetFirstPassOptions();
          clearMdPreview();
          showWorkspaceError("");
          prevViewOrderKey = "";
          if (pdfUrlInput) pdfUrlInput.value = "";
          syncPdfUrlFieldVisibility();
          syncImportActionButtons();
          closeDocumentUI();
        }

        drop.addEventListener("click", function (e) {
          if (e.target === fileInput) return;
          fileInput.click();
        });
        drop.addEventListener("keydown", function (e) {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            fileInput.click();
          }
        });
        ["dragenter", "dragover"].forEach(function (ev) {
          drop.addEventListener(ev, function (e) {
            e.preventDefault();
            drop.classList.add("dragover");
          });
        });
        ["dragleave", "drop"].forEach(function (ev) {
          drop.addEventListener(ev, function (e) {
            e.preventDefault();
            drop.classList.remove("dragover");
          });
        });
        drop.addEventListener("drop", function (e) {
          var f = e.dataTransfer.files && e.dataTransfer.files[0];
          if (f && isPdfFile(f)) {
            setFile(f);
            showUploadError("");
          } else if (f) {
            showUploadError("Please drop a PDF (.pdf).");
          }
        });
        fileInput.addEventListener("change", function () {
          var f = fileInput.files && fileInput.files[0];
          if (!f) {
            setFile(null);
            return;
          }
          if (!isPdfFile(f)) {
            showUploadError("Please choose a PDF (.pdf).");
            fileInput.value = "";
            setFile(null);
            return;
          }
          setFile(f);
          showUploadError("");
        });

        function scheduleUrlAutoImport() {
          if (urlAutoImportTimer) clearTimeout(urlAutoImportTimer);
          urlAutoImportTimer = setTimeout(function () {
            urlAutoImportTimer = null;
            if (importInFlight || selectedFile) return;
            var u = (pdfUrlInput && pdfUrlInput.value ? pdfUrlInput.value : "").trim();
            if (!isFunctionalHttpUrl(u)) return;
            runImport();
          }, URL_AUTO_IMPORT_DEBOUNCE_MS);
        }

        if (pdfUrlInput) {
          pdfUrlInput.addEventListener("input", function () {
            syncImportActionButtons();
            scheduleUrlAutoImport();
          });
          pdfUrlInput.addEventListener("change", function () {
            syncImportActionButtons();
            scheduleUrlAutoImport();
          });
          pdfUrlInput.addEventListener("keydown", function (e) {
            if (e.key !== "Enter") return;
            e.preventDefault();
            if (selectedFile) return;
            if (urlAutoImportTimer) clearTimeout(urlAutoImportTimer);
            urlAutoImportTimer = null;
            runImport();
          });
        }
        syncImportActionButtons();
        closeDocumentUI();

        function extractPdfFromServer() {
          if (!documentId || !extractDeferred) return;
          var btn = document.getElementById("btnExtractPdf");
          var prev = btn && btn.textContent;
          if (btn) {
            btn.disabled = true;
            btn.textContent = "Extracting…";
          }
          setAgentProgress(true, "OCR…");
          fetch(apiUrl("/api/extract-markdown"), {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              document_id: documentId,
              mistral_api_key: mistralOcrApiKeyPayload(),
            }),
          })
            .then(function (res) {
              var ct = (res.headers.get("content-type") || "").toLowerCase();
              return (ct.indexOf("application/json") >= 0 ? res.json() : Promise.resolve({})).then(function (data) {
                return { res: res, data: data };
              });
            })
            .then(function (_ref) {
              var res = _ref.res;
              var data = _ref.data;
              if (!res.ok) throw new Error(formatApiError(data, res));
              extractDeferred = !!data.extract_deferred;
              rawMarkdown = data.markdown || "";
              lastImages = data.images || {};
              if (data.filename_base) {
                lastBase = String(data.filename_base).replace(/[^\w\-]+/g, "_") || lastBase;
              }
              resetMdUndoStacks();
              setEditorMarkdown(rawMarkdown);
              syncExtractPdfButton();
              syncImportModeUi();
              syncImportActionButtons();
              showWorkspaceError("");
            })
            .catch(function (e) {
              showWorkspaceError(e.message || String(e));
            })
            .finally(function () {
              setAgentProgress(false);
              if (btn) {
                btn.disabled = false;
                btn.textContent = prev || "Extract Markdown from PDF";
              }
            });
        }

        btnOtherPdf.addEventListener("click", function () {
          fileInput.value = "";
          setFile(null);
          resetWorkspace();
        });

        var btnExtractPdfEl = document.getElementById("btnExtractPdf");
        if (btnExtractPdfEl) btnExtractPdfEl.addEventListener("click", extractPdfFromServer);

        function runImport() {
          if (importInFlight) return;
          if (!hasImportSource()) {
            showUploadError("Add a PDF file or a valid PDF URL first.");
            return;
          }
          syncImportModeUi();

          openDocumentUI();
          setAgentProgress(true, "OCR…");

          importInFlight = true;
          syncImportActionButtons();
          showUploadError("");

          function endImport() {
            importInFlight = false;
            setAgentProgress(false);
            syncImportActionButtons();
          }

          function onNetErr(e) {
            if (e instanceof TypeError && e.message === "Failed to fetch") {
              showUploadError("Network error — use the app URL (not file://).");
            } else {
              showUploadError(e.message || String(e));
            }
          }

          if (selectedFile) {
            var body = new FormData();
            body.append("file", selectedFile, selectedFile.name);
            body.append("extract_markdown", "true");
            var ocrKey = mistralOcrApiKeyPayload();
            if (ocrKey) body.append("mistral_api_key", ocrKey);
            fetch(apiUrl("/api/convert"), { method: "POST", body })
              .then(function (res) {
                var ct = (res.headers.get("content-type") || "").toLowerCase();
                return (ct.indexOf("application/json") >= 0 ? res.json() : Promise.resolve({})).then(function (data) {
                  return { res: res, data: data };
                });
              })
              .then(function (_ref) {
                if (!_ref.res.ok) throw new Error(formatApiError(_ref.data, _ref.res));
                finishConversionSuccess(_ref.data, { mode: "blob", file: selectedFile });
              })
              .catch(onNetErr)
              .finally(endImport);
          } else {
            var url = (pdfUrlInput.value || "").trim();
            if (!isFunctionalHttpUrl(url)) {
              showUploadError("Enter a valid http:// or https:// URL.");
              endImport();
              return;
            }
            fetch(apiUrl("/api/convert-from-url"), {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                url: url,
                extract_markdown: true,
                mistral_api_key: mistralOcrApiKeyPayload(),
              }),
            })
              .then(function (res) {
                var ct = (res.headers.get("content-type") || "").toLowerCase();
                return (ct.indexOf("application/json") >= 0 ? res.json() : Promise.resolve({})).then(function (data) {
                  return { res: res, data: data };
                });
              })
              .then(function (_ref) {
                if (!_ref.res.ok) throw new Error(formatApiError(_ref.data, _ref.res));
                finishConversionSuccess(_ref.data, {
                  mode: "server",
                  title: _ref.data.filename_base || "Document",
                });
              })
              .catch(onNetErr)
              .finally(endImport);
          }
        }

        function currentDownloadMarkdown() {
          syncBlockNotesToMarkdown();
          return (markdownForApi() || "").trim();
        }

        function kickoffClarifyRoundWithPrompt(optionalText) {
          showWorkspaceError("");
          var prompt = (optionalText || "").trim();
          if (!prompt) {
            var n = activeClarifyBlock && activeClarifyBlock.allIndices ? activeClarifyBlock.allIndices.length : 0;
            prompt = n > 1 ? CLARIFY_DEFAULT_MULTI : CLARIFY_DEFAULT_ONE;
          }
          chatHistory.push({ role: "user", content: prompt });
          appendChatBubble("user", prompt);
          return withClarifyRound();
        }

        function withClarifyRound() {
          function clarifyFail(err) {
            clarifyRoundInFlight = false;
            handleAgentError(err instanceof Error ? err : new Error(String(err)));
            return Promise.resolve();
          }
          try {
            assertMistralKeyIfNeeded();
          } catch (eKey) {
            return clarifyFail(eKey);
          }
          if (clarifyRoundInFlight) return Promise.resolve();
          clarifyRoundInFlight = true;
          if (!activeClarifyBlock || !activeClarifyBlock.allIndices || !activeClarifyBlock.allIndices.length) {
            return clarifyFail(new Error("Choose a block first, then use Explain (⌘E) or Send."));
          }
          var aix = activeClarifyBlock.allIndices.slice();
          var cblocks = [];
          for (var ci = 0; ci < aix.length; ci++) {
            var cb = getBlockByIndex(aix[ci]);
            if (cb) cblocks.push(cb);
          }
          if (!cblocks.length) {
            return clarifyFail(
              new Error("Could not read selected blocks — try clicking the block again, then Explain.")
            );
          }
          var msgsPayload = [];
          for (var mi = 0; mi < chatHistory.length; mi++) {
            var m = chatHistory[mi];
            if (!m || (m.role !== "user" && m.role !== "assistant")) continue;
            var c = String(m.content == null ? "" : m.content).trim();
            if (!c) continue;
            msgsPayload.push({ role: m.role, content: c });
          }
          if (!msgsPayload.length || msgsPayload[msgsPayload.length - 1].role !== "user") {
            return clarifyFail(new Error("Your message was not sent correctly — try again."));
          }
          var clarifyPayload = { document_id: documentId };
          if (cblocks.length === 1) clarifyPayload.block = cblocks[0];
          else clarifyPayload.blocks = cblocks;
          setAgentProgress(true, "Explaining…");
          if (chatSend) chatSend.disabled = true;
          var sendLabel = chatSend ? chatSend.textContent : "Send";
          if (chatSend) chatSend.textContent = "…";
          if (chatInput) chatInput.disabled = true;
          return fetch(apiUrl("/api/agent-block-clarify"), {
            method: "POST",
            headers: {
              "Content-Type": "application/json",
            },
            body: JSON.stringify(
              Object.assign({}, clarifyPayload, {
                provider: currentLlmProvider() || "mistral",
                model: currentLlmModel() || null,
                messages: msgsPayload,
                mistral_api_key: mistralApiKeyPayload(),
              })
            ),
          })
            .then(function (res) {
              return res.text().then(function (text) {
                var data = parseApiJsonBody(res, text);
                if (!res.ok) throw new Error(formatApiError(data, res));
                return data;
              });
            })
            .then(function (data) {
              var assistantText = data.assistant_message || "";
              if (!String(assistantText).trim()) {
                throw new Error("Assistant returned an empty reply.");
              }
              chatHistory.push({ role: "assistant", content: assistantText });
              appendChatBubble("assistant", assistantText);
            })
            .catch(function (err) {
              handleAgentError(err);
            })
            .finally(function () {
              clarifyRoundInFlight = false;
              setAgentProgress(false);
              if (chatInput) chatInput.disabled = false;
              if (chatSend) {
                chatSend.disabled = false;
                chatSend.textContent = sendLabel;
              }
              if (chatInput) chatInput.focus();
            });
        }

        function handleAgentError(err) {
          if (chatHistory.length) chatHistory.pop();
          if (chatMessages) {
            var bubbles = chatMessages.querySelectorAll(".chat-bubble.user");
            if (bubbles.length) bubbles[bubbles.length - 1].remove();
          }
          showWorkspaceError(err.message || String(err));
        }

        function sendChat() {
          if (!documentId) {
            showWorkspaceError("Import a PDF (file or URL) first.");
            return;
          }
          if (clarifyRoundInFlight || (chatSend && chatSend.disabled)) return;
          if (!activeClarifyBlock || !activeClarifyBlock.allIndices || !activeClarifyBlock.allIndices.length) {
            showWorkspaceError("Pick block(s) with the square toggles, then Explain (⌘E) or use the toolbar.");
            return;
          }
          var text = (chatInput.value || "").trim();
          if (!text) return;
          chatInput.value = "";
          kickoffClarifyRoundWithPrompt(text);
        }

        chatSend.addEventListener("click", sendChat);
        chatInput.addEventListener("keydown", function (e) {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            sendChat();
          }
        });
        (function bindNotionPreviewEditSync() {
          function innerFromEventTarget(node) {
            var el = node && node.nodeType === 3 ? node.parentElement : node;
            if (!el || !el.closest) return null;
            var inner = el.closest(".sd-doc-block-inner");
            if (!inner || !mdRendered.contains(inner)) return null;
            if (inner.getAttribute("contenteditable") !== "true") return null;
            return inner;
          }
          mdRendered.addEventListener("input", function (e) {
            if (!innerFromEventTarget(e.target)) return;
            var bi = blockIndexFromEditEventTarget(e.target);
            if (bi === bi) {
              if (!previewDirtyBlockIndices) previewDirtyBlockIndices = {};
              previewDirtyBlockIndices[bi] = 1;
            }
            clearTimeout(previewSyncTimer);
            previewSyncTimer = setTimeout(function () {
              previewSyncTimer = null;
              flushPendingDomEditsIncrementalOrFull();
            }, 90);
          });
          mdRendered.addEventListener(
            "focusout",
            function (e) {
              if (!innerFromEventTarget(e.target)) return;
              flushPendingDomEditsIncrementalOrFull();
            },
            true
          );
          mdRendered.addEventListener("paste", function (e) {
            if (!innerFromEventTarget(e.target)) return;
            e.preventDefault();
            var text = e.clipboardData.getData("text/plain") || "";
            document.execCommand("insertText", false, text);
          });
          if (typeof InputEvent !== "undefined" && "inputType" in InputEvent.prototype) {
            mdRendered.addEventListener("beforeinput", function (e) {
              if (!innerFromEventTarget(e.target)) return;
              armMdUndoBurstCapture();
              if (e.inputType !== "insertText" || e.data == null) return;
              if (e.data === " ") {
                if (tryApplyBlockMarkdownShortcut()) {
                  e.preventDefault();
                  mdRendered.dispatchEvent(new Event("input", { bubbles: true }));
                }
                return;
              }
              if (e.data === "*") {
                tryApplyClosingBoldShortcut(e);
              }
            });
          } else {
            mdRendered.addEventListener(
              "keydown",
              function (e) {
                if (!innerFromEventTarget(e.target)) return;
                if (mdApplyingUndoRedo) return;
                if (e.ctrlKey || e.metaKey || e.altKey) return;
                if (e.key === "Backspace" || e.key === "Delete" || e.key.length === 1) {
                  armMdUndoBurstCapture();
                }
              },
              true
            );
          }
          mdRendered.addEventListener("keydown", function (e) {
            if (!innerFromEventTarget(e.target)) return;
            if (tryApplyHrOnEnter(e)) return;
            if (tryMergeWithNextBlockOnModifierM(e)) return;
            if (typeof InputEvent !== "undefined" && "inputType" in InputEvent.prototype) return;
            if (e.key === " " && !e.ctrlKey && !e.metaKey && !e.altKey) {
              if (tryApplyBlockMarkdownShortcut()) {
                e.preventDefault();
                mdRendered.dispatchEvent(new Event("input", { bubbles: true }));
              }
              return;
            }
            if (e.key === "*" && !e.ctrlKey && !e.metaKey && !e.altKey) {
              var fe = {
                data: "*",
                preventDefault: function () {
                  e.preventDefault();
                },
              };
              tryApplyClosingBoldShortcut(fe);
            }
          });
        })();

        function downloadBlob(blob, filename) {
          var a = document.createElement("a");
          a.href = URL.createObjectURL(blob);
          a.download = filename;
          a.click();
          URL.revokeObjectURL(a.href);
        }

        function closeExportMenu() {
          var panel = document.getElementById("exportMenuPanel");
          var toggle = document.getElementById("exportMenuToggle");
          if (panel) panel.classList.add("hidden");
          if (toggle) toggle.setAttribute("aria-expanded", "false");
        }

        (function bindExportDownloadMenu() {
          var toggle = document.getElementById("exportMenuToggle");
          var panel = document.getElementById("exportMenuPanel");
          if (!toggle || !panel) return;
          toggle.addEventListener("click", function (e) {
            e.stopPropagation();
            var open = panel.classList.contains("hidden");
            panel.classList.toggle("hidden", !open);
            toggle.setAttribute("aria-expanded", open ? "true" : "false");
          });
          document.addEventListener("click", closeExportMenu);
          panel.addEventListener("click", function (e) {
            e.stopPropagation();
          });
        })();

        if (dlMd) {
          dlMd.addEventListener("click", function () {
            var mdText = currentDownloadMarkdown();
            if (!mdText) return;
            downloadBlob(new Blob([mdText], { type: "text/markdown;charset=utf-8" }), lastBase + ".md");
            closeExportMenu();
          });
        }

        if (dlZip) {
          dlZip.addEventListener("click", function () {
            var mdText = currentDownloadMarkdown();
            if (!mdText) return;
            var zip = new JSZip();
            zip.file("converted.md", mdText);
            var folder = zip.folder("images");
            Object.keys(lastImages).forEach(function (relPath) {
              var b64 = lastImages[relPath];
              var name = relPath.replace(/^images\//, "");
              if (!name || !folder) return;
              var bin = Uint8Array.from(atob(b64), function (c) {
                return c.charCodeAt(0);
              });
              folder.file(name, bin);
            });
            zip.generateAsync({ type: "blob" }).then(function (blob) {
              downloadBlob(blob, lastBase + ".zip");
              closeExportMenu();
            });
          });
        }

        if (dlDocx) {
          dlDocx.addEventListener("click", function () {
            var mdText = restoreDataUriImagesToPaths(currentDownloadMarkdown());
            if (!mdText || !String(mdText).trim()) return;
            dlDocx.disabled = true;
            fetch(apiUrl("/api/export-docx"), {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                markdown: mdText,
                filename_base: lastBase,
                images: lastImages,
                include_images: true,
              }),
            })
              .then(function (res) {
                if (!res.ok) {
                  return res.text().then(function (t) {
                    var data = parseApiJsonBody(res, t);
                    throw new Error(formatApiError(data, res));
                  });
                }
                return res.blob();
              })
              .then(function (blob) {
                downloadBlob(blob, lastBase + ".docx");
                closeExportMenu();
              })
              .catch(function (e) {
                showWorkspaceError(e.message || String(e));
              })
              .finally(function () {
                dlDocx.disabled = false;
              });
          });
        }

        var notionExportPanel = document.getElementById("notionExportPanel");
        var toggleNotionExportBtn = document.getElementById("toggleNotionExportBtn");
        if (toggleNotionExportBtn && notionExportPanel) {
          toggleNotionExportBtn.addEventListener("click", function () {
            notionExportPanel.classList.toggle("hidden");
            var expanded = !notionExportPanel.classList.contains("hidden");
            toggleNotionExportBtn.setAttribute("aria-expanded", expanded ? "true" : "false");
            toggleNotionExportBtn.textContent = expanded ? "Hide Notion export" : "Sync to Notion";
          });
        }

        var notionPageUrlInput = document.getElementById("notionPageUrl");
        try {
          var savedPage =
            localStorage.getItem("notion_page_url") || localStorage.getItem("notion_database_id");
          if (savedPage && notionPageUrlInput) notionPageUrlInput.value = savedPage;
        } catch (eN0) {}
        if (notionPageUrlInput) {
          notionPageUrlInput.addEventListener("change", function () {
            try {
              localStorage.setItem("notion_page_url", notionPageUrlInput.value || "");
            } catch (eN1) {}
          });
        }

        function normalizeNotionIntegrationToken(s) {
          if (s == null || s === "") return "";
          var t = String(s).replace(/\uFEFF/g, "").trim();
          t = t.replace(/[\u200B-\u200D\u2060]/g, "").trim();
          t = t.replace(/\r/g, "").replace(/\n/g, "").trim();
          if (/^bearer\s+/i.test(t)) t = t.replace(/^bearer\s+/i, "").trim();
          if (
            (t.charAt(0) === '"' && t.charAt(t.length - 1) === '"') ||
            (t.charAt(0) === "'" && t.charAt(t.length - 1) === "'")
          ) {
            t = t.slice(1, -1).trim();
          }
          return t;
        }

        function renderNotionPropertyFields(rows, suggested) {
          var container = document.getElementById("notionPropsFields");
          var wrap = document.getElementById("notionPropsWrap");
          if (!container || !wrap) return;
          container.innerHTML = "";
          suggested = suggested || {};
          if (!rows || !rows.length) {
            wrap.classList.add("hidden");
            return;
          }
          wrap.classList.remove("hidden");
          rows.forEach(function (row) {
            var name = row.name;
            var typ = row.type || "rich_text";
            var def = Object.prototype.hasOwnProperty.call(suggested, name)
              ? String(suggested[name] == null ? "" : suggested[name])
              : "";
            var lab = document.createElement("label");
            lab.className = "notion-export-label";
            var id =
              "np_" +
              String(name)
                .replace(/[^a-z0-9]+/gi, "_")
                .replace(/^_|_$/g, "")
                .slice(0, 40) +
              "_" +
              Math.random().toString(36).slice(2, 8);
            lab.setAttribute("for", id);
            lab.textContent = name + " (" + typ + ")";
            container.appendChild(lab);
            var input;
            if (typ === "select" || typ === "status") {
              input = document.createElement("select");
              var z0 = document.createElement("option");
              z0.value = "";
              z0.textContent = "—";
              input.appendChild(z0);
              (row.options || []).forEach(function (opt) {
                var o = document.createElement("option");
                o.value = opt;
                o.textContent = opt;
                input.appendChild(o);
              });
            } else if (typ === "multi_select") {
              input = document.createElement("input");
              input.type = "text";
              input.placeholder = "Comma-separated";
            } else if (typ === "checkbox") {
              input = document.createElement("input");
              input.type = "checkbox";
            } else if (typ === "date") {
              input = document.createElement("input");
              input.type = "date";
            } else if (typ === "number") {
              input = document.createElement("input");
              input.type = "number";
              input.step = "any";
            } else if (typ === "url") {
              input = document.createElement("input");
              input.type = "url";
            } else {
              input = document.createElement("input");
              input.type = "text";
            }
            input.id = id;
            if (typ === "checkbox") {
              input.className = "notion-export-field";
              input.style.margin = "0 0 0.5rem 0";
            } else {
              input.className = "field block notion-export-field";
            }
            input.setAttribute("data-notion-prop", name);
            input.setAttribute("data-notion-prop-type", typ);
            if (typ === "checkbox") {
              if (def === "true" || def === "1") input.checked = true;
            } else if (def) {
              input.value = def;
            }
            container.appendChild(input);
          });
        }

        function collectNotionExtraProperties() {
          var root = document.getElementById("notionPropsFields");
          if (!root) return {};
          var out = {};
          root.querySelectorAll("[data-notion-prop]").forEach(function (el) {
            var name = el.getAttribute("data-notion-prop");
            if (!name) return;
            var typ = el.getAttribute("data-notion-prop-type") || "";
            if (typ === "checkbox") {
              if (el.checked) out[name] = "true";
              return;
            }
            var v = (el.value || "").trim();
            if (v) out[name] = v;
          });
          return out;
        }

        var notionInspectBtn = document.getElementById("notionInspectBtn");
        if (notionInspectBtn) {
          notionInspectBtn.addEventListener("click", function () {
            var tokenEl = document.getElementById("notionToken");
            var pageEl = document.getElementById("notionPageUrl");
            var st = document.getElementById("notionInspectStatus");
            var token = normalizeNotionIntegrationToken(tokenEl && tokenEl.value ? tokenEl.value : "");
            var pageUrl = pageEl && pageEl.value ? pageEl.value.trim() : "";
            var mdText = currentDownloadMarkdown();
            if (!token) {
              showWorkspaceError("Paste your Notion integration secret.");
              return;
            }
            if (!pageUrl) {
              showWorkspaceError("Paste your Notion page URL or ID.");
              return;
            }
            notionInspectBtn.disabled = true;
            if (st) st.textContent = "Checking Notion…";
            fetch(apiUrl("/api/notion/inspect"), {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                notion_token: token,
                page_url: pageUrl,
                markdown: mdText && String(mdText).trim() ? mdText : null,
              }),
            })
              .then(function (res) {
                return res.text().then(function (text) {
                  var data = parseApiJsonBody(res, text);
                  if (!res.ok) throw new Error(formatApiError(data, res));
                  return data;
                });
              })
              .then(function (data) {
                if (st) {
                  st.textContent = "";
                  var ok = document.createElement("span");
                  ok.textContent =
                    "Connected as " +
                    (data.notion_user && data.notion_user.name ? data.notion_user.name : "integration") +
                    ". ";
                  st.appendChild(ok);
                }
                renderNotionPropertyFields(data.properties || [], data.suggested_defaults || {});
              })
              .catch(function (err) {
                if (st) st.textContent = "";
                renderNotionPropertyFields([], {});
                showWorkspaceError(err.message || String(err));
              })
              .finally(function () {
                notionInspectBtn.disabled = false;
              });
          });
        }

        var exportNotionBtn = document.getElementById("exportNotionBtn");
        if (exportNotionBtn) {
          exportNotionBtn.addEventListener("click", function () {
            var tokenEl = document.getElementById("notionToken");
            var pageEl = document.getElementById("notionPageUrl");
            var titleEl = document.getElementById("notionPageTitle");
            var st = document.getElementById("notionExportStatus");
            var token = normalizeNotionIntegrationToken(tokenEl && tokenEl.value ? tokenEl.value : "");
            var pageUrl = pageEl && pageEl.value ? pageEl.value.trim() : "";
            var pageTitle = titleEl && titleEl.value ? titleEl.value.trim() : "";
            var mdText = restoreDataUriImagesToPaths(currentDownloadMarkdown());
            if (!mdText || !String(mdText).trim()) {
              showWorkspaceError("Nothing to export yet.");
              return;
            }
            if (!token) {
              showWorkspaceError("Paste your Notion integration secret.");
              return;
            }
            if (!pageUrl) {
              showWorkspaceError("Paste your Notion page URL or ID.");
              return;
            }
            var notionOmitImg =
              document.getElementById("notionOmitImages") &&
              document.getElementById("notionOmitImages").checked;
            var notionPayload = {
              notion_token: token,
              page_url: pageUrl,
              title: pageTitle || null,
              markdown: mdText,
              images: notionOmitImg ? {} : lastImages || {},
              include_images: !notionOmitImg,
              extra_properties: collectNotionExtraProperties(),
            };
            var notionPayLen = JSON.stringify(notionPayload).length;
            if (notionPayLen > 2000000) {
              showWorkspaceError(
                "Notion export JSON is about " +
                  Math.round(notionPayLen / 1e6) +
                  " MB — too large for many servers. Enable \"Text only — skip images\" or raise client_max_body_size on your reverse proxy."
              );
              return;
            }
            exportNotionBtn.disabled = true;
            if (st) st.textContent = "Sending to Notion…";
            fetch(apiUrl("/api/notion/export"), {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(notionPayload),
            })
              .then(function (res) {
                return res.text().then(function (text) {
                  var data = parseApiJsonBody(res, text);
                  if (!res.ok) throw new Error(formatApiError(data, res));
                  return data;
                });
              })
              .then(function (data) {
                if (!st) return;
                st.textContent = "";
                var u = data.notion_url || "";
                if (u) {
                  var a = document.createElement("a");
                  a.href = u;
                  a.target = "_blank";
                  a.rel = "noopener noreferrer";
                  a.textContent = "Open page in Notion";
                  st.appendChild(a);
                }
                var w = data.warnings;
                if (w && w.length) {
                  var span = document.createElement("span");
                  span.textContent = (u ? " · " : "") + w.join(" ");
                  st.appendChild(span);
                }
                if (!u && (!w || !w.length)) st.textContent = "Done.";
              })
              .catch(function (err) {
                if (st) st.textContent = "";
                showWorkspaceError(err.message || String(err));
              })
              .finally(function () {
                exportNotionBtn.disabled = false;
              });
          });
        }

        copyMd.addEventListener("click", function () {
          var mdText = currentDownloadMarkdown();
          if (!mdText) return;
          navigator.clipboard.writeText(mdText).then(
            function () {
              copyMd.textContent = "Copied!";
              setTimeout(function () {
              copyMd.textContent = "Copy Markdown";
              }, 2000);
            },
            function () {
              showWorkspaceError("Could not copy to clipboard.");
            }
          );
        });
      })();
