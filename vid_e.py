import os
import logging
import subprocess
import shutil
import time
import requests
from flask import Flask, request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler
)
from elevenlabs.client import ElevenLabs
from elevenlabs import save

# ================== CONFIGURATION ==================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Load tokens from environment (set in Vercel dashboard)
TOKEN = os.getenv("BOT_TOKEN")
ELEVENLABS_API_KEY = os.getenv("ELEVEN_API_KEY")
client = ElevenLabs(api_key=ELEVENLABS_API_KEY)

# Voice options
VOICE_OPTIONS = {
    'rachel': 'pNInz6obpgDQGcFmaJgB',
    'adam': 'pVnrL6sighQX7hVz89cp',
    'alex': 'GzE4TcXfh9rYCU9gVgPp',
    'viraj': 'nPczCjzI2devNBz1zQrb',
    'rahul': 'nPczCjzI2devNBz1zQrb',
    'sam': '93nuHbke4dTER9x2pDwE'
}

BASE_DIR = 'voice_generator_temp'
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_TEXT_LENGTH = 5000
user_sessions = {}

# ================== UTILITY FUNCTIONS ==================
def get_media_duration(file_path: str) -> float:
    try:
        cmd = ['ffprobe', '-v', 'error', '-show_entries',
               'format=duration', '-of',
               'default=noprint_wrappers=1:nokey=1', file_path]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return float(result.stdout.strip())
    except Exception:
        return 0.0

def cleanup_user_files(chat_id: str):
    try:
        user_dir = os.path.join(BASE_DIR, str(chat_id))
        if os.path.exists(user_dir):
            shutil.rmtree(user_dir)
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")

def generate_voice_from_text(text: str, output_path: str, voice_id: str, language: str = 'en') -> bool:
    try:
        audio = client.generate(
            text=text,
            voice=voice_id,
            model="eleven_multilingual_v2",
            voice_settings={
                "stability": 0.3,
                "similarity_boost": 0.9,
                "style": 0.4,
                "speaker_boost": True
            }
        )
        save(audio, output_path)
        return True
    except Exception as e:
        logger.error(f"Voice generation error: {str(e)}")
        return False

# ================== COMMAND HANDLERS ==================
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    user_sessions[chat_id] = {
        'state': 'awaiting_video',
        'video_path': None,
        'video_duration': 0,
        'selected_voice': None,
        'language': 'en'
    }
    os.makedirs(os.path.join(BASE_DIR, chat_id), exist_ok=True)
    await update.message.reply_text(
        "🎬 Welcome to Voice Generator Bot!\n\n"
        "Send me a video file (max 50MB) to begin."
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Use /start to begin.\nSend a video, choose a voice, then text → get AI voice video.")

async def voices_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    voices_text = "🎤 Voices:\nRachel, Adam, Alex, Rahul, Sam, Viraj"
    await update.message.reply_text(voices_text)

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id in user_sessions:
        del user_sessions[chat_id]
        cleanup_user_files(chat_id)
    await update.message.reply_text("❌ Cancelled. Send /start to restart.")

# ================== VIDEO HANDLER ==================
async def handle_video_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id not in user_sessions or user_sessions[chat_id]['state'] != 'awaiting_video':
        await update.message.reply_text("⚠️ Send /start first.")
        return
    video = update.message.video or update.message.document
    if not video or video.file_size > MAX_FILE_SIZE:
        await update.message.reply_text("⚠️ Invalid or too large video.")
        return
    await update.message.reply_text("⏳ Downloading video...")
    user_dir = os.path.join(BASE_DIR, chat_id)
    video_path = os.path.join(user_dir, f"input_{int(time.time())}.mp4")
    file = await video.get_file()
    await file.download_to_drive(video_path)
    duration = get_media_duration(video_path)
    if duration == 0:
        await update.message.reply_text("❌ Could not process video.")
        return
    user_sessions[chat_id].update({'video_path': video_path, 'video_duration': duration, 'state': 'choosing_voice'})
    keyboard = [
        [InlineKeyboardButton("Rachel", callback_data="voice_rachel")],
        [InlineKeyboardButton("Adam", callback_data="voice_adam")],
        [InlineKeyboardButton("Alex", callback_data="voice_alex")],
        [InlineKeyboardButton("Rahul", callback_data="voice_rahul")],
        [InlineKeyboardButton("Sam", callback_data="voice_sam")],
        [InlineKeyboardButton("Viraj", callback_data="voice_viraj")]
    ]
    await update.message.reply_text("Select a voice:", reply_markup=InlineKeyboardMarkup(keyboard))

# ================== CALLBACK HANDLER ==================
async def handle_voice_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    chat_id = str(query.message.chat.id)
    voice_name = query.data.split('_')[1]
    if chat_id not in user_sessions:
        await query.edit_message_text("⚠️ Session expired. /start again.")
        return
    user_sessions[chat_id]['selected_voice'] = VOICE_OPTIONS[voice_name]
    user_sessions[chat_id]['state'] = 'awaiting_text'
    user_sessions[chat_id]['language'] = 'hi' if voice_name == 'viraj' else 'en'
    await query.edit_message_text(f"✅ Voice selected: {voice_name}. Now send me the text.")

# ================== TEXT HANDLER ==================
async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    text = update.message.text.strip()
    if chat_id not in user_sessions or user_sessions[chat_id]['state'] != 'awaiting_text':
        await update.message.reply_text("Send /start to begin.")
        return
    if len(text) > MAX_TEXT_LENGTH:
        await update.message.reply_text("❌ Text too long.")
        return
    await update.message.reply_text("🎤 Generating voice...")
    user_dir = os.path.join(BASE_DIR, chat_id)
    audio_path = os.path.join(user_dir, f"tts_{int(time.time())}.mp3")
    success = generate_voice_from_text(text, audio_path, user_sessions[chat_id]['selected_voice'], user_sessions[chat_id]['language'])
    if not success:
        await update.message.reply_text("❌ Voice generation failed.")
        return
    await update.message.reply_text("✅ Voice ready! (Video combining logic here...)")

# ================== INIT APP ==================
application = Application.builder().token(TOKEN).build()
application.add_handler(CommandHandler("start", start_command))
application.add_handler(CommandHandler("help", help_command))
application.add_handler(CommandHandler("voices", voices_command))
application.add_handler(CommandHandler("cancel", cancel_command))
application.add_handler(CallbackQueryHandler(handle_voice_selection))
application.add_handler(MessageHandler((filters.VIDEO | filters.Document.ALL), handle_video_file))
application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

# ================== FLASK APP (for Vercel) ==================
flask_app = Flask(__name__)

@flask_app.route("/", methods=["GET"])
def index():
    return "🤖 Voice Generator Bot is running on Vercel!"

@flask_app.route("/webhook", methods=["POST"])
async def webhook():
    try:
        update = Update.de_json(request.get_json(force=True), application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    return "ok"