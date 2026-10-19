"""Local review page: video + transcript with every cut visible and toggleable,
subtitle text editing, and export."""

import json
import socket
import subprocess
import threading
import webbrowser
from dataclasses import asdict
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response

from mcut import burn, glossary
from mcut.cli import replace_all, write_lines
from mcut.edit import Cut, phrases
from mcut.plan import Envelope
from mcut.project import Project
from mcut.render import make_plan, render
from mcut.textfix import PRIORITY, diff_fixes

INDEX = Path(__file__).with_name("index.html")


def build_app(proj: Project) -> FastAPI:
    raw = proj.raw_tokens()
    lines = phrases(raw)
    env = Envelope(str(proj.audio))
    export = {"state": "idle", "progress": 0.0, "output": None, "error": None}
    app = FastAPI()

    def plan_json(st) -> dict:
        plan = make_plan(proj, st, env)
        deleted = st.deleted()
        user_fixed = {k for f in st.fixes if f.source == "user" for k in range(f.start, f.end + 1)}

        def heard(c) -> str:  # what the recogniser wrote, before any correction
            return "".join(raw[k].t for k in range(c.key, c.last + 1) if k not in deleted)

        return {
            "ranges": plan.ranges,
            "duration": plan.timeline.duration,
            "cues": [{**asdict(c), "heard": heard(c),
                      "edited": c.key in st.subs or any(c.key <= k <= c.last for k in user_fixed)} for c in plan.cues],
        }

    def tokens_json(st) -> list:
        """[text, start, end, punct, heard_as, fixed_by] -- the last two only where the
        text was corrected."""
        shown = proj.display_tokens(st)
        fixed_by = {}
        for f in sorted(st.fixes, key=lambda f: -PRIORITY.get(f.source, 0)):  # most authoritative first
            for k in range(f.start, f.end + 1):
                fixed_by.setdefault(k, f.source)
        return [[d.t, d.s, d.e, d.p] + ([r.t, fixed_by.get(r.i, "")] if d.t != r.t else []) for d, r in zip(shown, raw)]

    def text_changed(st) -> dict:
        proj.save_state(st)
        write_lines(proj, st)
        return {"tokens": tokens_json(st), "plan": plan_json(st), "subs": st.subs}

    @app.get("/")
    def index() -> HTMLResponse:
        return HTMLResponse(INDEX.read_text())

    @app.get("/api/whoami")
    def whoami() -> dict:
        return {"project": str(proj.dir)}

    @app.get("/media")
    def media() -> FileResponse:
        return FileResponse(proj.proxy if proj.proxy.exists() else proj.video)

    @app.get("/api/state")
    def get_state() -> dict:
        st = proj.load_state()
        return {
            "name": proj.name,
            "video": str(proj.video),
            "tokens": tokens_json(st),
            "lines": lines,
            "cuts": [asdict(c) for c in st.cuts],
            "subs": st.subs,
            "plan": plan_json(st),
            "export": export,
            "style": asdict(st.style),
            "style_builtin": asdict(burn.Style()),
            "hide_endings": bool(st.settings.sub_hide_endings),
            "fonts": burn.list_fonts(),
            "clips": proj.clips if len(proj.clips) > 1 else [],
        }

    @app.get("/api/subtitle.png")
    def subtitle_png(text: str, w: int, style: str) -> Response:
        """The subtitle exactly as Export will burn it, at the preview's size."""
        fw, fh = burn.frame_size(str(proj.video))
        w = max(64, min(w, fw))
        img = burn.render_subtitle(text, (w, round(w * fh / fw)), burn.Style.from_dict(json.loads(style)))
        return Response(burn.png_bytes(img), media_type="image/png", headers={"Cache-Control": "max-age=3600"})

    @app.get("/api/font_sample.png")
    def font_sample(font: str, label: str) -> Response:
        return Response(burn.font_sample(font, label), media_type="image/png", headers={"Cache-Control": "max-age=86400"})

    @app.post("/api/style")
    def set_style(body: dict = Body(...)) -> dict:
        st = proj.load_state()
        st.style = burn.Style.from_dict(body.get("style"))
        st.settings.sub_hide_endings = "啊" if body.get("hide_endings", True) else ""
        proj.save_state(st)
        if body.get("save_default"):
            st.style.save_default()
        return {"plan": plan_json(st)}

    @app.post("/api/state")
    def save_state(body: dict = Body(...)) -> dict:
        st = proj.load_state()
        st.cuts = sorted((Cut(**c) for c in body["cuts"]), key=lambda c: c.start)
        st.subs = {int(k): v for k, v in body.get("subs", {}).items()}
        proj.save_state(st)
        return plan_json(st)

    @app.post("/api/fix_line")
    def fix_line(body: dict = Body(...)) -> dict:
        st = proj.load_state()
        a, b = lines[int(body["line"])]
        st.add_fixes(diff_fixes(proj.display_tokens(st)[a : b + 1], body["text"], "user"))
        return text_changed(st)

    @app.post("/api/fix_cue")
    def fix_cue(body: dict = Body(...)) -> dict:
        """Edit a subtitle in place: stored as corrections of the words under it, so the
        edit survives later cut changes."""
        st = proj.load_state()
        key = int(body["key"])
        cue = next((c for c in make_plan(proj, st, env).cues if c.key == key), None)
        if cue:
            deleted = st.deleted()
            toks = [t for t in proj.display_tokens(st)[cue.key : cue.last + 1] if t.i not in deleted]
            st.subs.pop(key, None)  # a subtitle-only override would hide the correction
            st.add_fixes(diff_fixes(toks, body["text"], "user"))
        return text_changed(st)

    @app.post("/api/replace")
    def replace(body: dict = Body(...)) -> dict:
        st = proj.load_state()
        n = replace_all(proj, st, body["old"], body["new"], "user")
        if body.get("glossary") and body["new"]:
            glossary.save(glossary.load() + [body["new"]])
        return {**text_changed(st), "count": n}

    @app.post("/api/export")
    def start_export(body: dict = Body(...)) -> dict:
        if export["state"] == "running":
            return export
        preview = bool(body.get("preview"))
        out = proj.output.with_stem(proj.output.stem + ("_preview" if preview else ""))
        plan = make_plan(proj, proj.load_state(), env)
        export.update(state="running", progress=0.0, output=str(out), error=None)

        def work() -> None:
            try:
                render(proj, plan, out, preview=preview, burn_subs=bool(body.get("burn", True)),
                       progress=lambda f: export.update(progress=f))
                export.update(state="done", progress=1.0)
            except Exception as e:  # surfaced in the page
                export.update(state="error", error=str(e))

        threading.Thread(target=work, daemon=True).start()
        return export

    @app.get("/api/export")
    def export_status() -> dict:
        return export

    @app.post("/api/reveal")
    def reveal() -> dict:
        if export["output"]:
            subprocess.run(["open", "-R", export["output"]])
        return {}

    return app


def _find_port(proj: Project, port: int) -> tuple[int, bool]:
    """(port, already_running): the page already serving this project, else the first
    free port from `port` on."""
    import urllib.request

    for p in range(port, port + 50):
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", p)):
                return p, False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{p}/api/whoami", timeout=0.5) as r:
                if json.load(r).get("project") == str(proj.dir):
                    return p, True
        except (OSError, ValueError):
            pass  # some other service
    raise RuntimeError("no free port")


def serve(proj: Project, port: int = 8765, open_browser: bool = True) -> None:
    port, running = _find_port(proj, port)
    url = f"http://127.0.0.1:{port}"
    if running:
        print(f"review page already running: {url}", flush=True)
        if open_browser:
            webbrowser.open(url)
        return
    app = build_app(proj)
    print(f"review page: {url}   (Ctrl-C to stop)", flush=True)
    if open_browser:
        threading.Timer(1.0, webbrowser.open, [url]).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
