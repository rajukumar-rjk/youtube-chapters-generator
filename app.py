import os
import re
import json
import streamlit as st
from youtube_transcript_api import (
    YouTubeTranscriptApi,
    TranscriptsDisabled,
    NoTranscriptFound,
    VideoUnavailable,
)
from openai import OpenAI
from dotenv import load_dotenv
# from pytube import YouTube

# ---------------- CONFIG ----------------
load_dotenv()
api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise ValueError("OPENAI_API_KEY not found. Add it to your .env file.")

client = OpenAI(api_key=api_key)
CACHE_FILE = "cache.json"

# ---------------- HELPERS ----------------
def extract_video_id(url: str) -> str:
    match = re.search(r"(?:v=|/)([0-9A-Za-z_-]{11})", url)
    return match.group(1) if match else None

def format_timestamp(seconds: float) -> str:
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"

def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except Exception:
                return {}
    return {}

def save_cache(cache):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

def get_available_transcripts(video_id: str):
    ytt = YouTubeTranscriptApi()
    try:
        transcript_list = ytt.list(video_id)
        available = []
        for t in transcript_list:
            available.append({
                "language": t.language,
                "language_code": t.language_code,
                "is_generated": t.is_generated
            })
        return {"status": "ok", "available_transcripts": available}
    except TranscriptsDisabled:
        return {"status": "error", "message": "Transcripts are disabled. Enable captions in YouTube Studio."}
    except NoTranscriptFound:
        return {"status": "error", "message": "No transcripts found. Upload captions in YouTube Studio."}
    except VideoUnavailable:
        return {"status": "error", "message": "This video is unavailable. Check the video ID or URL."}
    except Exception as e:
        return {"status": "error", "message": f"Unexpected error: {str(e)}"}

def summarize_chunk(chunk_id: str, text: str, cache: dict) -> str:
    if chunk_id in cache:
        return cache[chunk_id]
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You create short, catchy YouTube chapter titles."},
                {"role": "user", "content": f"Summarize this transcript chunk into a short chapter title (max 8 words):\n\n{text}"}
            ],
            max_tokens=30,
            temperature=0.2,
        )
        title = response.choices[0].message.content.strip()
    except Exception:
        title = "Chapter Title Error"
    cache[chunk_id] = title
    save_cache(cache)
    return title

def generate_ai_chapters(video_url: str, max_words_per_chunk: int = 1500):
    video_id = extract_video_id(video_url)
    if not video_id:
        return {"status": "error", "message": "Invalid YouTube URL."}

    trans_result = get_available_transcripts(video_id)
    if trans_result["status"] != "ok":
        return trans_result

    ytt = YouTubeTranscriptApi()
    transcript_list = ytt.list(video_id)
    preferred = ["en", "en-US", "en-GB", "hi", "hi-IN"]

    try:
        chosen = transcript_list.find_transcript(preferred)
    except NoTranscriptFound:
        available_codes = [t["language_code"] for t in trans_result["available_transcripts"]]
        chosen = transcript_list.find_transcript([available_codes[0]])

    transcript = chosen.fetch()
    cache = load_cache()
    chapters = []
    buffer, buffer_start_time, word_count = [], None, 0

    for entry in transcript:
        if buffer_start_time is None:
            buffer_start_time = entry.start
        buffer.append(entry.text)
        word_count += len(entry.text.split())
        if word_count >= max_words_per_chunk:
            chunk_text = " ".join(buffer).strip()
            chunk_id = f"{video_id}_{int(buffer_start_time)}"
            title = summarize_chunk(chunk_id, chunk_text, cache)
            chapters.append((format_timestamp(buffer_start_time), title))
            buffer, buffer_start_time, word_count = [], None, 0

    if buffer:
        if buffer_start_time is None:
            buffer_start_time = 0.0
        chunk_text = " ".join(buffer).strip()
        chunk_id = f"{video_id}_{int(buffer_start_time)}"
        title = summarize_chunk(chunk_id, chunk_text, cache)
        chapters.append((format_timestamp(buffer_start_time), title))

    return {"status": "ok", "chapters": chapters}

def format_chapters_block(video_url: str, chapters: list) -> str:
    """Return a single text block with full YouTube link + timestamps for copy/paste"""
    block = f"Video Link: {video_url}\n\n"
    for ts, title in chapters:
        block += f"{ts} - {title}\n"
    return block

# ---------------- STREAMLIT APP ----------------
st.set_page_config(page_title="YouTube AI Chapters Generator", layout="wide")
st.title("📺 YouTube AI Chapters Generator")
st.markdown("""
Generate **AI-powered YouTube chapter titles** in plain text format.  
- Copy the full block to your video description or comment.  
- Cached results avoid repeated API calls to save credits.  
""")

menu = st.sidebar.selectbox("Menu", ["Generate Chapters", "View Cached Videos"])
cache = load_cache()

# ---------------- GENERATE CHAPTERS PAGE ----------------
if menu == "Generate Chapters":
    st.subheader("Step 1: Enter YouTube URL")
    video_url = st.text_input("YouTube URL:", placeholder="https://www.youtube.com/watch?v=example")

    if st.button("Generate Chapters"):
        if not video_url:
            st.error("Please enter a YouTube URL.")
        else:
            with st.spinner("Generating chapters..."):
                result = generate_ai_chapters(video_url)
                if result["status"] != "ok":
                    st.error(result.get("message"))
                else:
                    chapters = result["chapters"]
                    st.success("✅ Chapters generated successfully!")
                    chapters_block = format_chapters_block(video_url, chapters)
                    st.text_area("Copy all chapters below:", value=chapters_block, height=300)
                    st.download_button(
                        "💾 Download Chapters as Text",
                        chapters_block,
                        file_name="youtube_chapters.txt",
                        mime="text/plain"
                    )

# ---------------- CACHED VIDEOS PAGE ----------------
elif menu == "View Cached Videos":
    st.subheader("📂 Cached Videos")
    if not cache:
        st.info("No cached videos yet.")
    else:
        # Group chapters by video_id
        grouped = {}
        for chunk_id, title in cache.items():
            video_id, ts_seconds = chunk_id.split("_")
            timestamp = format_timestamp(int(ts_seconds))
            grouped.setdefault(video_id, []).append((timestamp, title))

        for video_id, chapters in grouped.items():
            video_link = f"https://www.youtube.com/watch?v={video_id}"
            chapters_block = format_chapters_block(video_link, chapters)
            st.markdown(f"### {video_link}")
            st.text_area("Copy all chapters below:", value=chapters_block, height=200)
            st.download_button(
                f"💾 Download Chapters",
                chapters_block,
                file_name=f"{video_id}_chapters.txt",
                mime="text/plain"
            )

# ---------------- SITE CREDIT ----------------
st.markdown("""
---
Built by [Raju Singh](https://www.linkedin.com/in/rajukumar-rjk/)
""")
