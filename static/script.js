document.addEventListener('DOMContentLoaded', () => {
    const startBtn = document.getElementById('startBtn');
    const inputFile = document.getElementById('input_file');
    const inputUrl = document.getElementById('input_url');
    const basePath = document.getElementById('base_path');
    const uedaMode = document.getElementById('ueda_mode');
    const transcribePrompt = document.getElementById('transcribe_prompt');
    const summarizePrompt = document.getElementById('summarize_prompt');
    const categorizePrompt = document.getElementById('categorize_prompt');
    const splitMethod = document.getElementById('split_method');
    const splitValue = document.getElementById('split_value');
    const transcribeModel = document.getElementById('transcribe_model');
    
    const processingCard = document.getElementById('processingCard');
    const resultCard = document.getElementById('resultCard');
    const logArea = document.getElementById('logArea');
    const progressBar = document.getElementById('progressBar');
    const progressValue = document.getElementById('progressValue');
    const progressText = document.getElementById('progressText');
    const resultOutput = document.getElementById('resultOutput');
    
    const copyResultBtn = document.getElementById('copyResultBtn');
    const downloadResultBtn = document.getElementById('downloadResultBtn');
  
    let pollInterval = null;
  
    document.querySelector('.theme-toggle').addEventListener('click', () => {
      document.body.classList.toggle('light-mode');
    });
  
    startBtn.addEventListener('click', () => {
      if(!inputUrl.value.trim() && !inputFile.files.length) {
        alert("ファイルかURLを指定してください。");
        return;
      }
  
      const formData = new FormData();
      if(inputUrl.value.trim()) {
        formData.append('input_url', inputUrl.value.trim());
      } else {
        formData.append('input_file', inputFile.files[0]);
      }
      formData.append('base_path', basePath.value.trim());
      formData.append('ueda_mode', uedaMode.checked);
      formData.append('transcribe_prompt', transcribePrompt.value);
      formData.append('summarize_prompt', summarizePrompt.value);
      formData.append('categorize_prompt', categorizePrompt.value);
      formData.append('split_method', splitMethod.value);
      formData.append('split_value', splitValue.value);
      formData.append('transcribe_model', transcribeModel.value);
  
      fetch('/start', {
        method:'POST',
        body: formData
      }).then(res=>res.json())
      .then(data=>{
        if(data.status=="started") {
          showToast("処理開始しました!");
          appendLog("[INFO] 処理開始");
          processingCard.style.display = 'block';
          scrollIntoView(processingCard);
          startPolling();
        } else {
          appendLog("[ERROR] "+JSON.stringify(data));
        }
      })
      .catch(e=>{
        appendLog("[ERROR] "+e);
      });
    });
  
    function appendLog(msg) {
      logArea.textContent += msg+"\n";
      logArea.scrollTop = logArea.scrollHeight;
    }
  
    function startPolling() {
      if(pollInterval) return;
      pollInterval = setInterval(()=>{
        updateProgress();
        updateLogs();
        checkCompletion();
      },1000);
    }
  
    function stopPolling() {
      if(pollInterval) {
        clearInterval(pollInterval);
        pollInterval = null;
      }
    }
  
    function updateProgress() {
      fetch('/progress')
        .then(r=>r.json())
        .then(data=>{
          const pct = data.progress || 0;
          const running = data.running;
          progressBar.style.width = pct+"%";
          progressValue.textContent = pct+"%";
          progressText.textContent = running ? "処理中..." : "完了";
        });
    }
  
    function updateLogs() {
      fetch('/logs')
        .then(r=>r.json())
        .then(data=>{
          const logs = data.logs;
          logArea.textContent = logs.join("\n")+"\n";
          logArea.scrollTop = logArea.scrollHeight;
        });
    }
  
    function checkCompletion() {
      fetch('/progress')
        .then(r=>r.json())
        .then(data=>{
          if(!data.running) {
            stopPolling();
            appendLog("[INFO] 処理終了。結果を取得します...");
            fetch('/result')
              .then(r=>{
                if(r.status==200) {
                  return r.blob();
                } else {
                  throw "No result available";
                }
              })
              .then(blob=>{
                const reader = new FileReader();
                reader.onload = function(e) {
                  const resultText = e.target.result;
                  resultOutput.textContent = resultText;
                  Prism.highlightAll();
                  resultCard.style.display = 'block';
                  scrollIntoView(resultCard);
                  showToast("結果を取得しました");
                }
                reader.readAsText(blob);
              })
              .catch(e=>{
                appendLog("[WARNING] 結果ファイル取得失敗: "+e);
                showToast("結果取得に失敗しました", true);
              });
          }
        });
    }
  
    copyResultBtn.addEventListener('click', () => {
      const text = resultOutput.textContent;
      navigator.clipboard.writeText(text).then(()=>{
        showToast("クリップボードにコピーしました！");
      });
    });
  
    downloadResultBtn.addEventListener('click', () => {
      const text = resultOutput.textContent;
      const blob = new Blob([text], {type:'application/json'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = 'result.json';
      a.click();
      URL.revokeObjectURL(url);
      showToast("結果ファイルをダウンロードしました！");
    });
  
    function showToast(msg, isError=false) {
      let toast = document.createElement('div');
      toast.className = 'toast';
      toast.textContent = msg;
      if(isError) {
        toast.style.background = 'var(--error-color)';
        toast.style.color = '#fff';
      }
      document.body.appendChild(toast);
  
      setTimeout(()=>{
        toast.classList.add('show');
      },50);
  
      setTimeout(()=>{
        toast.classList.remove('show');
        setTimeout(()=>{
          toast.remove();
        },500);
      },3000);
    }
  
    function scrollIntoView(el) {
      el.scrollIntoView({behavior:'smooth', block:'start'});
    }
  });
