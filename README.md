# Smartdown

Smartdown converts PDFs to Markdown in the browser, with a block-based editor, optional AI chat (Explain / note bullets), exports (ZIP, Word, Notion), and PDF preview.

## Hosted app (Mistral)

Use the deployed site for a zero-install experience:

**[https://smartdownnotes.vercel.app](https://smartdownnotes.vercel.app)**

- Choose a **Mistral** model in the AI panel and paste your **Mistral API key** when Mistral is selected (stored in `sessionStorage` in your browser).
- **Ollama does not appear on Vercel**: the server that runs on Vercel cannot reach `localhost` on your computer. For local models, run the app yourself (below).

## Local hosting (Ollama or Mistral)

Fork or clone the repo, then:

```bash
cd pdf2md
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
# or: pip install -e .
```

Run the API + static UI:

```bash
uvicorn smartdown.app:app --reload --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**.

### Ollama (local LLM)

1. Install [Ollama](https://ollama.com) and pull the models you want (`ollama pull …`).
2. Start the daemon: `ollama serve` (default **http://127.0.0.1:11434**).
3. In Smartdown, pick an **Ollama** model in the AI dropdown. The app’s **backend** calls Ollama, so the URL must be reachable from where `uvicorn` runs (same machine is fine).

Optional environment variables (e.g. in a `.env` file at the project root):

| Variable | Purpose |
|----------|---------|
| `OLLAMA_HOST` | Base URL for Ollama (default `http://127.0.0.1:11434`) |
| `MISTRAL_API_KEY` | Server-side Mistral key (optional; UI can supply a key per session) |
| `NOTION_INTEGRATION_SECRET` / `NOTION_PAGE_ID` | Defaults for Notion export/inspect |
| `SMARTDOWN_PDF_USE_OCR` | `true`/`false` — OCR in pymupdf4llm pipeline |
| `MISTRAL_OCR_EXTRACT_IMAGES` | `1`/`true` — richer PDF→MD with figure PNGs (heavier) |

Python **3.12+** is required (see `pyproject.toml`).

## Keyboard shortcuts

**Mod** means **⌘ on macOS** and **Ctrl on Windows/Linux**.

| Shortcut | Action |
|----------|--------|
| **Mod+B** | **Add bullet** — LLM note for selected block(s) (use row checkboxes or focus a block). |
| **Mod+E** | **Explain** — open AI chat for the current / selected block(s). |
| **Mod+Z** / **Mod+Shift+Z** | **Undo / redo** Markdown (works when the notes preview is active; not while typing in the sidebar chat or other sidebar fields). |
| **Delete** or **Backspace** | **Delete** selected blocks (checkboxes), or the block under focus when allowed. |
| **Mod+M** | While editing inside a block, **merge** that block with the **next** one. |
| **Enter** (on a line that is only `---`, `***`, or `___`) | Turn the line into a **horizontal rule** in the preview. |

### Preview editing (Markdown helpers)

With the caret in a block in the rendered notes, typing patterns such as `#` … **space**, `-` **space**, `1.` **space**, `>` **space** can expand into headings, lists, blockquotes, etc. (see in-app behavior).

### AI chat input

- **Enter** — send the message.  
- **Shift+Enter** — new line.

---

Questions or improvements: open an issue or PR on your fork.
