// chip.js – Handles mic, audio, GPT response, and Chip's playback logic

let mediaRecorder;
let audioChunks = [];

document.addEventListener("DOMContentLoaded", () => {
  const recordBtn = document.getElementById("recordBtn");
  const recordPrompt = document.getElementById("recordPrompt");
  const caption = document.getElementById("caption");
  const statusDiv = document.getElementById("status");

  recordBtn.addEventListener("click", async () => {
    recordBtn.disabled = true;
    recordPrompt.classList.remove("idle");
    recordPrompt.classList.add("listening");
    recordBtn.classList.remove("blinking");

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      mediaRecorder = new MediaRecorder(stream);
      audioChunks = [];

      mediaRecorder.ondataavailable = (e) => audioChunks.push(e.data);

      mediaRecorder.onstop = async () => {
        const audioBlob = new Blob(audioChunks, { type: "audio/webm" });
        const formData = new FormData();
        formData.append("audio", audioBlob, "input.webm");

        statusDiv.textContent = "🧠 Chip is thinking...";
        recordPrompt.innerText = "🤔 Processing...";
        recordPrompt.classList.remove("listening");

        try {
          const res = await fetch("/ask-chip", { method: "POST", body: formData });
          const reader = res.body.getReader();
          const decoder = new TextDecoder();

          let audioUrl = "";
          let visemes = [];
          let textTranscript = "";

          while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const text = decoder.decode(value);
            const lines = text.split("--frame").filter(Boolean);

            for (const line of lines) {
              const [, headers, content] = line.split(/\r?\n\r?\n/);
              if (!headers || !content) continue;

              const contentType = headers.match(/Content-Type: (.+)/)?.[1];
              if (contentType === "application/json") {
                const data = JSON.parse(content);
                if (data.viseme_timestamps) visemes = data.viseme_timestamps;
                if (data.audio) audioUrl = data.audio;
                if (data.transcript) {
                  textTranscript = data.transcript;
                  caption.textContent = textTranscript;
                  caption.style.visibility = "visible";
                  caption.style.display = "block";
                  setTimeout(() => { caption.style.display = "none"; }, 4000);
                  statusDiv.textContent = "🗣️ You said: " + textTranscript;
                }
              }
            }
          }

          if (audioUrl) {
            const audio = new Audio(audioUrl);
            syncVisemes(visemes, audio);
            audio.play();
            // Optionally trigger viseme sync here with visemes
          } else {
            statusDiv.textContent = "⚠️ Chip could not respond.";
          }

        } catch (err) {
          console.error("❌ Chip error:", err);
          statusDiv.textContent = "❌ Chip failed to respond.";
        } finally {
          recordBtn.disabled = false;
          recordPrompt.classList.add("idle");
        }
      };

      mediaRecorder.start();
      setTimeout(() => mediaRecorder.stop(), 5000);
    } catch (err) {
      console.error("❌ Mic access error:", err);
      statusDiv.textContent = "❌ Mic access failed.";
      recordBtn.disabled = false;
    }
  });
});
