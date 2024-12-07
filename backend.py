import os
import time
import subprocess
import json
import tempfile
import shutil
import platform
import math
import pathlib
import logging
import traceback

import google.generativeai as genai
import openai
from moviepy.editor import VideoFileClip, AudioFileClip
import yt_dlp

MODEL_NAME_GEMINI = "gemini-1.5-pro"
WHISPER_MAX_SIZE_MB = 24  # Whisper: 24MB

DEFAULT_TRANSCRIBE_PROMPT = """あなたはプロフェッショナルな日本語文字起こしエージェントです。
音声は複数クリップに分割されますが後で結合されます。
【要件】
- 話者A/Bで区別
- 専門用語(補足)
- [不明瞭][ノイズ]
- 分割点で不自然な終わり→[不明瞭]
- 後結合を想定し統一フォーマット維持
"""

DEFAULT_SUMMARIZE_PROMPT = """あなたはプロフェッショナルなサマリー作成エージェントです。
以下は結合後文字起こし。
重要事項を簡潔要約:
- 話者毎要旨箇条書き/時系列
- 専門用語は(補足)
- 冗長表現省略
"""

DEFAULT_CATEGORIZE_PROMPT = """あなたはテキスト解析エージェント。
テキストから状況、参加者属性、出力形式判定しJSON出力:
{
  "situation": "会議/商談/インタビュー/講演/その他",
  "attendee_attributes": ["属性1","属性2",...],
  "output_format": "要約/詳細な議事録/商談のフィードバック/講演レポート/その他"
}
"""

def setup_api_keys():
    genai.configure(api_key=os.environ.get("GENAI_API_KEY"))
    openai.api_key = os.environ.get("OPENAI_API_KEY")

def youtube_download(url, output_dir):
    ydl_opts = {
        'outtmpl': os.path.join(output_dir, '%(title)s.%(ext)s'),
        'format': 'bestaudio/best',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp4',
            'preferredquality': '192'
        }]
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filename)
        possible_ext = ['.mp3', '.m4a', '.wav', '.webm', '.opus', '.mp4']
        for ext in possible_ext:
            if os.path.exists(base + ext):
                return base + ext
        return filename

def generate_gemini_content(prompts):
    model = genai.GenerativeModel(MODEL_NAME_GEMINI)
    response = model.generate_content(prompts)
    return response.text

def proofread_transcription_with_gemini(transcription_text, log_callback):
    proofread_prompt = """あなたは日本語文章校正エージェントです。
以下は全クリップ結合後テキスト。誤字脱字・文脈不自然箇所を補正し自然な表現にして下さい。
"""
    try:
        response = generate_gemini_content([proofread_prompt, transcription_text])
        return response
    except Exception as e:
        tb = traceback.format_exc()
        log_callback(f"[ERROR] Gemini校正エラー: {str(e)}\n{tb}")
        return transcription_text

def summarize_and_categorize_transcriptions(transcription_text, summary_prompt, categorize_prompt, log_callback):
    try:
        summarized_content = generate_gemini_content([summary_prompt, transcription_text])
    except Exception as e:
        tb = traceback.format_exc()
        log_callback(f"[ERROR] 要約エラー: {str(e)}\n{tb}")
        summarized_content = transcription_text

    try:
        categorize_response = generate_gemini_content([categorize_prompt, summarized_content])
        response_text = categorize_response.strip()
        json_start = response_text.find('{')
        json_end = response_text.rfind('}') + 1
        if json_start >= 0 and json_end > json_start:
            json_str = response_text[json_start:json_end]
            try:
                categorization_data = json.loads(json_str)
            except:
                categorization_data = {"situation":"不明","attendee_attributes":["不明"],"output_format":"詳細な議事録"}
        else:
            categorization_data = {"situation":"不明","attendee_attributes":["不明"],"output_format":"詳細な議事録"}
    except Exception as e:
        tb = traceback.format_exc()
        log_callback(f"[ERROR] 分類エラー: {str(e)}\n{tb}")
        categorization_data = {"situation":"不明","attendee_attributes":["不明"],"output_format":"詳細な議事録"}

    return summarized_content, categorization_data

def transcribe_chunk_whisper(audio_path, log_callback):
    try:
        with open(audio_path, "rb") as audio_file:
            response = openai.Audio.transcribe(
                model="whisper-1",
                file=audio_file,
                language="ja"
            )
            return response.get('text', '')
    except Exception as e:
        tb = traceback.format_exc()
        log_callback(f"[ERROR] Whisper詳細エラー: {str(e)}\n{tb}")
        return ""

def transcribe_with_gemini(audio_path, log_callback):
    # Geminiで文字起こし（2GB対応）、File APIでアップ後generate_contentで文字起こし指示
    try:
        file_obj = genai.upload_file(pathlib.Path(audio_path))
        start_t = time.time()
        while True:
            file_obj = genai.get_file(file_obj.name)
            if file_obj.state.name == "ACTIVE":
                break
            if time.time() - start_t > 300:
                log_callback("[ERROR] GeminiファイルACTIVE待機タイムアウト")
                return ""
            if file_obj.state.name == "FAILED":
                log_callback("[ERROR] Geminiファイル状態FAILED")
                return ""
            time.sleep(5)
        prompt = "以下の音声を正確に文字起こししてください。話者分けや聞き取り困難部分は[不明瞭]表記:"
        res = genai.GenerativeModel(MODEL_NAME_GEMINI).generate_content([file_obj, prompt])
        return res.text
    except Exception as e:
        tb = traceback.format_exc()
        log_callback(f"[ERROR] Gemini文字起こしエラー: {str(e)}\n{tb}")
        return ""

def handle_413_error_whisper(chunk_path, log_callback):
    durations = [30,10,5,1]
    for d in durations:
        log_callback(f"[INFO] 再分割: {d}秒単位で再トライ {chunk_path}")
        new_chunks = split_video_by_duration(chunk_path, d, log_callback)
        results = []
        success = True
        for nc in new_chunks:
            text = transcribe_chunk_whisper(nc, log_callback)
            if "Maximum content size limit" in text:
                success = False
                log_callback(f"[ERROR] {nc}でも413発生、諦め")
                text = ""
            if not text.strip():
                success = False
                log_callback(f"[WARNING] {nc}文字起こし空")
            results.append(text)
            os.remove(nc)
        if success:
            return "\n".join(results)
    log_callback("[ERROR] 全フォールバック失敗")
    return ""

def split_video_by_duration(input_path, segment_duration, log_callback):
    out_files = []
    with VideoFileClip(input_path) as video:
        duration = video.duration
        for start in range(0, int(duration), segment_duration):
            end = min(start+segment_duration, duration)
            temp_seg = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
            seg_path = temp_seg.name
            temp_seg.close()
            cmd = [
                'ffmpeg', '-y', '-i', input_path,
                '-ss', str(start), '-t', str(end-start),
                '-c:v', 'libx264', '-preset', 'ultrafast',
                '-c:a', 'aac', '-strict', '-2',
                seg_path
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            out_files.append(seg_path)
    return out_files

def transcribe_large_file_whisper(path, log_callback, split_value):
    size_bytes = os.path.getsize(path)
    if size_bytes <= WHISPER_MAX_SIZE_MB * 1024 * 1024:
        text = transcribe_chunk_whisper(path, log_callback)
        if "Maximum content size limit" in text:
            return handle_413_error_whisper(path, log_callback)
        return text
    else:
        log_callback(f"[INFO] {path} > {WHISPER_MAX_SIZE_MB}MB, {split_value}秒刻み分割")
        initial_chunks = split_video_by_duration(path, split_value, log_callback)
        final_results = []
        for ic in initial_chunks:
            chunk_size = os.path.getsize(ic)
            if chunk_size > WHISPER_MAX_SIZE_MB * 1024 * 1024:
                partial_text = handle_413_error_whisper(ic, log_callback)
                final_results.append(partial_text)
            else:
                t = transcribe_chunk_whisper(ic, log_callback)
                if "Maximum content size limit" in t:
                    t = handle_413_error_whisper(ic, log_callback)
                final_results.append(t)
            os.remove(ic)
        return "\n".join(final_results)

def apply_prompt_if_empty(user_prompt, default_prompt):
    if not user_prompt.strip():
        return default_prompt
    return user_prompt

def process_all(input_url_or_path, base_path, ueda_mode=False,
                progress_callback=None, log_callback=None,
                transcribe_prompt="",
                summarize_prompt="",
                categorize_prompt="",
                split_method="duration",
                split_value=60,
                transcribe_model="whisper"):

    if log_callback is None:
        log_callback = lambda x: None
    if progress_callback is None:
        progress_callback = lambda val, tot: None

    transcribe_prompt = apply_prompt_if_empty(transcribe_prompt, DEFAULT_TRANSCRIBE_PROMPT)
    summarize_prompt = apply_prompt_if_empty(summarize_prompt, DEFAULT_SUMMARIZE_PROMPT)
    categorize_prompt = apply_prompt_if_empty(categorize_prompt, DEFAULT_CATEGORIZE_PROMPT)

    setup_api_keys()

    def log(msg):
        log_callback(msg)
        logging.info(msg)

    def prog(val):
        progress_callback(val, 100)

    log("[INFO] 処理開始")
    prog(0)

    temp_dir = tempfile.mkdtemp()
    log("[INFO] 一時ディレクトリ作成")

    if input_url_or_path.startswith("http"):
        log("[INFO] YouTubeダウンロード中...")
        downloaded_path = youtube_download(input_url_or_path, temp_dir)
        input_path = downloaded_path
        log("[INFO] ダウンロード完了")
    else:
        input_path = input_url_or_path

    if not base_path.strip():
        home = os.path.expanduser("~")
        base_path = os.path.join(home, "Downloads")
    base_path = base_path.strip('"').strip()
    base_path = os.path.normpath(base_path)

    log("[INFO] 文字起こしモデル:" + transcribe_model)
    if transcribe_model == "whisper":
        log("[INFO] Whisperで文字起こし開始...")
        text = transcribe_large_file_whisper(input_path, log, split_value)
    else:
        log("[INFO] Geminiで文字起こし開始...")
        text = transcribe_with_gemini(input_path, log)

    if not text.strip():
        log("[ERROR] 文字起こし失敗")
        shutil.rmtree(temp_dir, ignore_errors=True)
        return {"error":"transcription failed"}

    prog(30)
    log("[INFO] 校正処理(Gemini)...")
    proofread_text = proofread_transcription_with_gemini(text, log)
    prog(50)

    log("[INFO] 要約・分類処理...")
    summarized_content, categorization_data = summarize_and_categorize_transcriptions(proofread_text, summarize_prompt, categorize_prompt, log)
    prog(70)

    os.makedirs(base_path, exist_ok=True)
    result_data = {
        "proofread_transcription": proofread_text,
        "summary": summarized_content,
        "metadata": categorization_data
    }
    result_file = os.path.join(base_path, "result.json")
    try:
        with open(result_file, "w", encoding="utf-8") as f:
            json.dump(result_data, f, ensure_ascii=False, indent=4)
        log(f"[INFO] 結果保存: {result_file}")
        prog(100)
    except Exception as e:
        tb = traceback.format_exc()
        log(f"[ERROR] 結果ファイル保存失敗: {str(e)}\n{tb}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    log("[INFO] 完了")
    return {"result_file": result_file, "summary": summarized_content, "metadata": categorization_data}
