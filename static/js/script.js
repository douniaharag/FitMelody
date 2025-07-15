document.addEventListener("DOMContentLoaded", async () => {
  try {
    const res = await fetch("/biometrics");
    const data = await res.json();

    document.getElementById("date-val").textContent = data.date || "-";
    document.getElementById("time-val").textContent = data.time || "-";
    document.getElementById("bpm-val").textContent = data.bpm || "-";
    document.getElementById("cal-val").textContent = data.cal || "-";
    document.getElementById("sedentary-val").textContent = data.sedentary || "-";
    document.getElementById("steps-val").textContent = data.steps || "-";
    document.getElementById("asleep-val").textContent = data.asleep || "-";
    document.getElementById("eff-val").textContent = data.eff || "-";
    document.getElementById("rem-val").textContent = data.rem || "-";
    document.getElementById("deep-val").textContent = data.deep || "-";
    document.getElementById("wake-val").textContent = data.wake || "-";
  } catch (error) {
    console.error("Erreur lors du chargement des données :", error);
    document.getElementById("status-msg").textContent = "❌ Impossible de charger les données Fitbit.";
  }
});

let lastPrompt = "";
let lastInputText = "";

document.getElementById("generate-music-btn").addEventListener("click", async () => {
  const statusMsg = document.getElementById("status-msg");
  statusMsg.textContent = "🎶 Génération en cours... Veuillez patienter...";
  statusMsg.style.color = "#fab1a0";

  const biometricData = {
    date: document.getElementById("date-val").textContent,
    time: document.getElementById("time-val").textContent,
    bpm: document.getElementById("bpm-val").textContent,
    cal: document.getElementById("cal-val").textContent,
    sedentary: document.getElementById("sedentary-val").textContent,
    steps: document.getElementById("steps-val").textContent,
    asleep: document.getElementById("asleep-val").textContent,
    eff: document.getElementById("eff-val").textContent,
    rem: document.getElementById("rem-val").textContent,
    deep: document.getElementById("deep-val").textContent,
    wake: document.getElementById("wake-val").textContent
  };

  try {
    const response = await fetch("/generate_music", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(biometricData),
    });

    const result = await response.json();
    const audioPlayer = document.getElementById("audio-player");
    const audioSrc = document.getElementById("audio-src");

    if (result.status === "success") {
      audioSrc.src = result.url;
      audioPlayer.style.display = "block";
      audioPlayer.style.opacity = 0;
      audioPlayer.load();

      setTimeout(() => {
        audioPlayer.play();
        audioPlayer.style.transition = "opacity 1.2s ease";
        audioPlayer.style.opacity = 1;
        statusMsg.textContent = "✅ Musique générée avec succès ! Profitez-en 🎧";
        statusMsg.style.color = "#55efc4";
        document.getElementById("track-name").textContent = result.filename;
      }, 1000);

      // ✅ Stocker pour feedback plus tard
      lastPrompt = result.prompt;
      lastInputText = result.input_text;

    } else {
      statusMsg.textContent = "❌ Erreur : " + result.message;
      statusMsg.style.color = "#ff7675";
    }
  } catch (err) {
    console.error("Erreur lors de la génération :", err);
    statusMsg.textContent = "❌ Une erreur est survenue pendant la génération.";
    statusMsg.style.color = "#ff7675";
  }
});

// 🎧 Feedback utilisateur (avec envoi vers /submit_feedback)
document.querySelectorAll(".feedback-star").forEach((star, index) => {
  star.addEventListener("click", async () => {
    const rating = index + 1;

    // Activer les étoiles visuellement
    document.querySelectorAll(".feedback-star").forEach((s, i) => {
      s.classList.toggle("active", i <= index);
    });

    // ✅ Afficher la note
    document.getElementById("feedback-result").textContent = `Merci pour votre note : ${rating} étoile(s)`;
    document.getElementById("feedback-result").style.color = "#00b894";

    // ✅ Envoyer le feedback complet
    try {
      const res = await fetch("/submit_feedback", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          input_text: lastInputText,
          music_prompt: lastPrompt,
          score: rating
        })
      });

      const result = await res.json();
      console.log("✅ Feedback envoyé :", result.message);
    } catch (err) {
      console.error("❌ Erreur lors de l’envoi du feedback :", err);
      document.getElementById("feedback-result").textContent = "Erreur lors de l'envoi du feedback.";
      document.getElementById("feedback-result").style.color = "#d63031";
    }
  });
});
