# CLAUDE.md — Socialline Video Pipeline Spec
# Read this at the start of every session before making any changes.

---

## PROJECT OVERVIEW

Socialline is a multi-brand social media automation platform built in Python.
It pulls content from various sources, processes it, schedules posts, and
publishes to multiple social media platforms via their APIs.

The core loop is:
```
Source pull → Queue → Schedule → Publish
```

Existing architecture is documented in the codebase. This file covers the
NEW video pipeline feature being added on top of it.

---

## THE GOAL

Enable a user to drop a text script into a folder and have Socialline
automatically:

1. Detect the script
2. Generate visuals (background, AI images, stock photos)
3. Render a video via Remotion (complex) or FFmpeg (simple)
4. Resize the video to all required platform formats
5. Generate platform-specific captions via Claude API
6. Queue the rendered videos for user review
7. After user approval — auto-schedule and post

**Minimum user interaction:** Drop script → Review rendered video → Approve.
Everything else is automated.

**The user oversees but does not operate the pipeline.**

---

## EXISTING ARCHITECTURE — DO NOT BREAK

```
app/sources.py      — SourceStore (SQLite) + PullEngine
app/scheduler.py    — ScheduleStore (SQLite) + auto-scheduler
server.py           — Flask API + background threads
data/brands/<bid>/
    sources.db      — source configurations
    content.db      — queue items + post history
    schedule.db     — scheduled posts
    queue/          — files ready to post
    posted/         — archived after posting
```

Existing source types: reddit, google, url, article, folder, website.
Existing background threads: start_scheduler() (30s), start_source_puller() (60s).
Existing sidecar pattern: .article.json, .caption.txt travel alongside media.

**Do not modify existing source types or scheduler logic unless explicitly required.**
**Add new functionality alongside existing — do not replace.**

---

## NEW: VIDEO PIPELINE

### Data Flow

```
data/brands/<bid>/scripts/     ← user drops scripts here
         ↓
_pull_script() detects .txt/.md/.json + .meta.json sidecar
         ↓
RenderJob created in renders.db (status: pending_render)
         ↓
start_renderer() thread picks up pending jobs
         ↓
VideoRenderer.render_job():
    → _prepare_visuals()    — stars_bg / ComfyUI / stock
    → _generate_slides()    — Claude API breaks script into slides
    → _render_remotion()    — npx remotion render → master 1080x1920 MP4
    → _resize_formats()     — FFmpeg → platform-specific formats
    → _generate_captions()  — Claude API → platform-specific captions
         ↓
RenderJob status → pending_review
         ↓
User reviews in Socialline UI
         ↓
User approves → auto-schedule → post via existing pipeline
User rejects → status: rejected, reason logged
```

---

## NEW FILES TO CREATE

```
app/renderer.py           — VideoRenderer class
app/render_store.py       — RenderStore class (renders.db)
app/captions.py           — caption generation via Claude API
data/brands/<bid>/
    renders.db            — render jobs + outputs
    scripts/              — user drops scripts here
    renders/              — rendered video files
        <job_id>/
            master.mp4
            tiktok.mp4
            instagram.mp4
            youtube_short.mp4
            facebook.mp4
            linkedin.mp4
```

---

## NEW: renders.db SCHEMA

```sql
-- Render jobs — one per script file
CREATE TABLE IF NOT EXISTS render_jobs (
    id                TEXT PRIMARY KEY,
    brand_id          TEXT NOT NULL,
    script_file       TEXT,              -- original script filename
    script_text       TEXT NOT NULL,     -- full script content
    meta              TEXT,              -- JSON: pillar, topic, template, etc
    status            TEXT DEFAULT 'pending_render',
    -- Status flow:
    -- pending_render → rendering → pending_review → 
    -- approved → scheduling → scheduled → complete
    -- failed | rejected
    review_required   INTEGER DEFAULT 1,
    reviewed_by       TEXT,
    reviewed_at       TEXT,
    reject_reason     TEXT,
    error             TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT
);

-- Individual platform render outputs
CREATE TABLE IF NOT EXISTS render_outputs (
    id              TEXT PRIMARY KEY,
    job_id          TEXT NOT NULL REFERENCES render_jobs(id),
    platform        TEXT NOT NULL,      -- tiktok, instagram_reel, etc
    dimensions      TEXT NOT NULL,      -- 1080x1920
    orientation     TEXT,               -- vertical, square, horizontal
    engine          TEXT NOT NULL,      -- remotion, ffmpeg
    file_path       TEXT,
    caption         TEXT,               -- platform-specific caption
    hashtags        TEXT,               -- JSON array
    status          TEXT DEFAULT 'pending',
    -- pending → rendering → complete → failed
    error           TEXT,
    rendered_at     TEXT
);

-- Captions generated per platform
CREATE TABLE IF NOT EXISTS render_captions (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL REFERENCES render_jobs(id),
    platform    TEXT NOT NULL,
    caption     TEXT NOT NULL,
    hashtags    TEXT,                   -- JSON array
    generated_by TEXT DEFAULT 'claude',
    created_at  TEXT NOT NULL
);
```

---

## NEW: SCRIPT FILE FORMAT

### Script file (.txt, .md, or .json)

Plain text script — exactly what will appear as text overlays in the video.

**Example: cognify_001.txt**
```
Kids with a personal tutor outperformed 
98% of classroom students.

At $50 per hour most families simply 
can't afford it consistently.

AI gives every child unlimited, infinitely 
patient, one-on-one tutoring. For free. 
Available tonight.

Follow Cognify Learn for one tip a day. 🧠
```

### Metadata sidecar (.meta.json) — same filename, different extension

**Example: cognify_001.meta.json**
```json
{
  "brand": "cognify_learn",
  "pillar": "AI + Learning",
  "topic": "98% tutoring stat",
  "template": "stat_overlay",
  "tone": "warm_bold",
  "duration_seconds": 30,
  "platforms": [
    "tiktok",
    "instagram_reel",
    "youtube_short",
    "facebook",
    "linkedin"
  ],
  "visual": {
    "type": "stars_bg",
    "background_file": "stars_bg.mp4",
    "comfyui_prompt": null,
    "stock_query": null
  },
  "caption_style": "hook_problem_solution_cta",
  "review_required": true,
  "priority": 1,
  "schedule": {
    "preferred_days": ["tuesday", "thursday"],
    "preferred_time": "09:00",
    "timezone": "America/Chicago"
  }
}
```

### If no .meta.json exists — AI infers metadata

When a .txt/.md file is dropped without a sidecar, the system should:
1. Use Claude API to infer: pillar, topic, template, tone, duration
2. Default to brand's default_schedule.json for scheduling
3. Default visual type to stars_bg
4. Set review_required: true always
5. Create the .meta.json automatically for future reference

---

## NEW: app/render_store.py

```python
class RenderStore:
    """
    Manages renders.db — render jobs and outputs.
    Same pattern as existing SourceStore and ScheduleStore.
    """
    
    def __init__(self, brand_id):
        self.brand_id = brand_id
        self.db_path = f"data/brands/{brand_id}/renders.db"
        self._init_db()
    
    def _init_db(self):
        # Create tables if not exist
        # Run the schema above
        pass
    
    def create_job(self, script_text, script_file, meta):
        # Insert new render_job
        # Return job_id
        pass
    
    def get_next_pending(self):
        # SELECT * FROM render_jobs 
        # WHERE status = 'pending_render'
        # AND brand_id = ?
        # ORDER BY priority ASC, created_at ASC
        # LIMIT 1
        pass
    
    def update_status(self, job_id, status, error=None):
        pass
    
    def get_pending_review(self):
        # Return all jobs with status = 'pending_review'
        pass
    
    def approve_job(self, job_id, reviewed_by='user'):
        pass
    
    def reject_job(self, job_id, reason, reviewed_by='user'):
        pass
    
    def add_output(self, job_id, platform, file_path, 
                   dimensions, engine, caption, hashtags):
        pass
    
    def get_outputs(self, job_id):
        pass
```

---

## NEW: app/renderer.py

```python
class VideoRenderer:
    """
    Orchestrates the full video generation pipeline:
    visuals → slides → render → resize → captions
    """
    
    def __init__(self, brand_id, brand_config):
        self.brand_id = brand_id
        self.brand_config = brand_config
        self.render_store = RenderStore(brand_id)
        
        # From brand config video_pipeline section
        self.remotion_project = brand_config['video_pipeline']['remotion_project']
        self.remotion_composition = brand_config['video_pipeline']['remotion_composition']
        self.comfyui_host = brand_config['video_pipeline'].get('comfyui_host', 'http://localhost:8188')
        self.comfyui_enabled = brand_config['video_pipeline'].get('comfyui_enabled', False)
        
        # Output directory
        self.output_dir = f"data/brands/{brand_id}/renders"
        os.makedirs(self.output_dir, exist_ok=True)
    
    def render_job(self, job):
        """
        Main entry point. Orchestrates full pipeline.
        Updates job status at each step.
        Never raises — catches all errors and marks job failed.
        """
        try:
            job_id = job['id']
            meta = json.loads(job['meta'])
            job_output_dir = f"{self.output_dir}/{job_id}"
            os.makedirs(job_output_dir, exist_ok=True)
            
            self.render_store.update_status(job_id, 'rendering')
            
            # Step 1 — Prepare visuals
            visuals = self._prepare_visuals(job, meta)
            
            # Step 2 — Generate slide structure via Claude
            slides = self._generate_slides(job['script_text'], meta, visuals)
            
            # Step 3 — Render master video via Remotion (1080x1920)
            master_path = self._render_remotion(
                job_id, slides, visuals, meta, job_output_dir
            )
            
            # Step 4 — Resize to all platform formats via FFmpeg
            outputs = self._resize_formats(
                master_path, meta['platforms'], job_output_dir
            )
            
            # Step 5 — Generate captions via Claude
            captions = self._generate_captions(
                job['script_text'], meta, self.brand_config
            )
            
            # Step 6 — Save all outputs to renders.db
            for output in outputs:
                caption_data = captions.get(output['platform'], {})
                self.render_store.add_output(
                    job_id=job_id,
                    platform=output['platform'],
                    file_path=output['file_path'],
                    dimensions=output['dimensions'],
                    engine=output['engine'],
                    caption=caption_data.get('caption', ''),
                    hashtags=json.dumps(caption_data.get('hashtags', []))
                )
            
            # Step 7 — Move to review or auto-approve
            review_required = meta.get('review_required', True)
            # Check brand-level override
            brand_review = self.brand_config['video_pipeline'].get(
                'review_required', True
            )
            
            if review_required and brand_review:
                self.render_store.update_status(job_id, 'pending_review')
            else:
                self.render_store.update_status(job_id, 'approved')
                self._auto_schedule(job_id, outputs, captions, meta)
                
        except Exception as e:
            self.render_store.update_status(job_id, 'failed', error=str(e))
            logger.error(f"Render failed for job {job_id}: {e}")
    
    def _prepare_visuals(self, job, meta):
        """
        Get all visual assets needed for this video.
        Returns dict with background path and any generated images.
        """
        visual_config = meta.get('visual', {})
        visual_type = visual_config.get('type', 'stars_bg')
        
        if visual_type == 'stars_bg':
            bg_file = visual_config.get('background_file', 'stars_bg.mp4')
            bg_path = f"{self.remotion_project}/public/{bg_file}"
            return {
                'type': 'stars_bg',
                'background': bg_path,
                'generated_images': []
            }
        
        elif visual_type == 'ai_generated' and self.comfyui_enabled:
            prompt = visual_config.get('comfyui_prompt', '')
            images = self._comfyui_generate(prompt, job['id'])
            return {
                'type': 'ai_generated',
                'background': None,
                'generated_images': images
            }
        
        elif visual_type == 'stock':
            query = visual_config.get('stock_query', meta.get('topic', ''))
            images = self._fetch_stock_images(query)
            return {
                'type': 'stock',
                'background': None,
                'generated_images': images
            }
        
        # Fallback — always return something
        return {
            'type': 'stars_bg',
            'background': f"{self.remotion_project}/public/stars_bg.mp4",
            'generated_images': []
        }
    
    def _generate_slides(self, script_text, meta, visuals):
        """
        Call Claude API to break the script into structured slide data
        that Remotion can consume as props.
        
        Returns a list of slide objects matching Remotion component props.
        """
        # Call Claude API with the script
        # Prompt instructs Claude to return JSON slide array
        # Each slide has: type, headline, body, duration_seconds, motion
        # See YAML template in cognify_course.yaml for slide types
        pass
    
    def _render_remotion(self, job_id, slides, visuals, meta, output_dir):
        """
        Render master 1080x1920 MP4 via Remotion CLI.
        Returns path to rendered file.
        """
        import subprocess
        import json
        
        # Write props file for Remotion to consume
        props = {
            'slides': slides,
            'background': visuals['background'],
            'backgroundType': visuals['type'],
            'generatedImages': visuals.get('generated_images', []),
            'brand': {
                'name': self.brand_config.get('name', 'Cognify Learn'),
                'handle': self.brand_config.get('handle', '@cognifylearn'),
                'website': self.brand_config.get('website', 'cognifylearn.com'),
                'colors': {
                    'primary': '#FFFFFF',
                    'accent': '#FFB800',
                    'dark': '#0F1E45'
                }
            },
            'duration': meta.get('duration_seconds', 30)
        }
        
        props_path = f"{output_dir}/props.json"
        with open(props_path, 'w') as f:
            json.dump(props, f)
        
        master_path = f"{output_dir}/master.mp4"
        
        result = subprocess.run(
            [
                'npx', 'remotion', 'render',
                self.remotion_composition,
                master_path,
                f'--props={props_path}',
                '--log=verbose'
            ],
            cwd=self.remotion_project,
            capture_output=True,
            text=True,
            timeout=300  # 5 minute timeout
        )
        
        if result.returncode != 0:
            raise Exception(f"Remotion render failed: {result.stderr}")
        
        return master_path
    
    def _resize_formats(self, master_path, platforms, output_dir):
        """
        Use FFmpeg to resize master video to all platform formats.
        Master is always 1080x1920 (vertical).
        Returns list of output dicts.
        """
        import subprocess
        
        platform_specs = {
            'tiktok':           {'dims': '1080x1920', 'engine': 'ffmpeg', 'note': 'same as master'},
            'instagram_reel':   {'dims': '1080x1920', 'engine': 'ffmpeg', 'note': 'same as master'},
            'youtube_short':    {'dims': '1080x1920', 'engine': 'ffmpeg', 'note': 'same as master'},
            'facebook':         {'dims': '1080x1080', 'engine': 'ffmpeg', 'note': 'square crop'},
            'linkedin':         {'dims': '1920x1080', 'engine': 'ffmpeg', 'note': 'horizontal'},
            'instagram_post':   {'dims': '1080x1080', 'engine': 'ffmpeg', 'note': 'square crop'},
            'twitter':          {'dims': '1280x720',  'engine': 'ffmpeg', 'note': 'horizontal'},
        }
        
        outputs = []
        
        for platform in platforms:
            if platform not in platform_specs:
                continue
            
            spec = platform_specs[platform]
            w, h = spec['dims'].split('x')
            output_path = f"{output_dir}/{platform}.mp4"
            
            # For vertical platforms — just copy master (no resize needed)
            if spec['dims'] == '1080x1920':
                import shutil
                shutil.copy2(master_path, output_path)
            else:
                # Resize with padding to maintain aspect ratio
                result = subprocess.run([
                    'ffmpeg', '-i', master_path,
                    '-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,'
                           f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black',
                    '-c:v', 'libx264',
                    '-c:a', 'copy',
                    '-y',  # overwrite without asking
                    output_path
                ], capture_output=True, text=True, timeout=120)
                
                if result.returncode != 0:
                    logger.error(f"FFmpeg failed for {platform}: {result.stderr}")
                    continue
            
            outputs.append({
                'platform': platform,
                'file_path': output_path,
                'dimensions': spec['dims'],
                'engine': spec['engine']
            })
        
        return outputs
    
    def _generate_captions(self, script_text, meta, brand_config):
        """
        Generate platform-specific captions via Claude API.
        Returns dict keyed by platform.
        """
        # See app/captions.py for implementation
        from app.captions import generate_caption
        
        captions = {}
        for platform in meta.get('platforms', []):
            result = generate_caption(
                script=script_text,
                platform=platform,
                brand_config=brand_config,
                tone=meta.get('tone', 'warm_bold'),
                pillar=meta.get('pillar', ''),
                topic=meta.get('topic', '')
            )
            captions[platform] = result
        
        return captions
    
    def _comfyui_generate(self, prompt, job_id):
        """
        Generate image via local ComfyUI REST API + WebSocket.
        Uses urllib (no third-party HTTP library).
        WebSocket listens for execution_end event.
        Returns list of generated image paths.
        """
        import urllib.request
        import urllib.parse
        import websocket  # websockets library
        import json
        import uuid
        
        client_id = str(uuid.uuid4())
        workflow = self._build_comfyui_workflow(prompt)
        
        # Queue the prompt via REST
        payload = json.dumps({
            'prompt': workflow,
            'client_id': client_id
        }).encode('utf-8')
        
        req = urllib.request.Request(
            f'{self.comfyui_host}/prompt',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
            prompt_id = result['prompt_id']
        
        # Wait for completion via WebSocket
        images = []
        ws_url = self.comfyui_host.replace('http://', 'ws://') + '/ws'
        
        ws = websocket.WebSocket()
        ws.connect(f"{ws_url}?clientId={client_id}")
        
        try:
            while True:
                message = ws.recv()
                data = json.loads(message)
                
                if data.get('type') == 'executing':
                    if data['data'].get('node') is None:
                        # Execution complete
                        break
                
                elif data.get('type') == 'execution_error':
                    raise Exception(f"ComfyUI error: {data['data']}")
        finally:
            ws.close()
        
        # Download generated images
        history_req = urllib.request.Request(
            f'{self.comfyui_host}/history/{prompt_id}'
        )
        with urllib.request.urlopen(history_req) as response:
            history = json.loads(response.read())
        
        output_dir = f"data/brands/{self.brand_id}/renders/{job_id}/comfyui"
        os.makedirs(output_dir, exist_ok=True)
        
        for node_id, node_output in history[prompt_id]['outputs'].items():
            if 'images' in node_output:
                for image in node_output['images']:
                    filename = image['filename']
                    subfolder = image.get('subfolder', '')
                    image_type = image.get('type', 'output')
                    
                    params = urllib.parse.urlencode({
                        'filename': filename,
                        'subfolder': subfolder,
                        'type': image_type
                    })
                    
                    image_req = urllib.request.Request(
                        f'{self.comfyui_host}/view?{params}'
                    )
                    with urllib.request.urlopen(image_req) as img_response:
                        local_path = f"{output_dir}/{filename}"
                        with open(local_path, 'wb') as f:
                            f.write(img_response.read())
                        images.append(local_path)
        
        return images
    
    def _fetch_stock_images(self, query):
        """
        Fetch stock images from Unsplash API (free tier).
        Returns list of downloaded image paths.
        """
        # Unsplash API: https://api.unsplash.com/search/photos
        # Free tier: 50 requests/hour
        # Requires UNSPLASH_ACCESS_KEY in brand config or env
        pass
    
    def _build_comfyui_workflow(self, prompt):
        """
        Build a ComfyUI workflow JSON for the given prompt.
        Loads from a saved workflow template file.
        Template at: data/brands/<bid>/comfyui_workflow.json
        Replaces the text prompt placeholder.
        """
        workflow_path = f"data/brands/{self.brand_id}/comfyui_workflow.json"
        
        if os.path.exists(workflow_path):
            with open(workflow_path) as f:
                workflow = json.load(f)
            # Find the CLIPTextEncode node and replace prompt
            for node_id, node in workflow.items():
                if node.get('class_type') == 'CLIPTextEncode':
                    if 'inputs' in node and 'text' in node['inputs']:
                        node['inputs']['text'] = prompt
            return workflow
        
        # Fallback — basic SDXL workflow
        return self._default_sdxl_workflow(prompt)
    
    def _auto_schedule(self, job_id, outputs, captions, meta):
        """
        After approval — hand off to existing Socialline scheduler.
        Uses the existing ScheduleStore and auto-schedule logic.
        """
        # Move rendered files to queue/
        # Create schedule entries using existing scheduler
        # Respect preferred_days and preferred_time from meta
        pass
```

---

## NEW: app/captions.py

```python
"""
Caption generation via Claude API.
Generates platform-specific captions from video scripts.
"""

import json
import urllib.request

CLAUDE_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-20250514"

PLATFORM_RULES = {
    'tiktok': {
        'max_chars': 150,
        'tone': 'conversational, energetic, first person',
        'hashtags': '3-5 relevant hashtags',
        'notes': 'Hook in first 5 words. Short sentences.'
    },
    'instagram_reel': {
        'max_chars': 2200,
        'tone': 'warm, personal, storytelling',
        'hashtags': '8-12 hashtags at end',
        'notes': 'Hook first line. Line breaks between paragraphs.'
    },
    'youtube_short': {
        'max_chars': 100,
        'tone': 'curiosity-driven, title-style',
        'hashtags': '2-3 hashtags',
        'notes': 'Reads like a YouTube title. Intrigue over explanation.'
    },
    'facebook': {
        'max_chars': 500,
        'tone': 'warm, community-focused, parent-to-parent',
        'hashtags': '2-3 hashtags only',
        'notes': 'Link in caption is fine. Personal and relatable.'
    },
    'linkedin': {
        'max_chars': 700,
        'tone': 'professional but human, data-aware',
        'hashtags': '3-4 professional hashtags',
        'notes': 'Lead with insight. Teachers and educators audience.'
    },
    'twitter': {
        'max_chars': 280,
        'tone': 'punchy, direct, one clear idea',
        'hashtags': '1-2 hashtags maximum',
        'notes': 'One idea per tweet. No fluff.'
    }
}

def generate_caption(script, platform, brand_config, tone, pillar, topic):
    """
    Generate a single platform-specific caption via Claude API.
    Returns dict with caption and hashtags.
    """
    rules = PLATFORM_RULES.get(platform, PLATFORM_RULES['facebook'])
    
    brand_voice = brand_config.get('voice', 
        'warm, bold, parent-to-parent. Never academic. Always specific.')
    brand_name = brand_config.get('name', 'Cognify Learn')
    brand_handle = brand_config.get('handle', '@cognifylearn')
    brand_website = brand_config.get('website', 'cognifylearn.com/links')
    
    prompt = f"""You are writing a {platform} caption for {brand_name}.

BRAND VOICE: {brand_voice}
PILLAR: {pillar}
TOPIC: {topic}
TONE: {tone}

VIDEO SCRIPT:
{script}

PLATFORM RULES FOR {platform.upper()}:
- Maximum characters: {rules['max_chars']}
- Tone: {rules['tone']}
- Hashtags: {rules['hashtags']}
- Notes: {rules['notes']}

ALWAYS END WITH: {brand_website}

Return ONLY a JSON object with these fields:
{{
  "caption": "the full caption text",
  "hashtags": ["hashtag1", "hashtag2"]
}}

No preamble. No explanation. JSON only."""

    headers = {
        'Content-Type': 'application/json',
        'x-api-key': '',  # handled by API proxy
        'anthropic-version': '2023-06-01'
    }
    
    body = json.dumps({
        'model': CLAUDE_MODEL,
        'max_tokens': 1000,
        'messages': [{'role': 'user', 'content': prompt}]
    }).encode('utf-8')
    
    req = urllib.request.Request(
        CLAUDE_API_URL,
        data=body,
        headers=headers,
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
            text = result['content'][0]['text']
            # Strip any markdown fences if present
            text = text.replace('```json', '').replace('```', '').strip()
            return json.loads(text)
    except Exception as e:
        # Fallback — return script as caption
        return {
            'caption': script[:rules['max_chars']] + f'\n\n{brand_website}',
            'hashtags': []
        }


def generate_all_captions(script, platforms, brand_config, tone, pillar, topic):
    """Generate captions for all platforms at once."""
    return {
        platform: generate_caption(
            script, platform, brand_config, tone, pillar, topic
        )
        for platform in platforms
    }
```

---

## EXTEND: app/sources.py — _pull_folder()

Add script detection to the existing folder handler:

```python
# In _pull_folder() — ADD after existing media detection:

SCRIPT_EXTENSIONS = {'.txt', '.md', '.json'}

def _classify_folder_file(filepath):
    """Determine what type of file this is."""
    path = Path(filepath)
    ext = path.suffix.lower()
    meta_path = str(path).replace(ext, '.meta.json')
    
    # Skip sidecar files themselves
    if '.meta.json' in filepath:
        return None
    
    # Script file
    if ext in SCRIPT_EXTENSIONS:
        if os.path.exists(meta_path):
            return 'script_with_meta'
        else:
            return 'script_infer'
    
    # Existing media handling
    if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']:
        return 'image'
    
    if ext in ['.mp4', '.mov', '.avi', '.webm']:
        return 'video'
    
    return None


def _handle_script_file(self, source, filepath, file_type):
    """Route script files to the render pipeline."""
    path = Path(filepath)
    ext = path.suffix.lower()
    meta_path = str(path).replace(ext, '.meta.json')
    
    # Read script text
    if ext == '.json':
        with open(filepath) as f:
            data = json.load(f)
            script_text = data.get('script', data.get('text', str(data)))
    else:
        script_text = Path(filepath).read_text(encoding='utf-8')
    
    # Read or infer metadata
    if file_type == 'script_with_meta':
        with open(meta_path) as f:
            meta = json.load(f)
    else:
        meta = self._infer_meta(script_text, source)
    
    # Create render job
    render_store = RenderStore(source['brand_id'])
    job_id = render_store.create_job(
        script_text=script_text,
        script_file=os.path.basename(filepath),
        meta=meta
    )
    
    # Move processed script to queue (maintains dedup)
    # Do NOT delete — archive like existing media
    
    logger.info(f"Script queued for render: {filepath} → job {job_id}")
    return job_id


def _infer_meta(self, script_text, source):
    """
    When no .meta.json exists — use Claude to infer metadata.
    Falls back to brand defaults.
    """
    # Call Claude API to analyze script and return meta JSON
    # Use brand default_schedule.json for scheduling defaults
    # Always set review_required: true
    pass
```

---

## EXTEND: server.py — Add Renderer Thread

```python
# Add alongside existing background threads in server.py

def start_renderer():
    """
    Checks for pending_render jobs every 60 seconds.
    Processes one job at a time per brand to avoid GPU overload.
    ComfyUI and Remotion are resource-intensive — no parallel renders.
    """
    while True:
        try:
            for brand_id in get_active_brands():
                brand_config = get_brand_config(brand_id)
                
                # Skip brands without video pipeline enabled
                if not brand_config.get('video_pipeline', {}).get('enabled', False):
                    continue
                
                render_store = RenderStore(brand_id)
                job = render_store.get_next_pending()
                
                if job:
                    logger.info(f"Starting render for job {job['id']} (brand: {brand_id})")
                    renderer = VideoRenderer(brand_id, brand_config)
                    renderer.render_job(job)
                    
        except Exception as e:
            logger.error(f"Renderer thread error: {e}")
        
        time.sleep(60)

# Add to thread startup block:
threading.Thread(target=start_renderer, daemon=True, name='renderer').start()
```

---

## NEW: API ENDPOINTS

Add to existing Flask routes in server.py:

```python
# Review queue
GET  /api/brands/<bid>/renders/review
     → Returns all jobs with status = pending_review
     → Includes outputs array with file paths and captions

GET  /api/brands/<bid>/renders/<jid>
     → Single job detail with all outputs

POST /api/brands/<bid>/renders/<jid>/approve
     → Sets status = approved
     → Triggers _auto_schedule()

POST /api/brands/<bid>/renders/<jid>/approve-schedule
     {
       "scheduled_at": "2026-05-27T09:00:00",
       "platforms": ["tiktok", "instagram_reel"]
     }
     → Approve + schedule specific platforms at specific time

POST /api/brands/<bid>/renders/<jid>/reject
     {"reason": "wrong tone"}
     → Sets status = rejected, logs reason

GET  /api/brands/<bid>/renders/history
     → All completed/failed jobs, paginated
```

---

## REMOTION PROJECT STRUCTURE

Build this structure in D:/remotion/ (or wherever the project lives):

```
D:/remotion/
├── public/
│   ├── stars_bg.mp4           ← Cognify brand background (animated)
│   ├── stars_bg.png           ← Static fallback
│   ├── cognify_intro.mp4      ← 3-second branded intro
│   └── cognify_outro.mp4      ← 7-second branded outro
│
├── src/
│   ├── compositions/
│   │   ├── CognifyShort.jsx   ← Main 30-60sec vertical composition
│   │   └── index.js           ← Register all compositions
│   │
│   ├── components/
│   │   ├── TitleSlide.jsx     ← Black + amber border + white text
│   │   ├── StatSlide.jsx      ← White bg + huge amber number
│   │   ├── TeachingSlide.jsx  ← Navy bg + headline + 3 bullets
│   │   ├── ActionSlide.jsx    ← Amber bg + navy CTA text
│   │   └── QuoteSlide.jsx     ← Dark navy + white italic centered
│   │
│   ├── constants/
│   │   └── colors.js          ← Brand color system
│   │
│   └── utils/
│       └── motion.js          ← Reusable Ken Burns, zoom, spring helpers
│
├── remotion.config.js
└── package.json
```

### colors.js
```javascript
export const Colors = {
  bgPrimary:    'transparent',   // stars_bg.mp4 is the background
  bgDark:       '#1A1F3A',
  textPrimary:  '#FFFFFF',
  textSoft:     '#E8EAFF',
  textDark:     '#0F1E45',
  amber:        '#FFB800',
  indigo:       '#5C27FE',
  overlayDark:  'rgba(15, 30, 69, 0.6)',
  overlayAmber: 'rgba(255, 184, 0, 0.15)',
};
```

### CognifyShort.jsx — composition props
```javascript
// Props passed in from Socialline via --props=props.json
export const CognifyShort = ({
  slides,           // array of slide objects
  background,       // path to stars_bg.mp4
  backgroundType,   // 'stars_bg' | 'ai_generated' | 'stock'
  generatedImages,  // array of image paths
  brand,            // { name, handle, website, colors }
  duration,         // total seconds
}) => {
  // Render intro + slides + outro in sequence
  // Each slide component reads its own props from the slides array
};
```

---

## BRAND CONFIG ADDITIONS

Add to each brand's config file:

```json
{
  "name": "Cognify Learn",
  "handle": "@cognifylearn",
  "website": "cognifylearn.com/links",
  "voice": "warm, bold, parent-to-parent. Never academic. Always specific.",

  "video_pipeline": {
    "enabled": true,
    "review_required": true,
    "remotion_project": "D:/remotion",
    "remotion_composition": "CognifyShort",
    "comfyui_enabled": true,
    "comfyui_host": "http://localhost:8188",
    "default_background": "stars_bg.mp4",
    "output_formats": [
      "tiktok",
      "instagram_reel",
      "youtube_short",
      "facebook",
      "linkedin"
    ],
    "caption_generation": "claude",
    "schedule_days_ahead": 1,
    "scripts_folder": "data/brands/cognify_learn/scripts"
  }
}
```

---

## FOLDER SOURCE CONFIG FOR COGNIFY LEARN

Add a new source in Socialline pointing to the scripts folder:

```json
{
  "type": "folder",
  "name": "Cognify Scripts",
  "path": "data/brands/cognify_learn/scripts",
  "schedule": "hourly",
  "watch_scripts": true,
  "auto_render": true,
  "brand_id": "cognify_learn"
}
```

---

## _generate_slides() — COMPLETE SPECIFICATION

This is the most critical function in the pipeline. It takes a raw script
and returns a structured JSON array that Remotion consumes as props.

---

### What It Does

```
Raw script text (free-form)
        ↓
Claude API — splits into slides, assigns types, timing, motion
        ↓
JSON slide array
        ↓
Written to props.json
        ↓
Remotion reads props.json → renders each slide as a React component
```

---

### The 5 Slide Types

Every slide must be one of these 5 types. Each maps to a Remotion component.

```
TYPE 1: "title"
  Use for: opening of video only, one time
  Remotion component: TitleSlide.jsx
  Visual: black bg + amber border + white headline + white subheadline
  Fields: headline, subheadline (optional), module_label (optional)
  Motion: zoom_in_slow

TYPE 2: "stat"
  Use for: any specific number, percentage, or research finding
  Remotion component: StatSlide.jsx
  Visual: white bg + amber left bar + huge centered number + caption + source
  Fields: stat_number, stat_caption, source (optional)
  Motion: zoom_out_slow

TYPE 3: "teaching"
  Use for: explaining a concept, tool, or technique (most common type)
  Remotion component: TeachingSlide.jsx
  Visual: navy bg + white headline + 2-3 white bullets
  Fields: headline, bullet_1, bullet_2, bullet_3 (optional)
  Motion: ken_burns_left_right

TYPE 4: "action"
  Use for: specific step for parent/teacher to take — always last or near-last
  Remotion component: ActionSlide.jsx
  Visual: amber bg + small navy label + large navy instruction + optional note
  Fields: label (e.g. "TRY THIS TONIGHT"), instruction, sub_note (optional)
  Motion: slide_up

TYPE 5: "quote"
  Use for: single bold reframe statement, emotional hook, or key truth
  Remotion component: QuoteSlide.jsx
  Visual: dark navy bg + large white italic centered text
  Fields: quote_text, attribution (optional)
  Motion: zoom_in_slow
```

---

### The Slide JSON Structure

Every slide in the array must match this exact schema:

```json
{
  "id": "slide_001",
  "type": "quote | stat | teaching | action | title",
  "duration_seconds": 5,
  "motion": "zoom_in_slow | zoom_out_slow | ken_burns_left_right | slide_up",

  // TYPE-SPECIFIC FIELDS — include only fields for the slide type:

  // title fields:
  "headline": "string — max 8 words",
  "subheadline": "string — max 12 words (optional)",
  "module_label": "string — e.g. 'Module 1' (optional)",

  // stat fields:
  "stat_number": "string — e.g. '98%' or '$50/hr'",
  "stat_caption": "string — one line, max 10 words",
  "source": "string — short citation (optional)",

  // teaching fields:
  "headline": "string — max 6 words, bold claim",
  "bullet_1": "string — max 10 words",
  "bullet_2": "string — max 10 words",
  "bullet_3": "string — max 10 words (optional)",

  // action fields:
  "label": "string — max 4 words, ALL CAPS, e.g. 'TRY THIS TONIGHT'",
  "instruction": "string — max 15 words, direct imperative",
  "sub_note": "string — max 10 words (optional)",

  // quote fields:
  "quote_text": "string — one sentence, max 20 words",
  "attribution": "string — optional, e.g. 'Cognify Learn'"
}
```

---

### Complete Example — cognify_001 Script Broken Into Slides

**Input script:**
```
Kids with a personal tutor outperformed 
98% of classroom students.

At $50 per hour most families simply 
can't afford it consistently.

AI gives every child unlimited, infinitely 
patient, one-on-one tutoring. For free. 
Available tonight.

Follow Cognify Learn for one tip a day. 🧠
```

**Output slides JSON:**
```json
[
  {
    "id": "slide_001",
    "type": "quote",
    "duration_seconds": 5,
    "motion": "zoom_in_slow",
    "quote_text": "Kids with a personal tutor outperformed 98% of classroom students.",
    "attribution": null
  },
  {
    "id": "slide_002",
    "type": "stat",
    "duration_seconds": 6,
    "motion": "zoom_out_slow",
    "stat_number": "98%",
    "stat_caption": "of classroom students outperformed by tutored kids",
    "source": "Bloom, Educational Researcher, 1984"
  },
  {
    "id": "slide_003",
    "type": "teaching",
    "duration_seconds": 6,
    "motion": "ken_burns_left_right",
    "headline": "Most families can't afford it",
    "bullet_1": "Average tutoring: $50 per hour",
    "bullet_2": "2 sessions a week = $5,000 a year",
    "bullet_3": "Most kids never get one-on-one help"
  },
  {
    "id": "slide_004",
    "type": "quote",
    "duration_seconds": 7,
    "motion": "zoom_in_slow",
    "quote_text": "AI gives every child unlimited, patient, one-on-one tutoring. For free.",
    "attribution": null
  },
  {
    "id": "slide_005",
    "type": "action",
    "duration_seconds": 6,
    "motion": "slide_up",
    "label": "FOLLOW FOR MORE",
    "instruction": "Follow Cognify Learn for one AI tip a day",
    "sub_note": "cognifylearn.com/links"
  }
]
```

Total duration: 5+6+6+7+6 = 30 seconds ✓

---

### The Claude API Prompt For _generate_slides()

```python
def _generate_slides(self, script_text, meta, visuals):
    """
    Call Claude API to break script into structured slide JSON.
    Returns list of slide dicts matching Remotion component props.
    """
    import urllib.request
    import json

    duration = meta.get('duration_seconds', 30)
    tone = meta.get('tone', 'warm_bold')
    template = meta.get('template', 'stat_overlay')

    # Template hints — tell Claude what slide pattern to use
    template_hints = {
        'stat_overlay': 'Lead with a quote hook, follow with a stat slide, then teaching, end with action CTA.',
        'tutorial': 'Start with a teaching slide, use 2-3 more teaching slides, end with action CTA.',
        'story': 'Start with a quote hook, use teaching slides for the narrative, end with action CTA.',
        'reframe': 'Start with a bold quote, use teaching slides to flip the assumption, end with quote + action.',
        'tip': 'Start with a hook quote, one teaching slide with 3 bullets, end with action CTA.'
    }
    hint = template_hints.get(template, template_hints['stat_overlay'])

    system_prompt = """You are a video slide designer for Cognify Learn — an educational brand
teaching parents and K-12 teachers to use AI with kids.

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
9. teaching slides must have headline + at least 2 bullets
10. quote slides must have quote_text (attribution optional)
11. action slides must have label (ALL CAPS, max 4 words) + instruction"""

    user_prompt = f"""Break this script into slides for a {duration}-second video.

SCRIPT:
{script_text}

TARGET DURATION: {duration} seconds total
TEMPLATE PATTERN: {hint}
TONE: {tone}

Rules reminder:
- 4-6 slides for a 30-second video
- Slide durations must add up to exactly {duration} seconds
- End with an action slide
- Return ONLY the JSON array, nothing else

Example of correct output format:
[
  {{"id": "slide_001", "type": "quote", "duration_seconds": 5, "motion": "zoom_in_slow", "quote_text": "Your quote here", "attribution": null}},
  {{"id": "slide_002", "type": "stat", "duration_seconds": 6, "motion": "zoom_out_slow", "stat_number": "98%", "stat_caption": "caption here", "source": "Source, Year"}},
  {{"id": "slide_003", "type": "teaching", "duration_seconds": 7, "motion": "ken_burns_left_right", "headline": "Short headline", "bullet_1": "First point here", "bullet_2": "Second point here", "bullet_3": null}},
  {{"id": "slide_004", "type": "action", "duration_seconds": 6, "motion": "slide_up", "label": "TRY THIS TONIGHT", "instruction": "Open ChatGPT and type this prompt", "sub_note": "cognifylearn.com/links"}}
]

Return the JSON array now:"""

    body = json.dumps({
        'model': 'claude-sonnet-4-20250514',
        'max_tokens': 2000,
        'system': system_prompt,
        'messages': [{'role': 'user', 'content': user_prompt}]
    }).encode('utf-8')

    req = urllib.request.Request(
        'https://api.anthropic.com/v1/messages',
        data=body,
        headers={
            'Content-Type': 'application/json',
            'anthropic-version': '2023-06-01'
        },
        method='POST'
    )

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read())
            raw = result['content'][0]['text'].strip()

            # Strip markdown fences if Claude wraps in ```json
            if raw.startswith('```'):
                raw = raw.split('```')[1]
                if raw.startswith('json'):
                    raw = raw[4:]
            raw = raw.strip()

            slides = json.loads(raw)

            # Validate total duration
            total = sum(s.get('duration_seconds', 0) for s in slides)
            if total != duration:
                # Adjust last slide to fix duration
                diff = duration - total
                slides[-1]['duration_seconds'] += diff

            return slides

    except Exception as e:
        logger.error(f"_generate_slides failed: {e}")
        # Fallback — return a minimal 2-slide structure
        return [
            {
                'id': 'slide_001',
                'type': 'quote',
                'duration_seconds': duration - 6,
                'motion': 'zoom_in_slow',
                'quote_text': script_text[:100].strip(),
                'attribution': None
            },
            {
                'id': 'slide_002',
                'type': 'action',
                'duration_seconds': 6,
                'motion': 'slide_up',
                'label': 'FOLLOW FOR MORE',
                'instruction': 'Follow Cognify Learn for daily AI tips',
                'sub_note': 'cognifylearn.com/links'
            }
        ]
```

---

### How Remotion Consumes The Slides

The `CognifyShort.jsx` composition receives the slides array as props
and maps each slide to its component:

```javascript
// CognifyShort.jsx
import { Series } from 'remotion';
import { TitleSlide } from '../components/TitleSlide';
import { StatSlide } from '../components/StatSlide';
import { TeachingSlide } from '../components/TeachingSlide';
import { ActionSlide } from '../components/ActionSlide';
import { QuoteSlide } from '../components/QuoteSlide';

const SLIDE_COMPONENTS = {
  title:    TitleSlide,
  stat:     StatSlide,
  teaching: TeachingSlide,
  action:   ActionSlide,
  quote:    QuoteSlide,
};

export const CognifyShort = ({ slides, background, brand, duration }) => {
  return (
    <Series>
      {slides.map((slide) => {
        const Component = SLIDE_COMPONENTS[slide.type];
        const durationInFrames = slide.duration_seconds * 30; // 30fps
        return (
          <Series.Sequence
            key={slide.id}
            durationInFrames={durationInFrames}
          >
            <Component
              {...slide}
              background={background}
              brand={brand}
            />
          </Series.Sequence>
        );
      })}
    </Series>
  );
};
```

---

### Each Slide Component Receives

Every slide component receives its type-specific fields PLUS:
```javascript
{
  // Shared props passed to every component:
  background: "path/to/stars_bg.mp4",  // or image path
  brand: {
    name: "Cognify Learn",
    handle: "@cognifylearn",
    website: "cognifylearn.com/links",
    colors: {
      primary: "#FFFFFF",
      accent: "#FFB800",
      dark: "#0F1E45"
    }
  },
  motion: "zoom_in_slow",  // handled inside each component

  // Type-specific fields from the slide JSON:
  // (headline, bullets, stat_number, etc.)
}
```

---

### Motion Implementation In Each Component

Each component handles its own motion using Remotion's hooks:

```javascript
// Example — zoom_in_slow used in QuoteSlide.jsx
import { useCurrentFrame, useVideoConfig, interpolate } from 'remotion';

const frame = useCurrentFrame();
const { durationInFrames } = useVideoConfig();

const getMotionStyle = (motion) => {
  switch(motion) {
    case 'zoom_in_slow':
      return {
        scale: interpolate(frame, [0, durationInFrames], [1.0, 1.08]),
      };
    case 'zoom_out_slow':
      return {
        scale: interpolate(frame, [0, durationInFrames], [1.08, 1.0]),
      };
    case 'ken_burns_left_right':
      return {
        scale: interpolate(frame, [0, durationInFrames], [1.05, 1.0]),
        translateX: interpolate(frame, [0, durationInFrames], [0, -20]),
      };
    case 'slide_up':
      return {
        translateY: interpolate(
          frame, [0, 15], [30, 0],
          { extrapolateRight: 'clamp' }
        ),
        opacity: interpolate(frame, [0, 10], [0, 1]),
      };
    default:
      return {};
  }
};
```

---

## KNOWN CLASHES WITH EXISTING SYSTEM — RESOLVE THESE

```
1. content.db vs renders.db
   Current: all content tracked in content.db
   New: render jobs tracked in renders.db
   RESOLUTION: renders.db is separate — render jobs are NOT
   queue items until approved. Only after approval do
   rendered videos move to queue/ and enter content.db.

2. File routing in _pull_folder()
   Current: all files go to queue/
   New: script files go to render pipeline instead
   RESOLUTION: Add file type check at TOP of _pull_folder().
   Scripts branch to _handle_script_file() and return early.
   Media files continue to existing queue logic unchanged.

3. Caption generation
   Current: captions.txt sidecars or manual entry
   New: Claude API generates captions per platform
   RESOLUTION: Generated captions stored in render_captions table.
   When render is approved and moves to queue, captions are
   written as .caption.txt sidecars in the existing format.
   Existing caption system continues to work unchanged.

4. Scheduling
   Current: schedule.db with filename-based queue items
   New: render outputs need to enter schedule.db
   RESOLUTION: _auto_schedule() in VideoRenderer takes approved
   render outputs, copies MP4 files to queue/, writes caption
   sidecars, then calls existing ScheduleStore to create entries.
   Existing scheduler thread picks them up with zero changes.
```

---

## BUILD ORDER FOR CLAUDE CODE

Work through these in sequence. Each step is independently testable.

```
STEP 1 — Database
□ Create app/render_store.py with RenderStore class
□ Schema: render_jobs, render_outputs, render_captions
□ Methods: create_job, get_next_pending, update_status,
           add_output, get_pending_review, approve_job,
           reject_job, get_outputs
□ Test: create a job, update status, retrieve it

STEP 2 — Script Detection
□ Add _classify_folder_file() to sources.py
□ Add _handle_script_file() to PullEngine
□ Add _infer_meta() stub (returns defaults for now)
□ Extend _pull_folder() to call script handler
□ Test: drop a .txt + .meta.json into scripts folder,
        verify render_job is created in renders.db

STEP 3 — Captions
□ Create app/captions.py
□ Implement generate_caption() for all platforms
□ Test: pass a script, get back caption + hashtags for each platform

STEP 4 — FFmpeg Resize
□ Implement _resize_formats() in VideoRenderer
□ Test with an existing MP4: resize to all 5 platform formats
□ Verify dimensions are correct for each

STEP 5 — Remotion Integration
□ Set up Remotion project structure
□ Build 5 slide components (Title, Stat, Teaching, Action, Quote)
□ Build CognifyShort composition
□ Test: render manually with npx remotion render
□ Implement _render_remotion() subprocess call in VideoRenderer
□ Test: call from Python, verify MP4 output

STEP 6 — ComfyUI Integration
□ Implement _comfyui_generate() in VideoRenderer
□ Test: generate one image, verify file downloaded
□ Test WebSocket completion detection

STEP 7 — Full Pipeline
□ Implement VideoRenderer.render_job() orchestration
□ Wire _prepare_visuals() → _generate_slides() → 
  _render_remotion() → _resize_formats() → _generate_captions()
□ Test end-to-end: drop script → verify all platform MP4s created

STEP 8 — Review Queue
□ Add review API endpoints to server.py
□ Implement _auto_schedule() to hand off to existing scheduler
□ Test: approve a job, verify it enters queue/ and schedule.db

STEP 9 — Background Thread
□ Implement start_renderer() in server.py
□ Add to thread startup block
□ Test: drop script, wait 60s, verify render starts automatically

STEP 10 — Cognify Learn Test Run
□ Create scripts/cognify_001.txt with first script
□ Create scripts/cognify_001.meta.json
□ Run full pipeline
□ Review output in Socialline UI
□ Approve and verify scheduled post
```

---

## FIRST 10 SCRIPTS — READY TO USE

These scripts are ready to drop into data/brands/cognify_learn/scripts/
as .txt files with matching .meta.json sidecars.

See the Cognify Learn conversation for full script text for:
- cognify_001: 98% tutoring stat
- cognify_002: The cheating question  
- cognify_003: The patient tutor (fractions)
- cognify_004: The shutdown moment
- cognify_005: 3 free AI tools overview
- cognify_006: The reframe post
- cognify_007: The one rule
- cognify_008: The teacher angle
- cognify_009: Curiosity driver (water cycle)
- cognify_010: The CTA post

All use: visual.type = stars_bg, duration = 30, review_required = true

---

## NOTES FOR CLAUDE CODE

- Always check existing patterns before adding new ones
- Match coding style of existing app/ files
- Use same logging pattern as existing code
- SQLite connections should follow existing SourceStore pattern
- Never break existing source types or scheduler
- When in doubt — add alongside, never replace
- Test each step before moving to next
- The goal is minimal user interaction — automate everything possible
  but always preserve the review step before posting
