#!/usr/bin/env python3
import sys
import copy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(APP_DIR))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from flask import Flask, render_template, request, jsonify, send_from_directory

app = Flask(__name__, template_folder="templates", static_folder="static")

QUEUE_DIR = ROOT / "famjammemes" / "queue"
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}


@app.route("/")
def queue():
    images = []
    if QUEUE_DIR.exists():
        images = [
            {"filename": f.name, "caption": f.stem.replace("_", " ").replace("-", " ")}
            for f in sorted(QUEUE_DIR.iterdir())
            if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
        ]
    return render_template("queue.html", images=images)


@app.route("/queue/<path:filename>")
def serve_queue_image(filename):
    return send_from_directory(QUEUE_DIR, filename)


@app.route("/post/<path:filename>")
def editor(filename):
    image_path = QUEUE_DIR / filename
    if not image_path.exists():
        return "Image not found", 404
    caption = image_path.stem.replace("_", " ").replace("-", " ")
    return render_template("editor.html", filename=filename, caption=caption)


@app.route("/api/generate-captions", methods=["POST"])
def api_generate_captions():
    from caption_ai import generate_platform_captions
    data = request.get_json()
    core_caption = (data or {}).get("caption", "").strip()
    if not core_caption:
        return jsonify({"error": "No caption provided"}), 400
    try:
        captions = generate_platform_captions(core_caption)
        return jsonify(captions)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/publish", methods=["POST"])
def api_publish():
    from pipeline.core.content_item import ContentItem
    from pipeline.core.content_store import ContentStore
    from pipeline.brands.famjam.config import create_pipeline

    data = request.get_json()
    filename  = data.get("filename")
    captions  = data.get("captions", {})
    platforms = data.get("platforms", [])

    if not filename or not platforms:
        return jsonify({"error": "filename and platforms required"}), 400

    image_path = QUEUE_DIR / filename
    if not image_path.exists():
        return jsonify({"error": "Image not found"}), 404

    store    = ContentStore()
    pipeline = create_pipeline(platforms=platforms, store=store)

    item = ContentItem(
        source_url=f"file://{image_path.resolve()}",
        source_platform="local",
        media_path=image_path,
        caption=captions.get("facebook", image_path.stem),
        tags=["local"],
    )

    if not store.exists(item.id):
        store.save(item)

    for transformer in pipeline.shared_transformers:
        item = transformer.transform(item)

    results = {}
    for platform_config in pipeline.platforms:
        pname = platform_config.name
        try:
            platform_item = copy.deepcopy(item)
            platform_item.caption = captions.get(pname, item.caption)

            for transformer in platform_config.transformers:
                platform_item = transformer.transform(platform_item)

            success = platform_config.publisher.publish(platform_item)
            if success:
                post_id = platform_item.metadata.get(f"{pname}_post_id")
                store.mark_posted(item.id, pname, post_id)
                results[pname] = "posted"
            else:
                store.mark_failed(item.id, pname)
                results[pname] = "failed"
        except Exception as e:
            results[pname] = f"error: {e}"

    return jsonify(results)


if __name__ == "__main__":
    print(f"Queue folder: {QUEUE_DIR}")
    print("Starting FamJam posting UI at http://localhost:5000")
    app.run(debug=True, port=5000)
