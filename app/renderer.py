"""
Video render pipeline — script → slides → Remotion → FFmpeg → queue.
"""
import json
import logging
import os
import shutil
import subprocess
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger("socialline")

# Encoder quality flags — equivalent visual quality across vendors
_ENCODE_FLAGS: dict[str, list[str]] = {
    "h264_nvenc": ["-c:v", "h264_nvenc", "-rc", "vbr", "-cq", "23", "-preset", "p4"],
    "h264_amf":   ["-c:v", "h264_amf",   "-quality", "balanced", "-rc", "vbr_peak"],
    "h264_qsv":   ["-c:v", "h264_qsv",   "-global_quality", "23", "-look_ahead", "1"],
    "libx264":    ["-c:v", "libx264",     "-crf", "23", "-preset", "fast"],
}

_PLATFORM_SPECS: dict[str, dict] = {
    "tiktok":          {"dims": "1080x1920", "copy": True},
    "instagram_reel":  {"dims": "1080x1920", "copy": True},
    "youtube_short":   {"dims": "1080x1920", "copy": True},
    "facebook":        {"dims": "1080x1080", "copy": False},
    "linkedin":        {"dims": "1920x1080", "copy": False},
    "instagram_post":  {"dims": "1080x1080", "copy": False},
    "twitter":         {"dims": "1280x720",  "copy": False},
}


def _probe_encoder(encoder: str) -> bool:
    """Test whether a hardware encoder is actually usable (not just compiled in)."""
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-f", "lavfi", "-i", "color=black:s=128x128:d=1",
                "-c:v", encoder, "-frames:v", "1", "-f", "null", "-",
            ],
            capture_output=True,
            timeout=15,
        )
        return result.returncode == 0
    except Exception:
        return False


def detect_encoder() -> str:
    """
    Probe GPU encoders in priority order; fall back to libx264.
    Returns the encoder name string.
    """
    for enc in ("h264_nvenc", "h264_amf", "h264_qsv"):
        if _probe_encoder(enc):
            logger.info(f"GPU encoder selected: {enc}")
            return enc
    logger.info("No GPU encoder available — using libx264")
    return "libx264"


class VideoRenderer:
    """
    Orchestrates the full video generation pipeline:
    visuals → slides → Remotion render → FFmpeg resize → captions → review queue.
    """

    def __init__(self, brand_id: str, brand_config: dict,
                 data_root: Path = None, encoder: str = None):
        self.brand_id     = brand_id
        self.brand_config = brand_config

        if data_root is None:
            data_root = Path(__file__).resolve().parent.parent / "data" / "brands"

        vp = brand_config.get("video_pipeline", {})
        self.remotion_project     = vp.get("remotion_project", "")
        self.remotion_composition = vp.get("remotion_composition", "")
        self.comfyui_host         = vp.get("comfyui_host", "http://localhost:8188")
        self.comfyui_enabled      = vp.get("comfyui_enabled", False)

        self.brand_data_dir = data_root / brand_id
        self.output_dir     = self.brand_data_dir / "renders"
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Merge SEO strategy into brand_config so captions.py can read it
        seo_path = self.brand_data_dir / "seo_strategy.json"
        if seo_path.exists():
            try:
                self.brand_config = {**brand_config, "seo_strategy": json.loads(seo_path.read_text())}
            except Exception:
                pass

        from render_store import RenderStore
        self.render_store = RenderStore(brand_id, data_root)

        # Detect GPU encoder once at init; caller may pass a pre-detected value
        self.encoder      = encoder or detect_encoder()
        self.encode_flags = _ENCODE_FLAGS[self.encoder]

    # ── Main orchestration ────────────────────────────────────────────────────

    def render_job(self, job: dict):
        """
        Entry point called by the background renderer thread.
        Never raises — all errors are caught and logged to renders.db.
        """
        job_id = job["id"]
        try:
            meta          = job["meta"] if isinstance(job["meta"], dict) else json.loads(job["meta"] or "{}")
            job_output_dir = self.output_dir / job_id
            job_output_dir.mkdir(parents=True, exist_ok=True)

            self.render_store.update_status(job_id, "rendering")

            visuals    = self._prepare_visuals(job, meta)
            slides     = self._generate_slides(job["script_text"], meta, visuals)
            master     = self._render_remotion(job_id, slides, visuals, meta, job_output_dir)
            outputs    = self._resize_formats(master, meta.get("platforms", []), job_output_dir)
            captions   = self._generate_captions(job["script_text"], meta)

            for out in outputs:
                cap = captions.get(out["platform"], {})
                self.render_store.add_output(
                    job_id=job_id,
                    platform=out["platform"],
                    file_path=str(out["file_path"]),
                    dimensions=out["dimensions"],
                    engine=out["engine"],
                    caption=cap.get("caption", ""),
                    hashtags=json.dumps(cap.get("hashtags", [])),
                )
                self.render_store.save_caption(
                    job_id=job_id,
                    platform=out["platform"],
                    caption=cap.get("caption", ""),
                    hashtags=cap.get("hashtags", []),
                )

            review_required = meta.get("review_required", True)
            brand_review    = self.brand_config.get("video_pipeline", {}).get("review_required", True)

            if review_required and brand_review:
                self.render_store.update_status(job_id, "pending_review")
            else:
                self.render_store.approve_job(job_id, reviewed_by="auto")
                self._auto_schedule(job_id, outputs, captions, meta)

        except Exception as exc:
            logger.error(f"Render failed for job {job_id}: {exc}", exc_info=True)
            self.render_store.update_status(job_id, "failed", error=str(exc))

    # ── Visuals ───────────────────────────────────────────────────────────────

    def _prepare_visuals(self, job: dict, meta: dict) -> dict:
        visual_config = meta.get("visual", {})
        visual_type   = visual_config.get("type", "stars_bg")

        if visual_type == "stars_bg":
            bg_file = visual_config.get("background_file", "stars_bg.mp4")
            full_path = Path(self.remotion_project) / "public" / bg_file
            # Only pass path if the file actually exists — Background renders gradient fallback when None
            bg_path = str(full_path) if full_path.exists() else None
            return {"type": "stars_bg", "background": bg_path, "generated_images": []}

        if visual_type == "ai_generated" and self.comfyui_enabled:
            prompt = visual_config.get("comfyui_prompt", "")
            images = self._comfyui_generate(prompt, job["id"])
            return {"type": "ai_generated", "background": None, "generated_images": images}

        if visual_type == "stock":
            query  = visual_config.get("stock_query", meta.get("topic", ""))
            images = self._fetch_stock_images(query)
            return {"type": "stock", "background": None, "generated_images": images}

        # Fallback — gradient only
        return {"type": "stars_bg", "background": None, "generated_images": []}

    # ── Slides (Claude API) ───────────────────────────────────────────────────

    def _generate_slides(self, script_text: str, meta: dict, visuals: dict) -> list:
        duration = meta.get("duration_seconds", 30)
        tone     = meta.get("tone", "warm_bold")
        template = meta.get("template", "stat_overlay")

        template_hints = {
            "stat_overlay": "Lead with a quote hook, follow with a stat slide, then teaching, end with action CTA.",
            "tutorial":     "Start with a teaching slide, use 2-3 more teaching slides, end with action CTA.",
            "story":        "Start with a quote hook, use teaching slides for the narrative, end with action CTA.",
            "reframe":      "Start with a bold quote, use teaching slides to flip the assumption, end with quote + action.",
            "tip":          "Start with a hook quote, one teaching slide with 3 bullets, end with action CTA.",
        }
        hint = template_hints.get(template, template_hints["stat_overlay"])

        brand_name = self.brand_config.get("name", "")

        system_prompt = f"""You are a video slide designer for {brand_name}.

Your job: take a short video script and break it into 3-6 slides.
Each slide is a Remotion React component. Return ONLY valid JSON.

SLIDE TYPES AVAILABLE:
- "quote": single bold statement, emotional hook
- "stat": a specific number or research finding
- "teaching": headline + 2-3 bullet points explaining a concept
- "action": a direct call-to-action step
- "title": opening card only (rarely needed for 30-sec shorts)

STRICT RULES:
1. Return ONLY a JSON array — no preamble, no explanation, no markdown fences
2. Total duration of all slides must equal the target duration exactly
3. Every slide must have: id, type, duration_seconds, motion, and type-specific fields
4. Headlines max 6 words. Bullets max 10 words each. Quotes max 20 words.
5. Always end with an "action" slide as the CTA
6. For a 30-second video: use 4-6 slides
7. Motion options: zoom_in_slow, zoom_out_slow, ken_burns_left_right, slide_up
8. stat slides must have stat_number, stat_caption (source optional)
9. teaching slides must have headline, bullet_1, bullet_2 (bullet_3 optional)
10. quote slides must have quote_text (attribution optional)
11. action slides must have label (ALL CAPS, max 4 words) + instruction + optional sub_note"""

        example_slide = (
            '[{"id":"slide_001","type":"quote","duration_seconds":5,"motion":"zoom_in_slow",'
            '"quote_text":"Your hook here","attribution":null},'
            '{"id":"slide_002","type":"teaching","duration_seconds":9,"motion":"zoom_out_slow",'
            '"headline":"Why It Works","bullet_1":"First reason","bullet_2":"Second reason","bullet_3":null},'
            '{"id":"slide_003","type":"action","duration_seconds":6,"motion":"slide_up",'
            '"label":"FOLLOW FOR MORE","instruction":"Follow for one tip a day","sub_note":null}]'
        )

        user_prompt = f"""Break this script into slides for a {duration}-second video.

SCRIPT:
{script_text}

TARGET DURATION: {duration} seconds total
TEMPLATE PATTERN: {hint}
TONE: {tone}

Rules:
- 4-6 slides for a 30-second video
- Slide durations must add up to exactly {duration} seconds
- End with an action slide
- Return ONLY the JSON array, nothing else

Example format:
{example_slide}

Return the JSON array now:"""

        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        body = json.dumps({
            "model":      "claude-sonnet-4-6",
            "max_tokens": 2000,
            "system":     system_prompt,
            "messages":   [{"role": "user", "content": user_prompt}],
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
                raw = result["content"][0]["text"].strip()
                if raw.startswith("```"):
                    raw = raw.split("```")[1]
                    if raw.startswith("json"):
                        raw = raw[4:]
                slides = json.loads(raw.strip())

            # Normalize teaching slides: bullets[] → bullet_1/2/3
            for s in slides:
                if s.get("type") == "teaching" and "bullets" in s:
                    blist = s.pop("bullets")
                    s["bullet_1"] = blist[0] if len(blist) > 0 else ""
                    s["bullet_2"] = blist[1] if len(blist) > 1 else ""
                    s["bullet_3"] = blist[2] if len(blist) > 2 else None

            # Ensure total duration matches
            total = sum(s.get("duration_seconds", 0) for s in slides)
            if total != duration:
                slides[-1]["duration_seconds"] += duration - total

            return slides

        except Exception as exc:
            logger.error(f"_generate_slides failed: {exc}")
            return [
                {
                    "id": "slide_001", "type": "quote",
                    "duration_seconds": duration - 6, "motion": "zoom_in_slow",
                    "quote_text": script_text[:100].strip(), "attribution": None,
                },
                {
                    "id": "slide_002", "type": "action",
                    "duration_seconds": 6, "motion": "slide_up",
                    "label": "FOLLOW FOR MORE",
                    "instruction": f"Follow {self.brand_config.get('name', '')} for daily tips",
                    "sub_note": self.brand_config.get("website", ""),
                },
            ]

    # ── Remotion render ───────────────────────────────────────────────────────

    def _render_remotion(self, job_id: str, slides: list, visuals: dict,
                         meta: dict, output_dir: Path) -> Path:
        props = {
            "slides":          slides,
            "background":      visuals["background"],
            "backgroundType":  visuals["type"],
            "generatedImages": visuals.get("generated_images", []),
            "brand": {
                "name":    self.brand_config.get("name", ""),
                "handle":  self.brand_config.get("handle", ""),
                "website": self.brand_config.get("website", ""),
                "colors": {
                    "primary": "#FFFFFF",
                    "accent":  "#FFB800",
                    "dark":    "#0F1E45",
                },
            },
            "duration": meta.get("duration_seconds", 30),
        }

        props_path  = output_dir / "props.json"
        master_path = output_dir / "master.mp4"
        props_path.write_text(json.dumps(props, indent=2))

        import sys
        # On Windows, npx is a .cmd shim — must use shell=True or cmd /c
        if sys.platform == "win32":
            cmd = (
                f'npx remotion render {self.remotion_composition}'
                f' "{master_path}"'
                f' "--props={props_path}"'
                f' --timeout=60000'
                f' --log=verbose'
            )
            result = subprocess.run(
                cmd,
                cwd=self.remotion_project,
                capture_output=True,
                text=True,
                timeout=900,
                shell=True,
            )
        else:
            result = subprocess.run(
                [
                    "npx", "remotion", "render",
                    self.remotion_composition,
                    str(master_path),
                    f"--props={props_path}",
                    "--timeout=60000",
                    "--log=verbose",
                ],
                cwd=self.remotion_project,
                capture_output=True,
                text=True,
                timeout=900,
            )

        if result.returncode != 0:
            raise RuntimeError(f"Remotion render failed: {result.stderr[-2000:]}")

        return master_path

    # ── FFmpeg resize (GPU-accelerated) ───────────────────────────────────────

    def _resize_formats(self, master_path: Path, platforms: list,
                        output_dir: Path) -> list[dict]:
        outputs = []

        for platform in platforms:
            spec = _PLATFORM_SPECS.get(platform)
            if not spec:
                logger.warning(f"Unknown platform spec: {platform}")
                continue

            out_path = output_dir / f"{platform}.mp4"
            dims     = spec["dims"]

            if spec["copy"]:
                # Same dimensions as master — no re-encode needed
                shutil.copy2(master_path, out_path)
                engine = "copy"
            else:
                w, h = dims.split("x")
                ok = self._ffmpeg_resize(master_path, out_path, w, h)
                if not ok:
                    continue
                engine = self.encoder

            outputs.append({
                "platform":   platform,
                "file_path":  out_path,
                "dimensions": dims,
                "engine":     engine,
            })

        return outputs

    def _ffmpeg_resize(self, src: Path, dst: Path, w: str, h: str) -> bool:
        """
        Scale + letterbox/pillarbox src to WxH and write to dst.
        Uses GPU encoder when available; CPU decode always (filter compat).
        """
        vf = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"format=yuv420p"
        )

        cmd = [
            "ffmpeg", "-i", str(src),
            "-vf", vf,
            *self.encode_flags,
            "-c:a", "copy",
            "-y", str(dst),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            logger.error(f"FFmpeg failed ({self.encoder}) for {dst.name}: {result.stderr[-1000:]}")

            # If GPU encoder failed mid-job, retry with libx264
            if self.encoder != "libx264":
                logger.info(f"Retrying {dst.name} with libx264")
                fallback_cmd = [
                    "ffmpeg", "-i", str(src),
                    "-vf", vf,
                    *_ENCODE_FLAGS["libx264"],
                    "-c:a", "copy",
                    "-y", str(dst),
                ]
                retry = subprocess.run(fallback_cmd, capture_output=True, text=True, timeout=120)
                if retry.returncode == 0:
                    return True
                logger.error(f"libx264 fallback also failed: {retry.stderr[-500:]}")

            return False

        return True

    # ── Caption generation ────────────────────────────────────────────────────

    def _generate_captions(self, script_text: str, meta: dict) -> dict:
        from captions import generate_caption

        captions = {}
        for platform in meta.get("platforms", []):
            captions[platform] = generate_caption(
                script=script_text,
                platform=platform,
                brand_config=self.brand_config,
                tone=meta.get("tone", "warm_bold"),
                pillar=meta.get("pillar", ""),
                topic=meta.get("topic", ""),
            )
        return captions

    # ── Auto-schedule (Step 8) ────────────────────────────────────────────────

    def _auto_schedule(self, job_id: str, outputs: list, captions: dict,
                       meta: dict) -> list:
        """
        Copy approved render outputs to queue/ and create schedule.db entries.
        Groups outputs by aspect ratio so same-dimension platforms share one entry.
        Returns list of scheduled post dicts.
        """
        from scheduler import ScheduleStore, _load_sched_file
        from datetime import datetime, timedelta

        queue_dir = self.brand_data_dir / "queue"
        queue_dir.mkdir(parents=True, exist_ok=True)

        sched_store = ScheduleStore(self.brand_data_dir / "schedule.db")
        sched_cfg   = _load_sched_file(self.brand_data_dir / "default_schedule.json")

        # Preferred schedule — meta overrides default_schedule.json
        sched_meta = meta.get("schedule", {})
        preferred_time = (
            sched_meta.get("preferred_time")
            or (sched_cfg.get("times") or ["09:00"])[0]
        )

        _DAY_MAP = {
            "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6,
        }
        raw_days = sched_meta.get("preferred_days", [])
        preferred_weekdays = (
            [_DAY_MAP[d.lower()] for d in raw_days if d.lower() in _DAY_MAP]
            if raw_days else sched_cfg.get("days_of_week", list(range(7)))
        )
        days_ahead = int(sched_cfg.get("days_ahead", 7))

        h, m = map(int, preferred_time.split(":"))
        now = datetime.now()
        occupied = {p["scheduled_at"][:16] for p in sched_store.list_all(status="pending")}

        def _next_slot() -> datetime:
            for delta in range(1, days_ahead + 2):
                candidate = now + timedelta(days=delta)
                if candidate.weekday() in preferred_weekdays:
                    slot = candidate.replace(hour=h, minute=m, second=0, microsecond=0)
                    key  = slot.strftime("%Y-%m-%dT%H:%M")
                    if key not in occupied:
                        occupied.add(key)
                        return slot
            # Fallback: tomorrow at preferred time regardless of weekday
            return (now + timedelta(days=1)).replace(hour=h, minute=m, second=0, microsecond=0)

        # Only schedule to platforms that have connected accounts
        from pipeline.core.accounts import AccountStore
        acct_file = self.brand_data_dir / "accounts.enc"
        acct_store = AccountStore(acct_file, brand_id=self.brand_id)
        configured_platforms = {
            a.platform for a in acct_store.list_all()
        }

        # Group outputs by orientation — one queue file + schedule entry per aspect ratio
        _ORIENT = {"1080x1920": "vertical", "1080x1080": "square"}
        groups: dict[str, dict] = {}
        for out in outputs:
            if out["platform"] not in configured_platforms:
                logger.info(f"Skipping {out['platform']} — no connected account")
                continue
            key = _ORIENT.get(out["dimensions"], "horizontal")
            if key not in groups:
                groups[key] = {
                    "file_path": out["file_path"],
                    "dimensions": out["dimensions"],
                    "platforms": [],
                }
            groups[key]["platforms"].append(out["platform"])

        # Copy all format files to queue and build format_map
        format_map = {}
        all_platforms = []
        all_captions = {}
        primary_filename = None

        for orientation, group in groups.items():
            src = Path(group["file_path"])
            if not src.exists():
                logger.warning(f"Render output missing: {src}")
                continue
            queue_filename = f"{job_id}_{orientation}.mp4"
            dst = queue_dir / queue_filename
            shutil.copy2(src, dst)
            format_map[orientation] = queue_filename
            all_platforms.extend(group["platforms"])
            for p in group["platforms"]:
                cap = captions.get(p, {})
                all_captions[p] = cap.get("caption", "") if isinstance(cap, dict) else ""
            if primary_filename is None or orientation == "vertical":
                primary_filename = queue_filename

        if not format_map:
            return []

        # Caption sidecar for primary platform
        primary_caption = next(iter(all_captions.values()), "")
        if primary_caption and primary_filename:
            (queue_dir / f"{Path(primary_filename).stem}.caption.txt").write_text(
                primary_caption, encoding="utf-8"
            )

        slot    = _next_slot()
        post_id = sched_store.add(
            filename=primary_filename,
            captions=all_captions,
            platforms=all_platforms,
            platform_options={},
            scheduled_at=slot.isoformat(timespec="minutes"),
            format_map=format_map,
        )
        scheduled = [{
            "post_id":    post_id,
            "filename":   primary_filename,
            "platforms":  all_platforms,
            "format_map": format_map,
            "scheduled_at": slot.isoformat(timespec="minutes"),
        }]
        logger.info(
            f"Render {job_id} auto-scheduled: {primary_filename} → "
            f"{slot.strftime('%Y-%m-%d %H:%M')} ({', '.join(all_platforms)})"
        )

        if scheduled:
            self.render_store.update_status(job_id, "scheduled")

        return scheduled

    # ── ComfyUI ───────────────────────────────────────────────────────────────

    def _comfyui_generate(self, prompt: str, job_id: str) -> list[str]:
        import websocket  # websocket-client package

        client_id = str(uuid.uuid4())
        workflow  = self._build_comfyui_workflow(prompt)

        payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.comfyui_host}/prompt",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            prompt_id = json.loads(resp.read())["prompt_id"]

        ws_url = self.comfyui_host.replace("http://", "ws://") + "/ws"
        ws = websocket.WebSocket()
        ws.connect(f"{ws_url}?clientId={client_id}")
        try:
            while True:
                data = json.loads(ws.recv())
                if data.get("type") == "executing" and data["data"].get("node") is None:
                    break
                if data.get("type") == "execution_error":
                    raise RuntimeError(f"ComfyUI error: {data['data']}")
        finally:
            ws.close()

        history_req = urllib.request.Request(f"{self.comfyui_host}/history/{prompt_id}")
        with urllib.request.urlopen(history_req, timeout=30) as resp:
            history = json.loads(resp.read())

        out_dir = self.output_dir / job_id / "comfyui"
        out_dir.mkdir(parents=True, exist_ok=True)

        images = []
        for node_output in history[prompt_id]["outputs"].values():
            for image in node_output.get("images", []):
                import urllib.parse
                params = urllib.parse.urlencode({
                    "filename": image["filename"],
                    "subfolder": image.get("subfolder", ""),
                    "type": image.get("type", "output"),
                })
                img_req = urllib.request.Request(f"{self.comfyui_host}/view?{params}")
                with urllib.request.urlopen(img_req, timeout=30) as img_resp:
                    local = out_dir / image["filename"]
                    local.write_bytes(img_resp.read())
                    images.append(str(local))

        return images

    def _fetch_stock_images(self, query: str) -> list[str]:
        """Unsplash API — implemented in Step 6 alongside ComfyUI."""
        return []

    def _build_comfyui_workflow(self, prompt: str) -> dict:
        workflow_path = (
            Path(__file__).resolve().parent.parent
            / "data" / "brands" / self.brand_id / "comfyui_workflow.json"
        )
        if workflow_path.exists():
            workflow = json.loads(workflow_path.read_text())
            for node in workflow.values():
                if node.get("class_type") == "CLIPTextEncode":
                    node.get("inputs", {})["text"] = prompt
            return workflow
        return self._default_sdxl_workflow(prompt)

    def _default_sdxl_workflow(self, prompt: str) -> dict:
        """Minimal SDXL workflow — replace with a saved workflow for production."""
        return {
            "1": {
                "class_type": "CLIPTextEncode",
                "inputs": {"text": prompt, "clip": ["2", 0]},
            }
        }
