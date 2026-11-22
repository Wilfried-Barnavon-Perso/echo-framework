<!-- Copyright (c) 2025-2026 Wilfried BARNAVON | SPDX-License-Identifier: Apache-2.0 -->
<script lang="ts">
  import { onDestroy, onMount } from "svelte";
  import { toast } from "svelte-sonner";
  import Spinner from "$lib/components/common/Spinner.svelte";
  import {
    fetchEchoBrowserFrameBlob,
    getEchoBrowserFrameClipUrl,
    getEchoBrowserFrameImageUrl,
    getEchoBrowserFrames,
    getEchoBrowserLiveWsUrl,
    getEchoToken,
    retainEchoBrowserFrame,
    type EchoBrowserFrame,
  } from "$lib/apis/echo";
  import { echoBrowserLiveFrames, echoBrowserTarget } from "$lib/stores/echo";
  import { isSavedChatId } from "$lib/utils/chatId";

  export let chatId: string = "";

  let boundChatId = "";
  let loading = false;
  let persistedFrames: EchoBrowserFrame[] = [];
  let currentIndex = 0;
  let isLive = true;
  let isPlaying = false;
  let playbackSpeedMs = 1000;
  let playTimer: ReturnType<typeof setTimeout> | null = null;

  // Flux WebSocket binaire temps réel (/browser-ws) + canvas GPU & curseur SVG
  let liveSocket: WebSocket | null = null;
  let liveCanvasEl: HTMLCanvasElement | null = null;
  let hasLiveBitmap = false;
  let lastLiveBitmap: ImageBitmap | null = null;
  let liveBitmapDims = { w: 1280, h: 800 };
  let liveReconnectTimer: ReturnType<typeof setTimeout> | null = null;
  let liveActive = false;
  let liveUrl = "";
  let liveViewport = { w: 1280, h: 800 };
  let liveCursor: { x: number; y: number; clicking: boolean } | null = null;
  let lastLiveFileId = "";

  // Replay clip MP4 & conservation
  let videoElement: HTMLVideoElement | null = null;
  let showClipVideo = false;
  let playClipsInAutoplay = true;
  let retaining = false;

  // Cache local des Blob URLs authentifiés (révoqués sur onDestroy / changement de chat)
  let blobUrlCache: Record<string, string> = {};
  const pendingFetches = new Set<string>();

  // Zoom & Pan
  let zoom = 1;
  let panX = 0;
  let panY = 0;
  let isPanning = false;
  let panStartX = 0;
  let panStartY = 0;

  // Recadrage 8 poignées (coordonnées normalisées 0..1 sur le média affiché)
  let cropMode = false;
  let cropRect = { x: 0.1, y: 0.1, w: 0.8, h: 0.8 };
  let cropDragHandle: string | null = null;
  let cropDragStart = {
    clientX: 0,
    clientY: 0,
    rect: { x: 0.1, y: 0.1, w: 0.8, h: 0.8 },
  };
  let imgElement: HTMLImageElement | null = null;
  let copyFallbackUrl: string | null = null;
  let viewportWidth = 0;
  let viewportHeight = 0;
  let imgNaturalWidth = 0;
  let imgNaturalHeight = 0;

  const getActiveMediaElement = ():
    | HTMLImageElement
    | HTMLVideoElement
    | HTMLCanvasElement
    | null => {
    if (showLiveFeed && liveCanvasEl) {
      return liveCanvasEl;
    }
    if (!showLiveFeed && showClipVideo && activeClipSrc && videoElement) {
      return videoElement;
    }
    return imgElement;
  };

  const paintLiveCanvas = () => {
    if (!liveCanvasEl || !lastLiveBitmap) return;
    if (liveCanvasEl.width !== lastLiveBitmap.width) {
      liveCanvasEl.width = lastLiveBitmap.width;
    }
    if (liveCanvasEl.height !== lastLiveBitmap.height) {
      liveCanvasEl.height = lastLiveBitmap.height;
    }
    const ctx = liveCanvasEl.getContext("2d");
    if (ctx) {
      ctx.drawImage(lastLiveBitmap, 0, 0);
    }
  };

  const handleImageLoad = () => {
    if (!imgElement) return;
    imgNaturalWidth = imgElement.naturalWidth || 0;
    imgNaturalHeight = imgElement.naturalHeight || 0;
  };

  const handleVideoMetadata = () => {
    if (!videoElement) return;
    imgNaturalWidth = videoElement.videoWidth || 0;
    imgNaturalHeight = videoElement.videoHeight || 0;
  };

  $: if (showLiveFeed && liveCanvasEl && lastLiveBitmap) {
    paintLiveCanvas();
  }

  $: if (showLiveFeed) {
    if (hasLiveBitmap && lastLiveBitmap) {
      imgNaturalWidth = lastLiveBitmap.width;
      imgNaturalHeight = lastLiveBitmap.height;
    } else {
      imgNaturalWidth = 0;
      imgNaturalHeight = 0;
    }
  } else if (showClipVideo && activeClipSrc) {
    if (videoElement && videoElement.readyState >= 2 && videoElement.videoWidth > 0) {
      imgNaturalWidth = videoElement.videoWidth;
      imgNaturalHeight = videoElement.videoHeight;
    } else if (!videoElement || videoElement.readyState < 1) {
      imgNaturalWidth = 0;
      imgNaturalHeight = 0;
    }
  } else {
    const displaySrc = activeImageSrc;
    if (!displaySrc) {
      imgNaturalWidth = 0;
      imgNaturalHeight = 0;
    } else if (imgElement?.complete && imgElement.naturalWidth > 0) {
      imgNaturalWidth = imgElement.naturalWidth;
      imgNaturalHeight = imgElement.naturalHeight;
    }
  }

  $: fittedDimensions =
    viewportWidth > 0 &&
    viewportHeight > 0 &&
    imgNaturalWidth > 0 &&
    imgNaturalHeight > 0
      ? (() => {
          const scale = Math.min(
            viewportWidth / imgNaturalWidth,
            viewportHeight / imgNaturalHeight,
          );
          return {
            width: imgNaturalWidth * scale,
            height: imgNaturalHeight * scale,
          };
        })()
      : null;

  const revokeAllBlobs = () => {
    for (const url of Object.values(blobUrlCache)) {
      try {
        URL.revokeObjectURL(url);
      } catch {
        // ignore
      }
    }
    blobUrlCache = {};
    pendingFetches.clear();
  };

  const stopPlayback = () => {
    isPlaying = false;
    if (playTimer !== null) {
      clearTimeout(playTimer);
      playTimer = null;
    }
  };

  const closeLiveStream = () => {
    if (liveReconnectTimer !== null) {
      clearTimeout(liveReconnectTimer);
      liveReconnectTimer = null;
    }
    if (liveSocket) {
      const ws = liveSocket;
      liveSocket = null;
      try {
        ws.close();
      } catch {
        // ignore
      }
    }
    liveActive = false;
    hasLiveBitmap = false;
    if (lastLiveBitmap) {
      lastLiveBitmap.close();
      lastLiveBitmap = null;
    }
    liveBitmapDims = { w: 1280, h: 800 };
    liveUrl = "";
    liveCursor = null;
  };

  const openLiveStream = (cid: string) => {
    if (typeof WebSocket === "undefined" || !cid || !isSavedChatId(cid)) {
      return;
    }
    const token = getEchoToken();
    if (!token || liveSocket) return;
    const ws = new WebSocket(getEchoBrowserLiveWsUrl(cid));
    ws.binaryType = "arraybuffer";
    liveSocket = ws;

    ws.onopen = () => {
      if (cid !== boundChatId || liveSocket !== ws) return;
      ws.send(JSON.stringify({ type: "auth", token }));
    };

    ws.onmessage = (event) => {
      if (cid !== boundChatId || liveSocket !== ws) return;
      if (event.data instanceof ArrayBuffer && event.data.byteLength > 16) {
        const dv = new DataView(event.data, 0, 16);
        const flags = dv.getUint8(1);
        const vpW = dv.getUint16(2, false);
        const vpH = dv.getUint16(4, false);
        const curX = dv.getUint16(6, false);
        const curY = dv.getUint16(8, false);
        const frameId = dv.getUint32(10, false);
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "ack", frame_id: frameId }));
        }
        createImageBitmap(
          new Blob([event.data.slice(16)], { type: "image/jpeg" }),
        )
          .then((bmp) => {
            if (cid !== boundChatId || liveSocket !== ws) {
              bmp.close();
              return;
            }
            if (lastLiveBitmap) lastLiveBitmap.close();
            lastLiveBitmap = bmp;
            liveBitmapDims = { w: bmp.width, h: bmp.height };
            liveViewport = {
              w: vpW > 0 ? vpW : 1280,
              h: vpH > 0 ? vpH : 800,
            };
            liveCursor =
              curX !== 0xffff && curY !== 0xffff
                ? { x: curX, y: curY, clicking: (flags & 1) === 1 }
                : liveCursor;
            hasLiveBitmap = true;
            liveActive = true;
            paintLiveCanvas();
          })
          .catch(() => {});
        return;
      }

      if (typeof event.data === "string") {
        try {
          const ev = JSON.parse(event.data);
          if (!ev || typeof ev !== "object") return;
          if (ev.type === "ping") {
            if (ws.readyState === WebSocket.OPEN) {
              ws.send('{"type":"pong"}');
            }
          } else if (ev.type === "state" || ev.type === "live_state") {
            if (typeof ev.url === "string") liveUrl = ev.url;
            liveActive =
              ev.active !== undefined
                ? Boolean(ev.active)
                : ev.status === "live";
            if (!liveActive) {
              liveCursor = null;
            }
          } else if (ev.type === "cursor" || ev.type === "live_cursor") {
            liveViewport = {
              w: Number(ev.vp_w) > 0 ? Number(ev.vp_w) : liveViewport.w,
              h: Number(ev.vp_h) > 0 ? Number(ev.vp_h) : liveViewport.h,
            };
            liveCursor =
              ev.x != null && ev.y != null
                ? {
                    x: Number(ev.x),
                    y: Number(ev.y),
                    clicking: Boolean(ev.clicking),
                  }
                : null;
          }
        } catch {
          // Ignorer les messages JSON malformés
        }
      }
    };

    ws.onclose = () => {
      if (liveSocket === ws) {
        liveSocket = null;
        if (cid === boundChatId && isSavedChatId(cid)) {
          if (liveReconnectTimer !== null) clearTimeout(liveReconnectTimer);
          liveReconnectTimer = setTimeout(() => {
            liveReconnectTimer = null;
            if (cid === boundChatId) {
              openLiveStream(cid);
            }
          }, 3000);
        }
      }
    };
  };

  const loadPersistedFrames = async (cid: string) => {
    const token = localStorage?.token;
    if (!token || !cid || !isSavedChatId(cid)) {
      persistedFrames = [];
      return;
    }
    loading = true;
    try {
      const res = await getEchoBrowserFrames(token, cid);
      if (cid === boundChatId) {
        persistedFrames = Array.isArray(res?.frames) ? res.frames : [];
      }
    } catch {
      // Silencieux si session absente ou aucune capture
    } finally {
      if (cid === boundChatId) loading = false;
    }
  };

  let pendingTarget: {
    chatId: string;
    sessionId?: string;
    frameId?: string;
    frameIndex?: number;
  } | null = null;

  const syncChat = (cid: string) => {
    if (cid === boundChatId) return;
    boundChatId = cid;
    closeCopyFallback();
    closeLiveStream();
    pendingTarget = null;
    stopPlayback();
    revokeAllBlobs();
    persistedFrames = [];
    currentIndex = 0;
    isLive = true;
    lastLiveFileId = "";
    showClipVideo = false;
    retaining = false;
    zoom = 1;
    panX = 0;
    panY = 0;
    cropMode = false;
    if (cid) {
      void loadPersistedFrames(cid);
      openLiveStream(cid);
    }
  };

  onMount(() => {
    syncChat(chatId);
  });

  $: syncChat(chatId);
  $: if (
    typeof window !== "undefined" &&
    boundChatId &&
    isSavedChatId(boundChatId) &&
    !liveSocket &&
    liveReconnectTimer === null
  ) {
    openLiveStream(boundChatId);
  }

  onDestroy(() => {
    closeCopyFallback();
    closeLiveStream();
    stopPlayback();
    revokeAllBlobs();
  });

  // Fusion réactive persistedFrames + $echoBrowserLiveFrames[chatId]
  $: liveFrames = chatId ? ($echoBrowserLiveFrames[chatId] ?? []) : [];
  $: frames = ((): EchoBrowserFrame[] => {
    const map = new Map<string, EchoBrowserFrame>();
    const ordered: EchoBrowserFrame[] = [];
    const pushFrame = (f: EchoBrowserFrame, idx: number) => {
      const key = f.file_id || `idx_${idx}_${f.timestamp ?? 0}`;
      const existing = map.get(key);
      if (existing) {
        Object.assign(existing, f);
      } else {
        const copy = { ...f };
        map.set(key, copy);
        ordered.push(copy);
      }
    };
    persistedFrames.forEach((f, i) => pushFrame(f, i));
    liveFrames.forEach((f, i) => pushFrame(f, persistedFrames.length + i));
    return ordered;
  })();

  // Bascule fluide du slot live vers l'image HD dès réception d'une nouvelle frame persistée
  $: {
    const latestFrame = frames.length > 0 ? frames[frames.length - 1] : null;
    const latestId = latestFrame?.file_id || "";
    if (latestId && latestId !== lastLiveFileId) {
      lastLiveFileId = latestId;
      if (isLive) {
        liveActive = false;
        liveCursor = null;
        showClipVideo = false;
      }
    }
  }

  // Suivi automatique en mode EN DIRECT
  $: if (frames.length > 0) {
    if (isLive) {
      currentIndex = frames.length - 1;
    } else if (currentIndex >= frames.length) {
      currentIndex = frames.length - 1;
    }
  } else {
    currentIndex = 0;
  }

  // Capture de la requête $echoBrowserTarget sans la perdre si frames.length === 0 au démarrage à froid
  $: if ($echoBrowserTarget && $echoBrowserTarget.chatId === chatId) {
    pendingTarget = $echoBrowserTarget;
    echoBrowserTarget.set(null);
  }

  // Application de pendingTarget dès que les frames sont disponibles
  $: if (
    pendingTarget &&
    pendingTarget.chatId === chatId &&
    frames.length > 0
  ) {
    const target = pendingTarget;
    pendingTarget = null;
    if (
      typeof target.frameIndex === "number" &&
      !Number.isNaN(target.frameIndex)
    ) {
      const clamped = Math.max(
        0,
        Math.min(frames.length - 1, target.frameIndex),
      );
      currentIndex = clamped;
      isLive = clamped === frames.length - 1;
      showClipVideo = false;
      stopPlayback();
    } else if (target.frameId) {
      const idx = frames.findIndex((f) => f.file_id === target.frameId);
      if (idx >= 0) {
        currentIndex = idx;
        isLive = idx === frames.length - 1;
        showClipVideo = false;
        stopPlayback();
      }
    } else if (target.sessionId) {
      let idx = -1;
      for (let i = frames.length - 1; i >= 0; i--) {
        if (frames[i].session_id === target.sessionId) {
          idx = i;
          break;
        }
      }
      if (idx >= 0) {
        currentIndex = idx;
        isLive = idx === frames.length - 1;
        showClipVideo = false;
        stopPlayback();
      }
    }
  }

  $: activeFrame = frames[currentIndex] ?? null;

  const fetchAuthenticatedBlob = async (url: string) => {
    if (!url || blobUrlCache[url] || pendingFetches.has(url)) return;
    const token = getEchoToken();
    const targetCid = boundChatId;
    if (!token || !targetCid) return;
    pendingFetches.add(url);
    try {
      const blob = await fetchEchoBrowserFrameBlob(token, targetCid, url);
      if (targetCid !== boundChatId) return;
      const objUrl = URL.createObjectURL(blob);
      if (targetCid !== boundChatId) {
        URL.revokeObjectURL(objUrl);
        return;
      }
      blobUrlCache = { ...blobUrlCache, [url]: objUrl };
    } catch {
      // Ignorer les erreurs réseau transitoires
    } finally {
      pendingFetches.delete(url);
    }
  };

  const getFrameImageSrc = (
    frame: EchoBrowserFrame | null,
    cache: Record<string, string>,
  ): string => {
    if (!frame) return "";
    if (frame.b64) {
      return frame.b64.startsWith("data:")
        ? frame.b64
        : `data:image/jpeg;base64,${frame.b64}`;
    }
    const remoteUrl =
      frame.image_url ||
      (frame.file_id && chatId
        ? getEchoBrowserFrameImageUrl(chatId, frame.file_id)
        : "");
    if (!remoteUrl) return "";
    if (cache[remoteUrl]) return cache[remoteUrl];
    void fetchAuthenticatedBlob(remoteUrl);
    return "";
  };

  const getFrameClipSrc = (
    frame: EchoBrowserFrame | null,
    cache: Record<string, string>,
  ): string => {
    if (!frame) return "";
    const remoteUrl =
      frame.clip_url ||
      (frame.file_id &&
      chatId &&
      (Number(frame.clip_duration_ms) > 0 || Number(frame.clip_size_bytes) > 0)
        ? getEchoBrowserFrameClipUrl(chatId, frame.file_id)
        : "");
    if (!remoteUrl) return "";
    if (cache[remoteUrl]) return cache[remoteUrl];
    void fetchAuthenticatedBlob(remoteUrl);
    return "";
  };

  $: activeImageSrc = getFrameImageSrc(activeFrame, blobUrlCache);
  $: activeClipSrc = getFrameClipSrc(activeFrame, blobUrlCache);
  $: if (!activeFrame?.clip_url && showClipVideo) {
    showClipVideo = false;
  }
  $: isOnLatestLive =
    isLive && (frames.length === 0 || currentIndex === frames.length - 1);
  $: showLiveFeed = isOnLatestLive && liveActive && hasLiveBitmap;
  $: activeMediaAvailable = showLiveFeed
    ? hasLiveBitmap
    : showClipVideo
      ? Boolean(activeClipSrc)
      : Boolean(activeImageSrc);

  const goToIndex = (idx: number) => {
    if (frames.length === 0) return;
    const clamped = Math.max(0, Math.min(frames.length - 1, idx));
    currentIndex = clamped;
    showClipVideo = false;
    isLive = clamped === frames.length - 1 && !isPlaying;
  };

  const returnToLive = () => {
    stopPlayback();
    isLive = true;
    showClipVideo = false;
    if (frames.length > 0) {
      currentIndex = frames.length - 1;
    }
  };

  const scheduleNextStep = () => {
    if (playTimer !== null) {
      clearTimeout(playTimer);
      playTimer = null;
    }
    if (!isPlaying) return;
    playTimer = setTimeout(() => {
      playTimer = null;
      if (!isPlaying) return;
      if (currentIndex < frames.length - 1) {
        startStepPlayback(currentIndex + 1);
      } else {
        stopPlayback();
      }
    }, playbackSpeedMs);
  };

  const startStepPlayback = (idx: number) => {
    if (idx < 0 || idx >= frames.length) {
      stopPlayback();
      return;
    }
    currentIndex = idx;
    const frame = frames[idx];
    if (playClipsInAutoplay && frame?.clip_url) {
      showClipVideo = true;
    } else {
      showClipVideo = false;
      scheduleNextStep();
    }
  };

  const handleVideoEnded = () => {
    if (isPlaying) {
      showClipVideo = false;
      scheduleNextStep();
    }
  };

  const handleVideoError = () => {
    if (isPlaying) {
      showClipVideo = false;
      scheduleNextStep();
    }
  };

  const togglePlayback = () => {
    if (frames.length <= 1) return;
    if (isPlaying) {
      stopPlayback();
      return;
    }
    isLive = false;
    isPlaying = true;
    cropMode = false;
    const startIdx = currentIndex >= frames.length - 1 ? 0 : currentIndex;
    startStepPlayback(startIdx);
  };

  const onSpeedChange = () => {
    if (isPlaying && !showClipVideo) {
      scheduleNextStep();
    }
  };

  const resetZoom = () => {
    zoom = 1;
    panX = 0;
    panY = 0;
  };

  const adjustZoom = (delta: number) => {
    zoom = Math.max(0.1, Math.min(15, Number((zoom + delta).toFixed(2))));
  };

  const handleWheel = (e: WheelEvent) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    const factor = e.deltaY < 0 ? 1.15 : 0.87;
    zoom = Math.max(0.1, Math.min(15, Number((zoom * factor).toFixed(2))));
  };

  const handleMouseDown = (e: MouseEvent) => {
    if (cropMode || e.button !== 0 || !activeMediaAvailable) return;
    isPanning = true;
    panStartX = e.clientX - panX;
    panStartY = e.clientY - panY;
  };

  const handleWindowMouseMove = (e: MouseEvent) => {
    if (isPanning) {
      panX = e.clientX - panStartX;
      panY = e.clientY - panStartY;
      return;
    }
    const mediaEl = getActiveMediaElement();
    if (cropDragHandle && mediaEl) {
      const bounds = mediaEl.getBoundingClientRect();
      if (bounds.width <= 0 || bounds.height <= 0) return;
      const dx = (e.clientX - cropDragStart.clientX) / bounds.width;
      const dy = (e.clientY - cropDragStart.clientY) / bounds.height;
      const r = { ...cropDragStart.rect };
      const minSize = 0.04;

      if (cropDragHandle === "move") {
        r.x = Math.max(0, Math.min(1 - r.w, r.x + dx));
        r.y = Math.max(0, Math.min(1 - r.h, r.y + dy));
      } else {
        if (cropDragHandle.includes("w")) {
          const newX = Math.max(0, Math.min(r.x + r.w - minSize, r.x + dx));
          r.w = r.w + (r.x - newX);
          r.x = newX;
        }
        if (cropDragHandle.includes("e")) {
          r.w = Math.max(minSize, Math.min(1 - r.x, r.w + dx));
        }
        if (cropDragHandle.includes("n")) {
          const newY = Math.max(0, Math.min(r.y + r.h - minSize, r.y + dy));
          r.h = r.h + (r.y - newY);
          r.y = newY;
        }
        if (cropDragHandle.includes("s")) {
          r.h = Math.max(minSize, Math.min(1 - r.y, r.h + dy));
        }
      }
      cropRect = r;
    }
  };

  const handleWindowMouseUp = () => {
    isPanning = false;
    cropDragHandle = null;
  };

  const startCropDrag = (handle: string, e: MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    cropDragHandle = handle;
    cropDragStart = {
      clientX: e.clientX,
      clientY: e.clientY,
      rect: { ...cropRect },
    };
  };

  const toggleCropMode = () => {
    if (!activeMediaAvailable) return;
    if (!cropMode && showClipVideo && videoElement) {
      videoElement.pause();
    }
    cropMode = !cropMode;
    if (cropMode) {
      cropRect = { x: 0.1, y: 0.1, w: 0.8, h: 0.8 };
    }
  };

  const renderToPngBlob = async (useCrop = false): Promise<Blob | null> => {
    const mediaEl = getActiveMediaElement();
    if (!mediaEl) return null;
    let nw = 0;
    let nh = 0;
    if (mediaEl instanceof HTMLVideoElement) {
      if (mediaEl.readyState < 2 || mediaEl.videoWidth === 0) return null;
      nw = mediaEl.videoWidth;
      nh = mediaEl.videoHeight;
    } else if (mediaEl instanceof HTMLCanvasElement) {
      if (mediaEl.width === 0 || mediaEl.height === 0) return null;
      nw = mediaEl.width;
      nh = mediaEl.height;
    } else {
      if (!mediaEl.complete || mediaEl.naturalWidth === 0) return null;
      nw = mediaEl.naturalWidth;
      nh = mediaEl.naturalHeight;
    }
    const sx = useCrop ? Math.round(cropRect.x * nw) : 0;
    const sy = useCrop ? Math.round(cropRect.y * nh) : 0;
    const sw = useCrop ? Math.max(1, Math.round(cropRect.w * nw)) : nw;
    const sh = useCrop ? Math.max(1, Math.round(cropRect.h * nh)) : nh;

    const canvas = document.createElement("canvas");
    canvas.width = sw;
    canvas.height = sh;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;
    ctx.drawImage(mediaEl, sx, sy, sw, sh, 0, 0, sw, sh);
    return new Promise((resolve) =>
      canvas.toBlob((blob) => resolve(blob), "image/png"),
    );
  };

  const closeCopyFallback = () => {
    if (copyFallbackUrl) {
      URL.revokeObjectURL(copyFallbackUrl);
      copyFallbackUrl = null;
    }
  };

  const openCopyFallback = async (
    useCrop: boolean,
    message: string,
    preRenderedBlob?: Blob | null,
  ) => {
    const blob = preRenderedBlob ?? (await renderToPngBlob(useCrop));
    if (!blob) {
      toast.error("Image indisponible pour la copie.");
      return;
    }
    closeCopyFallback();
    copyFallbackUrl = URL.createObjectURL(blob);
    toast.error(message);
  };

  const copyImage = async (useCrop = false) => {
    const mediaEl = getActiveMediaElement();
    const mediaReady =
      mediaEl instanceof HTMLVideoElement
        ? mediaEl.readyState >= 2 && mediaEl.videoWidth > 0
        : mediaEl instanceof HTMLCanvasElement
          ? mediaEl.width > 0 && mediaEl.height > 0
          : Boolean(mediaEl && mediaEl.complete && mediaEl.naturalWidth > 0);
    if (!mediaReady) {
      toast.error("Image indisponible pour la copie.");
      return;
    }
    const hasAsyncClipboard =
      typeof window !== "undefined" &&
      window.isSecureContext &&
      Boolean(navigator.clipboard?.write) &&
      typeof window.ClipboardItem !== "undefined";

    if (!hasAsyncClipboard) {
      await openCopyFallback(
        useCrop,
        !window?.isSecureContext
          ? "Copie directe bloquée en HTTP non sécurisé : faites un clic droit → Copier l'image."
          : "Presse-papiers direct indisponible : faites un clic droit → Copier l'image.",
      );
      return;
    }

    const blobPromise = renderToPngBlob(useCrop).then((b) => {
      if (!b) throw new Error("PNG_RENDER_FAILED");
      return b;
    });
    const successMsg = useCrop
      ? "Zone recadrée copiée dans le presse-papiers."
      : "Capture copiée dans le presse-papiers.";

    try {
      // Appel synchrone dans le geste utilisateur pour préserver la transient activation (Safari/WebKit)
      await navigator.clipboard.write([
        new ClipboardItem({ "image/png": blobPromise }),
      ]);
      toast.success(successMsg);
    } catch (err) {
      let renderedBlob: Blob | null = null;
      try {
        renderedBlob = await blobPromise;
      } catch {
        toast.error("Image indisponible pour la copie.");
        return;
      }
      if (err instanceof TypeError) {
        try {
          await navigator.clipboard.write([
            new ClipboardItem({ "image/png": renderedBlob }),
          ]);
          toast.success(successMsg);
          return;
        } catch (retryErr) {
          console.warn(
            "[EchoBrowserTab] Échec copie presse-papiers (fallback Blob) :",
            retryErr,
          );
        }
      } else {
        console.warn("[EchoBrowserTab] Échec copie presse-papiers :", err);
      }
      await openCopyFallback(
        useCrop,
        "Copie directe refusée par le navigateur : faites un clic droit → Copier l'image.",
        renderedBlob,
      );
    }
  };

  const exportPng = async (useCrop = false) => {
    const blob = await renderToPngBlob(useCrop);
    if (!blob) {
      toast.error("Image indisponible pour l'export.");
      return;
    }
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const ts = activeFrame?.timestamp ?? Math.floor(Date.now() / 1000);
    a.download = `echo-browser-${useCrop ? "crop-" : ""}${ts}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast.success(
      useCrop ? "Zone recadrée exportée en PNG." : "Capture exportée en PNG.",
    );
  };

  const applyCrop = async () => {
    await exportPng(true);
    cropMode = false;
  };

  const downloadClipMp4 = async () => {
    if (!activeFrame?.clip_url || !boundChatId) return;
    let blobUrl = blobUrlCache[activeFrame.clip_url];
    if (!blobUrl) {
      await fetchAuthenticatedBlob(activeFrame.clip_url);
      blobUrl = blobUrlCache[activeFrame.clip_url];
    }
    if (!blobUrl) {
      toast.error("Clip vidéo indisponible pour le téléchargement.");
      return;
    }
    const a = document.createElement("a");
    a.href = blobUrl;
    const ts = activeFrame?.timestamp ?? Math.floor(Date.now() / 1000);
    a.download = `echo-browser-clip-${ts}.mp4`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast.success("Clip vidéo MP4 téléchargé.");
  };

  const toggleRetainFrame = async () => {
    if (!activeFrame?.file_id || !boundChatId || retaining) return;
    const token = getEchoToken();
    if (!token) return;
    const targetCid = boundChatId;
    const targetFid = activeFrame.file_id;
    const nextRetain = !(
      activeFrame.retained || activeFrame.status === "retained"
    );
    retaining = true;
    try {
      const res = await retainEchoBrowserFrame(
        token,
        targetCid,
        targetFid,
        nextRetain,
      );
      if (targetCid !== boundChatId) return;
      const updatedFields = {
        retained: Boolean(res.retained),
        status:
          res.resource_status || (res.retained ? "retained" : "transient"),
      };
      persistedFrames = persistedFrames.map((f) =>
        f.file_id === targetFid ? { ...f, ...updatedFields } : f,
      );
      echoBrowserLiveFrames.update((all) => {
        const list = all[targetCid];
        if (!list) return all;
        return {
          ...all,
          [targetCid]: list.map((f) =>
            f.file_id === targetFid ? { ...f, ...updatedFields } : f,
          ),
        };
      });
      toast.success(
        res.retained
          ? "Capture conservée durablement."
          : "Capture repassée en mode éphémère.",
      );
    } catch (err: any) {
      toast.error(
        err?.message || "Impossible de modifier la conservation de la capture.",
      );
    } finally {
      retaining = false;
    }
  };

  const formatTimestamp = (ts?: number): string => {
    if (!ts) return "";
    const d = new Date(ts > 1e12 ? ts : ts * 1000);
    if (Number.isNaN(d.getTime())) return "";
    return d.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  };
</script>

<svelte:window
  on:mousemove={handleWindowMouseMove}
  on:mouseup={handleWindowMouseUp}
  on:keydown={(e) => {
    if (e.key === "Escape" && copyFallbackUrl) {
      closeCopyFallback();
    }
  }}
/>

<div
  class="flex flex-col h-full bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 overflow-hidden select-none"
>
  <!-- Barre supérieure : statut Live, URL et outils d'inspection -->
  <div
    class="flex flex-wrap items-center justify-between gap-2 px-3 py-2 border-b border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 shrink-0"
  >
    <div class="flex items-center gap-2 min-w-0 flex-1">
      {#if isOnLatestLive}
        <span
          class="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-red-500/15 text-red-600 dark:text-red-400 border border-red-500/30 shrink-0"
        >
          🔴 EN DIRECT
        </span>
      {:else}
        <span
          class="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30 shrink-0"
        >
          ⏸️ DIFFÉRÉ
        </span>
      {/if}

      {#if showLiveFeed && liveUrl}
        <span
          class="text-xs font-mono text-gray-600 dark:text-gray-300 truncate bg-white dark:bg-gray-900 px-2 py-0.5 rounded border border-gray-200 dark:border-gray-700"
          title={liveUrl}
        >
          {liveUrl}
        </span>
      {:else if activeFrame?.url}
        <span
          class="text-xs font-mono text-gray-600 dark:text-gray-300 truncate bg-white dark:bg-gray-900 px-2 py-0.5 rounded border border-gray-200 dark:border-gray-700"
          title={activeFrame.url}
        >
          {activeFrame.url}
        </span>
      {:else}
        <span class="text-xs text-gray-400 truncate">Navigateur Web ECHO</span>
      {/if}

      {#if activeFrame?.timestamp && !showLiveFeed}
        <span class="text-[11px] font-mono text-gray-400 shrink-0">
          {formatTimestamp(activeFrame.timestamp)}
        </span>
      {/if}

      {#if !showLiveFeed && activeFrame?.file_id}
        {@const isRetained = Boolean(
          activeFrame.retained || activeFrame.status === "retained",
        )}
        <button
          type="button"
          disabled={retaining}
          on:click={() => void toggleRetainFrame()}
          class="inline-flex items-center gap-1 text-[11px] font-medium px-2 py-0.5 rounded-full border transition shrink-0 disabled:opacity-50 {isRetained
            ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30 hover:bg-emerald-500/25'
            : 'bg-gray-200/70 dark:bg-gray-800 text-gray-600 dark:text-gray-300 border-gray-300 dark:border-gray-700 hover:bg-gray-300/70 dark:hover:bg-gray-700'}"
          title={isRetained
            ? "Cliquer pour repasser en mode éphémère"
            : "Cliquer pour conserver durablement cette capture"}
        >
          {isRetained ? "📌 Conservée" : "📌 Conserver"}
        </button>
      {/if}
    </div>

    <div class="flex items-center gap-1 shrink-0">
      {#if !isOnLatestLive && (frames.length > 0 || liveActive)}
        <button
          type="button"
          on:click={returnToLive}
          class="px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-red-600 hover:bg-red-700 text-white shadow-xs transition mr-1"
        >
          🔴 Retour au temps réel
        </button>
      {/if}

      {#if !showLiveFeed && activeFrame?.clip_url}
        <button
          type="button"
          on:click={() => {
            stopPlayback();
            showClipVideo = !showClipVideo;
            cropMode = false;
          }}
          class="px-2 py-1 rounded-lg text-xs font-medium border transition {showClipVideo
            ? 'bg-indigo-600 text-white border-indigo-600'
            : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700'}"
          title={showClipVideo
            ? "Afficher la capture image HD"
            : "Voir le clip vidéo de l'action"}
        >
          {showClipVideo ? "🖼️ Image HD" : "🎬 Voir l'action"}
        </button>
        <button
          type="button"
          on:click={() => void downloadClipMp4()}
          class="px-2 py-1 rounded-lg text-xs font-medium bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 transition"
          title="Télécharger le clip vidéo MP4"
        >
          ⬇️ MP4
        </button>
      {/if}

      <div
        class="flex items-center bg-gray-200/70 dark:bg-gray-800 rounded-lg p-0.5 text-[11px] font-mono"
      >
        <button
          type="button"
          on:click={() => adjustZoom(-0.25)}
          class="px-1.5 py-0.5 hover:bg-white dark:hover:bg-gray-700 rounded"
          title="Zoom arrière">-</button
        >
        <button
          type="button"
          on:click={resetZoom}
          class="px-2 py-0.5 hover:bg-white dark:hover:bg-gray-700 rounded"
          title="Réinitialiser zoom et position"
          >{Math.round(zoom * 100)}%</button
        >
        <button
          type="button"
          on:click={() => adjustZoom(0.25)}
          class="px-1.5 py-0.5 hover:bg-white dark:hover:bg-gray-700 rounded"
          title="Zoom avant">+</button
        >
      </div>

      <button
        type="button"
        disabled={!activeMediaAvailable}
        on:click={toggleCropMode}
        class="px-2 py-1 rounded-lg text-xs font-medium border transition disabled:opacity-40 {cropMode
          ? 'bg-sky-600 text-white border-sky-600'
          : 'bg-white dark:bg-gray-800 border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700'}"
        title="Recadrer une sous-région"
      >
        ✂️ Recadrer
      </button>
      <button
        type="button"
        disabled={!activeMediaAvailable}
        on:click={() => void copyImage(false)}
        class="px-2 py-1 rounded-lg text-xs font-medium bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 transition disabled:opacity-40"
        title="Copier l'image PNG"
      >
        📋 Copier
      </button>
      <button
        type="button"
        disabled={!activeMediaAvailable}
        on:click={() => void exportPng(false)}
        class="px-2 py-1 rounded-lg text-xs font-medium bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 transition disabled:opacity-40"
        title="Exporter en PNG"
      >
        💾 Exporter PNG
      </button>
    </div>
  </div>

  {#if cropMode}
    <div
      class="flex items-center justify-between px-3 py-1.5 bg-sky-50 dark:bg-sky-950/40 border-b border-sky-200 dark:border-sky-800 text-xs shrink-0"
    >
      <span class="text-sky-800 dark:text-sky-200 font-medium">
        Mode recadrage actif — ajustez les 8 poignées puis appliquez pour
        exporter en pixels naturels.
      </span>
      <div class="flex items-center gap-2">
        <button
          type="button"
          on:click={() => (cropMode = false)}
          class="px-2.5 py-1 rounded bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 text-gray-700 dark:text-gray-200 hover:bg-gray-100"
        >
          Annuler
        </button>
        <button
          type="button"
          on:click={() => void applyCrop()}
          class="px-2.5 py-1 rounded bg-sky-600 hover:bg-sky-700 text-white font-semibold"
        >
          Appliquer
        </button>
      </div>
    </div>
  {/if}

  <!-- Zone d'affichage de la capture / flux live / clip vidéo -->
  <div
    bind:clientWidth={viewportWidth}
    bind:clientHeight={viewportHeight}
    class="relative flex-1 min-h-0 overflow-hidden bg-gray-950/95 flex items-center justify-center {cropMode
      ? 'cursor-crosshair'
      : isPanning
        ? 'cursor-grabbing'
        : 'cursor-grab'}"
    on:wheel={handleWheel}
    on:mousedown={handleMouseDown}
    role="region"
    aria-label="Visionneuse Navigateur Web ECHO"
  >
    {#if showLiveFeed}
      <div
        class="relative {fittedDimensions
          ? 'shrink-0'
          : 'inline-block max-w-full max-h-full'}"
        style="{fittedDimensions
          ? `width: ${fittedDimensions.width}px; height: ${fittedDimensions.height}px; `
          : ''}transform: translate({panX}px, {panY}px) scale({zoom}); transform-origin: center center;"
      >
        <canvas
          bind:this={liveCanvasEl}
          width={liveBitmapDims.w}
          height={liveBitmapDims.h}
          class="{fittedDimensions
            ? 'w-full h-full block'
            : 'max-w-full max-h-full'} object-contain pointer-events-none select-none shadow-2xl"
        ></canvas>

        {#if liveCursor}
          <div
            class="absolute pointer-events-none -translate-x-1/2 -translate-y-1/2 transition-all duration-75"
            style="left: {(liveCursor.x / liveViewport.w) * 100}%; top: {(liveCursor.y / liveViewport.h) * 100}%;"
          >
            {#if liveCursor.clicking}
              <span
                class="absolute inset-0 -m-2 rounded-full border-2 border-[#ff0044] animate-ping opacity-75"
              ></span>
            {/if}
            <svg width="20" height="20" viewBox="0 0 20 20" class="drop-shadow">
              <circle
                cx="10"
                cy="10"
                r={liveCursor.clicking ? 6 : 5}
                fill="#ff0044"
                stroke="#ffffff"
                stroke-width="2"
              />
            </svg>
          </div>
        {/if}

        {#if cropMode}
          <!-- Overlay de recadrage 8 poignées -->
          <div
            class="absolute border-2 border-sky-400 bg-sky-400/10 shadow-[0_0_0_9999px_rgba(0,0,0,0.55)] cursor-move"
            style="left: {cropRect.x * 100}%; top: {cropRect.y *
              100}%; width: {cropRect.w * 100}%; height: {cropRect.h * 100}%;"
            on:mousedown={(e) => startCropDrag("move", e)}
            role="presentation"
          >
            {#each [{ id: "nw", cls: "-top-1.5 -left-1.5 cursor-nwse-resize" }, { id: "n", cls: "-top-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize" }, { id: "ne", cls: "-top-1.5 -right-1.5 cursor-nesw-resize" }, { id: "e", cls: "top-1/2 -right-1.5 -translate-y-1/2 cursor-ew-resize" }, { id: "se", cls: "-bottom-1.5 -right-1.5 cursor-nwse-resize" }, { id: "s", cls: "-bottom-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize" }, { id: "sw", cls: "-bottom-1.5 -left-1.5 cursor-nesw-resize" }, { id: "w", cls: "top-1/2 -left-1.5 -translate-y-1/2 cursor-ew-resize" }] as handle (handle.id)}
              <div
                class="absolute w-3 h-3 bg-white border-2 border-sky-500 rounded-xs {handle.cls}"
                on:mousedown={(e) => startCropDrag(handle.id, e)}
                role="presentation"
              ></div>
            {/each}
          </div>
        {/if}
      </div>
    {:else if loading && frames.length === 0}
      <Spinner className="w-7 h-7 text-sky-400" />
    {:else if frames.length === 0}
      <div class="text-center p-6 text-gray-400 text-xs space-y-1.5">
        <div class="text-2xl">🌐</div>
        <div class="font-medium text-gray-300">
          Aucune capture navigateur pour cette session
        </div>
        <div>
          Les captures apparaîtront ici en direct dès qu'une navigation
          démarrera.
        </div>
      </div>
    {:else if showClipVideo}
      {#if !activeClipSrc}
        <Spinner className="w-6 h-6 text-sky-400" />
      {:else}
        <div
          class="relative {fittedDimensions
            ? 'shrink-0'
            : 'inline-block max-w-full max-h-full'}"
          style="{fittedDimensions
            ? `width: ${fittedDimensions.width}px; height: ${fittedDimensions.height}px; `
            : ''}transform: translate({panX}px, {panY}px) scale({zoom}); transform-origin: center center;"
        >
          <video
            bind:this={videoElement}
            src={activeClipSrc}
            autoplay
            playsinline
            muted
            controls={!isPlaying && !cropMode}
            on:loadedmetadata={handleVideoMetadata}
            on:loadeddata={handleVideoMetadata}
            on:ended={handleVideoEnded}
            on:error={handleVideoError}
            class="{fittedDimensions
              ? 'w-full h-full block'
              : 'max-w-full max-h-full'} object-contain select-none shadow-2xl {cropMode
              ? 'pointer-events-none'
              : ''}"
          >
            <track kind="captions" />
          </video>

          {#if cropMode}
            <!-- Overlay de recadrage 8 poignées -->
            <div
              class="absolute border-2 border-sky-400 bg-sky-400/10 shadow-[0_0_0_9999px_rgba(0,0,0,0.55)] cursor-move"
              style="left: {cropRect.x * 100}%; top: {cropRect.y *
                100}%; width: {cropRect.w * 100}%; height: {cropRect.h * 100}%;"
              on:mousedown={(e) => startCropDrag("move", e)}
              role="presentation"
            >
              {#each [{ id: "nw", cls: "-top-1.5 -left-1.5 cursor-nwse-resize" }, { id: "n", cls: "-top-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize" }, { id: "ne", cls: "-top-1.5 -right-1.5 cursor-nesw-resize" }, { id: "e", cls: "top-1/2 -right-1.5 -translate-y-1/2 cursor-ew-resize" }, { id: "se", cls: "-bottom-1.5 -right-1.5 cursor-nwse-resize" }, { id: "s", cls: "-bottom-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize" }, { id: "sw", cls: "-bottom-1.5 -left-1.5 cursor-nesw-resize" }, { id: "w", cls: "top-1/2 -left-1.5 -translate-y-1/2 cursor-ew-resize" }] as handle (handle.id)}
                <div
                  class="absolute w-3 h-3 bg-white border-2 border-sky-500 rounded-xs {handle.cls}"
                  on:mousedown={(e) => startCropDrag(handle.id, e)}
                  role="presentation"
                ></div>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    {:else if !activeImageSrc}
      <Spinner className="w-6 h-6 text-sky-400" />
    {:else}
      <div
        class="relative {fittedDimensions
          ? 'shrink-0'
          : 'inline-block max-w-full max-h-full'}"
        style="{fittedDimensions
          ? `width: ${fittedDimensions.width}px; height: ${fittedDimensions.height}px; `
          : ''}transform: translate({panX}px, {panY}px) scale({zoom}); transform-origin: center center;"
      >
        <img
          bind:this={imgElement}
          src={activeImageSrc}
          alt={activeFrame?.action || activeFrame?.url || "Capture navigateur"}
          on:load={handleImageLoad}
          class="{fittedDimensions
            ? 'w-full h-full block'
            : 'max-w-full max-h-full'} object-contain pointer-events-none select-none shadow-2xl"
          draggable="false"
        />

        {#if cropMode}
          <!-- Overlay de recadrage 8 poignées -->
          <div
            class="absolute border-2 border-sky-400 bg-sky-400/10 shadow-[0_0_0_9999px_rgba(0,0,0,0.55)] cursor-move"
            style="left: {cropRect.x * 100}%; top: {cropRect.y *
              100}%; width: {cropRect.w * 100}%; height: {cropRect.h * 100}%;"
            on:mousedown={(e) => startCropDrag("move", e)}
            role="presentation"
          >
            {#each [{ id: "nw", cls: "-top-1.5 -left-1.5 cursor-nwse-resize" }, { id: "n", cls: "-top-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize" }, { id: "ne", cls: "-top-1.5 -right-1.5 cursor-nesw-resize" }, { id: "e", cls: "top-1/2 -right-1.5 -translate-y-1/2 cursor-ew-resize" }, { id: "se", cls: "-bottom-1.5 -right-1.5 cursor-nwse-resize" }, { id: "s", cls: "-bottom-1.5 left-1/2 -translate-x-1/2 cursor-ns-resize" }, { id: "sw", cls: "-bottom-1.5 -left-1.5 cursor-nesw-resize" }, { id: "w", cls: "top-1/2 -left-1.5 -translate-y-1/2 cursor-ew-resize" }] as handle (handle.id)}
              <div
                class="absolute w-3 h-3 bg-white border-2 border-sky-500 rounded-xs {handle.cls}"
                on:mousedown={(e) => startCropDrag(handle.id, e)}
                role="presentation"
              ></div>
            {/each}
          </div>
        {/if}
      </div>
    {/if}
  </div>

  <!-- Barre DVR Timeshift -->
  <div
    class="flex flex-wrap items-center gap-2 px-3 py-2 border-t border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-850 shrink-0 text-xs"
  >
    <div class="flex items-center gap-1">
      <button
        type="button"
        disabled={frames.length === 0 || currentIndex === 0}
        on:click={() => {
          stopPlayback();
          goToIndex(0);
        }}
        class="px-2 py-1 rounded bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40"
        title="Début">⏮</button
      >
      <button
        type="button"
        disabled={frames.length === 0 || currentIndex <= 0}
        on:click={() => {
          stopPlayback();
          goToIndex(currentIndex - 1);
        }}
        class="px-2 py-1 rounded bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40"
        title="Précédent (-1)">◀</button
      >
      <button
        type="button"
        disabled={frames.length <= 1}
        on:click={togglePlayback}
        class="px-2.5 py-1 rounded bg-indigo-600 hover:bg-indigo-700 text-white font-semibold disabled:opacity-40"
        title={isPlaying ? "Pause" : "Lecture auto"}
        >{isPlaying ? "⏸" : "▶"}</button
      >

      <select
        bind:value={playbackSpeedMs}
        on:change={onSpeedChange}
        aria-label="Vitesse de lecture"
        class="rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 px-1.5 py-1 text-[11px] font-mono"
      >
        <option value={500}>0.5s</option>
        <option value={1000}>1s</option>
        <option value={2000}>2s</option>
        <option value={3000}>3s</option>
        <option value={5000}>5s</option>
        <option value={10000}>10s</option>
      </select>

      <button
        type="button"
        on:click={() => (playClipsInAutoplay = !playClipsInAutoplay)}
        class="px-2 py-1 rounded border text-[11px] font-medium transition {playClipsInAutoplay
          ? 'bg-indigo-600/15 text-indigo-700 dark:text-indigo-300 border-indigo-500/40'
          : 'bg-white dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-200 dark:border-gray-700'}"
        title="Lire automatiquement les clips vidéo pendant la lecture continue"
      >
        🎬 Clips
      </button>

      <button
        type="button"
        disabled={frames.length === 0 || currentIndex >= frames.length - 1}
        on:click={() => {
          stopPlayback();
          goToIndex(currentIndex + 1);
        }}
        class="px-2 py-1 rounded bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40"
        title="Suivant (+1)">▶</button
      >
      <button
        type="button"
        disabled={frames.length === 0 || currentIndex >= frames.length - 1}
        on:click={() => {
          stopPlayback();
          goToIndex(frames.length - 1);
        }}
        class="px-2 py-1 rounded bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-40"
        title="Fin">⏭</button
      >
    </div>

    <input
      type="range"
      min={0}
      max={Math.max(0, frames.length - 1)}
      value={currentIndex}
      disabled={frames.length <= 1}
      on:input={(e) => {
        stopPlayback();
        goToIndex(Number(e.currentTarget.value));
      }}
      class="flex-1 min-w-[120px] accent-indigo-600"
      aria-label="Curseur temporel"
    />

    <span
      class="font-mono text-[11px] text-gray-600 dark:text-gray-300 shrink-0"
    >
      {frames.length > 0 ? `${currentIndex + 1} / ${frames.length}` : "0 / 0"}
    </span>
  </div>

  {#if copyFallbackUrl}
    <!-- Modale de repli quand l'API Clipboard est bloquée (HTTP non sécurisé / permission refusée) -->
    <div
      class="fixed inset-0 z-50 bg-black/80 backdrop-blur-xs flex flex-col items-center justify-center p-4 gap-3"
      role="presentation"
      on:click|self={closeCopyFallback}
    >
      <div
        class="flex items-center gap-3 px-4 py-2 rounded-full bg-emerald-500 text-gray-950 text-xs font-semibold shadow-lg"
      >
        <span>Mode repli : faites Clic droit → « Copier l'image »</span>
        <button
          type="button"
          on:click={closeCopyFallback}
          class="px-2 py-0.5 rounded-full bg-black/15 hover:bg-black/25 text-gray-950 font-bold"
          aria-label="Fermer"
        >
          ✕
        </button>
      </div>
      <img
        src={copyFallbackUrl}
        alt="Capture prête pour copie manuelle"
        class="max-w-[90vw] max-h-[75vh] object-contain rounded-lg border border-gray-700 shadow-2xl select-auto"
      />
    </div>
  {/if}
</div>
