import os
import pathlib
import sys
import fitz  # PyMuPDF
import re
from anthropic import AsyncAnthropic
from PIL import Image
import io
import base64
import subprocess
import asyncio



# --- Initial Configuration ---

def setup_environment() -> AsyncAnthropic:
    """
    Validates the environment and returns an async Anthropic client.
    Raises RuntimeError if the API key is not set (safe for GUI apps).
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError(
            "La variabile d'ambiente ANTHROPIC_API_KEY non è stata impostata. "
            "Per favore, imposta la tua chiave API per continuare."
        )
    print("Chiave API di Anthropic rilevata.")
    return AsyncAnthropic(api_key=api_key)


# --- PDF Processing Functions ---

def convert_page_to_image(page: fitz.Page) -> Image.Image:
    """
    Converts a PDF page into a PIL Image using raw pixel data.
    Avoids the unnecessary PNG encode/decode cycle of the previous implementation.
    """
    pix = page.get_pixmap(dpi=150)
    mode = "RGBA" if pix.alpha else "RGB"
    return Image.frombytes(mode, (pix.width, pix.height), pix.samples)


def image_to_base64(image: Image.Image, format: str = "png") -> str:
    """Converts a PIL image into a base64 string."""
    buffered = io.BytesIO()
    image.save(buffered, format=format)
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


async def analyze_and_summarize_slide(
    client: AsyncAnthropic,
    image: Image.Image,
    text_content: str,
    slide_number: int,
    total_slides: int,
    document_language: str,
    is_first_slide: bool = False,
    status_callback=None,
    stop_event=None,
) -> str:
    """
    Sends extracted text and slide image to Claude to format text and identify images.
    Uses exponential backoff on API errors.
    """
    system_prompt = (
        "You are an expert LaTeX formatter. Your task is to format extracted slide text into LaTeX. "
        "Output ONLY valid LaTeX code. Do not add any conversational text, notes, or lists of identified elements."
    )

    if is_first_slide:
        user_prompt = f"""This is the first slide of a presentation. Extract the main title and any subtitle or author.

**EXTRACTED TEXT:**
{text_content}

**TASK & RULES (Follow Strictly):**
1.  **EXTRACT TITLE AND AUTHOR:** Identify the main title and, if available, the author/subtitle.
2.  **FORMAT:** Format strictly as:
    - `\\title{{Main Title}}`
    - `\\author{{Author or Subtitle}}`
3.  **NO AUTHOR:** If no author is found, return only `\\title{{...}}`.
4.  **IRRELEVANT:** If no clear title exists, return `[IRRELEVANT]`."""
    else:
        user_prompt = f"""This is slide {slide_number} of {total_slides}. Format the text into LaTeX.

**EXTRACTED TEXT:**
{text_content}

**LANGUAGE:** The content is in {document_language}.

**TASK & RULES (Follow Strictly):**
1.  **IDENTIFY TITLE:** Format the slide title as `\\subsection*{{Slide Title}}`.
2.  **VISUALS & TEXT STRATEGY:**
    - **CHECK FOR VISUALS:** If the image contains a **TABLE**, graph, chart, diagram, or complex visual:
        - Insert `[EMBED_IMAGE]` on a new line.
        - **DO NOT** transcribe tables into LaTeX.
        - **DEFAULT:** Output ONLY the Title and `[EMBED_IMAGE]`.
        - **EXCEPTION:** If the slide has **more than 50 words**, include both text AND `[EMBED_IMAGE]` (tag after text).
    - **NO VISUALS:** Format the **EXTRACTED TEXT** into LaTeX (`itemize`/`enumerate` for lists, `\\textbf` for bold).
    - **IGNORE UI ELEMENTS:** Navigation tabs, slide counters, icons are NOT visuals. Ignore them entirely.
3.  **LATEX SYNTAX:** Valid LaTeX only. Escape special characters, use `\\textbf`, `\\[ ... \\]` for formulas.
    - Do not use `\\h` for the function h. Just use `h`.
    - Escape underscores `_` in text mode (use `\\_`).
    - Do not use `\\textit` or `\\textbf` inside math formulas. Use regular math notation (e.g. `s_i^*` not `s_i^\\textit{{*}}`).
    - Do not output the same line twice. Each line of content must appear only once.
    - Split long formulas with `\\begin{{align*}} ... \\\\ ... \\end{{align*}}`.
    - **CRITICAL: Every `\\begin{{itemize}}` MUST have a matching `\\end{{itemize}}`. Every `\\begin{{enumerate}}` MUST have a matching `\\end{{enumerate}}`. Never leave a list environment unclosed.**
    - Do NOT wrap formulas inside `\\[ ... \\]` or `align*` with extra `$` signs. The `$` delimiter is only for inline math. Display math environments already handle math mode.
    - Do NOT use `$$...$$`. Use `\\[ ... \\]` instead.
4.  **IRRELEVANT SLIDES:** Return only `[IRRELEVANT]` for slides with no academic content.
5.  **NO COMMENTARY:** Output **ONLY** the LaTeX code."""

    if status_callback:
        status_callback(f"Analisi della slide {slide_number}/{total_slides}...")

    if stop_event and stop_event.is_set():
        return None

    base64_image = image_to_base64(image)
    max_retries = 5
    message = None
    for attempt in range(max_retries):
        try:
            if stop_event and stop_event.is_set():
                return None
            message = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                system=system_prompt,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": base64_image,
                                },
                            },
                            {"type": "text", "text": user_prompt},
                        ],
                    }
                ],
            )
            break  # success
        except Exception as e:
            error_str = str(e)
            # Rate limit errors (429) need a full minute to recover
            is_rate_limit = "rate_limit_error" in error_str or "429" in error_str
            wait_time = 60 if is_rate_limit else 2 ** attempt
            if attempt < max_retries - 1:
                if status_callback:
                    status_callback(
                        f"API Error (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {wait_time}s..."
                    )
                await asyncio.sleep(wait_time)
            else:
                if status_callback:
                    status_callback(f"Failed after {max_retries} attempts: {e}")
                raise

    if status_callback:
        status_callback(f"Slide {slide_number} analizzata.")
    return message.content[0].text


async def detect_document_language(
    client: AsyncAnthropic, doc: fitz.Document, status_callback=None
) -> str:
    """Detects the primary language of the PDF by sampling text from the first pages."""
    if status_callback:
        status_callback("Detecting document language...")

    text_sample = ""
    for i in range(min(3, len(doc))):
        page = doc.load_page(i)
        text = page.get_text("text")
        if text.strip():
            text_sample += text + "\n"
            if len(text_sample) >= 500:
                break

    if not text_sample.strip():
        if status_callback:
            status_callback("No text found; defaulting to 'english'.")
        return "english"

    system_prompt = (
        "You are an expert language detection assistant. "
        "Your only response must be the name of the detected language in English and lowercase "
        "(e.g., 'italian', 'english', 'french', 'spanish', 'german', 'portuguese', 'dutch')."
    )
    user_prompt = f"Detect the language of the following text:\n\n{text_sample[:1000]}"

    try:
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=10,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        detected_lang = message.content[0].text.strip().lower().split(" ")[0]
        if status_callback:
            status_callback(f"Detected language: {detected_lang}")
        return detected_lang
    except Exception as e:
        if status_callback:
            status_callback(f"Language detection error: {e}. Defaulting to 'english'.")
        return "english"


def sanitize_filename(name: str) -> str:
    """Removes invalid characters from file/directory names."""
    return re.sub(r'[<>:"/\\|?*]', "_", name).strip()


def generate_latex_preamble(
    title: str,
    document_language: str,
    author: str = "Academic Summarizer Assistant",
) -> str:
    """Returns the LaTeX preamble, adapting the Babel language."""
    babel_language_map = {
        "english": "english",
        "italian": "italian",
        "french": "french",
        "spanish": "spanish",
        "german": "german",
        "portuguese": "portuguese",
        "dutch": "dutch",
    }
    babel_lang = babel_language_map.get(document_language, "english")

    preamble = rf"""\documentclass[11pt]{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage[T1]{{fontenc}}

% 1. Font moderno simile ad Aptos per pdflatex
\usepackage{{tgheros}}
\renewcommand{{\familydefault}}{{\sfdefault}}

% 2. Allineamento a sinistra
\usepackage{{ragged2e}}

\usepackage{{graphicx}}
\usepackage[{babel_lang}]{{babel}}

% 3. Margini
\usepackage{{geometry}}
\geometry{{a4paper, margin=1.27cm}}

% 4. Interlinea (simile a Word)
\usepackage{{setspace}}
\setstretch{{1.0}}

\usepackage{{amsmath}}
\usepackage{{amssymb}}
\usepackage{{hyperref}}
\usepackage{{float}}
\usepackage{{array}}
\usepackage{{microtype}}
\usepackage{{booktabs}}
"""
    safe_title = title.replace("_", r"\_")
    title_cmd = f"\\title{{{safe_title}}}"
    author_cmd = f"\\author{{{author}}}"
    rest = r"""
\date{\today}
\hypersetup{
    colorlinks=true,
    linkcolor=blue,
    filecolor=magenta,
    urlcolor=cyan,
}

\begin{document}
\RaggedRight
\maketitle
"""
    return preamble + "\n" + title_cmd + "\n" + author_cmd + "\n" + rest


def generate_latex_end() -> str:
    """Returns the closing tag of the LaTeX document."""
    return r"\end{document}"


def compile_latex(tex_path: str, status_callback=None):
    """
    Compiles the LaTeX file into PDF using pdflatex.
    Runs pdflatex TWICE to resolve cross-references and page numbers correctly.
    """
    if status_callback:
        status_callback("\n--- Compiling PDF ---")
    if not os.path.exists(tex_path):
        if status_callback:
            status_callback(f"Error: File not found: {tex_path}")
        return

    output_dir = os.path.abspath(os.path.dirname(tex_path) or ".")
    tex_filename = os.path.basename(tex_path)
    cmd = ["pdflatex", "-interaction=nonstopmode", tex_filename]

    try:
        # First pass
        subprocess.run(cmd, capture_output=True, text=True, cwd=output_dir)
        # Second pass — resolves cross-references, TOC, page numbers
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=output_dir)

        if result.returncode == 0:
            pdf_name = os.path.splitext(tex_filename)[0] + ".pdf"
            if status_callback:
                status_callback(
                    f"Success! PDF generated in: {os.path.join(output_dir, pdf_name)}"
                )
        else:
            if status_callback:
                status_callback("Error during LaTeX compilation:")
                status_callback("\n".join(result.stdout.splitlines()[-20:]))
    except FileNotFoundError:
        if status_callback:
            status_callback(
                "Error: 'pdflatex' not found. Install a LaTeX distribution (e.g. MiKTeX)."
            )
    except Exception as e:
        if status_callback:
            status_callback(f"Unexpected compilation error: {e}")


def _balance_list_environments(text: str) -> str:
    r"""
    Ensures every \begin{itemize/enumerate} has a matching \end{itemize/enumerate}.

    Strategy: walk through lines tracking a nesting stack.  Before any
    structural boundary (\subsection*, \section*, \begin{figure}, etc.)
    auto-close any open list environments.  At the very end, close any
    remaining open environments.  Also removes orphan \end tags that
    have no matching \begin.
    """
    lines = text.split('\n')
    env_stack = []  # stack of open environment names ('itemize' or 'enumerate')
    result_lines = []

    # Patterns
    begin_pat = re.compile(r'\\begin\{(itemize|enumerate)\}')
    end_pat = re.compile(r'\\end\{(itemize|enumerate)\}')
    # Structural boundaries where all open lists should be closed
    boundary_pat = re.compile(
        r'\\(?:sub)?section\*?\{|\\begin\{figure\}|\\begin\{table\}'
    )

    for line in lines:
        stripped = line.strip()

        # If this line is a structural boundary, close all open list envs first
        if boundary_pat.search(stripped) and env_stack:
            while env_stack:
                env = env_stack.pop()
                result_lines.append(f'\\end{{{env}}}')

        # Process \begin and \end on this line
        begins = begin_pat.findall(stripped)
        ends = end_pat.findall(stripped)

        # Check for orphan \end tags (more closes than the stack has)
        temp_stack = list(env_stack)
        new_line = line
        for env_name in ends:
            if temp_stack and temp_stack[-1] == env_name:
                temp_stack.pop()
            elif not temp_stack:
                # Orphan \end — remove it from the line
                new_line = re.sub(
                    r'\\end\{' + env_name + r'\}',
                    '', new_line, count=1
                )

        # Now apply to the real stack
        for env_name in begins:
            env_stack.append(env_name)
        for env_name in ends:
            if env_stack and env_stack[-1] == env_name:
                env_stack.pop()

        # Only add the line if it still has content (after orphan removal)
        if new_line.strip() or not ends:
            result_lines.append(new_line)

    # Close any remaining open environments at the end
    while env_stack:
        env = env_stack.pop()
        result_lines.append(f'\\end{{{env}}}')

    return '\n'.join(result_lines)


def _fix_redundant_dollars(text: str) -> str:
    """
    Fixes redundant $ signs in LaTeX math environments:
    1. Removes $ wrapping inside \\[...\\] display math
    2. Removes $ wrapping inside align*, equation*, gather* environments
    3. Converts $$...$$ to \\[...\\]
    """
    # Convert $$...$$ to \[...\]
    text = re.sub(r'\$\$(.+?)\$\$', lambda m: '\\[' + m.group(1) + '\\]', text, flags=re.DOTALL)

    # Remove redundant $ inside \[...\] — e.g. \[ $x + y$ \] → \[ x + y \]
    def _strip_dollars_in_display(match):
        inner = match.group(1)
        # Remove $ pairs inside the display math
        inner = re.sub(r'(?<!\\)\$(.+?)(?<!\\)\$', r'\1', inner)
        return r'\[' + inner + r'\]'
    text = re.sub(r'\\\[(.+?)\\\]', _strip_dollars_in_display, text, flags=re.DOTALL)

    # Remove redundant $ inside align*, equation*, gather*
    def _strip_dollars_in_env(match):
        env_name = match.group(1)
        inner = match.group(2)
        inner = re.sub(r'(?<!\\)\$(.+?)(?<!\\)\$', r'\1', inner)
        return f'\\begin{{{env_name}}}' + inner + f'\\end{{{env_name}}}'
    text = re.sub(
        r'\\begin\{(align\*|equation\*|gather\*|align|equation|gather)\}'
        r'(.+?)'
        r'\\end\{\1\}',
        _strip_dollars_in_env,
        text,
        flags=re.DOTALL,
    )

    return text


def _clean_analysis_result(analysis_result: str) -> str:
    """
    Post-processes Claude's output to fix common Markdown-in-LaTeX issues.
    Extracted into a helper for clarity and reusability.
    """
    # Remove Markdown code fences
    analysis_result = re.sub(r"```latex", "", analysis_result, flags=re.IGNORECASE)
    analysis_result = re.sub(r"```", "", analysis_result)

    # Convert Markdown bold (**text**) to \textbf{text}
    analysis_result = re.sub(r"\*\*(.*?)\*\*", r"\\textbf{\1}", analysis_result)

    # Convert Markdown italics (*text*) to \textit{text}, ignoring LaTeX * (e.g. \section*{})
    analysis_result = re.sub(
        r"(?<![\\w\\\\])\*(?!\s)(.*?)(?<!\s)\*", r"\\textit{\1}", analysis_result
    )

    # Convert Markdown bullet points (* Item) to LaTeX
    analysis_result = re.sub(
        r"^\s*\*\s+", r"\\textbullet\\ ", analysis_result, flags=re.MULTILINE
    )

    # Convert '#' Markdown headers to bold
    analysis_result = re.sub(r"#+\s*(.*)", r"\\textbf{\1}", analysis_result)

    # Fix common LLM LaTeX errors
    analysis_result = analysis_result.replace(r"\h(", "h(").replace(r"\h{", "h{")
    # Replace unicode minus with ASCII hyphen
    analysis_result = analysis_result.replace("−", "-")

    # Fix redundant $ signs in display math environments
    analysis_result = _fix_redundant_dollars(analysis_result)

    # Escape bare $ that are NOT part of a $...$ math-mode pair.
    # Strategy: find legitimate math-mode pairs first, then only escape
    # orphan $ signs (followed by a digit) outside of math pairs.
    def _escape_orphan_dollars(text):
        math_spans = [(m.start(), m.end()) for m in re.finditer(r'(?<!\\)\$(?!\$).+?(?<!\\)\$', text)]
        result = []
        last = 0
        for start, end in math_spans:
            gap = re.sub(r'(?<!\\)\$\s*(-?\d)', r'\\$\1', text[last:start])
            result.append(gap)
            result.append(text[start:end])  # keep math pair intact
            last = end
        tail = re.sub(r'(?<!\\)\$\s*(-?\d)', r'\\$\1', text[last:])
        result.append(tail)
        return ''.join(result)
    analysis_result = _escape_orphan_dollars(analysis_result)

    # Balance unclosed itemize/enumerate environments
    analysis_result = _balance_list_environments(analysis_result)

    # Remove duplicate consecutive lines (Claude sometimes outputs a broken
    # line followed by the corrected version)
    lines = analysis_result.split('\n')
    deduped = []
    for line in lines:
        stripped = line.strip()
        if deduped and stripped and stripped == deduped[-1].strip():
            continue
        deduped.append(line)
    analysis_result = '\n'.join(deduped)

    return analysis_result


async def process_pdf(
    client: AsyncAnthropic,
    pdf_path: str,
    status_callback=None,
    progress_callback=None,
    stop_event=None,
    stop_config=None,
    decision_event=None,
):
    """
    Orchestrates the PDF-to-LaTeX conversion.

    Strategy:
    - Slide 0 is processed first (sequentially) to extract the presentation title,
      which is then used to create the correctly-named output directory.
    - All remaining slides are processed concurrently (up to CONCURRENT_LIMIT at a time)
      using asyncio.Semaphore, then assembled in order.
    """
    CONCURRENT_LIMIT = 1  # Process one slide at a time to avoid rate limit bursts

    def update_status(msg):
        if status_callback:
            status_callback(msg)

    update_status(f"Starting to process file: {pdf_path}")
    doc = fitz.open(pdf_path)
    total_pages = len(doc)

    document_language = await detect_document_language(client, doc, status_callback)

    # ----------------------------------------------------------------
    # Step 1: Process slide 0 to extract title & author
    # ----------------------------------------------------------------
    if stop_event and stop_event.is_set():
        return None

    update_status(f"\n--- Processing Page 1/{total_pages} (title slide) ---")
    page_0 = doc.load_page(0)
    slide_0_image = convert_page_to_image(page_0)
    text_0 = page_0.get_text("text")

    title_result = await analyze_and_summarize_slide(
        client, slide_0_image, text_0, 1, total_pages, document_language,
        is_first_slide=True, status_callback=status_callback, stop_event=stop_event,
    )

    if title_result is None:
        return None

    # Parse title and author from slide 0 result
    pdf_title = os.path.splitext(os.path.basename(pdf_path))[0]
    author = "Academic Summarizer Assistant"
    if title_result.strip() != "[IRRELEVANT]":
        title_match = re.search(r"\\title\{(.*?)\}", title_result)
        author_match = re.search(r"\\author\{(.*?)\}", title_result)
        if title_match:
            pdf_title = sanitize_filename(title_match.group(1))
            if author_match:
                author = author_match.group(1)

    # Create output directories AFTER knowing the real title (fixes wrong-dir bug)
    output_dir = os.path.abspath(os.path.join("output", pdf_title))
    images_dir = os.path.join(output_dir, "images")
    pathlib.Path(images_dir).mkdir(parents=True, exist_ok=True)
    update_status(f"Output directory set to: {output_dir}")

    # Build preamble once with the real title
    preamble = generate_latex_preamble(pdf_title, document_language, author)

    if total_pages == 1:
        final_path = os.path.join(output_dir, f"{pdf_title}.tex")
        with open(final_path, "w", encoding="utf-8") as f:
            f.write("\n".join([preamble, generate_latex_end()]))
        compile_latex(final_path, status_callback)
        return final_path

    # ----------------------------------------------------------------
    # Step 2: Process remaining slides concurrently
    # ----------------------------------------------------------------
    semaphore = asyncio.Semaphore(CONCURRENT_LIMIT)
    completed_count = 0
    completed_lock = asyncio.Lock()

    async def process_slide(i: int):
        nonlocal completed_count
        if stop_event and stop_event.is_set():
            return i, None

        async with semaphore:
            # Check again after acquiring the semaphore
            if stop_event and stop_event.is_set():
                return i, None

            update_status(f"\n--- Processing Page {i + 1}/{total_pages} ---")
            # Page loading and image conversion happen inside the semaphore
            # so at most CONCURRENT_LIMIT pages are in memory simultaneously
            page = doc.load_page(i)
            slide_image = convert_page_to_image(page)
            text_content = page.get_text("text")

            result = await analyze_and_summarize_slide(
                client, slide_image, text_content,
                i + 1, total_pages, document_language,
                is_first_slide=False, status_callback=status_callback, stop_event=stop_event,
            )

        async with completed_lock:
            completed_count += 1
            if progress_callback:
                progress_callback((completed_count / (total_pages - 1)) * 100)

        return i, result

    tasks = [process_slide(i) for i in range(1, total_pages)]
    # asyncio.gather preserves input order: raw_results[j] == result for slide j+1
    raw_results = await asyncio.gather(*tasks)

    # ----------------------------------------------------------------
    # Step 3: Handle stop event (after all concurrent tasks finish)
    # ----------------------------------------------------------------
    stopped = stop_event is not None and stop_event.is_set()
    if stopped:
        if decision_event:
            update_status("Waiting for user decision...")
            decision_event.wait()
        if not (stop_config and stop_config.get("keep_partial", False)):
            return None

    # ----------------------------------------------------------------
    # Step 4: Assemble LaTeX content in slide order
    # ----------------------------------------------------------------
    latex_content = [preamble]
    last_slide_title = None

    for i, analysis_result in raw_results:
        if analysis_result is None:
            continue

        if analysis_result.strip() == "[IRRELEVANT]":
            update_status(f"Slide {i + 1} is irrelevant, skipping.")
            continue

        analysis_result = _clean_analysis_result(analysis_result)

        # Detect duplicate slide titles to merge content seamlessly
        current_title_match = re.search(
            r"\\subsection\*\{((?:[^{}]|\{[^{}]*\})*)\}", analysis_result, re.DOTALL
        )
        current_title = current_title_match.group(1).strip() if current_title_match else None

        if current_title and last_slide_title and current_title == last_slide_title:
            update_status(f"Merging content for duplicate title: {current_title}")
            analysis_result = analysis_result.replace(current_title_match.group(0), "", 1)

        last_slide_title = current_title

        # Handle [EMBED_IMAGE] tag
        tag_pattern = r"\[embed\\?_image\]"
        if re.search(tag_pattern, analysis_result, re.IGNORECASE):
            parts = re.split(tag_pattern, analysis_result, flags=re.IGNORECASE)
            latex_content.append("".join(parts))

            image_name = f"slide_{i + 1}.png"
            image_path = os.path.join(images_dir, image_name)
            latex_image_path = f"images/{image_name}"

            # Re-render the page for saving (fast, CPU-only, avoids keeping all images in RAM)
            save_image = convert_page_to_image(doc.load_page(i))
            save_image.save(image_path)
            update_status(f"Image saved: {image_path}")

            latex_content.append("\n\\begin{figure}[H]")
            latex_content.append(
                f"\\centering\\includegraphics[width=0.5\\textwidth]{{{latex_image_path}}}"
            )
            latex_content.append("\\end{figure}\n")
        else:
            latex_content.append(analysis_result)

    if stopped and stop_config and stop_config.get("keep_partial", False):
        latex_content.append(r"\vspace{1cm}")
        latex_content.append(
            r"\noindent\textbf{[DISCLAIMER: This file was interrupted during conversion and may be incomplete.]}"
        )

    latex_content.append(generate_latex_end())

    # Final pass: balance list environments across the full assembled document.
    # Per-slide balancing may miss unclosed envs that span slide boundaries.
    final_text = "\n".join(latex_content)
    final_text = _balance_list_environments(final_text)

    final_latex_path = os.path.join(output_dir, f"{pdf_title}.tex")
    with open(final_latex_path, "w", encoding="utf-8") as f:
        f.write(final_text)

    update_status(f"\nProcessing complete! File saved to: {final_latex_path}")
    update_status(f"Images saved to: {images_dir}")

    compile_latex(final_latex_path, status_callback)
    return final_latex_path


async def process_pdf_summary(
    client: AsyncAnthropic,
    pdf_path: str,
    status_callback=None,
    progress_callback=None,
    stop_event=None,
) -> str:
    """
    Generates a summary by sending the whole PDF directly to Claude (beta feature).
    Note: the entire PDF is loaded into memory as base64 — this is a constraint
    of the Anthropic PDF beta API and cannot be streamed.
    """
    if status_callback:
        status_callback(f"Lettura del file PDF: {pdf_path}")

    try:
        with open(pdf_path, "rb") as f:
            pdf_data = base64.b64encode(f.read()).decode("utf-8")
    except Exception as e:
        if status_callback:
            status_callback(f"Errore lettura PDF: {e}")
        return None

    if stop_event and stop_event.is_set():
        return None

    if status_callback:
        status_callback("Invio del PDF all'AI per il riassunto (potrebbe richiedere tempo)...")
    if progress_callback:
        progress_callback(10)

    system_prompt = (
        "Sei un esperto analista. Il tuo compito è fornire un riassunto completo e strutturato "
        "delle slide fornite. Concentrati sui concetti chiave, argomentazioni e conclusioni."
    )
    user_prompt = "Per favore, riassumi questa presentazione."

    try:
        message = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            system=system_prompt,
            extra_headers={"anthropic-beta": "pdfs-2024-09-25"},
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": pdf_data,
                            },
                        },
                        {"type": "text", "text": user_prompt},
                    ],
                }
            ],
        )
    except Exception as e:
        if status_callback:
            status_callback(f"Errore API: {e}")
        raise

    if stop_event and stop_event.is_set():
        return None

    summary_text = message.content[0].text
    pdf_title = os.path.splitext(os.path.basename(pdf_path))[0]
    output_dir = os.path.abspath(os.path.join("output", pdf_title))
    pathlib.Path(output_dir).mkdir(parents=True, exist_ok=True)

    output_path = os.path.join(output_dir, f"{pdf_title}_summary.md")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(f"# Summary: {pdf_title}\n\n")
        f.write(summary_text)

    if status_callback:
        status_callback(f"Riassunto salvato in: {output_path}")
    if progress_callback:
        progress_callback(100)

    return output_path


# --- GUI ---

import tkinter as tk
from tkinter import filedialog, ttk, messagebox
import threading


class PdfToLatexApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF to LaTeX Converter")
        self.root.geometry("600x400")
        self.root.resizable(False, False)

        self.pdf_path = tk.StringVar()
        self.status_message = tk.StringVar(value="Ready to convert...")

        self.anthropic_client = None
        self.convert_button = None
        self.stop_button = None
        self.stop_event = threading.Event()
        self.decision_event = threading.Event()
        self.simple_mode = None
        self.stop_config = {}
        self.running_thread = None

        self._create_widgets()
        self._setup_client()

        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    def _setup_client(self):
        try:
            self.anthropic_client = setup_environment()
            self.status_message.set("Anthropic client initialized. Ready to convert.")
        except RuntimeError as e:
            # setup_environment raises RuntimeError instead of calling exit()
            self.status_message.set(str(e))
            print(f"Error setting up Anthropic client: {e}")
            if self.convert_button:
                self.convert_button.config(state=tk.DISABLED)

    def _on_closing(self):
        print("Closing application...")
        if self.running_thread and self.running_thread.is_alive():
            self.stop_event.set()
            self.decision_event.set()
            self.status_message.set("Attempting to stop conversion gracefully...")
            self.running_thread.join(timeout=5)
            if self.running_thread.is_alive():
                print("Warning: Conversion thread did not terminate gracefully.")
        self.root.destroy()

    def _create_widgets(self):
        pdf_frame = ttk.LabelFrame(self.root, text="Select PDF File", padding="10")
        pdf_frame.pack(pady=10, padx=10, fill="x")

        ttk.Entry(
            pdf_frame, textvariable=self.pdf_path, width=50, state="readonly"
        ).pack(side="left", padx=(0, 10), fill="x", expand=True)
        ttk.Button(pdf_frame, text="Browse", command=self._browse_pdf_file).pack(
            side="right"
        )

        button_frame = ttk.Frame(self.root)
        button_frame.pack(pady=10)

        self.convert_button = ttk.Button(
            button_frame, text="Start Conversion", command=self._start_conversion_thread
        )
        self.convert_button.pack(side="left", padx=5)

        self.stop_button = ttk.Button(
            button_frame,
            text="Stop Conversion",
            command=self._stop_conversion,
            state=tk.DISABLED,
        )
        self.stop_button.pack(side="left", padx=5)

        self.simple_mode = tk.BooleanVar()
        ttk.Checkbutton(
            button_frame,
            text="Riassunto Semplice (PDF Diretto)",
            variable=self.simple_mode,
        ).pack(side="left", padx=5)

        ttk.Label(self.root, textvariable=self.status_message, wraplength=580).pack(
            pady=5, padx=10
        )

        self.progressbar = ttk.Progressbar(
            self.root, orient="horizontal", length=300, mode="determinate"
        )
        self.progressbar.pack(pady=10)

        self.open_output_button = ttk.Button(
            self.root,
            text="Open Output Folder",
            command=self._open_output_folder,
            state=tk.DISABLED,
        )
        self.open_output_button.pack(pady=10)

        self.output_dir = ""

    def _browse_pdf_file(self):
        filepath = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if filepath:
            self.pdf_path.set(filepath)
            self.status_message.set(f"Selected: {os.path.basename(filepath)}")

    def _start_conversion_thread(self):
        pdf_file = self.pdf_path.get()
        if not pdf_file:
            self.status_message.set("Please select a PDF file first.")
            return
        if not os.path.exists(pdf_file):
            self.status_message.set(f"Error: File not found at {pdf_file}")
            return

        self.status_message.set("Conversion started...")
        self.convert_button.config(state=tk.DISABLED)
        self.stop_button.config(state=tk.NORMAL)
        self.progressbar["value"] = 0

        self.stop_config = {}
        self.stop_event.clear()
        self.decision_event.clear()

        is_simple = self.simple_mode.get()
        self.running_thread = threading.Thread(
            target=self._run_async_conversion, args=(pdf_file, is_simple)
        )
        self.running_thread.daemon = True
        self.running_thread.start()

    def _stop_conversion(self):
        if self.running_thread and self.running_thread.is_alive():
            self.status_message.set("Stopping conversion...")
            self.stop_event.set()
            self.stop_button.config(state=tk.DISABLED)
            self.root.update()

            keep_partial = messagebox.askyesno(
                "Interruzione Conversione",
                "Vuoi mantenere il file parziale?\n\nSì: Salva il file (incompleto).\nNo: Elimina il file.",
            )
            self.stop_config["keep_partial"] = keep_partial
            self.decision_event.set()

    def _run_async_conversion(self, pdf_file, simple_mode=False):
        def status_cb(msg):
            self.root.after(0, self.status_message.set, msg)

        def progress_cb(value):
            self.root.after(0, lambda: self.progressbar.configure(value=value))

        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            if simple_mode:
                final_path = loop.run_until_complete(
                    process_pdf_summary(
                        self.anthropic_client, pdf_file,
                        status_callback=status_cb, progress_callback=progress_cb,
                        stop_event=self.stop_event,
                    )
                )
            else:
                final_path = loop.run_until_complete(
                    process_pdf(
                        self.anthropic_client, pdf_file,
                        status_callback=status_cb, progress_callback=progress_cb,
                        stop_event=self.stop_event, stop_config=self.stop_config,
                        decision_event=self.decision_event,
                    )
                )
            loop.close()

            if final_path:
                msg = "Conversion finished successfully!"
                if self.stop_event.is_set():
                    msg = "Conversion interrupted. Partial file saved."
                self.root.after(0, self._conversion_complete, msg, final_path)
            else:
                self.root.after(
                    0, self._conversion_complete, "Conversion cancelled by user.", None
                )
        except Exception as e:
            self.root.after(
                0, self._conversion_complete, f"Error during conversion: {e}", None
            )
            print(f"Error in conversion thread: {e}")
        finally:
            self.running_thread = None

    def _conversion_complete(self, message, final_latex_path=None):
        self.status_message.set(message)
        self.convert_button.config(state=tk.NORMAL)
        self.stop_button.config(state=tk.DISABLED)
        self.progressbar.stop()
        if final_latex_path:
            self.output_dir = os.path.dirname(final_latex_path)
            self.open_output_button.config(state=tk.NORMAL)
        else:
            self.open_output_button.config(state=tk.DISABLED)

    def _open_output_folder(self):
        if self.output_dir and os.path.exists(self.output_dir):
            try:
                if os.name == "nt":
                    os.startfile(self.output_dir)
                elif sys.platform == "darwin":
                    subprocess.Popen(["open", self.output_dir])
                else:
                    subprocess.Popen(["xdg-open", self.output_dir])
            except Exception as e:
                self.status_message.set(f"Error opening folder: {e}")
        else:
            self.status_message.set("Output directory not found.")


# --- Entry Point ---
if __name__ == "__main__":
    root = tk.Tk()
    app = PdfToLatexApp(root)
    root.mainloop()
