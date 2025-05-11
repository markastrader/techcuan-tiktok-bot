import os
import json
import random
import time
import schedule
import logging
import logging.handlers
import pytz
import requests
import glob
from datetime import datetime, timedelta
from dotenv import load_dotenv
from moviepy.editor import *
from moviepy.video.tools.subtitles import SubtitlesClip
from PIL import Image, ImageDraw
from tenacity import retry, stop_after_attempt, wait_fixed
from pytrends.request import TrendReq
from textwrap import wrap
from httpx import Client
from parsel import Selector
from flask import Flask, send_file

app = Flask(__name__)

# === Setup Logging ===
logger = logging.getLogger('TechcuanBot')
logger.setLevel(logging.DEBUG if os.getenv("DEBUG_MODE") == "true" else logging.INFO)
handler = logging.handlers.RotatingFileHandler(
    filename='logs/bot.log', maxBytes=5*1024*1024, backupCount=3
)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(funcName)s - %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)

# === Load .env ===
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SERVER_URL = os.getenv("SERVER_URL")  # Misalnya, https://techcuan-tiktok-bot.onrender.com

# === Config ===
TZ = pytz.timezone("Asia/Jakarta")
os.makedirs("temp_audio", exist_ok=True)
os.makedirs("videos", exist_ok=True)
os.makedirs("logs", exist_ok=True)
os.makedirs("backgrounds", exist_ok=True)
os.makedirs("music", exist_ok=True)
pytrends = TrendReq(hl='id-ID', tz=420)
http_client = Client(headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})

# Jam aktif audiens TikTok Indonesia (Hootsuite 2025)
ACTIVE_HOURS = [
    ("07:00", 0.7),  # Pagi
    ("12:00", 0.6),  # Siang
    ("19:00", 1.0),  # Malam (puncak)
    ("21:00", 0.9),  # Malam
    ("23:00", 0.5)   # Larut malam
]

# === Flask Routes ===
@app.route("/videos/<filename>")
def serve_video(filename):
    video_path = os.path.join("videos", filename)
    if os.path.exists(video_path):
        return send_file(video_path, mimetype="video/mp4")
    return "Video not found", 404

# === Helpers ===
def waktu_wib():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")

@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def kirim_telegram(pesan):
    logger.info("Mengirim notifikasi Telegram")
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    resp = requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": pesan})
    resp.raise_for_status()
    logger.info("Notifikasi Telegram terkirim")

def cleanup_temp_files(audio_path, video_path):
    for path in [audio_path, video_path]:
        if os.path.exists(path):
            os.remove(path)
            logger.info(f"File dihapus: {path}")

def check_storage(max_size_mb=100):
    total_size = sum(os.path.getsize(f) for f in glob.glob("videos/*.mp4")) / (1024 * 1024)
    if total_size > max_size_mb:
        logger.warning("Penyimpanan penuh, menghapus file lama")
        for f in sorted(glob.glob("videos/*.mp4"), key=os.path.getmtime)[:-5]:
            os.remove(f)

def cleanup_logs(max_size_mb=50):
    total_size = sum(os.path.getsize(f) for f in glob.glob('logs/*.log')) / (1024 * 1024)
    if total_size > max_size_mb:
        logger.info("Membersihkan log lama")
        for f in sorted(glob.glob('logs/*.log'), key=os.path.getmtime)[:-3]:
            os.remove(f)

# === Analitik TikTok ===
@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
def scrape_public_trends():
    logger.info("Mengambil tren TikTok publik")
    try:
        # Google Trends
        pytrends.build_payload(kw_list=['TikTok Indonesia', 'tren TikTok'], timeframe='now 7-d', geo='ID')
        topics = pytrends.trending_searches(pn='indonesia')[0].tolist()[:5]
        hashtags = pytrends.related_queries()['TikTok Indonesia']['top']['query'].tolist()[:5]
        
        # TokBoard (contoh URL publik)
        tokboard_url = "https://www.tokboard.com/"
        resp = http_client.get(tokboard_url)
        sel = Selector(resp.text)
        trending_sounds = sel.css(".sound-title::text").getall()[:5]
        
        trends = [f"{topic} #{hashtag.replace(' ', '')} #TechCuan" for topic, hashtag in zip(topics, hashtags)]
        trends.extend([f"Sound: {sound} #TechCuan" for sound in trending_sounds])
        logger.info(f"Tren TikTok diambil: {trends}")
        return trends
    except Exception as e:
        logger.error(f"Gagal mengambil tren: {str(e)}")
        return [
            "Tips cuan dengan AI #AICuan #TechCuan",
            "Tren kerja remote 2025 #RemoteWork #TechCuan",
            "Rahasia algoritma TikTok #TikTokTips #TechCuan",
            "Konten viral dengan AI #ViralAI #TechCuan",
            "Tools AI untuk Gen Z #AITools #TechCuan"
        ]

@retry(stop=stop_after_attempt(3), wait=wait_fixed(5))
def analyze_engagement(video_url):
    logger.info(f"Menganalisis engagement untuk: {video_url}")
    try:
        resp = http_client.get(video_url)
        sel = Selector(resp.text)
        json_data = sel.css("script[id='__UNIVERSAL_DATA']::text").get()
        if json_data:
            data = json.loads(json_data)
            stats = data.get("webapp.user-detail", {}).get("stats", {})
            engagement = {
                "views": stats.get("playCount", 0),
                "likes": stats.get("diggCount", 0),
                "comments": stats.get("commentCount", 0),
                "shares": stats.get("shareCount", 0)
            }
            logger.info(f"Engagement: {engagement}")
            return engagement
        return {"views": 0, "likes": 0, "comments": 0, "shares": 0}
    except Exception as e:
        logger.error(f"Gagal menganalisis engagement: {str(e)}")
        return {"views": 0, "likes": 0, "comments": 0, "shares": 0}

def log_performance(judul, hashtags, waktu, engagement):
    logger.info("Mencatat performa konten")
    with open("analytics.csv", "a") as f:
        f.write(f"{waktu},{judul},{hashtags},{engagement['views']},{engagement['likes']},{engagement['comments']},{engagement['shares']}\n")

def get_optimal_upload_time():
    probabilities = [weight for _, weight in ACTIVE_HOURS]
    selected_time = random.choices([time for time, _ in ACTIVE_HOURS], weights=probabilities, k=1)[0]
    base_hour, base_minute = map(int, selected_time.split(':'))
    random_minutes = random.randint(-30, 30)
    optimal_time = datetime.strptime(f"{base_hour}:{base_minute}", "%H:%M") + timedelta(minutes=random_minutes)
    logger.info(f"Waktu unggahan optimal: {optimal_time.strftime('%H:%M')}")
    return optimal_time.strftime("%H:%M")

# === AI Hashtag Generator ===
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def generate_hashtags(judul):
    logger.info(f"Memulai pembuatan hashtag untuk judul: {judul}")
    try:
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
        data = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": "Kamu adalah ahli media sosial TikTok Indonesia."},
                {"role": "user", "content": f"Buatkan 5-7 hashtag relevan untuk topik: {judul}. Fokus pada tren Indonesia dan Gen Z."}
            ]
        }
        resp = requests.post("https://api.openai.com/v1/chat/completions", json=data, headers=headers)
        resp.raise_for_status()
        hashtags = resp.json()["choices"][0]["message"]["content"].strip()
        return hashtags
    except Exception as e:
        logger.warning(f"Gagal membuat hashtag: {str(e)}")
        return "#TechCuan #AICuan #TikTokIndonesia #FYPIndonesia #GenZCuan"

# === AI Caption ===
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def generate_caption_openai(judul):
    logger.info(f"Memulai pembuatan caption untuk judul: {judul}")
    start_time = time.time()
    try:
        styles = [
            "Storytelling seru, seperti cerita temen",
            "Q&A cepat, jawab pertanyaan Gen Z",
            "Tips praktis, langsung bisa dipake",
            "Lucu dan menghibur, bikin ketawa",
            "Misterius, bikin penasaran"
        ]
        selected_style = random.choice(styles)
        headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
        data = {
            "model": "gpt-3.5-turbo",
            "messages": [
                {"role": "system", "content": f"Kamu adalah kreator TikTok Indonesia dengan gaya {selected_style}. Gunakan bahasa gaul dan referensi budaya lokal."},
                {"role": "user", "content": f"Buatkan narasi TikTok (max 100 kata) dan caption pendek untuk topik: {judul}. Sertakan humor atau slang Indonesia."}
            ]
        }
        resp = requests.post("https://api.openai.com/v1/chat/completions", json=data, headers=headers)
        resp.raise_for_status()
        teks = resp.json()["choices"][0]["message"]["content"].strip()
        hashtags = generate_hashtags(judul)
        caption = f"{teks}\n{hashtags} #KontenDibuatDenganAI"
        logger.info(f"Caption berhasil dibuat dengan gaya {selected_style} dalam {time.time() - start_time:.2f} detik")
        return caption
    except Exception as e:
        logger.warning(f"OpenAI gagal: {str(e)}")
        return f"🔥 {judul}! Gasskeun di TikTok! #KontenDibuatDenganAI"

# === ElevenLabs TTS ===
@retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
def elevenlabs_tts(teks, out_file):
    logger.info(f"Memulai pembuatan audio untuk teks: {teks[:20]}...")
    start_time = time.time()
    try:
        VOICE_IDS = [
            "exAV9Z2pS7V5OABHzkYk",  # Laki-laki energik
            "pNInz6obpgDQGcFmaJgB",  # Perempuan ceria
            "VR6AewLTigWG4xSOukaG",  # Netral profesional
            "MF3mGyEYCl7XYWbV9V6O",  # Remaja gaul
            "21m00Tcm4TlvDq8ikWAM",  # Karakter unik
            "AZnzlk1XvdvUeBnXmlld",  # Wanita lembut
            "TX3LPaxmHKxFdv7VOQHJ"   # Pria dalam
        ]
        selected_voice = random.choice(VOICE_IDS)
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{selected_voice}"
        headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
        data = {
            "text": teks,
            "voice_settings": {
                "stability": random.uniform(0.3, 0.6),
                "similarity_boost": 0.7,
                "style": random.uniform(0.1, 0.5)  # Efek suara
            }
        }
        resp = requests.post(url, headers=headers, json=data)
        resp.raise_for_status()
        with open(out_file, "wb") as f:
            f.write(resp.content)
        logger.info(f"Audio berhasil dibuat dengan voice {selected_voice} dalam {time.time() - start_time:.2f} detik: {out_file}")
    except Exception as e:
        logger.error(f"Gagal membuat audio: {str(e)}")
        raise

# === Efek Visual Kompleks ===
def create_particle_effect(size, duration):
    def make_frame(t):
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        for _ in range(10):
            x = random.randint(0, size[0])
            y = random.randint(0, size[1])
            draw.ellipse((x-5, y-5, x+5, y+5), fill=(255, 255, 255, 100))
        return np.array(img)
    return VideoClip(make_frame, duration=duration)

def apply_color_filter(clip, filter_type):
    if filter_type == "vintage":
        return clip.fx(vfx.colorx, 0.8).fx(vfx.lum_contrast, lum=10, contrast=0.2)
    elif filter_type == "neon":
        return clip.fx(vfx.colorx, 1.2).fx(vfx.lum_contrast, lum=20, contrast=0.3)
    elif filter_type == "cinematic":
        return clip.fx(vfx.colorx, 0.9).fx(vfx.lum_contrast, lum=5, contrast=0.1)
    return clip

# === Video Generator ===
def buat_video(judul, audio_file, out_file, teks):
    logger.info(f"Memulai pembuatan video untuk judul: {judul}")
    start_time = time.time()
    try:
        w, h = 1080, 1920
        bg_files = glob.glob("backgrounds/*.mp4") + glob.glob("backgrounds/*.jpg")
        bg_clip = (VideoFileClip(random.choice(bg_files)) if bg_files else ColorClip(size=(w,h), color=(0,0,0)))
        audio = AudioFileClip(audio_file)
        duration = audio.duration
        bg_clip = bg_clip.set_duration(duration).fx(vfx.fadein, 0.5).fx(vfx.fadeout, 0.5)

        # Musik latar
        music_files = glob.glob("music/*.mp3")
        if music_files:
            music = AudioFileClip(random.choice(music_files)).volumex(0.2).set_duration(duration)
            audio = CompositeAudioClip([audio, music])

        # Efek visual
        filter_type = random.choice(["vintage", "neon", "cinematic", "none"])
        bg_clip = apply_color_filter(bg_clip, filter_type)
        particle_clip = create_particle_effect((w, h), duration).set_opacity(0.3)

        # Teks utama
        fonts = ["Arial", "Helvetica", "Impact", "Roboto", "Comic-Sans-MS", "Montserrat", "Poppins"]
        colors = ["white", "yellow", "cyan", "red", "lime", "orange"]
        animations = [
            lambda t: ('center', t * 50 % h),
            lambda t: (t * 100 % w, 'center'),
            lambda t: ('center', h/2 + 100 * t/duration)
        ]
        selected_font = random.choice(fonts)
        selected_color = random.choice(colors)
        selected_animation = random.choice(animations)
        wrapped_judul = "\n".join(wrap(judul, 20))
        teks_utama = TextClip(wrapped_judul, fontsize=70, color=selected_color, font=selected_font, size=(w-100,None))
        teks_utama = teks_utama.set_position(selected_animation).set_duration(duration).fx(vfx.fadein, 0.3)

        # Subtitle
        subtitle_clips = [(0, duration, teks[:100])]
        subtitle_generator = lambda txt: TextClip(txt, fontsize=30, color="white", font="Arial", bg_color="black")
        subtitles = SubtitlesClip(subtitle_clips, subtitle_generator).set_position(("center", h-100)).set_duration(duration)

        # Watermark
        watermark = TextClip("@TechCuan", fontsize=30, color="white", font="Arial", bg_color="black")
        watermark = watermark.set_position(lambda t: ('right' if t < duration/2 else 'left', 'bottom')).set_duration(duration).set_opacity(0.7).fx(vfx.fadein, 0.2)

        # Komposisi video
        video = CompositeVideoClip([bg_clip, particle_clip, teks_utama, subtitles, watermark])
        video = video.set_audio(audio)
        video.write_videofile(out_file, fps=24, codec="libx264")
        logger.info(f"Video berhasil dibuat dalam {time.time() - start_time:.2f} detik: {out_file}")
    except Exception as e:
        logger.error(f"Gagal membuat video: {str(e)}")
        raise

# === Proses Konten ===
def proses_konten():
    judul = random.choice(scrape_public_trends())
    logger.info(f"Memulai proses konten untuk judul: {judul}")
    start_time = time.time()
    try:
        check_storage()
        cleanup_logs()
        teks = generate_caption_openai(judul)
        audio_path = f"temp_audio/{judul[:10].replace(' ','_')}.mp3"
        video_path = f"videos/{judul[:10].replace(' ','_')}.mp4"
        elevenlabs_tts(teks, audio_path)
        buat_video(judul, audio_path, video_path, teks)
        
        video_filename = os.path.basename(video_path)
        video_url = f"{SERVER_URL}/videos/{video_filename}"
        engagement = analyze_engagement(video_url)  # Analitik awal
        log_performance(judul, teks.split('\n')[-1], waktu_wib(), engagement)
        
        interaction_suggestions = [
            "Tonton 5-10 video di For You Page.",
            "Sukai 2-3 video terkait teknologi.",
            "Komentari 1-2 video dengan 'Keren!'",
            "Share 1 video ke WhatsApp."
        ]
        notifikasi = (
            f"📹 Video siap diunggah!\n"
            f"Link: {video_url}\n"
            f"Caption: {teks[:90]}\n"
            f"Engagement Awal: {engagement}\n"
            f"Saran Interaksi:\n- {random.choice(interaction_suggestions)}\n"
            f"Tasker akan mengunggah otomatis ke TikTok!"
        )
        kirim_telegram(notifikasi)
        
        cleanup_temp_files(audio_path, video_path)
        logger.info(f"Proses konten selesai dalam {time.time() - start_time:.2f} detik")
    except Exception as e:
        logger.error(f"Proses konten gagal: {str(e)}")
        kirim_telegram(f"❌ Gagal proses konten: {str(e)}")

# === Penjadwalan Adaptif ===
def schedule_content():
    optimal_time = get_optimal_upload_time()
    logger.info(f"Menjadwalkan konten pada {optimal_time}")
    schedule.every().day.at(optimal_time).do(proses_konten)
    kirim_telegram(f"🕒 Konten dijadwalkan pada {optimal_time} WIB")

# === Main Loop ===
if __name__ == "__main__":
    logger.info("Techcuan AI Pro aktif 24/7")
    kirim_telegram("🤖 Techcuan AI Pro aktif 24/7!")
    
    for _ in range(3):
        schedule_content()
    
    from threading import Thread
    def run_flask():
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    flask_thread = Thread(target=run_flask)
    flask_thread.start()
    
    while True:
        schedule.run_pending()
        time.sleep(10)
