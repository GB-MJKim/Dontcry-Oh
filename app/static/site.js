(() => {
  const overlay = document.getElementById("loadingFloat");
  const titleEl = document.getElementById("loadingTitle");
  const subtitleEl = document.getElementById("loadingSubtitle");
  const percentEl = document.getElementById("loadingPercent");
  const elapsedEl = document.getElementById("loadingElapsed");
  const progressBarEl = document.getElementById("loadingProgressBar");
  const stepsEl = document.getElementById("loadingSteps");
  if (!overlay || !titleEl || !subtitleEl || !percentEl || !elapsedEl || !progressBarEl || !stepsEl) {
    return;
  }

  const taskConfigs = {
    inspect: {
      title: "PDF 검수 실행 중",
      uploadText: "PDF 파일을 업로드하고 있습니다.",
      processingTitle: "PDF 검수 진행 중",
      stages: [
        { label: "PDF 정리", detail: "페이지와 상품 카드 영역을 정리하고 있습니다.", progress: 38 },
        { label: "AI 추출", detail: "AI가 상품명, 규격, 가격 정보를 읽고 있습니다.", progress: 56 },
        { label: "가격 비교", detail: "엑셀 기준 데이터와 1차 비교를 진행하고 있습니다.", progress: 72 },
        { label: "오류 재검증", detail: "오류가 난 항목만 골라 2차 AI 재검증을 진행하고 있습니다.", progress: 88 },
        { label: "결과 작성", detail: "최종 검수 결과 화면을 정리하고 있습니다.", progress: 96 },
      ],
    },
    "master-upload": {
      title: "기준 데이터 업로드 중",
      uploadText: "엑셀 파일을 업로드하고 있습니다.",
      processingTitle: "기준 데이터 반영 중",
      stages: [
        { label: "엑셀 확인", detail: "업로드한 엑셀 구조를 확인하고 있습니다.", progress: 58 },
        { label: "데이터 반영", detail: "기준 데이터를 교체하고 화면을 준비하고 있습니다.", progress: 90 },
      ],
    },
  };

  let elapsedTimer = null;
  let stageTimer = null;

  function setVisible(visible) {
    document.body.classList.toggle("loading-active", visible);
    overlay.setAttribute("aria-hidden", visible ? "false" : "true");
  }

  function setProgress(value) {
    const safeValue = Math.max(0, Math.min(100, Math.round(value)));
    percentEl.textContent = `${safeValue}%`;
    progressBarEl.style.width = `${safeValue}%`;
  }

  function renderSteps(config, activeIndex, mode) {
    const steps = ["파일 업로드", ...config.stages.map((stage) => stage.label)];
    stepsEl.innerHTML = steps.map((label, index) => {
      let className = "loading-step";
      if (mode === "error" && index === activeIndex) {
        className += " error";
      } else if (index < activeIndex) {
        className += " done";
      } else if (index === activeIndex) {
        className += " active";
      }
      return `<li class="${className}">${label}</li>`;
    }).join("");
  }

  function clearTimers() {
    if (elapsedTimer) {
      window.clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    if (stageTimer) {
      window.clearTimeout(stageTimer);
      stageTimer = null;
    }
  }

  function startElapsedClock() {
    const startedAt = Date.now();
    elapsedEl.textContent = "0초";
    elapsedTimer = window.setInterval(() => {
      const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
      elapsedEl.textContent = `${seconds}초`;
    }, 500);
  }

  function startStageFlow(xhr, config) {
    let stageIndex = 0;
    const advance = () => {
      if (xhr.readyState === 4) {
        return;
      }
      const currentIndex = Math.min(stageIndex, config.stages.length - 1);
      const stage = config.stages[currentIndex];
      titleEl.textContent = config.processingTitle;
      subtitleEl.textContent = stage.detail;
      setProgress(stage.progress);
      renderSteps(config, currentIndex + 1, "active");
      stageIndex += 1;
      stageTimer = window.setTimeout(advance, stageIndex <= config.stages.length ? 1800 : 2400);
    };
    advance();
  }

  function beginLoading(config, fileName) {
    clearTimers();
    setVisible(true);
    titleEl.textContent = config.title;
    subtitleEl.textContent = fileName ? `${fileName}\n${config.uploadText}` : config.uploadText;
    setProgress(6);
    renderSteps(config, 0, "active");
    startElapsedClock();
  }

  function finishLoading() {
    clearTimers();
    setProgress(100);
    subtitleEl.textContent = "응답 화면을 불러오고 있습니다.";
  }

  function failLoading(config, message) {
    clearTimers();
    titleEl.textContent = "요청 처리 실패";
    subtitleEl.textContent = message;
    setProgress(100);
    renderSteps(config, 0, "error");
    window.setTimeout(() => {
      setVisible(false);
    }, 1800);
  }

  function restoreButtons(buttons) {
    buttons.forEach((button) => {
      button.disabled = false;
      if (button.tagName === "BUTTON" && button.dataset.originalText) {
        button.textContent = button.dataset.originalText;
      }
    });
  }

  function submitWithProgress(form) {
    const taskName = form.dataset.progressTask || "inspect";
    const config = taskConfigs[taskName] || taskConfigs.inspect;
    const fileInput = form.querySelector('input[type="file"]');
    const fileName = fileInput && fileInput.files && fileInput.files[0] ? fileInput.files[0].name : "";
    const buttons = Array.from(form.querySelectorAll('button, input[type="submit"]'));
    const xhr = new XMLHttpRequest();
    const formData = new FormData(form);
    let uploadCompleted = false;

    buttons.forEach((button) => {
      button.disabled = true;
      if (button.tagName === "BUTTON") {
        button.dataset.originalText = button.textContent || "";
        button.textContent = "처리 중..";
      }
    });

    beginLoading(config, fileName);
    xhr.open((form.method || "POST").toUpperCase(), form.action, true);

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) {
        return;
      }
      setProgress(8 + ((event.loaded / event.total) * 24));
    };

    xhr.upload.onloadend = () => {
      if (uploadCompleted) {
        return;
      }
      uploadCompleted = true;
      setProgress(32);
      renderSteps(config, 1, "active");
      startStageFlow(xhr, config);
    };

    xhr.onerror = () => {
      restoreButtons(buttons);
      failLoading(config, "네트워크 오류가 발생했습니다. 다시 시도해 주세요.");
    };
    xhr.onabort = xhr.onerror;
    xhr.ontimeout = xhr.onerror;

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        finishLoading();
        document.open();
        document.write(xhr.responseText);
        document.close();
        return;
      }
      restoreButtons(buttons);
      failLoading(config, "서버 응답을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.");
    };

    xhr.send(formData);
  }

  document.querySelectorAll("form[data-progress-task]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      if (!form.reportValidity()) {
        return;
      }
      event.preventDefault();
      submitWithProgress(form);
    });
  });
})();
