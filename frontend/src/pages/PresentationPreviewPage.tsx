import { useCallback, useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react';
import { Button, Modal, Result, Skeleton, message } from 'antd';
import {
  CheckCircleFilled,
  ClockCircleOutlined,
  CloseCircleFilled,
  DownloadOutlined,
  ExpandOutlined,
  ExclamationCircleFilled,
  LeftOutlined,
  RightOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { useParams } from 'react-router-dom';
import { api } from '../api';
import {
  buildPresentationPreview,
  type PresentationPreviewSlide,
} from '../features/presentationPreview/presentationPreview';
import { toArtifactUrl } from '../lib/artifactUrls';
import type { RunDetail } from '../types';
import './PresentationPreviewPage.css';

const statusIcon = {
  success: <CheckCircleFilled />,
  active: <SyncOutlined spin />,
  warning: <ExclamationCircleFilled />,
  error: <CloseCircleFilled />,
};

const SlideArtwork = ({ slide, fullscreen = false }: { slide: PresentationPreviewSlide | null; fullscreen?: boolean }) => {
  const url = toArtifactUrl(slide?.artifactPath);
  if (!slide || !url) {
    return (
      <div className={`presentation-preview-empty ${fullscreen ? 'is-fullscreen' : ''}`}>
        <span>{slide ? `Slide ${slide.position}` : 'Presentation preview'}</span>
        <strong>Preview unavailable</strong>
        <p>{slide ? 'This slide has no displayable artifact yet.' : 'No slide artifact is available for this run.'}</p>
      </div>
    );
  }
  return <img src={url} alt={`Slide ${slide.position}: ${slide.title}`} draggable={false} />;
};

export default function PresentationPreviewPage() {
  const { runId: runIdParam } = useParams();
  const runId = Number(runIdParam);
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [fullscreenOpen, setFullscreenOpen] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const fullscreenButtonRef = useRef<HTMLButtonElement>(null);
  const swipeStartRef = useRef<{ pointerId: number; x: number } | null>(null);

  const fetchRun = useCallback(async (silent = false) => {
    if (!Number.isFinite(runId) || runId <= 0) {
      setLoadError('This presentation link is invalid.');
      setLoading(false);
      return;
    }
    if (!silent) setLoading(true);
    try {
      const nextRun = await api.runs.get(runId);
      const nextView = buildPresentationPreview(nextRun);
      setRun(nextRun);
      setLoadError(null);
      setSelectedId((current) => (
        nextView.slides.some((slide) => slide.id === current)
          ? current
          : nextView.slides.find((slide) => slide.displayable)?.id
            || nextView.slides[0]?.id
            || null
      ));
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setLoadError(detail);
      if (!silent) message.error(`Failed to load presentation: ${detail}`);
    } finally {
      if (!silent) setLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    queueMicrotask(() => {
      void fetchRun();
    });
  }, [fetchRun]);

  const view = useMemo(() => (run ? buildPresentationPreview(run) : null), [run]);

  useEffect(() => {
    if (!view?.shouldPoll) return undefined;
    const timer = window.setInterval(() => void fetchRun(true), 3000);
    return () => window.clearInterval(timer);
  }, [fetchRun, view?.shouldPoll]);

  const selectedIndex = view?.slides.findIndex((slide) => slide.id === selectedId) ?? -1;
  const selectedSlide = selectedIndex >= 0 ? view?.slides[selectedIndex] || null : null;
  const canMovePrevious = selectedIndex > 0;
  const canMoveNext = Boolean(view && selectedIndex >= 0 && selectedIndex < view.slides.length - 1);

  const selectRelative = useCallback((delta: number) => {
    if (!view?.slides.length || selectedIndex < 0) return;
    const nextIndex = Math.min(view.slides.length - 1, Math.max(0, selectedIndex + delta));
    setSelectedId(view.slides[nextIndex]?.id || null);
  }, [selectedIndex, view]);

  useEffect(() => {
    if (!fullscreenOpen) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'ArrowLeft' && canMovePrevious) {
        event.preventDefault();
        selectRelative(-1);
      }
      if (event.key === 'ArrowRight' && canMoveNext) {
        event.preventDefault();
        selectRelative(1);
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [canMoveNext, canMovePrevious, fullscreenOpen, selectRelative]);

  const downloadPresentation = async () => {
    if (!view?.downloadEnabled) return;
    setDownloading(true);
    let objectUrl: string | null = null;
    try {
      const result = await api.runs.download(view.runId);
      objectUrl = URL.createObjectURL(result.blob);
      const anchor = document.createElement('a');
      anchor.href = objectUrl;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
    } catch (error) {
      message.error(`Download failed: ${error instanceof Error ? error.message : String(error)}`);
    } finally {
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      setDownloading(false);
    }
  };

  const startFullscreenSwipe = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return;
    swipeStartRef.current = { pointerId: event.pointerId, x: event.clientX };
    event.currentTarget.setPointerCapture(event.pointerId);
  };

  const finishFullscreenSwipe = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = swipeStartRef.current;
    swipeStartRef.current = null;
    if (!start || start.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    const distance = event.clientX - start.x;
    if (Math.abs(distance) < 48) return;
    selectRelative(distance < 0 ? 1 : -1);
  };

  if (loading) {
    return (
      <main className="presentation-preview-page presentation-preview-loading" aria-label="Loading presentation preview" aria-busy="true">
        <div className="presentation-preview-content presentation-preview-skeleton">
          <section className="presentation-preview-hero" aria-hidden="true">
            <div className="presentation-preview-stage presentation-preview-skeleton-stage">
              <Skeleton.Node active />
            </div>
            <aside className="presentation-preview-summary">
              <Skeleton active title={{ width: '76%' }} paragraph={{ rows: 5 }} />
            </aside>
          </section>
          <section className="presentation-preview-story presentation-preview-skeleton-story" aria-hidden="true">
            <Skeleton active title={{ width: 180 }} paragraph={{ rows: 2 }} />
          </section>
        </div>
      </main>
    );
  }

  if (!view || loadError) {
    return (
      <main className="presentation-preview-page presentation-preview-error">
        <Result
          status="warning"
          title="Presentation preview unavailable"
          subTitle={loadError || 'This run could not be loaded.'}
          extra={<Button onClick={() => void fetchRun()}>Try again</Button>}
        />
      </main>
    );
  }

  return (
    <main className="presentation-preview-page">
      <div className="presentation-preview-content">
        <section className="presentation-preview-hero" aria-label="Presentation preview">
          <div className="presentation-preview-stage">
            <SlideArtwork slide={selectedSlide} />
          </div>

          <aside className="presentation-preview-summary">
            <div className={`presentation-preview-status is-${view.statusTone}`}>
              {statusIcon[view.statusTone]}
              <span>{view.statusTitle}</span>
            </div>
            <h1>{view.title}</h1>
            <div className="presentation-preview-facts">
              <div>
                <CheckCircleFilled aria-hidden="true" />
                <span>{view.statusDescription}</span>
              </div>
              {view.durationLabel && (
                <div>
                  <ClockCircleOutlined aria-hidden="true" />
                  <span>{view.durationLabel}</span>
                </div>
              )}
            </div>
            <div className="presentation-preview-actions">
              <Button
                type="primary"
                size="large"
                icon={<DownloadOutlined />}
                loading={downloading}
                disabled={!view.downloadEnabled}
                onClick={() => void downloadPresentation()}
                aria-label="Download presentation package"
              >
                Download presentation package
              </Button>
              <Button
                ref={fullscreenButtonRef}
                size="large"
                icon={<ExpandOutlined />}
                disabled={!selectedSlide?.displayable}
                onClick={() => setFullscreenOpen(true)}
                aria-label="Fullscreen preview"
              >
                Fullscreen preview
              </Button>
            </div>
          </aside>
        </section>

        <nav className="presentation-preview-controls" aria-label="Slide navigation">
          <Button
            shape="circle"
            icon={<LeftOutlined />}
            disabled={!canMovePrevious}
            onClick={() => selectRelative(-1)}
            aria-label="Previous slide"
          />
          <strong aria-live="polite">{selectedIndex >= 0 ? selectedIndex + 1 : 0} / {view.slides.length}</strong>
          <Button
            shape="circle"
            icon={<RightOutlined />}
            disabled={!canMoveNext}
            onClick={() => selectRelative(1)}
            aria-label="Next slide"
          />
        </nav>

        <section className="presentation-preview-story" aria-labelledby="presentation-story-title">
          <h2 id="presentation-story-title">Presentation story ({view.slides.length} slides)</h2>
          <div className="presentation-preview-rail">
            {view.slides.map((slide) => {
              const selected = slide.id === selectedId;
              const thumbnailUrl = toArtifactUrl(slide.artifactPath);
              return (
                <button
                  type="button"
                  key={slide.id}
                  className={`presentation-preview-thumbnail ${selected ? 'is-selected' : ''}`}
                  onClick={() => setSelectedId(slide.id)}
                  aria-label={`Select slide ${slide.position} ${slide.title}`}
                  aria-pressed={selected}
                >
                  <span className="presentation-preview-thumbnail-art">
                    {thumbnailUrl ? (
                      <img src={thumbnailUrl} alt="" />
                    ) : (
                      <span className="presentation-preview-thumbnail-empty">Preview unavailable</span>
                    )}
                    <span className="presentation-preview-thumbnail-number">{slide.position}</span>
                  </span>
                  <span className="presentation-preview-thumbnail-title">{slide.title}</span>
                </button>
              );
            })}
          </div>
        </section>
      </div>

      <Modal
        open={fullscreenOpen}
        title="Presentation fullscreen preview"
        footer={null}
        keyboard
        destroyOnHidden
        wrapClassName="presentation-preview-fullscreen"
        width="100vw"
        onCancel={() => setFullscreenOpen(false)}
        afterClose={() => fullscreenButtonRef.current?.focus()}
      >
        <div
          className="presentation-preview-fullscreen-stage"
          onPointerDown={startFullscreenSwipe}
          onPointerUp={finishFullscreenSwipe}
          onPointerCancel={() => { swipeStartRef.current = null; }}
        >
          <SlideArtwork slide={selectedSlide} fullscreen />
        </div>
        <div className="presentation-preview-fullscreen-controls">
          <Button
            shape="circle"
            icon={<LeftOutlined />}
            disabled={!canMovePrevious}
            onClick={() => selectRelative(-1)}
            aria-label="Previous fullscreen slide"
          />
          <strong aria-live="polite">{selectedIndex >= 0 ? selectedIndex + 1 : 0} / {view.slides.length}</strong>
          <Button
            shape="circle"
            icon={<RightOutlined />}
            disabled={!canMoveNext}
            onClick={() => selectRelative(1)}
            aria-label="Next fullscreen slide"
          />
        </div>
      </Modal>
    </main>
  );
}
