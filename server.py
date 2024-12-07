import os
from flask import Flask, request, jsonify, send_from_directory, render_template
from backend import process_all
import tempfile
import shutil
import logging
import traceback

app = Flask(__name__, template_folder='templates', static_folder='static')
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

CURRENT_PROGRESS = 0
LOG_MESSAGES = []
RESULT_FILE = None
PROCESS_RUNNING = False

def log_callback(msg):
    LOG_MESSAGES.append(msg)
    print(msg)
    logging.info(msg)

def progress_callback(value, total):
    global CURRENT_PROGRESS
    CURRENT_PROGRESS = int((value/total)*100)

@app.route('/')
def root():
    return render_template('index.html')

@app.route('/start', methods=['POST'])
def start_process():
    global CURRENT_PROGRESS, LOG_MESSAGES, RESULT_FILE, PROCESS_RUNNING

    logging.basicConfig(
        filename='moziokosi.log',
        filemode='w',
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    LOG_MESSAGES.clear()
    CURRENT_PROGRESS = 0
    RESULT_FILE = None

    if PROCESS_RUNNING:
        return jsonify({"error": "Process already running"}), 400

    PROCESS_RUNNING = True

    base_path = request.form.get('base_path','')
    ueda_mode = request.form.get('ueda_mode','false') == 'true'
    transcribe_prompt = request.form.get('transcribe_prompt','')
    summarize_prompt = request.form.get('summarize_prompt','')
    categorize_prompt = request.form.get('categorize_prompt','')
    split_method = request.form.get('split_method','duration')
    split_value = request.form.get('split_value','60')
    try:
        split_value = int(split_value)
    except:
        split_value = 60
    transcribe_model = request.form.get('transcribe_model','whisper')

    input_url = request.form.get('input_url','')
    file_path = ''
    temp_dir = tempfile.mkdtemp()

    try:
        if input_url.strip():
            input_url = input_url.strip('"')
            input_path = input_url
        else:
            if 'input_file' not in request.files:
                PROCESS_RUNNING = False
                err_msg = "No file uploaded or no input_url given"
                log_callback(f"[ERROR] {err_msg}")
                return jsonify({"error":err_msg}),400
            uploaded_file = request.files['input_file']
            file_path = os.path.join(temp_dir, uploaded_file.filename)
            uploaded_file.save(file_path)
            log_callback(f"[INFO] ファイル受信: {file_path}")
            input_path = file_path

        from threading import Thread
        def background_task():
            global RESULT_FILE, PROCESS_RUNNING
            try:
                result = process_all(
                    input_path,
                    base_path,
                    ueda_mode=ueda_mode,
                    progress_callback=progress_callback,
                    log_callback=log_callback,
                    transcribe_prompt=transcribe_prompt,
                    summarize_prompt=summarize_prompt,
                    categorize_prompt=categorize_prompt,
                    split_method=split_method,
                    split_value=split_value,
                    transcribe_model=transcribe_model
                )
                if "result_file" in result:
                    RESULT_FILE = result["result_file"]
            except Exception as e:
                tb = traceback.format_exc()
                log_callback(f"[ERROR] 処理中例外: {str(e)}\n{tb}")
            finally:
                PROCESS_RUNNING = False
                shutil.rmtree(temp_dir, ignore_errors=True)

        t = Thread(target=background_task)
        t.start()
        return jsonify({"status":"started"}), 200
    except Exception as e:
        tb = traceback.format_exc()
        PROCESS_RUNNING = False
        shutil.rmtree(temp_dir, ignore_errors=True)
        log_callback(f"[ERROR] /startエンドポイント処理中エラー: {str(e)}\n{tb}")
        return jsonify({"error":str(e)}),500

@app.route('/progress', methods=['GET'])
def get_progress():
    global CURRENT_PROGRESS, PROCESS_RUNNING
    return jsonify({"progress": CURRENT_PROGRESS, "running": PROCESS_RUNNING})

@app.route('/logs', methods=['GET'])
def get_logs():
    return jsonify({"logs": LOG_MESSAGES})

@app.route('/result', methods=['GET'])
def get_result():
    global RESULT_FILE
    if RESULT_FILE and os.path.exists(RESULT_FILE):
        dirname = os.path.dirname(RESULT_FILE)
        filename = os.path.basename(RESULT_FILE)
        return send_from_directory(dirname, filename, as_attachment=True, mimetype='application/json')
    return jsonify({"error":"No result"}), 404

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)