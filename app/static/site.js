(() => {
  const overlay = document.getElementById("loadingFloat");
  const titleEl = document.getElementById("loadingTitle");
  const subtitleEl = document.getElementById("loadingSubtitle");
  const percentEl = document.getElementById("loadingPercent");
  const overallPercentEl = document.getElementById("loadingOverallPercent");
  const elapsedEl = document.getElementById("loadingElapsed");
  const progressBarEl = document.getElementById("loadingProgressBar");
  const overallProgressBarEl = document.getElementById("loadingOverallProgressBar");
  const currentFileCaptionEl = document.getElementById("loadingCurrentFileCaption");
  const overallCaptionEl = document.getElementById("loadingOverallCaption");
  const stepsEl = document.getElementById("loadingSteps");
  const mediaWrapEl = document.getElementById("loadingMedia");
  const mediaFrameEl = document.getElementById("loadingMediaFrame");
  const mediaLinkEl = document.getElementById("loadingMediaLink");
  if (
    !overlay ||
    !titleEl ||
    !subtitleEl ||
    !percentEl ||
    !overallPercentEl ||
    !elapsedEl ||
    !progressBarEl ||
    !overallProgressBarEl ||
    !currentFileCaptionEl ||
    !overallCaptionEl ||
    !stepsEl
  ) {
    return;
  }

  const ECONENIA_SHORTS = [
    "_fvNEYHXH1U",
    "0hbgIVoSkgQ",
    "0yHRwduhU6Q",
    "2TBLeSvk0YQ",
    "41SB_m4DG8Y",
    "4Ekq-EWM0Ik",
    "52kgEOPL6Mk",
    "5e6rHPlycA0",
    "7b0TDWTqd8k",
    "8shsAtXgnkQ",
    "BL5qWcqlCl4",
    "BNXYDYZ2NBQ",
    "CajivaCYGe4",
    "D0rJfhjsRts",
    "D6xiGQ5qukQ",
    "DbCzqw4NkVQ",
    "e_gWeXtLSqE",
    "EA_mcSAadUI",
    "EHuI0ANdaQA",
    "EYkzVlgvMJE",
    "G99p6rSlBPw",
    "GDxxS44hoec",
    "gv2RFB7UWik",
    "gwUKfPY7uhk",
    "hguntkQCp2Q",
    "iFJ6l82wxGo",
    "ijy4DoD9O_M",
    "IQXITGD-xME",
    "-jLhtUn08_c",
    "KxV0rxPBSRI",
    "lI0WRuxKWmE",
    "lk4PizE7xlU",
    "lLhYLFvRFgc",
    "M7txrR2uvxA",
    "n7-3hSC3vLQ",
    "nbR5WP2oqJk",
    "NyFRLeLjh_o",
    "oqSx3sr6eTg",
    "r4ypQ2ce4G4",
    "s7ngKsHP7zA",
    "SfD5c7C2ZoI",
    "smONg9dG2wc",
    "SpJ70JBRUf0",
    "StcJ0YiQJVs",
    "VbQwHz4Uw-4",
    "W1N71CEoT0E",
    "YiXcq6FE84M",
    "ZfP-YKkt-sc",
  ];

  const taskConfigs = {
    inspect: {
      title: "PDF 검수 실행 중",
      uploadText: "PDF 파일을 업로드하고 있습니다.",
      processingTitle: "PDF 검수 진행 중",
      showShorts: true,
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
      showShorts: false,
      stages: [
        { label: "엑셀 확인", detail: "업로드한 엑셀 구조를 확인하고 있습니다.", progress: 58 },
        { label: "데이터 반영", detail: "기준 데이터를 교체하고 화면을 준비하고 있습니다.", progress: 90 },
      ],
    },
  };

  let elapsedTimer = null;
  let stageTimer = null;
  let lastShortId = "";

  function clampPercent(value) {
    return Math.max(0, Math.min(100, Math.round(value)));
  }

  function createLoadingState(fileInput) {
    const files = Array.from((fileInput && fileInput.files) || []);
    return {
      totalFiles: Math.max(1, files.length || 1),
      fileNames: files.map((file) => file.name),
      currentFileIndex: 0,
    };
  }

  function getCurrentFileName(state) {
    const currentIndex = Math.max(0, state.currentFileIndex || 0);
    return state.fileNames[currentIndex] || `파일 ${currentIndex + 1}`;
  }

  function updateFileCaptions(state, completed = false) {
    const totalFiles = Math.max(1, state.totalFiles || 1);
    const currentIndex = Math.min(totalFiles - 1, Math.max(0, state.currentFileIndex || 0));
    const currentName = getCurrentFileName({ ...state, currentFileIndex: currentIndex });

    currentFileCaptionEl.textContent = completed ? `완료 · ${currentName}` : `${currentIndex + 1} / ${totalFiles} · ${currentName}`;
    overallCaptionEl.textContent = completed ? `${totalFiles} / ${totalFiles} 파일 완료` : `${currentIndex + 1} / ${totalFiles} 파일 진행 중`;
  }

  function setCurrentProgress(value) {
    const safeValue = clampPercent(value);
    percentEl.textContent = `개별 ${safeValue}%`;
    progressBarEl.style.width = `${safeValue}%`;
  }

  function setOverallProgress(value) {
    const safeValue = clampPercent(value);
    overallPercentEl.textContent = `전체 ${safeValue}%`;
    overallProgressBarEl.style.width = `${safeValue}%`;
  }

  function setCompositeProgress(state, currentValue) {
    const totalFiles = Math.max(1, state.totalFiles || 1);
    const currentIndex = Math.min(totalFiles - 1, Math.max(0, state.currentFileIndex || 0));
    const safeCurrent = clampPercent(currentValue);
    const overallValue = ((currentIndex + (safeCurrent / 100)) / totalFiles) * 100;
    updateFileCaptions(state);
    setCurrentProgress(safeCurrent);
    setOverallProgress(overallValue);
  }

  function isPdfFile(file) {
    const name = (file && file.name ? file.name : "").toLowerCase();
    return name.endsWith(".pdf") || file.type === "application/pdf";
  }

  function summarizeSelectedFiles(files) {
    const list = Array.from(files || []);
    if (!list.length) {
      return "";
    }
    if (list.length === 1) {
      return list[0].name;
    }
    return `${list[0].name} 외 ${list.length - 1}개`;
  }

  function buildDropzoneSummary(files) {
    const list = Array.from(files || []);
    if (!list.length) {
      return "선택된 파일 없음";
    }
    if (list.length === 1) {
      return `1개 선택됨 · ${list[0].name}`;
    }
    const visibleNames = list.slice(0, 3).map((file) => file.name).join(", ");
    if (list.length <= 3) {
      return `${list.length}개 선택됨 · ${visibleNames}`;
    }
    return `${list.length}개 선택됨 · ${visibleNames} 외 ${list.length - 3}개`;
  }

  function updateDropzone(dropzone, summaryOverride) {
    const input = dropzone.querySelector("[data-file-input]");
    const summary = dropzone.querySelector("[data-file-summary]");
    if (!input || !summary) {
      return;
    }
    const hasFiles = Boolean(input.files && input.files.length);
    dropzone.classList.toggle("has-files", hasFiles);
    summary.textContent = summaryOverride || buildDropzoneSummary(input.files);
  }

  function setupDropzones() {
    document.querySelectorAll("[data-dropzone]").forEach((dropzone) => {
      const input = dropzone.querySelector("[data-file-input]");
      if (!input) {
        return;
      }

      const activate = () => dropzone.classList.add("is-dragover");
      const deactivate = () => dropzone.classList.remove("is-dragover");

      ["dragenter", "dragover"].forEach((eventName) => {
        dropzone.addEventListener(eventName, (event) => {
          event.preventDefault();
          activate();
        });
      });

      ["dragleave", "dragend"].forEach((eventName) => {
        dropzone.addEventListener(eventName, () => {
          deactivate();
        });
      });

      dropzone.addEventListener("drop", (event) => {
        event.preventDefault();
        deactivate();
        const droppedFiles = Array.from(event.dataTransfer ? event.dataTransfer.files : []).filter(isPdfFile);
        if (!droppedFiles.length) {
          updateDropzone(dropzone, "PDF 파일만 업로드할 수 있습니다.");
          return;
        }
        const transfer = new DataTransfer();
        droppedFiles.forEach((file) => transfer.items.add(file));
        input.files = transfer.files;
        input.dispatchEvent(new Event("change", { bubbles: true }));
      });

      input.addEventListener("change", () => updateDropzone(dropzone));
      updateDropzone(dropzone);
    });
  }

  function setupPdfViewerSwitcher() {
    const frame = document.querySelector("[data-pdf-viewer-frame]");
    const nameEl = document.querySelector("[data-pdf-viewer-name]");
    const linkEl = document.querySelector("[data-pdf-viewer-link]");
    const resultPanelEl = document.querySelector("[data-result-panel-main]");
    const buttons = Array.from(document.querySelectorAll("[data-preview-button]"));
    const batches = Array.from(document.querySelectorAll("[data-result-batch]"));

    if (!frame && !buttons.length && !batches.length) {
      return;
    }

    let activeBatchKey = "";

    const setLinkState = (element, href) => {
      if (!element) {
        return;
      }
      if (href) {
        element.href = href;
        element.hidden = false;
        element.removeAttribute("aria-hidden");
      } else {
        element.hidden = true;
        element.setAttribute("aria-hidden", "true");
      }
    };

    const setActiveButton = (href) => {
      buttons.forEach((button) => {
        button.classList.toggle("is-active", Boolean(href) && button.dataset.previewHref === href);
      });
    };

    const updateCurrentFileUi = (name, href) => {
      const safeName = name || "업로드 PDF 미리보기";
      if (nameEl) {
        nameEl.textContent = safeName;
      }
      setLinkState(linkEl, href);
      setActiveButton(href);
    };

    const activateBatch = (batch, syncFrame = true) => {
      if (!batch) {
        return;
      }

      const previewHref = batch.dataset.previewHref || "";
      const previewSrc = batch.dataset.previewSrc || previewHref;
      const previewName = batch.dataset.previewName || "";

      batches.forEach((item) => {
        item.classList.toggle("is-active", item === batch);
      });

      updateCurrentFileUi(previewName, previewHref);

      if (frame && syncFrame && previewSrc) {
        const currentSrc = frame.getAttribute("src") || "";
        if (currentSrc !== previewSrc) {
          frame.src = previewSrc;
        }
      }

      activeBatchKey = batch.id || previewHref || previewName;
    };

    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        const previewHref = button.dataset.previewHref || "";
        const previewName = button.dataset.previewName || "";
        const matchedBatch = button.closest("[data-result-batch]") || batches.find((batch) => batch.dataset.previewHref === previewHref);
        if (matchedBatch) {
          activateBatch(matchedBatch, true);
          return;
        }

        const previewSrc = button.dataset.previewSrc || previewHref;
        updateCurrentFileUi(previewName, previewHref);
        if (frame && previewSrc) {
          frame.src = previewSrc;
        }
        setActiveButton(previewHref);
      });
    });

    if (!batches.length) {
      return;
    }

    const findCurrentBatch = () => {
      const panelTop = resultPanelEl ? resultPanelEl.getBoundingClientRect().top : 0;
      const activationLine = panelTop + 24;
      let candidate = null;
      let firstVisible = null;

      batches.forEach((batch) => {
        const rect = batch.getBoundingClientRect();
        if (rect.bottom <= activationLine) {
          return;
        }
        if (!firstVisible) {
          firstVisible = batch;
        }
        if (rect.top <= activationLine + 24) {
          candidate = batch;
        }
      });

      return candidate || firstVisible || batches[batches.length - 1];
    };

    let syncFrameTicket = 0;
    const syncViewerToScroll = () => {
      if (syncFrameTicket) {
        return;
      }
      syncFrameTicket = window.requestAnimationFrame(() => {
        syncFrameTicket = 0;
        const batch = findCurrentBatch();
        if (!batch) {
          return;
        }
        const nextKey = batch.id || batch.dataset.previewHref || batch.dataset.previewName || "";
        if (nextKey === activeBatchKey && batch.classList.contains("is-active")) {
          return;
        }
        activateBatch(batch, true);
      });
    };

    const initialBatch =
      batches.find((batch) => {
        const href = batch.dataset.previewHref || "";
        return buttons.some((button) => button.classList.contains("is-active") && button.dataset.previewHref === href);
      }) ||
      findCurrentBatch() ||
      batches[0];

    activateBatch(initialBatch, false);

    if (resultPanelEl) {
      resultPanelEl.addEventListener("scroll", syncViewerToScroll, { passive: true });
    }
    window.addEventListener("scroll", syncViewerToScroll, { passive: true });
    window.addEventListener("resize", syncViewerToScroll);
  }

  function setupResultPanelPageHandoff() {
    const resultPanelEl = document.querySelector("[data-result-panel-main]");
    if (!resultPanelEl) {
      return;
    }

    const desktopQuery = window.matchMedia("(min-width: 921px)");
    const handoffThreshold = 220;
    let wheelDragAccum = 0;
    let wheelResetTimer = 0;
    let touchLastY = null;
    let touchDragAccum = 0;

    const resetWheelAccum = () => {
      wheelDragAccum = 0;
      if (wheelResetTimer) {
        window.clearTimeout(wheelResetTimer);
        wheelResetTimer = 0;
      }
    };

    const scheduleWheelReset = () => {
      if (wheelResetTimer) {
        window.clearTimeout(wheelResetTimer);
      }
      wheelResetTimer = window.setTimeout(() => {
        wheelDragAccum = 0;
        wheelResetTimer = 0;
      }, 180);
    };

    const pageBottom = () => Math.max(0, document.documentElement.scrollHeight - window.innerHeight);
    const remainingPageScroll = () => Math.max(0, pageBottom() - window.scrollY);
    const pageNeedsMoreScroll = () => desktopQuery.matches && remainingPageScroll() > 8;
    const jumpPageToBottom = () => {
      const target = pageBottom();
      if (target <= window.scrollY + 8) {
        return;
      }
      window.scrollTo({ top: target, behavior: "smooth" });
    };

    resultPanelEl.addEventListener("wheel", (event) => {
      if (!desktopQuery.matches) {
        resetWheelAccum();
        return;
      }

      if (event.deltaY <= 0) {
        resetWheelAccum();
        return;
      }

      if (!pageNeedsMoreScroll()) {
        resetWheelAccum();
        return;
      }

      event.preventDefault();
      const moveAmount = Math.min(event.deltaY, remainingPageScroll());
      if (moveAmount > 0) {
        window.scrollBy({ top: moveAmount, behavior: "auto" });
      }

      wheelDragAccum += Math.max(0, event.deltaY);
      if (wheelDragAccum >= handoffThreshold) {
        jumpPageToBottom();
        resetWheelAccum();
        return;
      }
      scheduleWheelReset();
    }, { passive: false });

    resultPanelEl.addEventListener("touchstart", (event) => {
      if (!desktopQuery.matches || !event.touches.length) {
        touchLastY = null;
        touchDragAccum = 0;
        return;
      }
      touchLastY = event.touches[0].clientY;
      touchDragAccum = 0;
    }, { passive: true });

    resultPanelEl.addEventListener("touchmove", (event) => {
      if (!desktopQuery.matches || !pageNeedsMoreScroll() || !event.touches.length || touchLastY === null) {
        return;
      }

      const currentY = event.touches[0].clientY;
      const delta = touchLastY - currentY;
      touchLastY = currentY;

      if (delta <= 0) {
        touchDragAccum = 0;
        return;
      }

      event.preventDefault();
      const moveAmount = Math.min(delta, remainingPageScroll());
      if (moveAmount > 0) {
        window.scrollBy({ top: moveAmount, behavior: "auto" });
      }

      touchDragAccum += delta;
      if (touchDragAccum >= handoffThreshold) {
        jumpPageToBottom();
        touchDragAccum = 0;
      }
    }, { passive: false });

    ["touchend", "touchcancel"].forEach((eventName) => {
      resultPanelEl.addEventListener(eventName, () => {
        touchLastY = null;
        touchDragAccum = 0;
      }, { passive: true });
    });
  }

  function setVisible(visible) {
    document.body.classList.toggle("loading-active", visible);
    overlay.setAttribute("aria-hidden", visible ? "false" : "true");
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

  function pickRandomShortId() {
    if (!ECONENIA_SHORTS.length) {
      return "";
    }
    if (ECONENIA_SHORTS.length === 1) {
      return ECONENIA_SHORTS[0];
    }
    let picked = lastShortId;
    while (picked === lastShortId) {
      picked = ECONENIA_SHORTS[Math.floor(Math.random() * ECONENIA_SHORTS.length)];
    }
    lastShortId = picked;
    return picked;
  }

  function stopShort() {
    if (mediaFrameEl) {
      mediaFrameEl.src = "";
    }
  }

  function setRandomShort(config) {
    if (!mediaWrapEl || !mediaFrameEl) {
      return;
    }
    if (!config.showShorts) {
      mediaWrapEl.style.display = "none";
      stopShort();
      return;
    }

    const shortId = pickRandomShortId();
    if (!shortId) {
      mediaWrapEl.style.display = "none";
      stopShort();
      return;
    }

    mediaWrapEl.style.display = "";
    mediaFrameEl.src = `https://www.youtube-nocookie.com/embed/${shortId}?autoplay=1&mute=1&controls=1&loop=1&playlist=${shortId}&playsinline=1&rel=0&modestbranding=1`;
    if (mediaLinkEl) {
      mediaLinkEl.href = `https://www.youtube.com/shorts/${shortId}`;
    }
  }

  function startStageFlow(xhr, config, state) {
    let stageIndex = 0;
    let fileIndex = 0;
    const advance = () => {
      if (xhr.readyState === 4) {
        return;
      }
      const currentStageIndex = Math.min(stageIndex, config.stages.length - 1);
      const stage = config.stages[currentStageIndex];
      state.currentFileIndex = fileIndex;
      const fileName = getCurrentFileName(state);
      const filePrefix = state.totalFiles > 1 ? `${fileIndex + 1}/${state.totalFiles} 파일` : "현재 파일";
      titleEl.textContent = config.processingTitle;
      subtitleEl.textContent = `${filePrefix} · ${fileName}\n${stage.detail}`;
      setCompositeProgress(state, stage.progress);
      renderSteps(config, currentStageIndex + 1, "active");

      if (stageIndex < config.stages.length - 1) {
        stageIndex += 1;
        stageTimer = window.setTimeout(advance, 1800);
        return;
      }

      if (fileIndex < state.totalFiles - 1) {
        fileIndex += 1;
        stageIndex = 0;
        stageTimer = window.setTimeout(advance, 900);
        return;
      }

      stageTimer = window.setTimeout(advance, 2400);
    };
    advance();
  }

  function beginLoading(config, fileName, state) {
    clearTimers();
    setVisible(true);
    setRandomShort(config);
    titleEl.textContent = config.title;
    if (state.totalFiles > 1) {
      subtitleEl.textContent = `${state.totalFiles}개 파일을 업로드하고 있습니다.\n첫 번째 파일: ${getCurrentFileName(state)}`;
    } else {
      subtitleEl.textContent = fileName ? `${fileName}\n${config.uploadText}` : config.uploadText;
    }
    setCompositeProgress(state, 6);
    renderSteps(config, 0, "active");
    startElapsedClock();
  }

  function finishLoading(state) {
    clearTimers();
    updateFileCaptions({ ...state, currentFileIndex: Math.max(0, state.totalFiles - 1) }, true);
    setCurrentProgress(100);
    setOverallProgress(100);
    subtitleEl.textContent = "응답 화면을 불러오고 있습니다.";
    stopShort();
  }

  function failLoading(config, message, state) {
    clearTimers();
    titleEl.textContent = "요청 처리 실패";
    subtitleEl.textContent = message;
    updateFileCaptions(state);
    setCurrentProgress(100);
    setOverallProgress(100);
    renderSteps(config, 0, "error");
    stopShort();
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
    const loadingState = createLoadingState(fileInput);
    const fileName = fileInput ? summarizeSelectedFiles(fileInput.files) : "";
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

    beginLoading(config, fileName, loadingState);
    xhr.open((form.method || "POST").toUpperCase(), form.action, true);

    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) {
        return;
      }
      setCompositeProgress(loadingState, 8 + ((event.loaded / event.total) * 24));
    };

    xhr.upload.onloadend = () => {
      if (uploadCompleted) {
        return;
      }
      uploadCompleted = true;
      loadingState.currentFileIndex = 0;
      setCompositeProgress(loadingState, 32);
      renderSteps(config, 1, "active");
      startStageFlow(xhr, config, loadingState);
    };

    xhr.onerror = () => {
      restoreButtons(buttons);
      failLoading(config, "네트워크 오류가 발생했습니다. 다시 시도해 주세요.", loadingState);
    };
    xhr.onabort = xhr.onerror;
    xhr.ontimeout = xhr.onerror;

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        finishLoading(loadingState);
        document.open();
        document.write(xhr.responseText);
        document.close();
        return;
      }
      restoreButtons(buttons);
      failLoading(config, "서버 응답을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.", loadingState);
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

  setupDropzones();
  setupPdfViewerSwitcher();
  setupResultPanelPageHandoff();
})();
